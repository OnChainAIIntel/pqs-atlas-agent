/**
 * virtuals-handler — reusable handler logic for Beat 2 (Virtuals ACP v2).
 *
 * This is the bridge between the pqs-atlas-agent pipeline and the
 * openclaw-acp CLI seller runtime. The openclaw-acp offering's handlers.ts
 * imports these functions; keeping the logic here means pqs-atlas-agent
 * remains the single source of truth for the PQS pipeline, and the
 * openclaw-acp directory only contains adapter code.
 *
 * Why this architecture (vs using @virtuals-protocol/acp-node directly):
 *   The raw SDK path (AcpContractClientV2.build) requires a pre-deployed
 *   Modular Account V2 smart wallet on Base mainnet. Virtuals' dashboard
 *   deploy step is no longer documented (tutorial URLs 404), and the SDK
 *   has no `deploy` method. The openclaw-acp CLI auto-provisions the
 *   wallet via Virtuals' backend API, bypassing the chicken-and-egg
 *   issue and shipping Beat 2 on the supported self-hosted path.
 *
 * Contract (matches openclaw-acp ExecuteJobResult type):
 *   executeJobHandler({ prompt, vertical? })
 *     -> { deliverable: <stringified AtlasRow JSON> }
 *   validateRequirementsHandler({ prompt, vertical? })
 *     -> { valid: true } | { valid: false, reason }
 *
 * Grade-gate rule for buyer-side decisions: import `shouldPay` from
 * ../src/grade-gate.js. Handler itself does not gate — the buyer decides.
 */

// ---------- env bootstrap ----------
// The openclaw-acp seller runtime launches from ~/Desktop/openclaw-acp, not
// this repo, so dotenv's default CWD lookup misses our .env.local. Load it
// by absolute path derived from THIS file's location (import.meta.url).
// PQS_API_KEY and PQS_INTERNAL_TOKEN are required by generate-atlas-row.
import { config as dotenvConfig } from "dotenv";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
const __dirname_vh = dirname(fileURLToPath(import.meta.url));
const __envPath_vh = resolve(__dirname_vh, "..", ".env.local");
const __dotenvResult_vh = dotenvConfig({ path: __envPath_vh, override: true });
console.log(
  `[virtuals-handler] dotenv load from ${__envPath_vh}: ${
    __dotenvResult_vh.error ? `ERROR ${__dotenvResult_vh.error.message}` : "OK"
  }; PQS_API_KEY=${process.env.PQS_API_KEY ? "set" : "missing"}`,
);

import { generateAtlasRow } from "./generate-atlas-row.js";
import { VALID_VERTICALS, type AtlasRow, type Vertical } from "../schemas/atlas-row.js";

// ---------- types (mirror openclaw-acp's runtime/offeringTypes) ----------

export interface ExecuteJobResult {
  deliverable: string | { type: string; value: unknown };
  payableDetail?: { amount: number; tokenAddress: string };
}

export type ValidationResult = boolean | { valid: boolean; reason?: string };

export interface JobRequest {
  prompt?: string;
  vertical?: string;
  [k: string]: unknown;
}

// ---------- input validation ----------

const MIN_PROMPT_CHARS = 8;
const MAX_PROMPT_CHARS = 8_000;

/**
 * Validate the buyer-supplied request BEFORE accepting the job. The ACP
 * runtime calls this first — returning { valid: false, reason } rejects
 * early (no payment request sent), saving both sides gas/time.
 */
export function validateRequirementsHandler(request: JobRequest): ValidationResult {
  if (!request || typeof request !== "object") {
    return { valid: false, reason: "request body missing or non-object" };
  }
  const prompt = request.prompt;
  if (typeof prompt !== "string") {
    return { valid: false, reason: "request.prompt is required (string)" };
  }
  if (prompt.trim().length < MIN_PROMPT_CHARS) {
    return {
      valid: false,
      reason: `request.prompt must be at least ${MIN_PROMPT_CHARS} chars`,
    };
  }
  if (prompt.length > MAX_PROMPT_CHARS) {
    return {
      valid: false,
      reason: `request.prompt too long (max ${MAX_PROMPT_CHARS} chars)`,
    };
  }

  // Vertical is optional — default "general". If supplied, must be in the
  // allow-list so the score API doesn't 400 mid-flight.
  const v = request.vertical;
  if (v !== undefined) {
    if (typeof v !== "string" || !VALID_VERTICALS.includes(v as Vertical)) {
      return {
        valid: false,
        reason: `request.vertical must be one of: ${VALID_VERTICALS.join(", ")}`,
      };
    }
  }
  return { valid: true };
}

/**
 * Custom payment-request message — shown to the buyer in the NEGOTIATION
 * phase. Keep it short and specific to this offering.
 */
export function requestPaymentHandler(request: JobRequest): string {
  const v = (request?.vertical as string) ?? "general";
  return `PQS AtlasRow for vertical=${v}. Grade-gate: buyer pays only if pre_score.total >= 60 (B+).`;
}

/**
 * Run the full PQS pipeline (pre-flight score + post-flight score + grade)
 * and return the AtlasRow as the deliverable. Runs AFTER the buyer pays.
 */
export async function executeJobHandler(request: JobRequest): Promise<ExecuteJobResult> {
  // Re-validate defensively — the runtime should have called
  // validateRequirementsHandler, but we don't trust transport.
  const check = validateRequirementsHandler(request);
  if (check !== true && (typeof check === "object" && check.valid === false)) {
    throw new Error(`invalid request: ${check.reason ?? "unknown"}`);
  }

  const prompt = (request.prompt as string).trim();
  const vertical = ((request.vertical as string | undefined) ?? "general") as Vertical;

  const row: AtlasRow = await generateAtlasRow(prompt, vertical);

  // Deliverable is a JSON-stringified AtlasRow so the buyer side can
  // JSON.parse + apply the grade-gate without format guessing. Keep the
  // post_grade_placeholder + rationales intact — full audit trail.
  return { deliverable: JSON.stringify(row) };
}
