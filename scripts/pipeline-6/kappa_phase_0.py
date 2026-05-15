"""
Pipeline 6 — Phase 0: kappa calibration.

Validates that the local Gemma 4 9B judge agrees with the Haiku 4.5 judge on
the 4 structural dimensions BEFORE committing "local" for the full 500-prompt
atlas run.

Procedure:
  1. Read KAPPA_SAMPLE_SIZE (50) prompts from the source corpus.
  2. Generate an output for each via the Haiku 4.5 generator.
  3. Score each (prompt, output) pair on the 4 structural dimensions with BOTH
     the local judge and the Haiku judge.
  4. Compute Cohen's weighted kappa per dimension (quadratic weights) between
     the two judges.
  5. Write outputs/kappa-phase-0.md with per-dim kappa, a local-vs-haiku
     recommendation, total Haiku cost, and wall time.

Exit code 0 if every structural dimension passes kappa >= KAPPA_PASS_THRESHOLD.
Exit code 1 if any fail — the report names the failing dimensions. Ken then
amends JUDGE_CONFIG in config.py (set the failed dim to "haiku") before
running score_outputs.py.

Resumable: per-prompt judge results are cached to outputs/kappa-phase-0-raw.jsonl;
a re-run reuses cached prompts and only fills in the rest.

Cost target: under $15. Wall-time target: under 90 minutes.

Dependency: scikit-learn (cohen_kappa_score). See README.md.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from config import (
    BATCH_SIZE,
    HAIKU_MODEL,
    JUDGE_CONFIG,
    KAPPA_PASS_THRESHOLD,
    KAPPA_REPORT_PATH,
    KAPPA_SAMPLE_SIZE,
    OLLAMA_MODEL,
    ROOT,
    STRUCTURAL_DIMENSIONS,
)
from cloud_judge import generate_output, score_dimension_haiku
from local_judge import score_dimension_local
from score_outputs import load_env, read_corpus

RAW_CACHE_PATH = KAPPA_REPORT_PATH.parent / "kappa-phase-0-raw.jsonl"


def _landis_koch(k: float) -> str:
    """Landis-Koch (1977) agreement bands."""
    if k >= 0.81:
        return "almost perfect"
    if k >= 0.61:
        return "substantial"
    if k >= 0.41:
        return "moderate"
    if k >= 0.21:
        return "fair"
    if k >= 0.0:
        return "slight"
    return "poor (below chance)"


def score_one_prompt(row: dict) -> dict:
    """Generate an output and dual-score the 4 structural dimensions.

    Returns a raw-cache record: prompt_id, output, generator stats, and for
    each structural dim a {local, haiku} score pair. Prompts where the
    generator refuses are recorded with status="refused" and excluded from
    kappa (documented in the report).
    """
    prompt = row["prompt_text"]
    gen = generate_output(prompt)

    if gen.get("stop_reason") == "refusal" or not gen["text"]:
        return {
            "prompt_id": row["prompt_id"],
            "status": "refused",
            "gen_cost_usd": gen.get("cost_usd", 0.0),
            "haiku_cost_usd": gen.get("cost_usd", 0.0),
        }

    output = gen["text"]
    dims: dict[str, dict] = {}
    haiku_cost = gen.get("cost_usd", 0.0)
    for dim in STRUCTURAL_DIMENSIONS:
        local_res = score_dimension_local(prompt, output, dim)
        haiku_res = score_dimension_haiku(prompt, output, dim)
        haiku_cost += haiku_res.get("cost_usd", 0.0)
        dims[dim] = {"local": local_res["score"], "haiku": haiku_res["score"]}

    return {
        "prompt_id": row["prompt_id"],
        "status": "ok",
        "output_chars": len(output),
        "dimensions": dims,
        "gen_cost_usd": gen.get("cost_usd", 0.0),
        "haiku_cost_usd": round(haiku_cost, 6),
    }


def _load_raw_cache() -> dict[str, dict]:
    if not RAW_CACHE_PATH.exists():
        return {}
    cache: dict[str, dict] = {}
    for line in RAW_CACHE_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            cache[rec["prompt_id"]] = rec
        except (json.JSONDecodeError, KeyError):
            continue
    return cache


def _compute_kappa(records: list[dict]) -> dict:
    """Per-dimension Cohen's weighted (quadratic) kappa between local + haiku."""
    from sklearn.metrics import cohen_kappa_score

    ok = [r for r in records if r.get("status") == "ok"]
    per_dim: dict[str, dict] = {}
    for dim in STRUCTURAL_DIMENSIONS:
        local = [r["dimensions"][dim]["local"] for r in ok]
        haiku = [r["dimensions"][dim]["haiku"] for r in ok]
        try:
            kappa = cohen_kappa_score(local, haiku, weights="quadratic")
        except Exception as e:  # noqa: BLE001 — degenerate labels etc.
            per_dim[dim] = {"kappa": None, "n": len(ok), "reason": repr(e)}
            continue
        # All-identical scores can yield NaN; surface that explicitly.
        if kappa != kappa:  # NaN
            per_dim[dim] = {
                "kappa": None, "n": len(ok),
                "reason": "undefined (no score variance)",
            }
            continue
        kappa = round(float(kappa), 4)
        passed = kappa >= KAPPA_PASS_THRESHOLD
        per_dim[dim] = {
            "kappa": kappa,
            "n": len(ok),
            "label": _landis_koch(kappa),
            "pass": passed,
            "recommendation": "local" if passed else "haiku",
        }
    return per_dim


def _write_report(per_dim: dict, n_total: int, n_ok: int, n_refused: int,
                  haiku_cost: float, wall_min: float) -> list[str]:
    """Write the kappa-phase-0.md report. Returns the list of failed dims."""
    failed = [
        d for d in STRUCTURAL_DIMENSIONS
        if not per_dim[d].get("pass", False)
    ]
    all_pass = not failed

    lines: list[str] = []
    lines.append("# Pipeline 6 — Phase 0 Kappa Calibration")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_")
    lines.append("")
    lines.append(
        f"**Result: {'PASS' if all_pass else 'FAIL'}** — "
        + ("all 4 structural dimensions clear the kappa floor; the planned "
           "JUDGE_CONFIG holds."
           if all_pass else
           f"{len(failed)} dimension(s) below the kappa floor: "
           f"{', '.join(failed)}.")
    )
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append(
        f"- Sample: {n_total} prompts from the source corpus "
        f"({n_ok} scored, {n_refused} excluded for generator refusal)."
    )
    lines.append(f"- Generator: `{HAIKU_MODEL}`.")
    lines.append(
        f"- Judges: local `{OLLAMA_MODEL}` vs cloud `{HAIKU_MODEL}`, "
        f"one judge call per dimension."
    )
    lines.append(
        "- Statistic: Cohen's weighted kappa, quadratic weights "
        "(`sklearn.metrics.cohen_kappa_score`, `weights='quadratic'`), per "
        "dimension on the 1-10 ordinal scale."
    )
    lines.append(f"- Pass floor: kappa >= {KAPPA_PASS_THRESHOLD}.")
    lines.append("")
    lines.append("## Per-dimension kappa")
    lines.append("")
    lines.append("| Dimension | n | kappa | agreement | pass | recommended judge |")
    lines.append("|---|---|---|---|---|---|")
    for dim in STRUCTURAL_DIMENSIONS:
        d = per_dim[dim]
        if d.get("kappa") is None:
            lines.append(
                f"| `{dim}` | {d['n']} | n/a | {d.get('reason','-')} | "
                f"FAIL | haiku |"
            )
        else:
            lines.append(
                f"| `{dim}` | {d['n']} | {d['kappa']:+.4f} | {d['label']} | "
                f"{'PASS' if d['pass'] else 'FAIL'} | {d['recommendation']} |"
            )
    lines.append("")
    lines.append("## Cost + wall time")
    lines.append("")
    lines.append(f"- Total Haiku cost (generation + cloud judge): "
                 f"**${haiku_cost:.4f}** (target < $15).")
    lines.append(f"- Wall time: **{wall_min:.1f} min** (target < 90 min).")
    lines.append(f"- Local Gemma judge calls: no API cost (Ollama on CRYPTOMINER).")
    lines.append("")
    lines.append("## Action")
    lines.append("")
    if all_pass:
        lines.append(
            "No change needed. Run `score_outputs.py` with the existing "
            "`JUDGE_CONFIG`."
        )
    else:
        lines.append(
            "Before running `score_outputs.py`, edit `JUDGE_CONFIG` in "
            "`config.py` and set the following dimension(s) to `\"haiku\"`:"
        )
        lines.append("")
        for d in failed:
            lines.append(f"- `{d}`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("### Methodology footnote")
    lines.append("")
    lines.append(
        "Weighted kappa with quadratic weights treats near-miss disagreements "
        "(e.g. 7 vs 8) as far less severe than wide disagreements (e.g. 2 vs "
        "9), which is appropriate for an ordinal 1-10 rubric. A floor of 0.70 "
        "sits inside the Landis-Koch \"substantial agreement\" band — the bar "
        "for trusting the cheaper local judge to stand in for Haiku on a "
        "structural dimension across the full atlas."
    )
    lines.append("")

    KAPPA_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    KAPPA_REPORT_PATH.write_text("\n".join(lines))
    return failed


def main() -> None:
    load_env()
    print(f"judge config (planned): {JUDGE_CONFIG}", file=sys.stderr)

    corpus = read_corpus(limit=KAPPA_SAMPLE_SIZE)
    print(f"kappa sample: {len(corpus)} prompts", file=sys.stderr)

    cache = _load_raw_cache()
    pending = [r for r in corpus if r["prompt_id"] not in cache]
    print(f"cached: {len(cache)} | pending: {len(pending)}", file=sys.stderr)

    t0 = time.perf_counter()
    records = list(cache.values())

    if pending:
        RAW_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RAW_CACHE_PATH.open("a", encoding="utf-8") as raw_out:
            for batch_start in range(0, len(pending), BATCH_SIZE):
                batch = pending[batch_start:batch_start + BATCH_SIZE]
                with ThreadPoolExecutor(max_workers=BATCH_SIZE) as pool:
                    futures = {pool.submit(score_one_prompt, r): r for r in batch}
                    for fut, row in futures.items():
                        try:
                            rec = fut.result()
                        except Exception as e:  # noqa: BLE001
                            print(f"  FAILED {row['prompt_id']}: {e}",
                                  file=sys.stderr)
                            continue
                        records.append(rec)
                        raw_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        raw_out.flush()
                done = len([r for r in records])
                print(f"  progress: {done}/{len(corpus)} prompts scored | "
                      f"{(time.perf_counter()-t0)/60:.1f} min",
                      file=sys.stderr)

    wall_min = (time.perf_counter() - t0) / 60
    n_ok = len([r for r in records if r.get("status") == "ok"])
    n_refused = len([r for r in records if r.get("status") == "refused"])
    haiku_cost = sum(r.get("haiku_cost_usd", 0.0) for r in records)

    if n_ok == 0:
        raise SystemExit("no successfully scored prompts — cannot compute kappa.")

    per_dim = _compute_kappa(records)
    failed = _write_report(per_dim, len(records), n_ok, n_refused,
                          haiku_cost, wall_min)

    print(f"\nreport written: {KAPPA_REPORT_PATH.relative_to(ROOT)}",
          file=sys.stderr)
    for dim in STRUCTURAL_DIMENSIONS:
        d = per_dim[dim]
        k = "n/a" if d.get("kappa") is None else f"{d['kappa']:+.4f}"
        print(f"  {dim:24s} kappa={k}  "
              f"{'PASS' if d.get('pass') else 'FAIL'}", file=sys.stderr)
    print(f"  Haiku cost ${haiku_cost:.4f} | wall {wall_min:.1f} min",
          file=sys.stderr)

    if failed:
        print(f"\nFAIL — amend JUDGE_CONFIG for: {', '.join(failed)}",
              file=sys.stderr)
        raise SystemExit(1)
    print("\nPASS — all structural dimensions cleared the kappa floor.",
          file=sys.stderr)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
