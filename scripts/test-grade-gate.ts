/**
 * test-grade-gate — minimal offline unit test for the buyer's grade-gate
 * logic. Runs with `tsx` — no test framework dependency. Exits 0 on pass,
 * 1 on any failure.
 *
 * Covers:
 *   - null / non-JSON-parseable memo → pay=false
 *   - AtlasRow with total >= 60 → pay=true
 *   - AtlasRow with total < 60 → pay=false
 *   - String (JSON-stringified AtlasRow) handled correctly
 *   - Pre-parsed object handled correctly
 *   - Missing pre_score.total → pay=false
 *
 * No network. No env dependency. Pure.
 */

import {
  tryParseAtlasRow,
  shouldPay,
  GRADE_GATE_MIN_TOTAL,
} from "../src/grade-gate.js";
import type { AtlasRow } from "../schemas/atlas-row.js";

// ---------- fixtures ----------

function rowWithTotal(total: number): AtlasRow {
  return {
    prompt: "test prompt",
    vertical: "general",
    pre_score: {
      total,
      out_of: 80,
      dimensions: {
        clarity: 7,
        specificity: 7,
        context: 7,
        constraints: 7,
        output_format: 7,
        role_definition: 7,
        examples: 7,
        cot_structure: 7,
      },
    },
    opus_output: "test output",
    post_score: {
      total: 50,
      out_of: 60,
      dimensions: {
        factual_grounding: 9,
        instruction_adherence: 9,
        coherence: 9,
        specificity: 8,
        verifiability: 8,
        hallucination_risk: 7,
      },
      rationales: {
        factual_grounding: "x",
        instruction_adherence: "x",
        coherence: "x",
        specificity: "x",
        verifiability: "x",
        hallucination_risk: "x",
      },
    },
    pre_grade: total >= 70 ? "A" : total >= 60 ? "B" : total >= 50 ? "C" : total >= 35 ? "D" : "F",
    post_grade_placeholder: null,
    source: "synthetic",
    human_verified: false,
    timestamp_utc: "2026-04-23T00:00:00Z",
  };
}

// ---------- assertions ----------

let failed = 0;
let passed = 0;

function assert(label: string, condition: boolean, detail?: string): void {
  if (condition) {
    passed++;
    console.log(`  ✓ ${label}`);
  } else {
    failed++;
    console.log(`  ✗ ${label}${detail ? `  (${detail})` : ""}`);
  }
}

// ---------- tests ----------

console.log("grade-gate tests");
console.log("================");
console.log("");

console.log("tryParseAtlasRow():");
assert("null → null", tryParseAtlasRow(null) === null);
assert("undefined → null", tryParseAtlasRow(undefined) === null);
assert("garbage string → null", tryParseAtlasRow("{not json") === null);
assert("number → null", tryParseAtlasRow(42) === null);
const parsed = tryParseAtlasRow(JSON.stringify(rowWithTotal(65)));
assert("JSON string → AtlasRow", parsed !== null && parsed.pre_score.total === 65);
const obj = tryParseAtlasRow(rowWithTotal(65));
assert("pre-parsed object → AtlasRow", obj !== null && obj.pre_score.total === 65);

console.log("");
console.log("shouldPay():");

// Null memo.
let d = shouldPay(null);
assert("null row → pay=false", d.pay === false, d.reason);

// Missing total.
d = shouldPay({ ...rowWithTotal(70), pre_score: {} as unknown as AtlasRow["pre_score"] });
assert("missing pre_score.total → pay=false", d.pay === false, d.reason);

// At the threshold.
d = shouldPay(rowWithTotal(GRADE_GATE_MIN_TOTAL));
assert(
  `at threshold (${GRADE_GATE_MIN_TOTAL}) → pay=true`,
  d.pay === true,
  d.reason,
);

// Just above.
d = shouldPay(rowWithTotal(GRADE_GATE_MIN_TOTAL + 1));
assert(`above threshold → pay=true`, d.pay === true, d.reason);

// Well above (A-grade).
d = shouldPay(rowWithTotal(75));
assert("A-grade (75) → pay=true", d.pay === true, d.reason);

// Just below.
d = shouldPay(rowWithTotal(GRADE_GATE_MIN_TOTAL - 1));
assert(`below threshold → pay=false`, d.pay === false, d.reason);

// F-grade.
d = shouldPay(rowWithTotal(22));
assert("F-grade (22) → pay=false", d.pay === false, d.reason);

// Reason message format check.
d = shouldPay(rowWithTotal(65));
assert(
  "pass reason mentions total and grade",
  d.reason.includes("65") && d.reason.includes("B"),
  `got: ${d.reason}`,
);
d = shouldPay(rowWithTotal(40));
assert(
  "fail reason mentions total and grade",
  d.reason.includes("40") && d.reason.includes("D"),
  `got: ${d.reason}`,
);

console.log("");
console.log(`${passed} pass, ${failed} fail`);
process.exit(failed === 0 ? 0 : 1);
