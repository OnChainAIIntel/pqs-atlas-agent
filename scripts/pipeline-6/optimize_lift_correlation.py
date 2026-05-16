"""
Pipeline 6 — optimize-lift analysis.

Reads the optimize-lift atlas (outputs/atlas-optimize-lift.jsonl) produced by
optimize_lift.py and writes findings/output-optimize-lift.md:

  1. Mean output_lift_points across all rows.
  2. Stdev (sample) of output_lift_points.
  3. Share of prompts where output_lift > 0 — i.e. optimization actually helped.
  4. Mean original output total vs mean optimized output total.
  5. The original_output_grade -> optimized_output_grade transition matrix.
  6. Mean prompt_lift_points — anchors the score-side lift.
  7. Correlation (Pearson r + Spearman rho) between prompt_lift_points and
     output_lift_points: does a bigger prompt lift produce a bigger output lift?
  8. A single quotable headline sentence.

Run AFTER optimize_lift.py completes. Rows missing either lift value are
excluded from the statistics that need it rather than imputed.

Dependency: scipy (pearsonr, spearmanr). See requirements.txt.
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone

from config import ROOT

ATLAS_ABS = (ROOT / "scripts/pipeline-6/outputs/atlas-optimize-lift.jsonl").resolve()
REPORT_PATH = ROOT / "findings" / "output-optimize-lift.md"

GRADES = ("A", "B", "C", "D", "F")

# PQS 8-dimension pre-flight prompt grade cutoffs on the [8,80] scale
# (config.py: A>=70, B>=60, C>=50, D>=35). Used only to label the mean
# original prompt total with a grade in the headline.
PROMPT_GRADE_CUTOFFS = (("A", 70), ("B", 60), ("C", 50), ("D", 35))


def _prompt_grade_from_total(total: float) -> str:
    for grade, cutoff in PROMPT_GRADE_CUTOFFS:
        if total >= cutoff:
            return grade
    return "F"


def _load_rows() -> list[dict]:
    if not ATLAS_ABS.exists():
        raise SystemExit(
            f"optimize-lift atlas not found: {ATLAS_ABS}\n"
            f"Run optimize_lift.py first."
        )
    rows: list[dict] = []
    for line in ATLAS_ABS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _num(x):
    return x if isinstance(x, (int, float)) else None


def _grade_transition_matrix(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Rows = original output grade, columns = optimized output grade."""
    matrix = {og: {pg: 0 for pg in GRADES} for og in GRADES}
    for r in rows:
        orig_g = (r.get("original_output_score") or {}).get("grade")
        opt_g = (r.get("optimized_output_score") or {}).get("grade")
        if orig_g in matrix and opt_g in matrix[orig_g]:
            matrix[orig_g][opt_g] += 1
    return matrix


def main() -> None:
    from scipy.stats import pearsonr, spearmanr

    rows = _load_rows()
    n_total = len(rows)

    # Output-lift statistics — rows with a numeric output_lift_points.
    output_lifts = [
        _num(r.get("output_lift_points")) for r in rows
    ]
    output_lifts = [x for x in output_lifts if x is not None]
    n = len(output_lifts)
    if n < 3:
        raise SystemExit(
            f"only {n} rows with a numeric output_lift_points — "
            f"need at least 3 for analysis."
        )

    mean_output_lift = round(statistics.mean(output_lifts), 2)
    stdev_output_lift = round(statistics.stdev(output_lifts), 2) if n > 1 else 0.0
    n_positive = sum(1 for x in output_lifts if x > 0)
    pct_positive = round(100.0 * n_positive / n, 1)
    n_zero = sum(1 for x in output_lifts if x == 0)
    n_negative = sum(1 for x in output_lifts if x < 0)

    # Original vs optimized output totals.
    orig_output_totals = [
        _num((r.get("original_output_score") or {}).get("total")) for r in rows
    ]
    orig_output_totals = [x for x in orig_output_totals if x is not None]
    opt_output_totals = [
        _num((r.get("optimized_output_score") or {}).get("total")) for r in rows
    ]
    opt_output_totals = [x for x in opt_output_totals if x is not None]
    mean_orig_output = round(statistics.mean(orig_output_totals), 2)
    mean_opt_output = round(statistics.mean(opt_output_totals), 2)

    # Prompt-lift statistics.
    prompt_lifts = [_num(r.get("prompt_lift_points")) for r in rows]
    prompt_lifts = [x for x in prompt_lifts if x is not None]
    mean_prompt_lift = round(statistics.mean(prompt_lifts), 2)

    # Original prompt total -> mean -> grade label.
    orig_prompt_totals = [
        _num((r.get("original_prompt_score") or {}).get("total")) for r in rows
    ]
    orig_prompt_totals = [x for x in orig_prompt_totals if x is not None]
    mean_orig_prompt_total = round(statistics.mean(orig_prompt_totals), 2)
    mean_orig_prompt_grade = _prompt_grade_from_total(mean_orig_prompt_total)

    # Correlation: does a bigger prompt lift produce a bigger output lift?
    # Use only rows where BOTH lifts are numeric.
    paired = [
        (_num(r.get("prompt_lift_points")), _num(r.get("output_lift_points")))
        for r in rows
    ]
    paired = [(p, o) for p, o in paired if p is not None and o is not None]
    n_paired = len(paired)
    if n_paired >= 3 and len({p for p, _ in paired}) > 1 \
            and len({o for _, o in paired}) > 1:
        pl = [p for p, _ in paired]
        ol = [o for _, o in paired]
        pearson_r, pearson_p = pearsonr(pl, ol)
        spearman_rho, spearman_p = spearmanr(pl, ol)
        pearson_r = round(pearson_r, 4)
        spearman_rho = round(spearman_rho, 4)
        corr_available = True
    else:
        pearson_r = spearman_rho = pearson_p = spearman_p = None
        corr_available = False

    matrix = _grade_transition_matrix(rows)

    # Headline.
    headline = (
        f"Across n={n} prompts (mean original prompt grade "
        f"{mean_orig_prompt_grade}, mean original output total "
        f"{mean_orig_output}/60), PQS optimization lifts output quality by "
        f"{mean_output_lift:+.1f} points on average ({pct_positive}% of "
        f"prompts showed positive lift)."
    )

    # ----- report -----
    lines: list[str] = []
    lines.append("# Pipeline 6 — PQS Optimize Lift")
    lines.append("")
    lines.append(f"> **{headline}**")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_")
    lines.append("")
    lines.append(
        "This atlas tests the PQS product thesis directly: does running a "
        "prompt through the PQS optimize transform produce a better OUTPUT? "
        "The original output and its score are reused from the first atlas "
        "(`atlas-software.jsonl`); the optimized prompt and output come from "
        "`/api/score/full`, and the optimized output is scored by "
        "`/api/score-output`."
    )
    lines.append("")

    lines.append("## Output lift")
    lines.append("")
    lines.append("| Statistic | Value |")
    lines.append("|---|---|")
    lines.append(f"| Rows analyzed (n) | {n} |")
    lines.append(f"| Mean output lift (/60) | {mean_output_lift:+.2f} |")
    lines.append(f"| Stdev of output lift | {stdev_output_lift} |")
    lines.append(f"| Prompts with positive lift | {n_positive} ({pct_positive}%) |")
    lines.append(f"| Prompts with zero lift | {n_zero} |")
    lines.append(f"| Prompts with negative lift | {n_negative} |")
    lines.append(f"| Mean original output total (/60) | {mean_orig_output} |")
    lines.append(f"| Mean optimized output total (/60) | {mean_opt_output} |")
    lines.append("")

    lines.append("## Prompt lift (score-side anchor)")
    lines.append("")
    lines.append("| Statistic | Value |")
    lines.append("|---|---|")
    lines.append(f"| Mean original prompt total (/80) | {mean_orig_prompt_total} "
                 f"(grade {mean_orig_prompt_grade}) |")
    lines.append(f"| Mean prompt lift (/80) | {mean_prompt_lift:+.2f} |")
    lines.append("")
    lines.append(
        "Prompt lift is the PQS 8-dimension pre-flight score gain from the "
        "optimize rewrite. It is reported to anchor the output-side result: a "
        "large prompt lift with a small output lift is itself the finding."
    )
    lines.append("")

    lines.append("## Does a bigger prompt lift produce a bigger output lift?")
    lines.append("")
    if corr_available:
        lines.append("| Statistic | Value | p-value |")
        lines.append("|---|---|---|")
        lines.append(f"| Pearson r | {pearson_r} | {pearson_p:.2e} |")
        lines.append(f"| Spearman ρ | {spearman_rho} | {spearman_p:.2e} |")
        lines.append("")
        lines.append(
            f"Correlation between `prompt_lift_points` and "
            f"`output_lift_points` over n={n_paired} rows with both lifts "
            f"present."
        )
    else:
        lines.append(
            "_Correlation unavailable — fewer than 3 paired rows, or one of "
            "the lift series has zero variance._"
        )
    lines.append("")

    lines.append("## Output grade transition matrix")
    lines.append("")
    lines.append(
        "Rows = original output grade, columns = optimized output grade, "
        "cells = count. The diagonal is no grade change; above the diagonal "
        "(toward A) is an improvement."
    )
    lines.append("")
    lines.append("| original \\ optimized | " + " | ".join(GRADES) + " | row total |")
    lines.append("|---|" + "---|" * (len(GRADES) + 1))
    for og in GRADES:
        row_total = sum(matrix[og].values())
        cells = " | ".join(str(matrix[og][pg]) for pg in GRADES)
        lines.append(f"| **{og}** | {cells} | {row_total} |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("### Methodology footnote")
    lines.append("")
    lines.append(
        "`output_lift_points = optimized_output_score.total - "
        "original_output_score.total`, both on the 6-dimension /60 post-flight "
        "scale. The original output score is carried over from the first "
        "atlas, where it was scored by the Pipeline 6 hybrid judge; the "
        "optimized output score comes from the `/api/score-output` endpoint. "
        "Both apply the canonical output rubric from `lib/pqs-output-rubric.js`, "
        "but the judge path differs — read the lift as directional evidence of "
        "the optimize transform's effect, not a within-judge controlled delta. "
        "Stdev is the sample standard deviation. Rows missing a lift value are "
        "excluded from the statistic that needs it rather than imputed."
    )
    lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines))

    print(headline, file=sys.stderr)
    print(f"report written: {REPORT_PATH.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
