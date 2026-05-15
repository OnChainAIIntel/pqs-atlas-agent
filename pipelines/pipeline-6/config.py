"""
Pipeline 6 — config: judge routing, model IDs, endpoint URLs, paths, and the
6-dimension post-flight (output-quality) rubric.

This module is the single source of truth for:
  - JUDGE_CONFIG          which judge (local Gemma / cloud Haiku) scores each dim
  - OUTPUT_DIMENSIONS     the canonical 6-dim post-flight rubric + definitions
  - endpoints / models    PQS scoring endpoints, generator + judge model IDs
  - paths                 source corpus in, atlas JSONL out

Every other Pipeline 6 file imports from here. local_judge and cloud_judge both
pull their per-dimension prompt text from OUTPUT_DIMENSION_DEFINITIONS so the
two judges apply a byte-identical definition for any given dimension.

----------------------------------------------------------------------------
NOTES ON DISCREPANCIES WITH REPO / PRODUCTION (read before first run)
----------------------------------------------------------------------------
1. Directory: this pipeline lives under pipelines/pipeline-6/. Pipelines 4 and
   5 live under scripts/pipeline-N/. Pipeline 6 was specced as a standalone
   executor, hence the new top-level pipelines/ tree.

2. Source corpus: SOURCE_CORPUS_PATH defaults to the spec path
   pipelines/pipeline-4/outputs/source-corpus-software.jsonl. Pipeline 4's
   actual deliverables currently sit under data/ (e.g.
   data/source-prompts-clean-sampled.jsonl). Set PQS_SOURCE_CORPUS to point at
   the real software-vertical corpus before running score_outputs.py.

3. Dimension name: this rubric uses `specificity_of_claims`. The deployed
   /api/atlas/score/output endpoint calls the same dimension `specificity`.
   Pipeline 6's per-dim judge routing and atlas rows use `specificity_of_claims`
   throughout; score_output_full() (the production-path full score, used for
   observability only) returns whatever the endpoint returns.

4. Endpoints: ENDPOINT_OUTPUT_SCORE defaults to /api/score-output, which is
   delivered by the prompt-optimization-engine Session A PR. Until that PR is
   merged and deployed, set PQS_OUTPUT_SCORE_URL to a local dev URL
   (http://localhost:3000/api/score-output). The deployed equivalent today is
   /api/atlas/score/output.
"""
from __future__ import annotations

import os
from pathlib import Path

# -----------------------------------------------------------------------------
# Paths — ROOT is the pqs-atlas-agent repo root.
# config.py -> pipeline-6 -> pipelines -> pqs-atlas-agent
# -----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent.parent

# Source corpus (Pipeline 4 deliverable) and atlas output. Both overridable so
# the pipeline can target the real corpus location and a scratch output file.
SOURCE_CORPUS_PATH = os.getenv(
    "PQS_SOURCE_CORPUS",
    "pipelines/pipeline-4/outputs/source-corpus-software.jsonl",
)
OUTPUT_PATH = os.getenv(
    "PQS_ATLAS_OUTPUT",
    "pipelines/pipeline-6/outputs/atlas-software.jsonl",
)

# Resolved absolute paths (use these for I/O; the strings above are the
# spec-literal config values).
SOURCE_CORPUS_ABS = (ROOT / SOURCE_CORPUS_PATH).resolve()
OUTPUT_ABS = (ROOT / OUTPUT_PATH).resolve()

KAPPA_REPORT_PATH = ROOT / "pipelines" / "pipeline-6" / "outputs" / "kappa-phase-0.md"
CORRELATION_REPORT_PATH = ROOT / "findings" / "output-correlation.md"

# -----------------------------------------------------------------------------
# Endpoints — PQS scoring services. All overridable via env.
# Internal calls send Authorization: Bearer <PQS_INTERNAL_BEARER> which both
# bypasses the x402 paywall on /api/score and authenticates the internal-only
# /api/score-output endpoint. PQS_INTERNAL_TOKEN, when set, is additionally
# sent as the X-PQS-Internal observability flag (is_internal=true in
# pqs_api_calls) so atlas traffic does not pollute customer usage analytics.
# -----------------------------------------------------------------------------
ENDPOINT_PROMPT_SCORE = os.getenv("PQS_PROMPT_SCORE_URL", "https://pqs.onchainintel.net/api/score")
ENDPOINT_OUTPUT_SCORE = os.getenv("PQS_OUTPUT_SCORE_URL", "https://pqs.onchainintel.net/api/score-output")

# -----------------------------------------------------------------------------
# Local judge — Ollama on CRYPTOMINER.
# -----------------------------------------------------------------------------
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://192.168.1.205:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma2:9b")

# -----------------------------------------------------------------------------
# Cloud judge + generator — Anthropic Haiku 4.5.
# -----------------------------------------------------------------------------
HAIKU_MODEL = "claude-haiku-4-5-20251001"
GENERATOR_MODEL = "claude-haiku-4-5-20251001"

# -----------------------------------------------------------------------------
# Run sizing.
# -----------------------------------------------------------------------------
N_TARGET = 500
KAPPA_SAMPLE_SIZE = 50          # Phase 0 calibration sample
KAPPA_PASS_THRESHOLD = 0.70     # Cohen's weighted kappa floor for "local" assignment

# -----------------------------------------------------------------------------
# Judge routing.
#
# Planned config based on kappa expectations: 4 structural dimensions run on
# the local Gemma judge (cheap, fast), 2 knowledge dimensions run on Haiku
# (knowledge dims need a stronger judge). Phase 0 (kappa_phase_0.py) validates
# this: if a "local"-assigned dim scores kappa < KAPPA_PASS_THRESHOLD against
# Haiku, Ken updates that dim to "haiku" here before the full run.
# -----------------------------------------------------------------------------
JUDGE_CONFIG = {
    "factual_grounding":     "haiku",
    "instruction_adherence": "local",
    "coherence":             "local",
    "specificity_of_claims": "local",
    "verifiability":         "local",
    "hallucination_risk":    "haiku",
}

# Dimension order is fixed — atlas rows and the output total depend on it.
OUTPUT_DIMENSIONS = (
    "factual_grounding",
    "instruction_adherence",
    "coherence",
    "specificity_of_claims",
    "verifiability",
    "hallucination_risk",
)

# The 4 structural dimensions Phase 0 calibrates (planned "local" assignments).
STRUCTURAL_DIMENSIONS = (
    "instruction_adherence",
    "coherence",
    "specificity_of_claims",
    "verifiability",
)

assert set(JUDGE_CONFIG) == set(OUTPUT_DIMENSIONS)
assert set(STRUCTURAL_DIMENSIONS).issubset(set(OUTPUT_DIMENSIONS))

# -----------------------------------------------------------------------------
# The 6-dimension post-flight rubric.
#
# Each definition is a self-contained instruction for a judge scoring ONLY that
# dimension on an integer 1-10 scale. Adapted from the judge system prompt in
# prompt-optimization-engine/app/api/atlas/score/output/route.js so the local
# and cloud judges stay aligned with the production output-quality rubric.
# -----------------------------------------------------------------------------
OUTPUT_DIMENSION_DEFINITIONS = {
    "factual_grounding":
        "Are the claims in the output grounded in established knowledge? "
        "10 = every claim is well-grounded and accurate; 1 = speculative or "
        "unsupported assertions throughout.",
    "instruction_adherence":
        "Does the output follow the instructions given in the original prompt? "
        "10 = follows every instruction precisely; 1 = ignores the instructions. "
        "Score 5 if the prompt contained no actionable instruction.",
    "coherence":
        "Is the output logically coherent, well-structured, and internally "
        "consistent? 10 = highly coherent with a clear structure; 1 = "
        "incoherent or self-contradictory.",
    "specificity_of_claims":
        "Are the claims in the output specific and concrete rather than vague "
        "or generic? 10 = precise, detailed, concrete claims; 1 = vague, "
        "hand-wavy, generic statements.",
    "verifiability":
        "Are the claims in the output verifiable — checkable against a source, "
        "cited, or stated precisely enough to confirm? 10 = all claims "
        "verifiable or cited; 1 = no claim can be verified.",
    "hallucination_risk":
        "How LOW is the risk that the output contains hallucinations — "
        "confidently stated falsehoods, invented facts, fake citations? "
        "10 = very low risk, nothing fabricated; 1 = very high risk, obvious "
        "fabrications. Higher score means safer output.",
}

assert set(OUTPUT_DIMENSION_DEFINITIONS) == set(OUTPUT_DIMENSIONS)

# Per-dimension score range and output total range.
MIN_DIM, MAX_DIM = 1, 10
MIN_OUTPUT_TOTAL = len(OUTPUT_DIMENSIONS) * MIN_DIM   # 6
MAX_OUTPUT_TOTAL = len(OUTPUT_DIMENSIONS) * MAX_DIM   # 60

# -----------------------------------------------------------------------------
# Output grade cutoffs.
#
# Mirrors the 8-dim prompt rubric (A>=70, B>=60, C>=50, D>=35 on [8,80]) as
# percentage bands, applied to the [6,60] output total:
#   A >= 87.5%   B >= 75.0%   C >= 62.5%   D >= 43.75%   F otherwise
# -----------------------------------------------------------------------------
OUTPUT_GRADE_PCT_CUTOFFS = {"A": 0.875, "B": 0.750, "C": 0.625, "D": 0.4375}

PIPELINE_VERSION = "pipeline-6-v1.0"

# Generation + judge token budgets.
GENERATOR_MAX_TOKENS = 2048     # ~8k chars, under the 10k-char endpoint cap
JUDGE_MAX_TOKENS = 512
MAX_OUTPUT_CHARS = 10000        # endpoints reject output longer than this

# Concurrency + retry.
BATCH_SIZE = 10
MAX_RETRIES = 3
RETRY_BASE_SEC = 2.0
REQUEST_TIMEOUT_SEC = 120


def output_grade_from_total(total: int) -> str:
    """Map an output total in [6,60] to a letter grade via the percentage bands."""
    pct = total / MAX_OUTPUT_TOTAL
    if pct >= OUTPUT_GRADE_PCT_CUTOFFS["A"]:
        return "A"
    if pct >= OUTPUT_GRADE_PCT_CUTOFFS["B"]:
        return "B"
    if pct >= OUTPUT_GRADE_PCT_CUTOFFS["C"]:
        return "C"
    if pct >= OUTPUT_GRADE_PCT_CUTOFFS["D"]:
        return "D"
    return "F"


def internal_bearer() -> str:
    """The PQS internal bearer token. Sent as Authorization: Bearer <token>."""
    tok = os.environ.get("PQS_INTERNAL_BEARER")
    if not tok:
        raise RuntimeError(
            "PQS_INTERNAL_BEARER not set — required to call /api/score and "
            "/api/score-output. See pipelines/pipeline-6/README.md."
        )
    return tok


def internal_flag_header() -> dict:
    """Optional X-PQS-Internal observability header, if PQS_INTERNAL_TOKEN is set."""
    tok = os.environ.get("PQS_INTERNAL_TOKEN")
    return {"X-PQS-Internal": tok} if tok else {}


if __name__ == "__main__":
    print(f"ROOT                  {ROOT}")
    print(f"SOURCE_CORPUS_PATH    {SOURCE_CORPUS_PATH}")
    print(f"  -> resolved         {SOURCE_CORPUS_ABS}  (exists: {SOURCE_CORPUS_ABS.exists()})")
    print(f"OUTPUT_PATH           {OUTPUT_PATH}")
    print(f"  -> resolved         {OUTPUT_ABS}")
    print(f"ENDPOINT_PROMPT_SCORE {ENDPOINT_PROMPT_SCORE}")
    print(f"ENDPOINT_OUTPUT_SCORE {ENDPOINT_OUTPUT_SCORE}")
    print(f"OLLAMA_HOST / MODEL   {OLLAMA_HOST}  {OLLAMA_MODEL}")
    print(f"HAIKU_MODEL           {HAIKU_MODEL}")
    print(f"N_TARGET              {N_TARGET}")
    print(f"output total range    [{MIN_OUTPUT_TOTAL}, {MAX_OUTPUT_TOTAL}]")
    print("JUDGE_CONFIG:")
    for dim in OUTPUT_DIMENSIONS:
        print(f"  {dim:24s} -> {JUDGE_CONFIG[dim]}")
