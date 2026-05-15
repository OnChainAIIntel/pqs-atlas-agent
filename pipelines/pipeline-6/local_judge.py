"""
Pipeline 6 — local judge: Ollama Gemma 4 9B (gemma2:9b) scoring a single
post-flight dimension at a time.

The local judge handles the 4 structural dimensions (instruction_adherence,
coherence, specificity_of_claims, verifiability) — provided Phase 0 kappa
calibration confirms it agrees with Haiku. Each call scores ONE dimension so
the judge sees only that dimension's definition; this keeps the per-dim score
attributable and matches how cloud_judge.score_dimension_haiku() works.

Uses the Ollama HTTP API directly (urllib) — no ollama python client, to keep
the dependency surface minimal. Mirrors the urllib + retry style of
scripts/pipeline-5/run-raters.py.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from config import (
    MAX_DIM,
    MAX_OUTPUT_CHARS,
    MAX_RETRIES,
    MIN_DIM,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OUTPUT_DIMENSION_DEFINITIONS,
    REQUEST_TIMEOUT_SEC,
    RETRY_BASE_SEC,
)


def _judge_system(dimension: str) -> str:
    """System prompt scoped to exactly one dimension definition."""
    definition = OUTPUT_DIMENSION_DEFINITIONS[dimension]
    return (
        "You are an independent AI output quality judge. You evaluate the quality "
        "of an AI-generated OUTPUT — not the prompt that requested it. You are a "
        "separate evaluator instance, not the model that produced the output.\n\n"
        f"You score exactly one dimension: {dimension}.\n\n"
        f"Definition of {dimension}:\n{definition}\n\n"
        f"Score this single dimension as an integer from {MIN_DIM} to {MAX_DIM}. "
        "Give a one to two sentence reasoning that justifies the score with "
        "specific evidence from the output.\n\n"
        'Respond ONLY with minified JSON, no markdown, no commentary. Shape:\n'
        '{"score":0,"reasoning":""}'
    )


def _judge_user_message(prompt: str, output: str) -> str:
    """User message: original instruction (for adherence) plus the output."""
    output = output[:MAX_OUTPUT_CHARS]
    if prompt:
        return (
            "Original instruction:\n\n" + prompt
            + "\n\n---\n\nAI output to evaluate:\n\n" + output
        )
    return "AI output to evaluate:\n\n" + output


def _post_ollama(system: str, user_message: str) -> str:
    """POST one /api/generate request. Returns the raw `.response` string.

    Raises urllib.error.HTTPError on 5xx (the caller retries those).
    """
    body = json.dumps({
        "model": OLLAMA_MODEL,
        "system": system,
        "prompt": user_message,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_HOST.rstrip("/") + "/api/generate",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("response", "")


def _parse_score(raw: str, dimension: str) -> dict:
    """Parse {score, reasoning} from the model's JSON response.

    Raises ValueError on missing fields or an out-of-range / non-integer score
    so the caller's retry loop can re-attempt.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{dimension}: response is not valid JSON: {raw[:200]!r}") from e

    if "score" not in parsed:
        raise ValueError(f"{dimension}: response missing 'score': {parsed!r}")

    score = parsed["score"]
    if isinstance(score, float) and score.is_integer():
        score = int(score)
    if not isinstance(score, int):
        raise ValueError(f"{dimension}: score is not an integer: {score!r}")
    if not (MIN_DIM <= score <= MAX_DIM):
        raise ValueError(f"{dimension}: score {score} out of range [{MIN_DIM},{MAX_DIM}]")

    reasoning = parsed.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = str(reasoning)
    return {"score": score, "reasoning": reasoning.strip()}


def score_dimension_local(prompt: str, output: str, dimension: str) -> dict:
    """Score one post-flight dimension with the local Gemma judge.

    Returns {score: int (1-10), reasoning: str, model: "gemma2:9b"}.

    Retries up to MAX_RETRIES with exponential backoff on parse failure or an
    Ollama 5xx. Raises the last exception if all attempts fail.
    """
    if dimension not in OUTPUT_DIMENSION_DEFINITIONS:
        raise ValueError(f"unknown dimension: {dimension!r}")

    system = _judge_system(dimension)
    user_message = _judge_user_message(prompt, output)

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = _post_ollama(system, user_message)
            parsed = _parse_score(raw, dimension)
            return {
                "score": parsed["score"],
                "reasoning": parsed["reasoning"],
                "model": OLLAMA_MODEL,
            }
        except urllib.error.HTTPError as e:
            # Retry 5xx; surface 4xx immediately (bad request won't self-heal).
            if e.code < 500:
                raise
            last_exc = e
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            last_exc = e

        if attempt < MAX_RETRIES:
            wait = RETRY_BASE_SEC * (2 ** (attempt - 1))
            print(
                f"    [local retry {attempt}/{MAX_RETRIES}] {dimension}: "
                f"{type(last_exc).__name__}: {str(last_exc)[:160]} — sleeping {wait:.1f}s",
                flush=True,
            )
            time.sleep(wait)

    raise RuntimeError(
        f"local judge failed for dimension {dimension!r} after {MAX_RETRIES} attempts"
    ) from last_exc
