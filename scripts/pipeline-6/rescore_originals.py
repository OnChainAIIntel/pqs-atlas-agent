"""
Pipeline 6 — rescore originals: judge-controlled fix for the optimize-lift atlas.

THE BUG
-------
The optimize-lift atlas (outputs/atlas-optimize-lift.jsonl) compares an
ORIGINAL output score against an OPTIMIZED output score — but the two sides
were graded by different judges:

  - The ORIGINAL output score was carried over from the first atlas
    (atlas-general.jsonl), where outputs were scored by Pipeline 6's hybrid
    judge path (cloud_judge calling Haiku 4.5 per-dimension via the Anthropic
    SDK directly).
  - The OPTIMIZED output score came from /api/score-output, which runs
    Sonnet 4.6 internally.

Different graders on the two sides makes `output_lift_points` an uncontrolled
comparison. The original atlas headline (-4.2 points mean lift) is almost
certainly a judge-bias artifact, not a measurement of the optimize transform.

THE FIX
-------
Re-score every ORIGINAL output through the SAME /api/score-output endpoint
that scored the optimized outputs, so both sides are judged by Sonnet 4.6.
Then recompute output_lift_points from the two same-judge totals.

For every row in outputs/atlas-optimize-lift.jsonl:
  1. POST {prompt, output, vertical} to /api/score-output (Authorization:
     Bearer <PQS_API_KEY>) with the row's original_output_text.
  2. Replace original_output_score with the new /api/score-output result.
  3. Recompute output_lift_points = optimized_output_score.total -
     new_original_output_score.total.
  4. Tag the row judge_path = "controlled" and append it to
     outputs/atlas-optimize-lift-judge-controlled.jsonl.

This file does NOT modify atlas-optimize-lift.jsonl — that file stays as the
contaminated baseline for reference. The judge-controlled file is the corrected
artifact; optimize_lift_correlation_controlled.py reads it for the clean report.

Resumable: prompt_ids already in the judge-controlled file are skipped on
re-run. Prompts are processed in parallel batches of BATCH_SIZE. A row whose
re-score fails after MAX_RETRIES is counted as a failure and never written —
re-run to retry. A row with no original_output_text cannot be re-scored; it is
skipped, logged, and counted.

Reuses load_env() from score_outputs.py, score_output_full() from cloud_judge.py
(the /api/score-output client, with Bearer auth + 3-retry exponential backoff),
and _normalize_output_score() from optimize_lift.py so the re-scored original
side is shaped byte-identically to the optimized side it is compared against.

Cost: 1 /api/score-output call per row. /api/score-output is free under the
internal Bearer key (no x402 path). Budget: API time only.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from config import BATCH_SIZE, ROOT
from cloud_judge import score_output_full
from optimize_lift import _normalize_output_score, _lift
from score_outputs import load_env

# -----------------------------------------------------------------------------
# Paths + version.
# -----------------------------------------------------------------------------
PIPELINE_VERSION = "pipeline-6-rescore-originals-v1.0"

# The contaminated baseline (input — read only) and the judge-controlled output.
OPTIMIZE_LIFT_ABS = (
    ROOT / "scripts/pipeline-6/outputs/atlas-optimize-lift.jsonl"
).resolve()
OUTPUT_ABS = (
    ROOT / "scripts/pipeline-6/outputs/atlas-optimize-lift-judge-controlled.jsonl"
).resolve()


# -----------------------------------------------------------------------------
# Row processing.
# -----------------------------------------------------------------------------
def rescore_row(row: dict) -> dict:
    """Re-score one optimize-lift row's ORIGINAL output via /api/score-output.

    Returns a NEW row dict that keeps every original field, but:
      - REPLACES original_output_score with the /api/score-output result
        (Sonnet 4.6 judge — the same grader that scored the optimized side),
      - RECOMPUTES output_lift_points from the two same-judge totals,
      - ADDS judge_path = "controlled" to mark the row as reprocessed.

    Raises ValueError if the row carries no original_output_text to score.
    """
    original_output_text = (row.get("original_output_text") or "").strip()
    if not original_output_text:
        raise ValueError("row has no original_output_text to re-score")

    # /api/score-output — same endpoint, same Bearer auth, same 3-retry
    # exponential backoff that scored the optimized outputs. Body is
    # {prompt, output, vertical}; score_output_full() assembles it.
    new_original_output_score = _normalize_output_score(
        score_output_full(
            row["prompt_text"], original_output_text, row["vertical"]
        )
    )

    optimized_total = (row.get("optimized_output_score") or {}).get("total")
    new_original_total = new_original_output_score.get("total")

    new_row = dict(row)  # keep every original field
    new_row["original_output_score"] = new_original_output_score
    new_row["output_lift_points"] = _lift(optimized_total, new_original_total)
    new_row["judge_path"] = "controlled"
    new_row["rescored_timestamp"] = datetime.now(timezone.utc).isoformat()
    new_row["pipeline_version"] = PIPELINE_VERSION
    return new_row


# -----------------------------------------------------------------------------
# Reading + resume support.
# -----------------------------------------------------------------------------
def load_rows(path: Path) -> list[dict]:
    """Read a JSONL atlas file into a list of row dicts (file order preserved)."""
    if not path.exists():
        raise SystemExit(
            f"optimize-lift atlas not found: {path}\n"
            f"Run optimize_lift.py first. See scripts/pipeline-6/README.md."
        )
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def load_done_ids(path: Path) -> set[str]:
    """prompt_ids already written to the judge-controlled file."""
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

    all_rows = load_rows(OPTIMIZE_LIFT_ABS)
    done = load_done_ids(OUTPUT_ABS)

    # Rows with no original_output_text cannot be re-scored — skip up front
    # and count separately, the same way optimize_lift.py handles un-scorable
    # first-atlas rows.
    pending: list[dict] = []
    skipped_no_original: list[str] = []
    for row in all_rows:
        pid = row["prompt_id"]
        if pid in done:
            continue
        if not (row.get("original_output_text") or "").strip():
            skipped_no_original.append(pid)
            continue
        pending.append(row)

    print(
        f"optimize-lift atlas: {len(all_rows)} rows | already done: "
        f"{len(done)} | skipped (no original output text): "
        f"{len(skipped_no_original)} | pending: {len(pending)}",
        file=sys.stderr,
    )
    if skipped_no_original:
        print(
            f"  skipped ids: {', '.join(skipped_no_original[:20])}"
            + (" ..." if len(skipped_no_original) > 20 else ""),
            file=sys.stderr,
        )
    if not pending:
        print("nothing to do — judge-controlled atlas already complete.",
              file=sys.stderr)
        return

    OUTPUT_ABS.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    written = 0
    recomputed_lifts: list[float] = []
    failures: list[tuple[str, str]] = []

    with OUTPUT_ABS.open("a", encoding="utf-8") as out:
        for batch_start in range(0, len(pending), BATCH_SIZE):
            batch = pending[batch_start:batch_start + BATCH_SIZE]
            results: dict[str, dict] = {}
            with ThreadPoolExecutor(max_workers=BATCH_SIZE) as pool:
                futures = {pool.submit(rescore_row, r): r for r in batch}
                for fut, row in futures.items():
                    try:
                        results[row["prompt_id"]] = fut.result()
                    except Exception as e:  # noqa: BLE001
                        failures.append((row["prompt_id"], repr(e)))
                        print(f"  FAILED {row['prompt_id']}: {e}",
                              file=sys.stderr)

            # Write the batch in input-file order for a deterministic layout.
            for row in batch:
                new_row = results.get(row["prompt_id"])
                if new_row is None:
                    continue
                out.write(json.dumps(new_row, ensure_ascii=False) + "\n")
                written += 1
                lift = new_row.get("output_lift_points")
                if isinstance(lift, (int, float)):
                    recomputed_lifts.append(lift)
            out.flush()

            elapsed = time.perf_counter() - t0
            print(
                f"  progress: {written}/{len(pending)} re-scored | "
                f"{len(failures)} failed | {elapsed/60:.1f} min elapsed",
                file=sys.stderr,
            )

    wall_min = (time.perf_counter() - t0) / 60
    mean_lift = (
        sum(recomputed_lifts) / len(recomputed_lifts)
        if recomputed_lifts else None
    )
    print(
        f"\ndone: {written} judge-controlled rows appended to "
        f"{OUTPUT_ABS.relative_to(ROOT)}",
        file=sys.stderr,
    )
    print(f"  rows processed:        {written}", file=sys.stderr)
    print(f"  rows failed:           {len(failures)}", file=sys.stderr)
    print(
        f"  mean recomputed lift:  "
        f"{mean_lift:+.2f} (/60)" if mean_lift is not None else
        "  mean recomputed lift:  n/a (no numeric lifts)",
        file=sys.stderr,
    )
    print(f"  wall time:             {wall_min:.1f} min", file=sys.stderr)

    if failures:
        print(
            f"\nFAILURES ({len(failures)}) — re-run to retry (resumable):",
            file=sys.stderr,
        )
        for pid, err in failures:
            print(f"  - {pid}: {err[:200]}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
