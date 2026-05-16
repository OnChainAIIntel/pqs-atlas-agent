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
ALIGNMENT WITH CANONICAL OUTPUT RUBRIC (PR #73, prompt-optimization-engine)
----------------------------------------------------------------------------
Pipeline 6 is reconciled against the merged output-scoring refactor:

1. Directory: lives under scripts/pipeline-6/, matching the scripts/pipeline-N/
   convention used by Pipelines 4 and 5.

2. Source corpus: SOURCE_CORPUS_PATH defaults to
   data/source-prompts-clean-deterministic.jsonl — the tracked 500-row
   Pipeline 4 deliverable. Override with PQS_SOURCE_CORPUS to target a
   different corpus without a code edit.

3. Dimension name: the post-flight rubric uses `specificity` — the canonical
   name now locked across the deployed endpoint, lib/pqs-output-rubric.js, and
   Pipeline 6.

4. Endpoint: ENDPOINT_OUTPUT_SCORE defaults to /api/score-output, the internal
   endpoint delivered by PR #73 (which imports its rubric from
   lib/pqs-output-rubric.js). This is NOT /api/score/output, the separate
   public paid endpoint. Override with PQS_OUTPUT_SCORE_URL for local dev.
"""
from __future__ import annotations

import os
from pathlib import Path

# -----------------------------------------------------------------------------
# Paths — ROOT is the pqs-atlas-agent repo root.
# config.py -> pipeline-6 -> scripts -> pqs-atlas-agent
# -----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent.parent

# Source corpus (Pipeline 4 deliverable) and atlas output. Both overridable so
# the pipeline can target the real corpus location and a scratch output file.
SOURCE_CORPUS_PATH = os.getenv(
    "PQS_SOURCE_CORPUS",
    "data/source-prompts-clean-deterministic.jsonl",
)
OUTPUT_PATH = os.getenv(
    "PQS_ATLAS_OUTPUT",
    "scripts/pipeline-6/outputs/atlas-general.jsonl",
)

# Resolved absolute paths (use these for I/O; the strings above are the
# spec-literal config values).
SOURCE_CORPUS_ABS = (ROOT / SOURCE_CORPUS_PATH).resolve()
OUTPUT_ABS = (ROOT / OUTPUT_PATH).resolve()

KAPPA_REPORT_PATH = ROOT / "scripts" / "pipeline-6" / "outputs" / "kappa-phase-0.md"
CORRELATION_REPORT_PATH = ROOT / "findings" / "output-correlation.md"

# -----------------------------------------------------------------------------
# Endpoints — PQS scoring services. All overridable via env.
#
# Pipeline 6 talks to two endpoints with TWO DIFFERENT auth contracts:
#
#   /api/score        (pre-flight prompt scoring) — middleware-bypass auth.
#       Send a single x-pqs-internal-bypass: <PQS_INTERNAL_BYPASS_KEY> header.
#       Production middleware.js validates this key against the
#       PQS_PARTNER_BYPASS_KEYS partner-key registry (with PQS_INTERNAL_BYPASS_KEY
#       as the legacy single-key fallback); on a match it sets
#       x-pqs-bypass-verified=1 and the route handler reads THAT to skip the
#       x402 paywall. Pipeline 6 only sends the bypass key — it never sets the
#       verified header itself; middleware does.
#
#   /api/score-output (post-flight output scoring) — Bearer API key auth.
#       This endpoint does NOT honor the middleware bypass. It validates an
#       Authorization: Bearer <PQS_*> API key against the pqs_api_keys Supabase
#       table (route comment: "No x402 payment path — this endpoint is
#       internal-use"; handler enforces hasApiKey(req)). Send the PQS_API_KEY
#       as an Authorization: Bearer header — the bypass header returns 401 here.
#
# See internal_bypass_headers() (/api/score) and api_key_headers()
# (/api/score-output) below, and scripts/pipeline-6/README.md.
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
    "instruction_adherence": "haiku",
    "coherence":             "haiku",
    "specificity":           "haiku",
    "verifiability":         "haiku",
    "hallucination_risk":    "haiku",
}

# Dimension order is fixed — atlas rows and the output total depend on it.
OUTPUT_DIMENSIONS = (
    "factual_grounding",
    "instruction_adherence",
    "coherence",
    "specificity",
    "verifiability",
    "hallucination_risk",
)

# The 4 structural dimensions Phase 0 calibrates (planned "local" assignments).
STRUCTURAL_DIMENSIONS = (
    "instruction_adherence",
    "coherence",
    "specificity",
    "verifiability",
)

assert set(JUDGE_CONFIG) == set(OUTPUT_DIMENSIONS)
assert set(STRUCTURAL_DIMENSIONS).issubset(set(OUTPUT_DIMENSIONS))

# -----------------------------------------------------------------------------
# The 6-dimension post-flight rubric.
#
# Each definition is a self-contained instruction for a judge scoring ONLY that
# dimension on an integer 1-10 scale. Adapted from the canonical output-quality
# rubric in prompt-optimization-engine/lib/pqs-output-rubric.js (the shared
# source the deployed /api/score-output endpoint imports) so the local and
# cloud judges stay aligned with production.
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
    "specificity":
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


def internal_bypass_key() -> str:
    """The PQS internal bypass key. Sent as the x-pqs-internal-bypass header;
    middleware.js validates it against the partner-key registry."""
    tok = os.environ.get("PQS_INTERNAL_BYPASS_KEY")
    if not tok:
        raise RuntimeError(
            "PQS_INTERNAL_BYPASS_KEY not set — required for /api/score and "
            "/api/score-output internal bypass (middleware validates against "
            "PQS_PARTNER_BYPASS_KEYS registry or legacy PQS_INTERNAL_BYPASS_KEY "
            "env var). See scripts/pipeline-6/README.md."
        )
    return tok


def internal_bypass_headers() -> dict:
    """The x-pqs-internal-bypass request header carrying the internal bypass
    key. Middleware validates the key and, on a match, sets x-pqs-bypass-verified
    so the route handler skips x402 — Pipeline 6 sends only the bypass key.
    Use on /api/score calls only; /api/score-output requires Bearer auth."""
    tok = os.environ.get("PQS_INTERNAL_BYPASS_KEY")
    return {"x-pqs-internal-bypass": tok} if tok else {}


def api_key() -> str:
    """The PQS API key. Sent as Authorization: Bearer <key> to /api/score-output
    which validates against the pqs_api_keys Supabase table."""
    tok = os.environ.get("PQS_API_KEY")
    if not tok:
        raise RuntimeError(
            "PQS_API_KEY not set — required for /api/score-output Bearer auth. "
            "Unlike /api/score, the output-scoring endpoint does not honor "
            "middleware bypass; it validates Authorization: Bearer <PQS_*> "
            "against pqs_api_keys. See scripts/pipeline-6/README.md."
        )
    return tok


def api_key_headers() -> dict:
    """The Authorization: Bearer header carrying the PQS API key. Used on
    /api/score-output calls (which require Bearer auth, not bypass header)."""
    tok = os.environ.get("PQS_API_KEY")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


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
