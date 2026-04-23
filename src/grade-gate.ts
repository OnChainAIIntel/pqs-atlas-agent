/**
 * grade-gate — pure functions the buyer uses to decide whether to pay or
 * reject based on the AtlasRow the seller surfaces in the NEGOTIATION
 * requirement memo. Kept in a dedicated module so it can be unit-tested
 * without pulling in the Virtuals SDK or the buyer script's env wiring.
 *
 * Gating rule: the brief says "post_score.grade >= B (>= 60/80)". The
 * post-score is a 0-60 scale with no defined grade thresholds yet
 * (post_grade_placeholder is null on every AtlasRow). The 60/80 hint
 * aligns with the pre-score B threshold (A>=70, B>=60, C>=50, D>=35).
 * So we gate on pre_score.total >= 60 — i.e. the *prompt* is at least
 * B-grade quality. Documented here so the choice is auditable.
 */

import type { AtlasRow } from "../schemas/atlas-row.js";

/** Minimum pre_score.total that triggers payment. */
export const GRADE_GATE_MIN_TOTAL = 60;

export interface PayDecision {
  pay: boolean;
  reason: string;
}

/**
 * Parse a NEGOTIATION-memo payload into an AtlasRow. Virtuals surfaces memo
 * content as either a parsed object (server-side did JSON.parse) or a raw
 * string. Returns null if we can't recover a structured row.
 */
export function tryParseAtlasRow(raw: unknown): AtlasRow | null {
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw) as AtlasRow;
    } catch {
      return null;
    }
  }
  if (raw && typeof raw === "object") {
    return raw as AtlasRow;
  }
  return null;
}

/**
 * Given a parsed AtlasRow (or null), decide whether the buyer should pay.
 * Pure function — deterministic, network-free, trivially unit-testable.
 */
export function shouldPay(row: AtlasRow | null): PayDecision {
  if (!row) {
    return {
      pay: false,
      reason: "seller requirement did not contain a parseable AtlasRow",
    };
  }
  const total = row.pre_score?.total;
  const grade = row.pre_grade;
  if (typeof total !== "number") {
    return { pay: false, reason: "AtlasRow missing pre_score.total" };
  }
  if (total >= GRADE_GATE_MIN_TOTAL) {
    return {
      pay: true,
      reason: `pre_score.total=${total}/80 grade=${grade} — meets B+ gate`,
    };
  }
  return {
    pay: false,
    reason: `pre_score.total=${total}/80 grade=${grade} — below B gate (${GRADE_GATE_MIN_TOTAL}/80)`,
  };
}
