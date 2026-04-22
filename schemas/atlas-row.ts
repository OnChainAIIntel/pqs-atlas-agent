// Canonical atlas row schema. Every row produced by scripts/generate-atlas-row.ts
// and scripts/generate-atlas-batch.ts conforms to this type.
//
// Two scoring stages per row:
//   1. Pre-flight  — PQS 8-dimension prompt-quality rubric (0-80 total)
//      via POST https://pqs.onchainintel.net/api/score/full
//   2. Post-flight — PQS 6-dimension output-quality rubric (0-60 total)
//      via POST https://pqs.onchainintel.net/api/atlas/score/output
//      which returns per-dimension `rationales` in addition to scores.
//
// `pre_grade` is derived client-side using canonical thresholds from
// prompt-optimization-engine's lib/pqs-grading.js: A≥70, B≥60, C≥50, D≥35.
// `post_grade_placeholder` is null — post-flight grade thresholds are not
// yet defined; the 0-60 scale needs separate calibration.

export type Vertical =
  | "software"
  | "content"
  | "business"
  | "education"
  | "science"
  | "crypto"
  | "general"
  | "research";

export type SourceType = "production" | "synthetic" | "benchmark" | "staged";

export type PreGrade = "A" | "B" | "C" | "D" | "F";

export interface PreScoreDimensions {
  clarity: number;
  specificity: number;
  context: number;
  constraints: number;
  output_format: number;
  role_definition: number;
  examples: number;
  cot_structure: number;
}

export interface PreScore {
  total: number;
  out_of: 80;
  dimensions: PreScoreDimensions;
}

export interface PostScoreDimensions {
  factual_grounding: number;
  instruction_adherence: number;
  coherence: number;
  specificity: number;
  verifiability: number;
  hallucination_risk: number;
}

export interface PostScoreRationales {
  factual_grounding: string;
  instruction_adherence: string;
  coherence: string;
  specificity: string;
  verifiability: string;
  hallucination_risk: string;
}

export interface PostScore {
  total: number;
  out_of: 60;
  dimensions: PostScoreDimensions;
  rationales: PostScoreRationales;
}

export interface AtlasRow {
  prompt: string;
  vertical: Vertical;
  pre_score: PreScore;
  opus_output: string;
  post_score: PostScore;
  pre_grade: PreGrade;
  post_grade_placeholder: null;
  source: SourceType;
  human_verified: boolean;
  timestamp_utc: string;
}

// --- Helpers used by generators ----------------------------------------------

export const PRE_DIMENSION_KEYS: readonly (keyof PreScoreDimensions)[] = [
  "clarity",
  "specificity",
  "context",
  "constraints",
  "output_format",
  "role_definition",
  "examples",
  "cot_structure",
] as const;

export const POST_DIMENSION_KEYS: readonly (keyof PostScoreDimensions)[] = [
  "factual_grounding",
  "instruction_adherence",
  "coherence",
  "specificity",
  "verifiability",
  "hallucination_risk",
] as const;

export const VALID_VERTICALS: readonly Vertical[] = [
  "software",
  "content",
  "business",
  "education",
  "science",
  "crypto",
  "general",
  "research",
] as const;

export const VALID_SOURCES: readonly SourceType[] = [
  "production",
  "synthetic",
  "benchmark",
  "staged",
] as const;

// Canonical grade thresholds, mirrored from prompt-optimization-engine's
// lib/pqs-grading.js. Keep in sync. See also docs/MIGRATION-2026-04-22.md in
// that repo for the threshold provenance.
export const GRADE_THRESHOLDS = Object.freeze({ A: 70, B: 60, C: 50, D: 35 });

export function gradeLabel(total: number): PreGrade {
  if (total >= GRADE_THRESHOLDS.A) return "A";
  if (total >= GRADE_THRESHOLDS.B) return "B";
  if (total >= GRADE_THRESHOLDS.C) return "C";
  if (total >= GRADE_THRESHOLDS.D) return "D";
  return "F";
}
