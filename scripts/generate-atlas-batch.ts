/**
 * generate-atlas-batch — batch atlas row orchestrator.
 *
 * Reads source prompts from JSONL, generates one AtlasRow per prompt via
 * generate-atlas-row, writes results incrementally to CSV + JSON.
 *
 * Design choices worth calling out:
 *   - Rate-limited to 1 row / 5 s by default (configurable) so we don't
 *     hammer the scoring endpoints during a 500-row batch.
 *   - Incremental writes: each completed row flushes to disk immediately.
 *     If the batch crashes at row 200, rows 1-199 are preserved.
 *   - JSON output is a streaming array: `[\n  {row1},\n  {row2}\n]`. On
 *     SIGINT we close the array gracefully; on crash we leave a trailing
 *     `,` — run `jq -s '.' data/atlas.json.partial` to recover.
 *   - Per-row failures are logged to data/atlas-failures.jsonl and do NOT
 *     abort the batch.
 *
 * CLI:
 *   tsx scripts/generate-atlas-batch.ts
 *     [--source data/source-prompts.jsonl]
 *     [--output-prefix data/atlas]
 *     [--rate-limit-seconds 5]
 *     [--max-rows N]
 *     [--dry-run]
 *     [--append]       # append to existing output files instead of overwriting
 *
 * Env: PQS_API_KEY + PQS_INTERNAL_TOKEN (see generate-atlas-row.ts).
 */

import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import {
  AtlasRow,
  PRE_DIMENSION_KEYS,
  POST_DIMENSION_KEYS,
  SourceType,
  Vertical,
  VALID_SOURCES,
  VALID_VERTICALS,
} from "../schemas/atlas-row.js";
import { generateAtlasRow } from "./generate-atlas-row.js";

interface SourcePrompt {
  prompt: string;
  vertical: Vertical;
  source: SourceType;
  /** Optional note/id for the operator's benefit; not stored in the row. */
  note?: string;
}

// ---------- CLI parsing ----------

const { values } = parseArgs({
  options: {
    source: { type: "string", default: "data/source-prompts.jsonl" },
    "output-prefix": { type: "string", default: "data/atlas" },
    "rate-limit-seconds": { type: "string", default: "5" },
    "max-rows": { type: "string" },
    "dry-run": { type: "boolean", default: false },
    append: { type: "boolean", default: false },
  },
  strict: true,
});

const sourcePath = values.source as string;
const outputPrefix = values["output-prefix"] as string;
const rateLimitSeconds = Number.parseFloat(values["rate-limit-seconds"] as string);
const maxRows = values["max-rows"] ? Number.parseInt(values["max-rows"] as string, 10) : Infinity;
const dryRun = Boolean(values["dry-run"]);
const appendMode = Boolean(values.append);

if (!Number.isFinite(rateLimitSeconds) || rateLimitSeconds < 0) {
  console.error("--rate-limit-seconds must be a non-negative number");
  process.exit(2);
}
// maxRows may be Infinity when --max-rows is unset; only reject NaN or negative.
if (Number.isNaN(maxRows) || maxRows < 0) {
  console.error("--max-rows must be a non-negative integer");
  process.exit(2);
}

// ---------- source file parsing ----------

function loadSourcePrompts(p: string): SourcePrompt[] {
  if (!fs.existsSync(p)) {
    console.error(`Source file not found: ${p}`);
    process.exit(2);
  }
  const raw = fs.readFileSync(p, "utf-8");
  const prompts: SourcePrompt[] = [];
  const errors: string[] = [];
  const lines = raw.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    try {
      const parsed = JSON.parse(line) as Partial<SourcePrompt>;
      if (typeof parsed.prompt !== "string" || !parsed.prompt.trim()) {
        errors.push(`line ${i + 1}: missing or empty "prompt"`);
        continue;
      }
      if (typeof parsed.vertical !== "string" || !VALID_VERTICALS.includes(parsed.vertical as Vertical)) {
        errors.push(
          `line ${i + 1}: invalid "vertical" (${parsed.vertical}); must be one of: ${VALID_VERTICALS.join(", ")}`,
        );
        continue;
      }
      if (typeof parsed.source !== "string" || !VALID_SOURCES.includes(parsed.source as SourceType)) {
        errors.push(
          `line ${i + 1}: invalid "source" (${parsed.source}); must be one of: ${VALID_SOURCES.join(", ")}`,
        );
        continue;
      }
      prompts.push({
        prompt: parsed.prompt,
        vertical: parsed.vertical as Vertical,
        source: parsed.source as SourceType,
        note: typeof parsed.note === "string" ? parsed.note : undefined,
      });
    } catch (e) {
      errors.push(`line ${i + 1}: JSON parse error — ${(e as Error).message}`);
    }
  }
  if (errors.length) {
    console.warn(`[source] ${errors.length} invalid row(s) skipped:`);
    for (const e of errors.slice(0, 10)) console.warn(`  • ${e}`);
    if (errors.length > 10) console.warn(`  ... and ${errors.length - 10} more`);
  }
  return prompts;
}

// ---------- CSV utilities ----------

function csvCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = String(v);
  if (s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r")) {
    return `"${s.replaceAll('"', '""')}"`;
  }
  return s;
}

function csvHeader(): string {
  const cols: string[] = [
    "prompt",
    "vertical",
    "pre_score_total",
    "pre_score_out_of",
    ...PRE_DIMENSION_KEYS.map((k) => `pre_dim_${k}`),
    "opus_output",
    "post_score_total",
    "post_score_out_of",
    ...POST_DIMENSION_KEYS.map((k) => `post_dim_${k}`),
    ...POST_DIMENSION_KEYS.map((k) => `post_rationale_${k}`),
    "pre_grade",
    "post_grade_placeholder",
    "source",
    "human_verified",
    "timestamp_utc",
  ];
  return cols.map(csvCell).join(",");
}

function csvRow(row: AtlasRow): string {
  const cells: unknown[] = [
    row.prompt,
    row.vertical,
    row.pre_score.total,
    row.pre_score.out_of,
    ...PRE_DIMENSION_KEYS.map((k) => row.pre_score.dimensions[k]),
    row.opus_output,
    row.post_score.total,
    row.post_score.out_of,
    ...POST_DIMENSION_KEYS.map((k) => row.post_score.dimensions[k]),
    ...POST_DIMENSION_KEYS.map((k) => row.post_score.rationales[k]),
    row.pre_grade,
    row.post_grade_placeholder,
    row.source,
    row.human_verified,
    row.timestamp_utc,
  ];
  return cells.map(csvCell).join(",");
}

// ---------- main ----------

async function main(): Promise<void> {
  const prompts = loadSourcePrompts(sourcePath);
  const plannedCount = Math.min(prompts.length, maxRows);

  console.log(`[batch] source: ${sourcePath}`);
  console.log(`[batch] ${prompts.length} prompts loaded; planning to process ${plannedCount}`);

  if (dryRun) {
    console.log("[batch] --dry-run: listing planned rows, NO API calls will be made\n");
    const toShow = prompts.slice(0, plannedCount);
    for (let i = 0; i < toShow.length; i++) {
      const p = toShow[i];
      console.log(
        `  ${String(i + 1).padStart(3, " ")}. [${p.vertical}] (${p.source})  ${p.prompt.slice(0, 100)}${p.prompt.length > 100 ? "…" : ""}`,
      );
    }
    console.log(`\n[batch] dry-run complete. Would generate ${toShow.length} rows.`);
    return;
  }

  // Pre-flight env check so we don't burn time running the loop with no auth.
  if (!process.env.PQS_API_KEY) {
    console.error(
      "PQS_API_KEY env var not set. Export your PQS bearer key (see .env.example).",
    );
    process.exit(3);
  }
  if (!process.env.PQS_INTERNAL_TOKEN) {
    console.warn(
      "[batch] PQS_INTERNAL_TOKEN not set — atlas calls will NOT be flagged as internal in pqs_api_calls analytics.",
    );
  }

  const csvPath = `${outputPrefix}.csv`;
  const jsonPath = `${outputPrefix}.json`;
  const failuresPath = `${path.dirname(outputPrefix)}/atlas-failures.jsonl`;

  fs.mkdirSync(path.dirname(csvPath) || ".", { recursive: true });

  const writeMode = appendMode ? "a" : "w";
  const csvStream = fs.createWriteStream(csvPath, { flags: writeMode });
  const jsonStream = fs.createWriteStream(jsonPath, { flags: writeMode });
  const failuresStream = fs.createWriteStream(failuresPath, { flags: "a" });

  if (!appendMode) {
    csvStream.write(csvHeader() + "\n");
    jsonStream.write("[\n");
  } else {
    console.log(`[batch] --append: writing to existing ${csvPath} and ${jsonPath} (no header, no JSON array re-open)`);
  }
  let firstJsonRow = !appendMode;

  // Graceful shutdown: on SIGINT/SIGTERM, flush partial output with valid JSON.
  let cleanedUp = false;
  const gracefulClose = (signal: string): void => {
    if (cleanedUp) return;
    cleanedUp = true;
    console.log(`\n[batch] ${signal} received — closing output streams...`);
    if (!appendMode) {
      jsonStream.write("\n]\n");
    }
    jsonStream.end();
    csvStream.end();
    failuresStream.end();
    // Exit with conventional interrupt code on SIGINT, else 143 on SIGTERM.
    const code = signal === "SIGINT" ? 130 : 143;
    process.exitCode = code;
  };
  process.on("SIGINT", () => gracefulClose("SIGINT"));
  process.on("SIGTERM", () => gracefulClose("SIGTERM"));

  const startedAt = Date.now();
  let attempted = 0;
  let succeeded = 0;
  let failed = 0;

  const toProcess = prompts.slice(0, plannedCount);

  for (let i = 0; i < toProcess.length; i++) {
    if (cleanedUp) break;
    const sp = toProcess[i];
    attempted++;
    const idx1 = i + 1;
    const preview = sp.prompt.slice(0, 60).replace(/\n/g, " ");
    console.log(
      `[batch] (${idx1}/${toProcess.length}) [${sp.vertical}] ${preview}${sp.prompt.length > 60 ? "…" : ""}`,
    );

    try {
      const row = await generateAtlasRow(sp.prompt, sp.vertical, sp.source);

      // Incremental writes — flush each row immediately so a crash preserves progress.
      csvStream.write(csvRow(row) + "\n");
      const jsonPrefix = firstJsonRow ? "  " : ",\n  ";
      jsonStream.write(jsonPrefix + JSON.stringify(row));
      firstJsonRow = false;
      succeeded++;
      console.log(
        `[batch]   ✓ pre=${row.pre_score.total}/80 (${row.pre_grade})  post=${row.post_score.total}/60`,
      );
    } catch (err) {
      failed++;
      const msg = (err as Error).message;
      console.error(`[batch]   ✗ failed: ${msg}`);
      failuresStream.write(
        JSON.stringify({
          timestamp_utc: new Date().toISOString(),
          prompt_index: i,
          prompt: sp.prompt,
          vertical: sp.vertical,
          source: sp.source,
          error: msg,
        }) + "\n",
      );
    }

    // Rate limit only between completed rows (don't wait after the last one).
    if (!cleanedUp && i < toProcess.length - 1 && rateLimitSeconds > 0) {
      await new Promise((r) => setTimeout(r, rateLimitSeconds * 1000));
    }
  }

  if (!cleanedUp) {
    if (!appendMode) {
      jsonStream.write("\n]\n");
    }
    await new Promise<void>((resolve) => {
      let pending = 3;
      const done = () => (--pending === 0 ? resolve() : undefined);
      csvStream.end(done);
      jsonStream.end(done);
      failuresStream.end(done);
    });
  }

  const elapsedSec = ((Date.now() - startedAt) / 1000).toFixed(1);
  console.log("");
  console.log("============================================================");
  console.log("[batch] summary");
  console.log(`  attempted: ${attempted}`);
  console.log(`  succeeded: ${succeeded}`);
  console.log(`  failed:    ${failed}`);
  console.log(`  runtime:   ${elapsedSec}s`);
  console.log(`  csv:       ${csvPath}`);
  console.log(`  json:      ${jsonPath}`);
  if (failed > 0) {
    console.log(`  failures:  ${failuresPath}`);
  }
  console.log("============================================================");
}

void main().catch((err) => {
  console.error("[batch] fatal:", err);
  process.exit(1);
});
