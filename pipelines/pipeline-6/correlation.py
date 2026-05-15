"""
Pipeline 6 — post-run analysis: correlation between PQS pre-flight prompt
score and post-flight output score.

Reads the atlas JSONL produced by score_outputs.py and writes
findings/output-correlation.md:
  - Pearson r between prompt_score.total and output_score.total
  - Spearman rho (rank-based, robust to non-linearity)
  - Per-grade mean output score (do A-grade prompts beat F-grade prompts?)
  - Grade transition matrix (prompt grade -> output grade frequency)
  - A headline sentence Ken can quote

Run AFTER score_outputs.py completes. Rows with a null output_score
(generator refusals) are excluded from every statistic.

Dependency: scipy (pearsonr, spearmanr). See README.md.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from config import CORRELATION_REPORT_PATH, OUTPUT_ABS, ROOT

GRADES = ("A", "B", "C", "D", "F")


def _load_rows() -> list[dict]:
    if not OUTPUT_ABS.exists():
        raise SystemExit(
            f"atlas file not found: {OUTPUT_ABS}\n"
            f"Run score_outputs.py first."
        )
    rows: list[dict] = []
    for line in OUTPUT_ABS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _scored_rows(rows: list[dict]) -> list[dict]:
    """Rows with both a prompt_score total and a non-null output_score total."""
    out: list[dict] = []
    for r in rows:
        ps = r.get("prompt_score") or {}
        os_ = r.get("output_score") or {}
        if ps.get("total") is None or os_.get("total") is None:
            continue
        out.append(r)
    return out


def _grade_transition_matrix(rows: list[dict]) -> dict[str, dict[str, int]]:
    matrix = {pg: {og: 0 for og in GRADES} for pg in GRADES}
    for r in rows:
        pg = (r.get("prompt_score") or {}).get("grade")
        og = (r.get("output_score") or {}).get("grade")
        if pg in matrix and og in matrix[pg]:
            matrix[pg][og] += 1
    return matrix


def _per_grade_means(rows: list[dict]) -> dict[str, dict]:
    """Mean output total + count, grouped by prompt grade."""
    buckets: dict[str, list[int]] = {g: [] for g in GRADES}
    for r in rows:
        pg = (r.get("prompt_score") or {}).get("grade")
        ot = (r.get("output_score") or {}).get("total")
        if pg in buckets and ot is not None:
            buckets[pg].append(ot)
    return {
        g: {
            "n": len(vals),
            "mean_output_total": round(sum(vals) / len(vals), 2) if vals else None,
        }
        for g, vals in buckets.items()
    }


def main() -> None:
    from scipy.stats import pearsonr, spearmanr

    rows = _load_rows()
    scored = _scored_rows(rows)
    n_total = len(rows)
    n = len(scored)
    n_excluded = n_total - n

    if n < 3:
        raise SystemExit(
            f"only {n} fully-scored rows — need at least 3 for correlation."
        )

    prompt_totals = [r["prompt_score"]["total"] for r in scored]
    output_totals = [r["output_score"]["total"] for r in scored]

    pearson_r, pearson_p = pearsonr(prompt_totals, output_totals)
    spearman_rho, spearman_p = spearmanr(prompt_totals, output_totals)
    pearson_r, spearman_rho = round(pearson_r, 4), round(spearman_rho, 4)

    per_grade = _per_grade_means(scored)
    matrix = _grade_transition_matrix(scored)

    # Headline. Vertical is uniform across the software corpus; report it.
    vertical = scored[0].get("vertical", "software")
    headline = (
        f"Across n={n} {vertical} prompts, PQS pre-flight score correlates "
        f"with output quality at Pearson r={pearson_r}, Spearman "
        f"ρ={spearman_rho}."
    )

    lines: list[str] = []
    lines.append("# Pipeline 6 — PQS Pre-flight vs Output Quality")
    lines.append("")
    lines.append(f"> **{headline}**")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_")
    lines.append("")
    lines.append("## Correlation")
    lines.append("")
    lines.append("| Statistic | Value | p-value |")
    lines.append("|---|---|---|")
    lines.append(f"| Pearson r | {pearson_r} | {pearson_p:.2e} |")
    lines.append(f"| Spearman ρ | {spearman_rho} | {spearman_p:.2e} |")
    lines.append("")
    lines.append(
        f"- Prompt score: PQS 8-dimension pre-flight total (out of 80)."
    )
    lines.append(
        f"- Output score: PQS 6-dimension post-flight total (out of 60), "
        f"hybrid local/Haiku judge."
    )
    lines.append(
        f"- n = {n} fully-scored rows ({n_excluded} excluded: generator "
        f"refusal or missing score)."
    )
    lines.append("")
    lines.append("## Mean output score by prompt grade")
    lines.append("")
    lines.append("| Prompt grade | n | Mean output total (/60) |")
    lines.append("|---|---|---|")
    for g in GRADES:
        pg = per_grade[g]
        mean = "n/a" if pg["mean_output_total"] is None else pg["mean_output_total"]
        lines.append(f"| {g} | {pg['n']} | {mean} |")
    lines.append("")
    a_mean = per_grade["A"]["mean_output_total"]
    f_mean = per_grade["F"]["mean_output_total"]
    if a_mean is not None and f_mean is not None:
        delta = round(a_mean - f_mean, 2)
        lines.append(
            f"A-grade prompts produce outputs averaging **{delta} points "
            f"higher** (/60) than F-grade prompts "
            f"({a_mean} vs {f_mean})."
        )
    else:
        lines.append(
            "_A-vs-F comparison unavailable — one of the grade buckets is "
            "empty in this run._"
        )
    lines.append("")
    lines.append("## Grade transition matrix")
    lines.append("")
    lines.append("Rows = prompt grade, columns = output grade, cells = count.")
    lines.append("")
    lines.append("| prompt \\ output | " + " | ".join(GRADES) + " | row total |")
    lines.append("|---|" + "---|" * (len(GRADES) + 1))
    for pg in GRADES:
        row_total = sum(matrix[pg].values())
        cells = " | ".join(str(matrix[pg][og]) for og in GRADES)
        lines.append(f"| **{pg}** | {cells} | {row_total} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("### Methodology footnote")
    lines.append("")
    lines.append(
        "Pearson r measures linear association between the two totals; "
        "Spearman ρ measures monotonic (rank) association and is the more "
        "robust of the two when the relationship is non-linear or contains "
        "outliers. Both are computed over the same n fully-scored rows. "
        "Generator refusals and rows missing either score are excluded rather "
        "than imputed. Prompt grades use the PQS 8-dimension cutoffs; output "
        "grades use the percentage-equivalent cutoffs on the 6-dimension /60 "
        "scale (see config.py)."
    )
    lines.append("")

    CORRELATION_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORRELATION_REPORT_PATH.write_text("\n".join(lines))

    print(headline, file=sys.stderr)
    print(f"report written: {CORRELATION_REPORT_PATH.relative_to(ROOT)}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
