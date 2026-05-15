"""
Pipeline 6 — cloud judge: Anthropic Haiku 4.5.

Two entry points:

  score_dimension_haiku(prompt, output, dimension)
      Scores ONE post-flight dimension via the Anthropic SDK directly, using
      a forced tool call for structured output (the openai-style structured-
      output idiom on the Anthropic SDK). Used for the knowledge dimensions
      (factual_grounding, hallucination_risk) in the full run, AND as the
      cross-judge reference in Phase 0 kappa calibration. The per-dimension
      prompt structure is identical to local_judge.score_dimension_local() so
      the two judges apply byte-identical dimension definitions.

  score_output_full(prompt, output, vertical)
      Calls the production /api/score-output endpoint for a full 6-dimension
      score in one request. This is the production-path score, kept for
      downstream observability. It is NOT used to build atlas rows — atlas
      rows need per-dimension judge attribution, which the single-dimension
      score_dimension_haiku() / score_dimension_local() calls provide.

Caching (SOP_PROMPT_CACHING): the per-dimension judge system prompt measures
well under 1k tokens — far below the 4096-token Haiku 4.5 cache floor. A
cache_control breakpoint would silently no-op (both cache_creation and
cache_read return 0). Caching is therefore not attempted here. The
/api/score-output endpoint handles its own server-side caching; we are the
client.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from config import (
    ENDPOINT_OUTPUT_SCORE,
    GENERATOR_MAX_TOKENS,
    GENERATOR_MODEL,
    HAIKU_MODEL,
    JUDGE_MAX_TOKENS,
    MAX_DIM,
    MAX_OUTPUT_CHARS,
    MAX_RETRIES,
    MIN_DIM,
    OUTPUT_DIMENSION_DEFINITIONS,
    REQUEST_TIMEOUT_SEC,
    RETRY_BASE_SEC,
    internal_bearer,
    internal_flag_header,
)

# Haiku 4.5 pricing per million tokens (USD) — SOP_PROMPT_CACHING table.
HAIKU_PRICE_PER_M = {"input": 1.00, "output": 5.00}

# Forced-tool schema for structured output. The model must call this tool;
# tool_choice pins it, so the response is always a typed {score, reasoning}.
_SCORE_TOOL = {
    "name": "submit_dimension_score",
    "description": "Submit the integer score and reasoning for the single "
                   "output-quality dimension under evaluation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "integer",
                "minimum": MIN_DIM,
                "maximum": MAX_DIM,
                "description": "Integer score for this dimension.",
            },
            "reasoning": {
                "type": "string",
                "description": "One to two sentences justifying the score with "
                               "specific evidence from the output.",
            },
        },
        "required": ["score", "reasoning"],
    },
}


def _judge_system(dimension: str) -> str:
    """System prompt scoped to exactly one dimension — matches local_judge."""
    definition = OUTPUT_DIMENSION_DEFINITIONS[dimension]
    return (
        "You are an independent AI output quality judge. You evaluate the quality "
        "of an AI-generated OUTPUT — not the prompt that requested it. You are a "
        "separate evaluator instance, not the model that produced the output.\n\n"
        f"You score exactly one dimension: {dimension}.\n\n"
        f"Definition of {dimension}:\n{definition}\n\n"
        f"Score this single dimension as an integer from {MIN_DIM} to {MAX_DIM}, "
        "then call the submit_dimension_score tool with the score and a one to "
        "two sentence reasoning grounded in specific evidence from the output."
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


def _haiku_cost(usage) -> float:
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    return (in_tok * HAIKU_PRICE_PER_M["input"]
            + out_tok * HAIKU_PRICE_PER_M["output"]) / 1_000_000


def generate_output(prompt: str) -> dict:
    """Generate an answer to `prompt` with the generator model (Haiku 4.5).

    Used by both kappa_phase_0.py and score_outputs.py so the generated output
    being scored is produced the same way in calibration and the full run.

    Returns {text: str, model: str, usage: {input_tokens, output_tokens},
             cost_usd: float}. Retries transient errors with backoff.
    """
    from anthropic import Anthropic
    client = Anthropic()

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            msg = client.messages.create(
                model=GENERATOR_MODEL,
                max_tokens=GENERATOR_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            if msg.stop_reason == "refusal":
                # Generator safety refusal — surface as structured outcome so
                # the caller can decide to skip rather than retry blindly.
                return {
                    "text": "",
                    "model": msg.model,
                    "stop_reason": "refusal",
                    "usage": {
                        "input_tokens": getattr(msg.usage, "input_tokens", 0) or 0,
                        "output_tokens": getattr(msg.usage, "output_tokens", 0) or 0,
                    },
                    "cost_usd": round(_haiku_cost(msg.usage), 6),
                }
            text = "".join(
                b.text for b in msg.content if getattr(b, "type", None) == "text"
            ).strip()
            if not text:
                raise ValueError("generator returned empty text")
            in_tok = getattr(msg.usage, "input_tokens", 0) or 0
            out_tok = getattr(msg.usage, "output_tokens", 0) or 0
            return {
                "text": text,
                "model": msg.model,
                "stop_reason": msg.stop_reason,
                "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
                "cost_usd": round(_haiku_cost(msg.usage), 6),
            }
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if attempt < MAX_RETRIES:
                wait = RETRY_BASE_SEC * (2 ** (attempt - 1))
                print(
                    f"    [generate retry {attempt}/{MAX_RETRIES}]: "
                    f"{type(e).__name__}: {str(e)[:160]} — sleeping {wait:.1f}s",
                    flush=True,
                )
                time.sleep(wait)

    raise RuntimeError(
        f"generator failed after {MAX_RETRIES} attempts"
    ) from last_exc


def score_dimension_haiku(prompt: str, output: str, dimension: str) -> dict:
    """Score one post-flight dimension with Haiku 4.5 via a forced tool call.

    Returns {score: int (1-10), reasoning: str, model: str,
             usage: {input_tokens, output_tokens}, cost_usd: float}.

    Retries up to MAX_RETRIES with exponential backoff on transient errors or
    a malformed tool call. Raises the last exception if all attempts fail.
    """
    if dimension not in OUTPUT_DIMENSION_DEFINITIONS:
        raise ValueError(f"unknown dimension: {dimension!r}")

    from anthropic import Anthropic
    client = Anthropic()

    system = _judge_system(dimension)
    user_message = _judge_user_message(prompt, output)

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            msg = client.messages.create(
                model=HAIKU_MODEL,
                max_tokens=JUDGE_MAX_TOKENS,
                system=system,
                tools=[_SCORE_TOOL],
                tool_choice={"type": "tool", "name": "submit_dimension_score"},
                messages=[{"role": "user", "content": user_message}],
            )
            tool_use = next(
                (b for b in msg.content if getattr(b, "type", None) == "tool_use"),
                None,
            )
            if tool_use is None:
                raise ValueError(f"{dimension}: no tool_use block in response")

            data = tool_use.input or {}
            score = data.get("score")
            if isinstance(score, float) and score.is_integer():
                score = int(score)
            if not isinstance(score, int) or not (MIN_DIM <= score <= MAX_DIM):
                raise ValueError(f"{dimension}: bad score {score!r}")

            reasoning = data.get("reasoning", "")
            if not isinstance(reasoning, str):
                reasoning = str(reasoning)

            in_tok = getattr(msg.usage, "input_tokens", 0) or 0
            out_tok = getattr(msg.usage, "output_tokens", 0) or 0
            return {
                "score": score,
                "reasoning": reasoning.strip(),
                "model": msg.model,
                "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
                "cost_usd": round(_haiku_cost(msg.usage), 6),
            }
        except Exception as e:  # noqa: BLE001 — retry any transient failure
            last_exc = e
            if attempt < MAX_RETRIES:
                wait = RETRY_BASE_SEC * (2 ** (attempt - 1))
                print(
                    f"    [haiku retry {attempt}/{MAX_RETRIES}] {dimension}: "
                    f"{type(e).__name__}: {str(e)[:160]} — sleeping {wait:.1f}s",
                    flush=True,
                )
                time.sleep(wait)

    raise RuntimeError(
        f"haiku judge failed for dimension {dimension!r} after {MAX_RETRIES} attempts"
    ) from last_exc


def score_output_full(prompt: str, output: str, vertical: str) -> dict:
    """Full 6-dimension score via the production /api/score-output endpoint.

    Production-path score, kept for downstream observability. Not used to build
    atlas rows. Returns the parsed `score` object from the endpoint response
    ({total, out_of, dimensions, ...}). Retries transient errors / 5xx.
    """
    body = json.dumps({
        "prompt": prompt,
        "output": output[:MAX_OUTPUT_CHARS],
        "vertical": vertical,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {internal_bearer()}",
        **internal_flag_header(),
    }

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                ENDPOINT_OUTPUT_SCORE, data=body, method="POST", headers=headers,
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            score = payload.get("score") or payload.get("original", {}).get("score")
            if not score:
                raise ValueError(f"/api/score-output response missing score: "
                                 f"keys={list(payload)}")
            return score
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise
            last_exc = e
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            last_exc = e

        if attempt < MAX_RETRIES:
            wait = RETRY_BASE_SEC * (2 ** (attempt - 1))
            print(
                f"    [score-output retry {attempt}/{MAX_RETRIES}]: "
                f"{type(last_exc).__name__}: {str(last_exc)[:160]} — sleeping {wait:.1f}s",
                flush=True,
            )
            time.sleep(wait)

    raise RuntimeError(
        f"/api/score-output failed after {MAX_RETRIES} attempts"
    ) from last_exc
