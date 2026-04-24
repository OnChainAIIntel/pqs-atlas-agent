"""
F->B Lift comparison: Claude Opus 4.7 vs Claude Sonnet 4.6 as rewriter.

Methodology:
  - 5 F-grade seeds from the Arm 3 WildChat-mid set (same seeds used in the
    Pipeline-4 F->B Lift pilot, findings/rubric-ceiling.md).
  - Scorer held CONSTANT: Claude Opus 4.7 + RUBRIC_PROMPT from pipeline-5
    (kappa-calibrated rubric, SHA256 asserted at runtime). Temperature 0.
  - Rewriter VARIES:
      arm A -> claude-opus-4-7
      arm B -> claude-sonnet-4-6
  - Per prompt: pre-score once (shared), rewrite twice (one per arm),
    post-score twice (one per arm rewrite).

This isolates rewrite quality from self-grading bias. No production endpoint
is touched. One-off hackathon comparison, not part of any deployed pipeline.

Outputs:
  findings/fb-lift-opus.jsonl
  findings/fb-lift-sonnet.jsonl

Env:
  Reads ANTHROPIC_KEY from ~/Desktop/prompt-optimization-engine/.env.local
  (matches the pattern in scripts/pipeline-5/run-raters.py).
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

ROOT = Path(__file__).resolve().parent.parent  # pqs-atlas-agent/
sys.path.insert(0, str(ROOT / "scripts" / "pipeline-5"))

from rubric import (  # noqa: E402
    RUBRIC_PROMPT,
    DIMENSIONS,
    grade_from_total,
    rubric_checksum,
)

EXPECTED_RUBRIC_SHA = "0dfa088cb5d2253fb2793b3dcc17da72ecda229b7f55bdc02cbcd690b38315cc"

OPUS_MODEL = "claude-opus-4-7"
SONNET_MODEL = "claude-sonnet-4-6"

SCORER_MODEL = OPUS_MODEL  # kappa-validated rater

CORPUS_PATH = ROOT / "data" / "source-prompts-clean-deterministic.jsonl"
FINDINGS_DIR = ROOT / "findings"
OUT_OPUS = FINDINGS_DIR / "fb-lift-opus.jsonl"
OUT_SONNET = FINDINGS_DIR / "fb-lift-sonnet.jsonl"

# Arm 3 seeds (WildChat mid, 5 F-grade) — canonical set from the Pipeline-4
# F->B Lift pilot. Pre-score totals from data/pilots/path2-scoping.jsonl.
ARM3_SEEDS = [
    {"seed_id": "wc-01", "source_row_id": "45503aaeb51ac7a7c49be6ca1e5b3842:103198",
     "vertical": "software",  "prior_pre_score": 12, "mode": "error-wall, no ask"},
    {"seed_id": "wc-02", "source_row_id": "07ad16b4621469b3f48a816d6d14db1c:109809",
     "vertical": "software",  "prior_pre_score": 19, "mode": "underspec assignment"},
    {"seed_id": "wc-03", "source_row_id": "2c2064ec094d57a664a76da3cef17f7a:112324",
     "vertical": "general",   "prior_pre_score": 23, "mode": "vague list request"},
    {"seed_id": "wc-04", "source_row_id": "8662da0afd8a3427bfd6c64d689cb9a0:106771",
     "vertical": "general",   "prior_pre_score": 27, "mode": "awkward constraints"},
    {"seed_id": "wc-05", "source_row_id": "8a912051c89d2781dd963232f9593eb0:102751",
     "vertical": "education", "prior_pre_score": 29, "mode": "persona, unbounded"},
]

MAX_RETRIES = 3
RETRY_BASE_SEC = 2.0
REQUEST_TIMEOUT_SEC = 120


# -----------------------------------------------------------------------------
# Env
# -----------------------------------------------------------------------------
def _load_env():
    env_path = Path.home() / "Desktop" / "prompt-optimization-engine" / ".env.local"
    if not env_path.exists():
        raise RuntimeError(f"missing {env_path}")
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k in {"ANTHROPIC_KEY", "ANTHROPIC_API_KEY"} and not os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = v
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not found")


# -----------------------------------------------------------------------------
# Seed resolution
# -----------------------------------------------------------------------------
def _resolve_seed_prompts():
    wanted = {s["source_row_id"] for s in ARM3_SEEDS}
    found = {}
    with CORPUS_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            rid = r.get("source_row_id")
            if rid in wanted:
                found[rid] = r.get("prompt") or r.get("text") or ""
    missing = wanted - set(found.keys())
    if missing:
        raise RuntimeError(f"missing seed rows: {missing}")
    for seed in ARM3_SEEDS:
        seed["prompt_text"] = found[seed["source_row_id"]]
    return ARM3_SEEDS


# -----------------------------------------------------------------------------
# Anthropic call helpers
# -----------------------------------------------------------------------------
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)

def _extract_json(text: str) -> dict:
    if not text:
        raise ValueError("empty response")
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```\s*$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        m = _JSON_OBJ_RE.search(stripped)
        if not m:
            raise ValueError(f"no JSON in: {text[:200]!r}")
        return json.loads(m.group(0))


def _anthropic(model: str, system: str, user: str, max_tokens: int, temperature: float | None = None) -> dict:
    url = "https://api.anthropic.com/v1/messages"
    body_obj = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if temperature is not None:
        body_obj["temperature"] = temperature
    body = json.dumps(body_obj).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            latency_ms = int((time.time() - t0) * 1000)
            text = "".join(b.get("text", "") for b in payload.get("content", []))
            usage = payload.get("usage", {})
            return {
                "text": text,
                "latency_ms": latency_ms,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "stop_reason": payload.get("stop_reason"),
            }
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode("utf-8", errors="replace")[:400]
            last_err = f"HTTP {e.code}: {body_txt}"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(RETRY_BASE_SEC * (2 ** attempt))
                continue
            raise RuntimeError(last_err)
        except Exception as e:
            last_err = str(e)
            time.sleep(RETRY_BASE_SEC * (2 ** attempt))
    raise RuntimeError(f"anthropic failed after {MAX_RETRIES} retries: {last_err}")


# -----------------------------------------------------------------------------
# Scorer (Opus 4.7 + RUBRIC_PROMPT)
# -----------------------------------------------------------------------------
def score_prompt(prompt_text: str) -> dict:
    user = f"Prompt to score:\n\n{prompt_text}\n\nReturn only the JSON object."
    r = _anthropic(SCORER_MODEL, RUBRIC_PROMPT, user, max_tokens=600)
    raw = _extract_json(r["text"])
    scores = {}
    total = 0
    for d in DIMENSIONS:
        v = raw.get(d)
        if v is None:
            raise ValueError(f"missing dim {d} in {raw}")
        n = int(round(float(v)))
        n = max(1, min(10, n))
        scores[d] = n
        total += n
    grade = grade_from_total(total)
    return {
        "dim_scores": scores,
        "total": total,
        "grade": grade,
        "latency_ms": r["latency_ms"],
        "tokens_in": r["input_tokens"],
        "tokens_out": r["output_tokens"],
    }


# -----------------------------------------------------------------------------
# Rewriter (varies: Opus 4.7 or Sonnet 4.6)
# -----------------------------------------------------------------------------
REWRITE_SYSTEM = """You are a prompt optimization expert. Your task is to rewrite the user's prompt so it scores highly on these 8 dimensions: clarity, specificity, context, constraints, output_format, role_definition, examples, cot_structure.

Rules:
1. Preserve the user's underlying intent. Do not invent new tasks.
2. Add the structure a model needs to produce a good answer without guessing.
3. Return ONLY valid JSON. No markdown, no preamble. Shape:
{"optimized_prompt": "..."}"""

def rewrite_prompt(model: str, prompt_text: str) -> dict:
    user = f'Rewrite this prompt:\n\n"""\n{prompt_text}\n"""'
    r = _anthropic(model, REWRITE_SYSTEM, user, max_tokens=2500)
    raw = _extract_json(r["text"])
    optimized = raw.get("optimized_prompt")
    if not optimized or not isinstance(optimized, str):
        raise ValueError(f"bad rewrite payload: {raw}")
    return {
        "optimized_prompt": optimized.strip(),
        "latency_ms": r["latency_ms"],
        "tokens_in": r["input_tokens"],
        "tokens_out": r["output_tokens"],
        "stop_reason": r["stop_reason"],
    }


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    _load_env()

    have_sha = rubric_checksum()
    if have_sha != EXPECTED_RUBRIC_SHA:
        raise RuntimeError(f"rubric checksum mismatch: {have_sha} != {EXPECTED_RUBRIC_SHA}")

    seeds = _resolve_seed_prompts()
    FINDINGS_DIR.mkdir(parents=True, exist_ok=True)

    # Cache pre-scores across arms.
    pre_score_cache = {}

    def write_row(path: Path, row: dict):
        with path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Truncate outputs for a clean run.
    OUT_OPUS.write_text("")
    OUT_SONNET.write_text("")

    for seed in seeds:
        sid = seed["seed_id"]
        prompt = seed["prompt_text"]

        print(f"\n[{sid}] pre-score (scorer={SCORER_MODEL}) ...", flush=True)
        if sid not in pre_score_cache:
            pre = score_prompt(prompt)
            pre_score_cache[sid] = pre
        else:
            pre = pre_score_cache[sid]
        print(f"  pre  total={pre['total']:2d} grade={pre['grade']} (prior={seed['prior_pre_score']})")

        for model, out_path, arm in [
            (OPUS_MODEL,   OUT_OPUS,   "opus"),
            (SONNET_MODEL, OUT_SONNET, "sonnet"),
        ]:
            print(f"  [{arm}] rewrite via {model} ...", flush=True)
            rw = rewrite_prompt(model, prompt)
            print(f"  [{arm}] post-score (scorer={SCORER_MODEL}) ...", flush=True)
            post = score_prompt(rw["optimized_prompt"])
            lift = post["total"] - pre["total"]
            print(f"  [{arm}] post total={post['total']:2d} grade={post['grade']}  lift=+{lift}")

            write_row(out_path, {
                "seed_id": sid,
                "source_row_id": seed["source_row_id"],
                "vertical": seed["vertical"],
                "mode": seed["mode"],
                "rewriter_model": model,
                "scorer_model": SCORER_MODEL,
                "rubric_sha256": EXPECTED_RUBRIC_SHA,
                "prompt": prompt,
                "optimized_prompt": rw["optimized_prompt"],
                "pre_score": {
                    "total": pre["total"], "grade": pre["grade"],
                    "dim_scores": pre["dim_scores"],
                    "tokens_in": pre["tokens_in"], "tokens_out": pre["tokens_out"],
                },
                "post_score": {
                    "total": post["total"], "grade": post["grade"],
                    "dim_scores": post["dim_scores"],
                    "tokens_in": post["tokens_in"], "tokens_out": post["tokens_out"],
                },
                "lift_delta": lift,
                "rewrite_latency_ms": rw["latency_ms"],
                "rewrite_tokens_in": rw["tokens_in"],
                "rewrite_tokens_out": rw["tokens_out"],
                "rewrite_stop_reason": rw["stop_reason"],
            })

    # Summary
    def _load(p):
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    opus_rows = _load(OUT_OPUS)
    sonnet_rows = _load(OUT_SONNET)

    def _mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    opus_lifts = [r["lift_delta"] for r in opus_rows]
    sonnet_lifts = [r["lift_delta"] for r in sonnet_rows]

    print("\n====== SUMMARY ======")
    print(f"Opus   n={len(opus_lifts)}  mean lift = +{_mean(opus_lifts):.2f}  lifts={opus_lifts}")
    print(f"Sonnet n={len(sonnet_lifts)}  mean lift = +{_mean(sonnet_lifts):.2f}  lifts={sonnet_lifts}")
    delta = _mean(opus_lifts) - _mean(sonnet_lifts)
    print(f"Opus - Sonnet mean delta = {delta:+.2f} points")
    print("=====================")


if __name__ == "__main__":
    main()
