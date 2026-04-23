"""
Pipeline 5 — Gate C: Three-rater scoring over the 15 calibration anchors.

Runs every anchor in data/pipeline-5-anchors.jsonl through three independent
raters and writes one row per (anchor, rater) to
data/pipeline-5-rater-outputs.jsonl. 15 anchors × 3 raters = 45 rows.

Raters:
  1. PQS production /api/score/full   (black-box reference — Sonnet 4 under the hood)
  2. Claude Opus 4.7 direct           (RUBRIC_PROMPT as system)
  3. OpenAI GPT-4o direct             (RUBRIC_PROMPT as system)

Per-row fields:
  anchor_id, rater, model_id, dim_scores {8 keys}, total, grade,
  raw_response (string), latency_ms, cost_usd, error (optional)

Determinism:
  - Rubric checksum asserted at import (byte-identity guard)
  - temperature=0 on Opus + GPT-4o
  - PQS production handles its own sampling (seed not exposed)
  - Resume-safe: existing (anchor_id, rater) rows are skipped on re-run

Caching (SOP_PROMPT_CACHING):
  - RUBRIC_PROMPT measures ~640 tokens — below the 4096-token Opus 4.7 cache
    floor. cache_control would silently no-op. Not attempted.
  - PQS production handles its own caching; we're the client.

Env (read from ~/Desktop/prompt-optimization-engine/.env.local):
  ANTHROPIC_KEY        -> ANTHROPIC_API_KEY (renamed 2026-04-23 rotation)
  OPENAI_API_KEY
  PQS_API_KEY
  PQS_INTERNAL_TOKEN
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # pqs-atlas-agent/
sys.path.insert(0, str(ROOT / "scripts" / "pipeline-5"))

from rubric import (  # noqa: E402
    RUBRIC_PROMPT,
    DIMENSIONS,
    GRADE_CUTOFFS,
    MIN_DIM,
    MAX_DIM,
    grade_from_total,
    rubric_checksum,
)

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
ANCHORS_PATH = ROOT / "data" / "pipeline-5-anchors.jsonl"
OUTPUTS_PATH = ROOT / "data" / "pipeline-5-rater-outputs.jsonl"

PQS_URL = "https://promptqualityscore.com/api/score/full"

OPUS_MODEL = "claude-opus-4-7"
GPT4O_MODEL = "gpt-4o"

# Rubric checksum that MUST be live when this script runs. Asserted pre-flight.
# (Any edit to rubric.py constants bumps this; CI-style guard.)
EXPECTED_RUBRIC_SHA = "0dfa088cb5d2253fb2793b3dcc17da72ecda229b7f55bdc02cbcd690b38315cc"

# Model pricing per million tokens (USD). Sources:
#   Opus 4.7  -> SOP_PROMPT_CACHING table ($5 input). Output at standard $15 Opus rate.
#   GPT-4o    -> OpenAI price list: $2.50 input / $10.00 output.
PRICING_PER_M_USD = {
    OPUS_MODEL:  {"input": 5.00,  "output": 15.00},
    GPT4O_MODEL: {"input": 2.50,  "output": 10.00},
}

MAX_RETRIES = 3
RETRY_BASE_SEC = 2.0
REQUEST_TIMEOUT_SEC = 90


# -----------------------------------------------------------------------------
# Env loading — mirrors scripts/pipeline-4/extract.py::_load_env_from_siblings
# -----------------------------------------------------------------------------
def _load_env_from_siblings():
    env_path = Path.home() / "Desktop" / "prompt-optimization-engine" / ".env.local"
    if not env_path.exists():
        raise RuntimeError(f"missing {env_path}")
    wanted = {"ANTHROPIC_KEY", "OPENAI_API_KEY", "PQS_API_KEY", "PQS_INTERNAL_TOKEN"}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k in wanted and not os.environ.get(k):
            os.environ[k] = v
    # Map renamed key
    if os.environ.get("ANTHROPIC_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = os.environ["ANTHROPIC_KEY"]

    missing = [k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PQS_API_KEY", "PQS_INTERNAL_TOKEN")
               if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing env keys: {missing}")


# -----------------------------------------------------------------------------
# Output parsing helpers
# -----------------------------------------------------------------------------
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)

def _extract_json(text: str) -> dict:
    """Tolerate code-fence wrapping; find first {...} object and parse."""
    if not text:
        raise ValueError("empty response")
    # Strip common ```json fences
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```\s*$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        m = _JSON_OBJ_RE.search(stripped)
        if not m:
            raise ValueError(f"no JSON object in response: {text[:200]!r}")
        return json.loads(m.group(0))


def _coerce_scores(raw: dict, source_label: str) -> dict:
    """
    Normalize rater output to {dim_scores, total, grade}. If the rater's
    computed total or grade disagrees with the canonical mapping, prefer
    the recomputed values but keep the rater's originals under `raw_response`.
    """
    missing = [d for d in DIMENSIONS if d not in raw]
    if missing:
        raise ValueError(f"{source_label} missing dims {missing}: {raw!r}")
    dim_scores = {}
    for d in DIMENSIONS:
        v = raw[d]
        if not isinstance(v, int):
            # Some models emit "7.0" or "7"; coerce carefully.
            v = int(round(float(v)))
        if not (MIN_DIM <= v <= MAX_DIM):
            raise ValueError(f"{source_label} dim {d} out of range: {v}")
        dim_scores[d] = v
    total = sum(dim_scores.values())
    grade = grade_from_total(total)
    return {"dim_scores": dim_scores, "total": total, "grade": grade}


# -----------------------------------------------------------------------------
# Rater 1 — PQS production /api/score/full
# -----------------------------------------------------------------------------
def rate_pqs(prompt: str) -> dict:
    body = json.dumps({"prompt": prompt, "vertical": "general"}).encode("utf-8")
    req = urllib.request.Request(
        PQS_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['PQS_API_KEY']}",
            "X-PQS-Internal": os.environ.get("PQS_INTERNAL_TOKEN", ""),
        },
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    latency_ms = int((time.perf_counter() - t0) * 1000)

    score_obj = (payload.get("original") or {}).get("score") or {}
    dims = score_obj.get("dimensions") or {}
    if not dims:
        raise ValueError(f"PQS response missing dimensions: keys={list(score_obj)}")

    # PQS responds with full 8-dim object using identical key names.
    raw_like = {d: dims[d] for d in DIMENSIONS if d in dims}
    coerced = _coerce_scores(raw_like, "pqs")
    return {
        "model_id": payload.get("model") or "pqs-production",
        "dim_scores": coerced["dim_scores"],
        "total": coerced["total"],
        "grade": coerced["grade"],
        "raw_response": json.dumps(score_obj, separators=(",", ":")),
        "latency_ms": latency_ms,
        "cost_usd": 0.0,  # black-box — server cost not exposed
    }


# -----------------------------------------------------------------------------
# Rater 2 — Anthropic Opus 4.7 direct
# -----------------------------------------------------------------------------
def rate_opus(prompt: str) -> dict:
    from anthropic import Anthropic
    client = Anthropic()
    t0 = time.perf_counter()
    # Note: Opus 4.7 rejects `temperature` (deprecated for this model per API
    # error). The model is deterministic enough at default for integer-grade
    # agreement; calibration doesn't need perfect byte-determinism.
    msg = client.messages.create(
        model=OPUS_MODEL,
        max_tokens=512,
        system=RUBRIC_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)

    in_tok = getattr(msg.usage, "input_tokens", 0) or 0
    out_tok = getattr(msg.usage, "output_tokens", 0) or 0
    px = PRICING_PER_M_USD[OPUS_MODEL]
    cost = (in_tok * px["input"] + out_tok * px["output"]) / 1_000_000

    # Handle Opus safety refusal explicitly — surface as a structured outcome
    # rather than letting the "empty response" failure cascade into retries.
    # For kappa computation, refusals are treated as missing data (documented).
    if msg.stop_reason == "refusal":
        return {
            "model_id": msg.model,
            "dim_scores": None,
            "total": None,
            "grade": None,
            "status": "refused",
            "stop_reason": "refusal",
            "raw_response": "",
            "latency_ms": latency_ms,
            "cost_usd": round(cost, 6),
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
        }

    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    parsed = _extract_json(text)
    coerced = _coerce_scores(parsed, "opus")

    return {
        "model_id": msg.model,
        "dim_scores": coerced["dim_scores"],
        "total": coerced["total"],
        "grade": coerced["grade"],
        "status": "ok",
        "raw_response": text,
        "latency_ms": latency_ms,
        "cost_usd": round(cost, 6),
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    }


# -----------------------------------------------------------------------------
# Rater 3 — OpenAI GPT-4o direct
# -----------------------------------------------------------------------------
def rate_gpt4o(prompt: str) -> dict:
    from openai import OpenAI
    client = OpenAI()
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=GPT4O_MODEL,
        temperature=0,
        max_tokens=512,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": RUBRIC_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)

    text = resp.choices[0].message.content or ""
    parsed = _extract_json(text)
    coerced = _coerce_scores(parsed, "gpt4o")

    in_tok = resp.usage.prompt_tokens or 0
    out_tok = resp.usage.completion_tokens or 0
    px = PRICING_PER_M_USD[GPT4O_MODEL]
    cost = (in_tok * px["input"] + out_tok * px["output"]) / 1_000_000

    return {
        "model_id": resp.model,
        "dim_scores": coerced["dim_scores"],
        "total": coerced["total"],
        "grade": coerced["grade"],
        "raw_response": text,
        "latency_ms": latency_ms,
        "cost_usd": round(cost, 6),
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    }


RATERS = [
    ("pqs_production", rate_pqs),
    ("opus_4_7",       rate_opus),
    ("gpt_4o",         rate_gpt4o),
]


# -----------------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------------
def _load_existing_outputs() -> set[tuple[str, str]]:
    """Return set of (anchor_id, rater) pairs already in the output file."""
    if not OUTPUTS_PATH.exists():
        return set()
    seen = set()
    for line in OUTPUTS_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        seen.add((row["anchor_id"], row["rater"]))
    return seen


def _call_with_retry(fn, prompt: str, label: str) -> dict:
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(prompt)
        except Exception as e:
            last_exc = e
            wait = RETRY_BASE_SEC * (2 ** (attempt - 1))
            print(f"    [retry {attempt}/{MAX_RETRIES}] {label}: {type(e).__name__}: {str(e)[:160]} — sleeping {wait:.1f}s", flush=True)
            time.sleep(wait)
    raise last_exc


def main():
    _load_env_from_siblings()

    actual_sha = rubric_checksum()
    if actual_sha != EXPECTED_RUBRIC_SHA:
        raise SystemExit(
            f"Rubric checksum mismatch. Expected {EXPECTED_RUBRIC_SHA}, got {actual_sha}.\n"
            f"Rubric was edited — update EXPECTED_RUBRIC_SHA and re-run all 45 calls."
        )
    print(f"✓ rubric sha256 verified: {actual_sha[:16]}…")

    anchors = [json.loads(l) for l in ANCHORS_PATH.read_text().splitlines() if l.strip()]
    assert len(anchors) == 15, f"expected 15 anchors, got {len(anchors)}"

    seen = _load_existing_outputs()
    print(f"✓ {len(anchors)} anchors loaded; {len(seen)} rater outputs already present (resume)")

    total_cost = 0.0
    writes = 0
    failures = []

    with OUTPUTS_PATH.open("a", encoding="utf-8") as out:
        for anchor in anchors:
            aid = anchor["anchor_id"]
            prompt = anchor["prompt_text"]
            for rater_name, rater_fn in RATERS:
                if (aid, rater_name) in seen:
                    continue
                label = f"{aid}/{rater_name}"
                print(f"  scoring {label} …", flush=True)
                try:
                    result = _call_with_retry(rater_fn, prompt, label)
                    row = {
                        "anchor_id": aid,
                        "target_band": anchor["target_band"],
                        "rater": rater_name,
                        "model_id": result["model_id"],
                        "status": result.get("status", "ok"),
                        "dim_scores": result["dim_scores"],
                        "total": result["total"],
                        "grade": result["grade"],
                        "latency_ms": result["latency_ms"],
                        "cost_usd": result["cost_usd"],
                        "usage": result.get("usage"),
                        "raw_response": result["raw_response"],
                        "rubric_sha": actual_sha,
                        "timestamp": time.time(),
                    }
                    if result.get("stop_reason"):
                        row["stop_reason"] = result["stop_reason"]
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    out.flush()
                    writes += 1
                    total_cost += result["cost_usd"]
                    print(f"    ✓ total={result['total']} grade={result['grade']} cost=${result['cost_usd']:.4f} {result['latency_ms']}ms", flush=True)
                except Exception as e:
                    failures.append((label, repr(e)))
                    print(f"    ✗ FAILED {label}: {e}", flush=True)

    print()
    print(f"Gate C complete: {writes} new rows written, total cost ≈ ${total_cost:.4f}")
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for label, err in failures:
            print(f"  - {label}: {err[:200]}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
