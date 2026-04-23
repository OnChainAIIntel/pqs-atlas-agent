"""
Pipeline 5 — Gate B: Canonical 8-dimension rubric.

Exports the canonical RUBRIC_PROMPT string used by Raters 2 and 3 (Opus 4.7
and GPT-4o direct) during kappa calibration. Rater 1 is the PQS production
endpoint (/api/score/full) which runs its own internal system prompt — we
treat it as a black-box reference.

Canonical sources:
  - Dimension set:   prompt-optimization-engine/lib/pqs-schemas.js
                     (dimensionsSchema, 8 fields at min=1 max=10)
  - Grade cutoffs:   prompt-optimization-engine/lib/pqs-grading.js
                     (A>=70, B>=60, C>=50, D>=35, F<35)
  - Prod system:     prompt-optimization-engine/app/api/score/full/route.js
                     (lines 94-102 — used as the stylistic template below)

The prod system prompt is terse (dim names only). For Raters 2 and 3 we
expand each dim into a single-sentence definition so an independent LLM
can apply the rubric consistently. The dim names, their 1-10 range, and
the grade cutoffs are byte-faithful to the production source.
"""
from __future__ import annotations


DIMENSIONS = (
    "clarity",
    "specificity",
    "context",
    "constraints",
    "output_format",
    "role_definition",
    "examples",
    "cot_structure",
)

DIMENSION_DEFINITIONS = {
    "clarity":         "Is the prompt unambiguous and easy to understand? A clear prompt states its intent without forcing the model to guess. Low scores for vague requests, contradictions, or garbled wording.",
    "specificity":     "Does the prompt give enough concrete detail for a single, well-defined task? A specific prompt narrows the solution space. Low scores for open-ended 'write something about X' requests.",
    "context":         "Does the prompt supply the background the model needs — audience, domain, inputs, prior state? A well-contextualized prompt reduces hallucination. Low scores when the model must invent context to answer.",
    "constraints":     "Does the prompt state boundaries — length, tone, what to avoid, hard rules? Constraints shape acceptable outputs. Low scores for unbounded requests where any answer is valid.",
    "output_format":   "Does the prompt specify the shape of the answer — bullet list, JSON, code block, table, word count? Low scores when the prompt leaves the model to pick a format.",
    "role_definition": "Does the prompt assign the model a role or persona ('You are an expert X') that scopes its answer? Low scores for prompts with no role or a role that is asserted but does nothing.",
    "examples":        "Does the prompt give the model one or more concrete examples of the desired input/output pattern? Few-shot examples raise this score. Low scores for zero-shot requests without patterns.",
    "cot_structure":   "Does the prompt scaffold the model's reasoning — 'think step by step', numbered phases, explicit sub-tasks? Low scores for one-line requests that hide the reasoning path.",
}

assert set(DIMENSIONS) == set(DIMENSION_DEFINITIONS.keys())
assert len(DIMENSIONS) == 8

GRADE_CUTOFFS = {"A": 70, "B": 60, "C": 50, "D": 35}
# Score range: per-dim [1, 10]; total [8, 80]
MIN_DIM, MAX_DIM = 1, 10
MIN_TOTAL, MAX_TOTAL = 8, 80


def grade_from_total(total: int) -> str:
    """Canonical grade mapping — mirrors lib/pqs-grading.js."""
    if total >= GRADE_CUTOFFS["A"]:
        return "A"
    if total >= GRADE_CUTOFFS["B"]:
        return "B"
    if total >= GRADE_CUTOFFS["C"]:
        return "C"
    if total >= GRADE_CUTOFFS["D"]:
        return "D"
    return "F"


# -----------------------------------------------------------------------------
# Canonical RUBRIC_PROMPT — byte-identical across Rater 2 and Rater 3.
# Used as the `system` block on both API calls.
# -----------------------------------------------------------------------------
_DIM_BLOCK = "\n".join(
    f"  - {name}: {DIMENSION_DEFINITIONS[name]}" for name in DIMENSIONS
)

RUBRIC_PROMPT = f"""You are a prompt-quality scorer applying the PQS v2.0 pre-flight rubric.

You will be given a single prompt. Score it on 8 dimensions, each on an integer scale from {MIN_DIM} to {MAX_DIM}. The 8 dimensions:

{_DIM_BLOCK}

Scoring guidance:
  - {MIN_DIM} = essentially absent or severely broken on this dimension
  - 3-4 = present but weak
  - 5-6 = adequate but unremarkable
  - 7-8 = strong, well-executed
  - {MAX_DIM} = exemplary; textbook example of this dimension

The total is the sum of the 8 dimension scores. It lies in [{MIN_TOTAL}, {MAX_TOTAL}].

Grade from the total using these cutoffs:
  - A if total >= {GRADE_CUTOFFS["A"]}
  - B if total >= {GRADE_CUTOFFS["B"]}
  - C if total >= {GRADE_CUTOFFS["C"]}
  - D if total >= {GRADE_CUTOFFS["D"]}
  - F otherwise

Respond with ONLY minified JSON, no markdown, no commentary. The shape is:
{{"clarity":0,"specificity":0,"context":0,"constraints":0,"output_format":0,"role_definition":0,"examples":0,"cot_structure":0,"total":0,"grade":"A"}}

where total = sum of the 8 dimension scores, and grade is the single letter produced by applying the cutoffs above to that total."""


def rubric_checksum() -> str:
    import hashlib
    return hashlib.sha256(RUBRIC_PROMPT.encode("utf-8")).hexdigest()


def _selftest():
    # Byte-length + shape assertions so any future edit to the constants
    # above surfaces as an immediate CI-like check at import time.
    assert MIN_DIM == 1 and MAX_DIM == 10
    assert MIN_TOTAL == 8 * MIN_DIM and MAX_TOTAL == 8 * MAX_DIM
    assert grade_from_total(70) == "A"
    assert grade_from_total(69) == "B"
    assert grade_from_total(59) == "C"
    assert grade_from_total(49) == "D"
    assert grade_from_total(35) == "D"
    assert grade_from_total(34) == "F"
    # Rubric must mention every dim by name
    for name in DIMENSIONS:
        assert name in RUBRIC_PROMPT, f"RUBRIC_PROMPT missing dim {name}"


_selftest()


if __name__ == "__main__":
    print(f"RUBRIC_PROMPT length: {len(RUBRIC_PROMPT)} chars")
    print(f"RUBRIC_PROMPT sha256: {rubric_checksum()}")
    print()
    print("-" * 60)
    print(RUBRIC_PROMPT)
    print("-" * 60)
