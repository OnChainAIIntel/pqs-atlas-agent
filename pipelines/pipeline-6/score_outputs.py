"""
Pipeline 6 — main executor: build the output-scoring atlas.

For each prompt in the Pipeline 4 software source corpus:
  1. POST to /api/score          -> 8-dim pre-flight prompt_score
  2. generate an output          -> Haiku 4.5 generator
  3. score the output on 6 dims  -> routed per JUDGE_CONFIG (local Gemma /
                                    cloud Haiku), one judge call per dimension
  4. assemble + append an atlas row to OUTPUT_PATH (JSONL)

Resumable: prompt_ids already present in OUTPUT_PATH are skipped on re-run.
Prompts are processed in parallel batches of BATCH_SIZE; the 6 dimension
scores within a single prompt run sequentially (keeps concurrent load on the
Ollama box bounded to BATCH_SIZE).

Run AFTER kappa_phase_0.py has passed (or after Ken has manually amended
JUDGE_CONFIG for any kappa-failed dimension). See README.md.

Cost target: under $50 for n=500. Wall-time target: under 12 hours.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from config import (
    BATCH_SIZE,
    ENDPOINT_PROMPT_SCORE,
    JUDGE_CONFIG,
    MAX_OUTPUT_TOTAL,
    MAX_RETRIES,
    N_TARGET,
    OUTPUT_ABS,
    OUTPUT_DIMENSIONS,
    PIPELINE_VERSION,
    REQUEST_TIMEOUT_SEC,
    RETRY_BASE_SEC,
    ROOT,
    SOURCE_CORPUS_ABS,
    internal_bearer,
    internal_flag_header,
    output_grade_from_total,
)
from cloud_judge import generate_output, score_dimension_haiku
from local_judge import score_dimension_local


# -----------------------------------------------------------------------------
# Env loading — load a repo-root .env.atlas if present, then leave the rest of
# the environment as-is. Required keys: ANTHROPIC_API_KEY, PQS_INTERNAL_BEARER.
# -----------------------------------------------------------------------------
def load_env() -> None:
    env_path = ROOT / ".env.atlas"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and not os.environ.get(k):
                os.environ[k] = v

    missing = [k for k in ("ANTHROPIC_API_KEY", "PQS_INTERNAL_BEARER")
               if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            f"missing required env keys: {missing} — set them in {env_path} "
            f"or the environment. See pipelines/pipeline-6/README.md."
        )


# -----------------------------------------------------------------------------
# Corpus reading. Shared with kappa_phase_0.py.
# -----------------------------------------------------------------------------
def normalize_row(raw: dict, line_no: int) -> dict:
    """Normalize a source-corpus row to {prompt_id, prompt_text, vertical}.

    Source rows (Pipeline 4) carry `prompt` plus provenance fields. A stable
    prompt_id is taken from `prompt_id` if present, else `source_row_id`, else
    a synthetic line-based id.
    """
    prompt_text = raw.get("prompt") or raw.get("prompt_text") or ""
    prompt_id = (
        raw.get("prompt_id")
        or raw.get("source_row_id")
        or f"corpus-line-{line_no}"
    )
    vertical = raw.get("vertical") or raw.get("vertical_source_label") or "software"
    return {
        "prompt_id": str(prompt_id),
        "prompt_text": prompt_text,
        "vertical": str(vertical),
    }


def read_corpus(path: Path = SOURCE_CORPUS_ABS, limit: int | None = None) -> list[dict]:
    """Read + normalize the source corpus. Skips rows with an empty prompt."""
    if not path.exists():
        raise SystemExit(
            f"source corpus not found: {path}\n"
            f"Set PQS_SOURCE_CORPUS to the software-vertical corpus path. "
            f"See pipelines/pipeline-6/README.md."
        )
    rows: list[dict] = []
    for i, line in enumerate(path.read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        row = normalize_row(json.loads(line), i)
        if not row["prompt_text"].strip():
            print(f"  skip line {i}: empty prompt", file=sys.stderr)
            continue
        rows.append(row)
        if limit is not None and len(rows) >= limit:
            break
    return rows


# -----------------------------------------------------------------------------
# Pre-flight prompt scoring — POST /api/score.
# -----------------------------------------------------------------------------
def score_prompt(prompt: str, vertical: str) -> dict:
    """POST the prompt to ENDPOINT_PROMPT_SCORE; return {total, out_of, grade,
    dimensions}. Tolerates both flat {score:{...}} and nested
    {original:{score:{...}}} response shapes. Retries 5xx / transient errors.
    """
    body = json.dumps({"prompt": prompt, "vertical": vertical}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {internal_bearer()}",
        **internal_flag_header(),
    }

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                ENDPOINT_PROMPT_SCORE, data=body, method="POST", headers=headers,
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            score = (
                payload.get("score")
                or payload.get("original", {}).get("score")
                or payload.get("data", {}).get("score")
            )
            if not score:
                raise ValueError(f"prompt-score response missing score: "
                                 f"keys={list(payload)}")
            return {
                "total": score.get("total"),
                "out_of": score.get("out_of", 80),
                "grade": score.get("grade"),
                "dimensions": score.get("dimensions", {}),
            }
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise
            last_exc = e
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            last_exc = e

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BASE_SEC * (2 ** (attempt - 1)))

    raise RuntimeError("prompt scoring failed") from last_exc


# -----------------------------------------------------------------------------
# Post-flight output scoring — one judge call per dimension, routed per
# JUDGE_CONFIG.
# -----------------------------------------------------------------------------
def route_dimension(dimension: str, prompt: str, output: str) -> dict:
    """Score one output dimension with the judge assigned in JUDGE_CONFIG.

    Returns {score, reasoning, judge_model}.
    """
    judge = JUDGE_CONFIG[dimension]
    if judge == "local":
        res = score_dimension_local(prompt, output, dimension)
    elif judge == "haiku":
        res = score_dimension_haiku(prompt, output, dimension)
    else:
        raise ValueError(f"JUDGE_CONFIG[{dimension!r}] = {judge!r} "
                         f"(expected 'local' or 'haiku')")
    return {
        "score": res["score"],
        "reasoning": res["reasoning"],
        "judge_model": res["model"],
    }


def score_output(prompt: str, output: str) -> dict:
    """Score the output across all 6 dimensions. Returns the output_score block:
    {total, out_of, grade, dimensions: {<dim>: {score, reasoning, judge_model}}}.
    """
    dimensions: dict[str, dict] = {}
    total = 0
    for dim in OUTPUT_DIMENSIONS:
        scored = route_dimension(dim, prompt, output)
        dimensions[dim] = scored
        total += scored["score"]
    return {
        "total": total,
        "out_of": MAX_OUTPUT_TOTAL,
        "grade": output_grade_from_total(total),
        "dimensions": dimensions,
    }


# -----------------------------------------------------------------------------
# Atlas row.
# -----------------------------------------------------------------------------
def process_prompt(row: dict) -> dict:
    """Run the full pipeline for one corpus row -> one atlas row."""
    prompt = row["prompt_text"]
    vertical = row["vertical"]

    prompt_score = score_prompt(prompt, vertical)

    gen = generate_output(prompt)
    if gen.get("stop_reason") == "refusal" or not gen["text"]:
        # Generator refused or produced nothing — record the row with an empty
        # output and a null output_score so the run stays resumable and the
        # skipped prompt is auditable rather than silently dropped.
        return {
            "prompt_id": row["prompt_id"],
            "prompt_text": prompt,
            "vertical": vertical,
            "generator_model": gen["model"],
            "output_text": "",
            "prompt_score": prompt_score,
            "output_score": None,
            "skipped_reason": "generator_refusal_or_empty",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline_version": PIPELINE_VERSION,
        }

    output_text = gen["text"]
    output_score = score_output(prompt, output_text)

    return {
        "prompt_id": row["prompt_id"],
        "prompt_text": prompt,
        "vertical": vertical,
        "generator_model": gen["model"],
        "output_text": output_text,
        "prompt_score": prompt_score,
        "output_score": output_score,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
    }


# -----------------------------------------------------------------------------
# Resume support.
# -----------------------------------------------------------------------------
def load_done_ids(path: Path) -> set[str]:
    """prompt_ids already written to the atlas file."""
    if not path.exists():
        return set()
    done: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            done.add(json.loads(line)["prompt_id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return done


# -----------------------------------------------------------------------------
# Runner.
# -----------------------------------------------------------------------------
def main() -> None:
    load_env()

    print(f"judge config: {JUDGE_CONFIG}", file=sys.stderr)
    corpus = read_corpus(limit=N_TARGET)
    done = load_done_ids(OUTPUT_ABS)
    pending = [r for r in corpus if r["prompt_id"] not in done]

    print(
        f"corpus: {len(corpus)} prompts | already done: {len(done)} | "
        f"pending: {len(pending)}",
        file=sys.stderr,
    )
    if not pending:
        print("nothing to do — atlas already complete.", file=sys.stderr)
        return

    OUTPUT_ABS.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    written = 0
    failures: list[tuple[str, str]] = []

    with OUTPUT_ABS.open("a", encoding="utf-8") as out:
        for batch_start in range(0, len(pending), BATCH_SIZE):
            batch = pending[batch_start:batch_start + BATCH_SIZE]
            results: dict[str, dict] = {}
            with ThreadPoolExecutor(max_workers=BATCH_SIZE) as pool:
                futures = {pool.submit(process_prompt, r): r for r in batch}
                for fut, row in futures.items():
                    try:
                        results[row["prompt_id"]] = fut.result()
                    except Exception as e:  # noqa: BLE001
                        failures.append((row["prompt_id"], repr(e)))
                        print(f"  FAILED {row['prompt_id']}: {e}", file=sys.stderr)

            # Write the batch in corpus order for a deterministic file layout.
            for row in batch:
                atlas_row = results.get(row["prompt_id"])
                if atlas_row is None:
                    continue
                out.write(json.dumps(atlas_row, ensure_ascii=False) + "\n")
                written += 1
            out.flush()

            elapsed = time.perf_counter() - t0
            print(
                f"  progress: {written}/{len(pending)} written | "
                f"{len(failures)} failed | {elapsed/60:.1f} min elapsed",
                file=sys.stderr,
            )

    print(
        f"done: {written} atlas rows appended to {OUTPUT_ABS.relative_to(ROOT)} "
        f"in {(time.perf_counter()-t0)/60:.1f} min",
        file=sys.stderr,
    )
    if failures:
        print(f"FAILURES ({len(failures)}) — re-run to retry (resumable):",
              file=sys.stderr)
        for pid, err in failures:
            print(f"  - {pid}: {err[:200]}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
