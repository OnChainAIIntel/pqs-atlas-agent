"""
Mid-bucket grade verification diagnostic.

Stratified spot-check: 10 mid rows (balanced across mid sources) + 10 messy
rows (balanced across messy sources) through /api/score/full. Answers
whether Pipeline 4's corpus has natural A-grade anchors for Pipeline 5
kappa calibration, and whether a messy→mid score gradient actually exists.

Decision tree (reported at end of run):
  - mid A-grade ≥ 3/10  → Path 1: calibration draws from mid directly
  - mid A-grade 1-2/10  → Borderline: supplement top end
  - mid A-grade 0/10    → Check messy→mid gradient:
      - gradient < 10   → Path 2 MANDATORY (PQS-grade-flat corpus)
      - gradient ≥ 10   → Path 2 extends top end (gradient exists)

Persisted evidence written to data/pilots/mid-grade-verification.jsonl
with full per-dim scores for each sampled row.
"""
from __future__ import annotations
import json
import random
import statistics
import sys
import time
from pathlib import Path

# Reuse PQS helpers and env loading from extract.py
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "pipeline-4"))
from extract import _pqs_score_full, _grade_label, _load_env_from_siblings

CORPUS = ROOT / "data" / "source-prompts-full-deterministic.jsonl"
OUT = ROOT / "data" / "pilots" / "mid-grade-verification.jsonl"

# Stratified sample plan — 10 mid + 10 messy, balanced across sources
# so per-source signals are visible. Clean file lacks no_robots; we use
# the full file which has all mid sources represented.
SAMPLE_PLAN = {
    # mid: 10 rows balanced across 4 mid sources
    ("mid", "allenai/WildChat-1M"):     3,
    ("mid", "OpenAssistant/oasst2"):    3,
    ("mid", "HuggingFaceH4/no_robots"): 2,
    ("mid", "Open-Orca/OpenOrca"):      2,
    # messy: 10 rows balanced across 3 messy sources (LMSYS, WildChat, oasst2)
    ("messy", "lmsys/lmsys-chat-1m"):    4,
    ("messy", "allenai/WildChat-1M"):    3,
    ("messy", "OpenAssistant/oasst2"):   3,
}

SEED = 4242  # Separate from extraction seed (42) to avoid accidental overlap


def _partition(rows):
    by_key = {}
    for r in rows:
        k = (r["quality_bucket"], r["source_dataset"])
        by_key.setdefault(k, []).append(r)
    return by_key


def main():
    _load_env_from_siblings()
    import os
    if not os.environ.get("PQS_API_KEY"):
        print("ERROR: PQS_API_KEY not set")
        sys.exit(2)

    rows = [json.loads(l) for l in CORPUS.open()]
    print(f"[verify] loaded {len(rows)} rows from {CORPUS.name}")
    by_key = _partition(rows)

    # Draw stratified sample
    rng = random.Random(SEED)
    selected = []
    for (bucket, src), n in SAMPLE_PLAN.items():
        pool = by_key.get((bucket, src), [])
        if len(pool) < n:
            print(f"[verify] WARN: only {len(pool)} rows in pool for {bucket}/{src}, wanted {n}")
            n = len(pool)
        selected.extend(rng.sample(pool, n))
    print(f"[verify] sampled {len(selected)} rows; starting API calls...\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    results = []
    t0 = time.time()
    with OUT.open("w") as f:
        for i, row in enumerate(selected, 1):
            try:
                resp = _pqs_score_full(row["prompt"], vertical="general")
                score_obj = (resp.get("original") or {}).get("score") or {}
                total = score_obj.get("total")
                if not isinstance(total, int):
                    raise RuntimeError(f"non-int total: {total!r}")
                dims = score_obj.get("dimensions", {})
                grade = _grade_label(total)
                result = {
                    "source_row_id": row["source_row_id"],
                    "source_dataset": row["source_dataset"],
                    "bucket": row["quality_bucket"],
                    "word_count": row.get("word_count"),
                    "pre_score_total": total,
                    "pre_grade": grade,
                    "dimensions": dims,
                    "prompt_preview": row["prompt"][:200],
                    "timestamp": time.time(),
                }
                results.append(result)
                f.write(json.dumps(result) + "\n")
                f.flush()
                print(f"  [{i:2d}/{len(selected)}] {row['quality_bucket']:5s} {row['source_dataset']:26s} "
                      f"total={total:2d} grade={grade}")
            except Exception as e:
                print(f"  [{i:2d}/{len(selected)}] {row['source_dataset']} ERROR: {e}")

    print(f"\n[verify] API calls done in {time.time()-t0:.1f}s; wrote {len(results)} rows to {OUT}")

    # --- Per-source per-bucket report ---
    print("\n=== Per-source per-bucket distribution ===")
    by_rk = {}
    for r in results:
        k = (r["bucket"], r["source_dataset"])
        by_rk.setdefault(k, []).append(r)
    for (bucket, src), rs in sorted(by_rk.items()):
        totals = [r["pre_score_total"] for r in rs]
        grades = [r["pre_grade"] for r in rs]
        avg = statistics.mean(totals)
        print(f"  {bucket:5s} {src:30s} n={len(rs):2d}  avg={avg:5.1f}  "
              f"min={min(totals):2d} max={max(totals):2d}  grades={grades}")

    # --- Aggregated bucket stats ---
    print("\n=== Aggregated bucket stats ===")
    for bucket in ["messy", "mid"]:
        bucket_rows = [r for r in results if r["bucket"] == bucket]
        if not bucket_rows:
            continue
        totals = [r["pre_score_total"] for r in bucket_rows]
        grades = [r["pre_grade"] for r in bucket_rows]
        counts = {g: grades.count(g) for g in ("A", "B", "C", "D", "F")}
        print(f"  {bucket:5s} n={len(bucket_rows):2d}  avg={statistics.mean(totals):5.1f} "
              f"median={statistics.median(totals):5.1f}  min={min(totals):2d} max={max(totals):2d}  "
              f"A={counts['A']} B={counts['B']} C={counts['C']} D={counts['D']} F={counts['F']}")

    # --- Decision ---
    mid_rows = [r for r in results if r["bucket"] == "mid"]
    messy_rows = [r for r in results if r["bucket"] == "messy"]
    mid_a = sum(1 for r in mid_rows if r["pre_grade"] == "A")
    mid_ab = sum(1 for r in mid_rows if r["pre_grade"] in ("A", "B"))

    print("\n=== Decision signal ===")
    print(f"Mid A-grade count: {mid_a}/{len(mid_rows)}")
    print(f"Mid A+B-grade count: {mid_ab}/{len(mid_rows)}")

    if mid_rows and messy_rows:
        messy_avg = statistics.mean([r["pre_score_total"] for r in messy_rows])
        mid_avg = statistics.mean([r["pre_score_total"] for r in mid_rows])
        gradient = mid_avg - messy_avg
        print(f"Messy avg: {messy_avg:.1f}  Mid avg: {mid_avg:.1f}  Gradient: {gradient:+.1f}")
    else:
        gradient = 0

    if mid_a >= 3:
        verdict = "PATH 1 — calibration can draw from mid directly"
    elif mid_a >= 1:
        verdict = "BORDERLINE — supplement top end"
    else:
        if gradient < 10:
            verdict = "PATH 2 MANDATORY — corpus is PQS-grade-flat"
        else:
            verdict = "PATH 2 EXTENDS TOP END — gradient exists but top is truncated"
    print(f"\n=== VERDICT: {verdict} ===")


if __name__ == "__main__":
    main()
