/**
 * generate-atlas-row — produce a single AtlasRow by orchestrating calls to
 * PQS's production endpoints.
 *
 * Pipeline:
 *   1. POST /api/score/full with the prompt → pre-flight scores + Opus's
 *      original output from that prompt.
 *   2. POST /api/atlas/score/output with { prompt, output } → post-flight
 *      scores + per-dimension rationales for that output.
 *   3. Derive pre_grade via canonical thresholds.
 *   4. Assemble the AtlasRow.
 *
 * Both API calls retry up to 3 times on transient failures (429, 5xx,
 * network timeouts) with exponential backoff (1s, 2s, 4s).
 *
 * Standalone CLI usage:
 *   tsx scripts/generate-atlas-row.ts --prompt "..." [--vertical general]
 *
 * Exported `generateAtlasRow(prompt, vertical, opts?)` for use by
 * generate-atlas-batch.ts.
 *
 * Env required:
 *   PQS_API_KEY        bearer key from pqs_api_keys table
 *   PQS_INTERNAL_TOKEN attribution token; sent as X-PQS-Internal so atlas
 *                      traffic is flagged is_internal=true in pqs_api_calls
 *
 * Env optional:
 *   PQS_BASE_URL       override for testing (default: https://pqs.onchainintel.net)
 */

import { parseArgs } from "node:util";
import {
  AtlasRow,
  SourceType,
  Vertical,
  VALID_VERTICALS,
  PRE_DIMENSION_KEYS,
  POST_DIMENSION_KEYS,
  gradeLabel,
} from "../schemas/atlas-row.js";

// ---------- config ----------

const PQS_BASE_URL = process.env.PQS_BASE_URL ?? "https://pqs.onchainintel.net";
const SCORE_FULL_PATH = "/api/score/full";
const ATLAS_OUTPUT_PATH = "/api/atlas/score/output";

const RETRY_ATTEMPTS = 3;
const RETRY_BASE_MS = 1000;
// Status codes we treat as transient and retry on.
const RETRY_STATUSES = new Set([408, 429, 500, 502, 503, 504]);

const REQUEST_TIMEOUT_MS = 60_000;

// ---------- PQS response types (loose — we only extract what we need) ----------

interface ScoreFullResponse {
  pqs_version?: string;
  original?: {
    prompt?: string;
    score?: {
      total?: number;
      out_of?: number;
      dimensions?: Record<string, number>;
    };
    output?: string;
  };
  optimized?: unknown;
}

interface AtlasScoreOutputResponse {
  pqs_version?: string;
  vertical?: string;
  score?: {
    total?: number;
    out_of?: number;
    dimensions?: Record<string, number>;
    rationales?: Record<string, string>;
  };
}

// ---------- generic HTTP helper with retry ----------

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function postWithRetry<T>(
  url: string,
  body: unknown,
  headers: Record<string, string>,
  maxAttempts = RETRY_ATTEMPTS,
): Promise<T> {
  let lastErr: unknown = null;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...headers },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      clearTimeout(timeoutId);

      if (resp.ok) {
        return (await resp.json()) as T;
      }

      const text = await resp.text().catch(() => "");
      // Non-retriable client errors (400, 401, 403, 404, 422, etc.)
      if (!RETRY_STATUSES.has(resp.status)) {
        throw new Error(`${url} → HTTP ${resp.status}: ${text.slice(0, 300)}`);
      }

      lastErr = new Error(`${url} → HTTP ${resp.status}: ${text.slice(0, 300)}`);
    } catch (e) {
      clearTimeout(timeoutId);
      const isAbort = (e as Error)?.name === "AbortError";
      lastErr = isAbort
        ? new Error(`${url} timed out after ${REQUEST_TIMEOUT_MS}ms`)
        : e;
      // Network-ish errors are retriable; rethrow immediately on the
      // non-retriable-HTTP-status branch above.
    }

    if (attempt < maxAttempts - 1) {
      const delay = RETRY_BASE_MS * 2 ** attempt; // 1s, 2s, 4s
      await sleep(delay);
    }
  }
  throw lastErr ?? new Error(`${url} failed after ${maxAttempts} attempts`);
}

// ---------- PQS calls ----------

function buildAuthHeaders(): Record<string, string> {
  const apiKey = process.env.PQS_API_KEY;
  if (!apiKey) {
    throw new Error(
      "PQS_API_KEY env var not set. Export your PQS bearer key before running.",
    );
  }
  const headers: Record<string, string> = {
    Authorization: `Bearer ${apiKey}`,
  };
  const internal = process.env.PQS_INTERNAL_TOKEN;
  if (internal) {
    headers["X-PQS-Internal"] = internal;
  }
  return headers;
}

async function callScoreFull(
  prompt: string,
  vertical: Vertical,
): Promise<ScoreFullResponse> {
  return postWithRetry<ScoreFullResponse>(
    PQS_BASE_URL + SCORE_FULL_PATH,
    { prompt, vertical },
    buildAuthHeaders(),
  );
}

async function callAtlasScoreOutput(
  prompt: string,
  output: string,
  vertical: Vertical,
): Promise<AtlasScoreOutputResponse> {
  return postWithRetry<AtlasScoreOutputResponse>(
    PQS_BASE_URL + ATLAS_OUTPUT_PATH,
    { prompt, output, vertical },
    buildAuthHeaders(),
  );
}

// ---------- row assembly ----------

function pickPreScore(
  resp: ScoreFullResponse,
): { total: number; dimensions: Record<string, number>; output: string } {
  const total = resp.original?.score?.total;
  const dimensions = resp.original?.score?.dimensions;
  const output = resp.original?.output;

  if (typeof total !== "number") {
    throw new Error(
      "Pre-flight score missing `original.score.total` — response shape changed?",
    );
  }
  if (!dimensions || typeof dimensions !== "object") {
    throw new Error(
      "Pre-flight score missing `original.score.dimensions` — response shape changed?",
    );
  }
  if (typeof output !== "string") {
    throw new Error(
      "Pre-flight response missing `original.output` — cannot proceed to post-flight without Opus's generation.",
    );
  }

  // Verify all 8 dimensions present (throw early rather than quietly defaulting).
  for (const key of PRE_DIMENSION_KEYS) {
    if (typeof dimensions[key] !== "number") {
      throw new Error(`Pre-flight dimension missing or non-numeric: ${key}`);
    }
  }

  return { total, dimensions, output };
}

function pickPostScore(resp: AtlasScoreOutputResponse): {
  total: number;
  dimensions: Record<string, number>;
  rationales: Record<string, string>;
} {
  const total = resp.score?.total;
  const dimensions = resp.score?.dimensions;
  const rationales = resp.score?.rationales;

  if (typeof total !== "number") {
    throw new Error(
      "Post-flight score missing `score.total` — response shape changed?",
    );
  }
  if (!dimensions || typeof dimensions !== "object") {
    throw new Error(
      "Post-flight score missing `score.dimensions` — response shape changed?",
    );
  }
  if (!rationales || typeof rationales !== "object") {
    throw new Error(
      "Post-flight score missing `score.rationales` — the atlas endpoint should return per-dimension rationales. Is PQS_BASE_URL pointing at prod with PR #4 deployed?",
    );
  }

  for (const key of POST_DIMENSION_KEYS) {
    if (typeof dimensions[key] !== "number") {
      throw new Error(`Post-flight dimension missing or non-numeric: ${key}`);
    }
    if (typeof rationales[key] !== "string") {
      throw new Error(`Post-flight rationale missing or non-string: ${key}`);
    }
  }

  return { total, dimensions, rationales };
}

// ---------- public entrypoint ----------

/**
 * Generate one AtlasRow for the given (prompt, vertical) pair.
 *
 * @param prompt   raw prompt string (1-10_000 chars)
 * @param vertical PQS vertical tag
 * @param source   atlas source tag (used as AtlasRow.source)
 */
export async function generateAtlasRow(
  prompt: string,
  vertical: Vertical,
  source: SourceType = "synthetic",
): Promise<AtlasRow> {
  if (!prompt || !prompt.trim()) {
    throw new Error("prompt is required and must be non-empty");
  }
  if (!VALID_VERTICALS.includes(vertical)) {
    throw new Error(
      `vertical must be one of: ${VALID_VERTICALS.join(", ")} (got: ${vertical})`,
    );
  }

  // Stage 1: pre-flight. Returns the 8-dim pre-score AND Opus's generated output.
  const scoreFullResp = await callScoreFull(prompt, vertical);
  const pre = pickPreScore(scoreFullResp);

  // Stage 2: post-flight. Scores Opus's output on the 6-dim post rubric with rationales.
  const atlasResp = await callAtlasScoreOutput(prompt, pre.output, vertical);
  const post = pickPostScore(atlasResp);

  const row: AtlasRow = {
    prompt,
    vertical,
    pre_score: {
      total: pre.total,
      out_of: 80,
      dimensions: {
        clarity: pre.dimensions.clarity,
        specificity: pre.dimensions.specificity,
        context: pre.dimensions.context,
        constraints: pre.dimensions.constraints,
        output_format: pre.dimensions.output_format,
        role_definition: pre.dimensions.role_definition,
        examples: pre.dimensions.examples,
        cot_structure: pre.dimensions.cot_structure,
      },
    },
    opus_output: pre.output,
    post_score: {
      total: post.total,
      out_of: 60,
      dimensions: {
        factual_grounding: post.dimensions.factual_grounding,
        instruction_adherence: post.dimensions.instruction_adherence,
        coherence: post.dimensions.coherence,
        specificity: post.dimensions.specificity,
        verifiability: post.dimensions.verifiability,
        hallucination_risk: post.dimensions.hallucination_risk,
      },
      rationales: {
        factual_grounding: post.rationales.factual_grounding,
        instruction_adherence: post.rationales.instruction_adherence,
        coherence: post.rationales.coherence,
        specificity: post.rationales.specificity,
        verifiability: post.rationales.verifiability,
        hallucination_risk: post.rationales.hallucination_risk,
      },
    },
    pre_grade: gradeLabel(pre.total),
    post_grade_placeholder: null,
    source,
    human_verified: false,
    timestamp_utc: new Date().toISOString(),
  };

  return row;
}

// ---------- CLI ----------

async function main(): Promise<void> {
  const { values } = parseArgs({
    options: {
      prompt: { type: "string" },
      vertical: { type: "string", default: "general" },
      source: { type: "string", default: "synthetic" },
    },
    allowPositionals: false,
    strict: true,
  });

  if (!values.prompt) {
    console.error(
      "Usage: tsx scripts/generate-atlas-row.ts --prompt \"...\" [--vertical general] [--source synthetic]",
    );
    process.exit(2);
  }

  const vertical = values.vertical as Vertical;
  if (!VALID_VERTICALS.includes(vertical)) {
    console.error(
      `Invalid vertical: ${vertical}. Must be one of: ${VALID_VERTICALS.join(", ")}`,
    );
    process.exit(2);
  }

  const source = values.source as SourceType;
  try {
    const row = await generateAtlasRow(values.prompt, vertical, source);
    process.stdout.write(JSON.stringify(row, null, 2) + "\n");
  } catch (err) {
    console.error("[generate-atlas-row] failed:", (err as Error).message);
    process.exit(1);
  }
}

// Run main() only when executed directly. Library callers (batch orchestrator)
// just import `generateAtlasRow`.
const isDirectInvocation = import.meta.url === `file://${process.argv[1]}`;
if (isDirectInvocation) {
  void main();
}
