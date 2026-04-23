"""
Pipeline 5 — Gate D: Cohen's weighted kappa (quadratic weights) pairwise
and per-dim across Rater 1 (PQS production), Rater 2 (Opus 4.7), Rater 3
(GPT-4o).

Grade-level agreement: 5-category ordinal scale F<D<C<B<A mapped to 0..4.
Per-dim agreement: 10-category ordinal scale on each of the 8 rubric dims
(1..10 → 0..9).

Refusal handling: F-01/opus_4_7 returned stop_reason=refusal (Opus safety
filter on the encrypted SHA512 blob). Any pair that includes Opus drops
this one anchor. The (PQS, GPT-4o) pair keeps all 15.

Reads:   data/pipeline-5-rater-outputs.jsonl
Writes:  data/pipeline-5-kappa-results.json

Formula (Cohen's weighted kappa):
    κ_w = 1 - (Σ w_ij × O_ij) / (Σ w_ij × E_ij)
where
    w_ij = (i - j)² / (K - 1)²         (quadratic weights)
    O_ij = observed probability of (rater_a=i, rater_b=j)
    E_ij = marginal_a(i) × marginal_b(j)    (expected under independence)
    K    = number of ordinal categories

Landis-Koch interpretation bands:
    ≥ 0.81  almost perfect
    ≥ 0.61  substantial
    ≥ 0.41  moderate
    ≥ 0.21  fair
    < 0.21  poor
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUTS_PATH = ROOT / "data" / "pipeline-5-rater-outputs.jsonl"
RESULTS_PATH = ROOT / "data" / "pipeline-5-kappa-results.json"

GRADE_TO_ORD = {"F": 0, "D": 1, "C": 2, "B": 3, "A": 4}
ORD_TO_GRADE = {v: k for k, v in GRADE_TO_ORD.items()}

DIMENSIONS = (
    "clarity", "specificity", "context", "constraints",
    "output_format", "role_definition", "examples", "cot_structure",
)

RATERS = ("pqs_production", "opus_4_7", "gpt_4o")
PAIRS = [
    ("pqs_production", "opus_4_7"),
    ("pqs_production", "gpt_4o"),
    ("opus_4_7",       "gpt_4o"),
]


def landis_koch(k: float) -> str:
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


def cohens_weighted_kappa(pairs: list[tuple[int, int]], k_cats: int) -> dict:
    """
    Cohen's weighted kappa with quadratic weights on an ordinal k_cats scale
    (category indices 0..k_cats-1). `pairs` is a list of (a, b) observed
    ratings. Returns dict with kappa, n, po, pe, and Landis-Koch label.

    When all pairs lie on the same category for BOTH raters (so the
    marginals are degenerate — Σ(w_ij × E_ij) == 0), kappa is undefined by
    the formula. We surface that as kappa=None with reason="degenerate_marginals".
    Exception: perfect agreement on a single category produces po=pe=0 and
    we treat that as kappa=1.0 (conventional).
    """
    n = len(pairs)
    if n == 0:
        return {"kappa": None, "n": 0, "reason": "no_pairs"}

    # Observed joint distribution
    observed = Counter(pairs)

    # Marginals
    marg_a = Counter(a for a, _ in pairs)
    marg_b = Counter(b for _, b in pairs)

    # Weighted discordance (quadratic)
    denom_max = (k_cats - 1) ** 2

    po = 0.0
    pe = 0.0
    for i in range(k_cats):
        for j in range(k_cats):
            w = ((i - j) ** 2) / denom_max
            o_ij = observed.get((i, j), 0) / n
            e_ij = (marg_a.get(i, 0) / n) * (marg_b.get(j, 0) / n)
            po += w * o_ij
            pe += w * e_ij

    if pe == 0:
        # Degenerate marginals — if po==0 too, that's perfect single-category
        # agreement (kappa conventionally = 1). Otherwise kappa is undefined.
        if po == 0:
            return {
                "kappa": 1.0,
                "n": n,
                "po": 0.0,
                "pe": 0.0,
                "label": "almost perfect",
                "note": "all pairs on a single category; kappa defined as 1",
            }
        return {
            "kappa": None,
            "n": n,
            "po": po,
            "pe": pe,
            "reason": "degenerate_marginals",
        }

    kappa = 1.0 - (po / pe)
    return {
        "kappa": round(kappa, 4),
        "n": n,
        "po": round(po, 6),
        "pe": round(pe, 6),
        "label": landis_koch(kappa),
    }


def _load_rows():
    return [json.loads(l) for l in OUTPUTS_PATH.read_text().splitlines() if l.strip()]


def _index_by_rater(rows):
    """Return {anchor_id: {rater: row}}. Skip rows where status != 'ok'."""
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        aid = r["anchor_id"]
        out.setdefault(aid, {})[r["rater"]] = r
    return out


def _pair_grades(index, rater_a, rater_b) -> tuple[list[tuple[int, int]], list[str]]:
    """Extract (ord_a, ord_b) pairs for the two raters, skipping refusals."""
    pairs, skipped = [], []
    for aid, ratings in sorted(index.items()):
        ra = ratings.get(rater_a)
        rb = ratings.get(rater_b)
        if not ra or not rb:
            skipped.append(f"{aid} (missing rater)")
            continue
        if ra.get("status", "ok") != "ok" or rb.get("status", "ok") != "ok":
            skipped.append(f"{aid} ({ra.get('status','?')}+{rb.get('status','?')})")
            continue
        ga, gb = ra.get("grade"), rb.get("grade")
        if ga not in GRADE_TO_ORD or gb not in GRADE_TO_ORD:
            skipped.append(f"{aid} (grade={ga!r}/{gb!r})")
            continue
        pairs.append((GRADE_TO_ORD[ga], GRADE_TO_ORD[gb]))
    return pairs, skipped


def _pair_dims(index, rater_a, rater_b, dim) -> tuple[list[tuple[int, int]], list[str]]:
    """Extract (dim_score_a, dim_score_b) pairs on 1..10 → 0..9 ordinal."""
    pairs, skipped = [], []
    for aid, ratings in sorted(index.items()):
        ra = ratings.get(rater_a)
        rb = ratings.get(rater_b)
        if not ra or not rb:
            continue
        if ra.get("status", "ok") != "ok" or rb.get("status", "ok") != "ok":
            skipped.append(f"{aid} (status)")
            continue
        ds_a = (ra.get("dim_scores") or {}).get(dim)
        ds_b = (rb.get("dim_scores") or {}).get(dim)
        if not isinstance(ds_a, int) or not isinstance(ds_b, int):
            skipped.append(f"{aid} (dim missing)")
            continue
        pairs.append((ds_a - 1, ds_b - 1))  # 1..10 → 0..9
    return pairs, skipped


def _confusion_matrix(pairs, k_cats):
    """K×K matrix (list of lists) of observed counts."""
    m = [[0] * k_cats for _ in range(k_cats)]
    for a, b in pairs:
        m[a][b] += 1
    return m


def main():
    rows = _load_rows()
    assert len(rows) == 45, f"expected 45 rater-output rows, got {len(rows)}"
    index = _index_by_rater(rows)

    results: dict = {
        "source_file": str(OUTPUTS_PATH.relative_to(ROOT)),
        "source_row_count": len(rows),
        "anchor_count": len(index),
        "grade_scale": {"F": 0, "D": 1, "C": 2, "B": 3, "A": 4},
        "dim_scale": {"min": 1, "max": 10, "k_categories": 10},
        "weights": "quadratic",
        "interpretation": "landis_koch_1977",
        "pairs": {},
        "per_dim": {},
        "refusals": [],
    }

    for r in rows:
        if r.get("status") == "refused":
            results["refusals"].append({
                "anchor_id": r["anchor_id"],
                "rater": r["rater"],
                "stop_reason": r.get("stop_reason"),
                "target_band": r["target_band"],
            })

    print("=" * 72)
    print("Pairwise grade-level kappa (5 categories: F D C B A)")
    print("=" * 72)
    for a, b in PAIRS:
        pairs, skipped = _pair_grades(index, a, b)
        k = cohens_weighted_kappa(pairs, k_cats=5)
        cm = _confusion_matrix(pairs, 5)
        pair_key = f"{a}__vs__{b}"
        results["pairs"][pair_key] = {
            **k,
            "confusion_matrix_rows_0F_1D_2C_3B_4A": cm,
            "skipped_anchors": skipped,
        }
        kappa_str = "None " if k.get("kappa") is None else f"{k['kappa']:+.4f}"
        label = k.get("label", k.get("reason", "-"))
        print(f"  {a:18s} vs {b:18s}  n={k['n']:2d}  κ={kappa_str}  ({label})")
        if skipped:
            print(f"    skipped: {', '.join(skipped)}")

    print()
    print("=" * 72)
    print("Per-dimension kappa (10 categories: 1..10 each)")
    print("=" * 72)
    print(f"{'dim':18s} " + " ".join(f"{a[:3]}/{b[:3]:<8s}" for a, b in PAIRS))
    for dim in DIMENSIONS:
        row_vals = []
        results["per_dim"][dim] = {}
        for a, b in PAIRS:
            pairs, _ = _pair_dims(index, a, b, dim)
            k = cohens_weighted_kappa(pairs, k_cats=10)
            pair_key = f"{a}__vs__{b}"
            results["per_dim"][dim][pair_key] = {
                "kappa": k.get("kappa"),
                "n": k["n"],
                "label": k.get("label"),
            }
            row_vals.append(
                "   NA  " if k.get("kappa") is None else f"{k['kappa']:+.3f}"
            )
        print(f"  {dim:16s} " + "   ".join(f"{v:>9s}" for v in row_vals))

    # Summary pass criteria
    pqs_opus = results["pairs"]["pqs_production__vs__opus_4_7"]["kappa"]
    pqs_gpt  = results["pairs"]["pqs_production__vs__gpt_4o"]["kappa"]
    opus_gpt = results["pairs"]["opus_4_7__vs__gpt_4o"]["kappa"]

    results["summary"] = {
        "pqs_vs_opus_4_7": pqs_opus,
        "pqs_vs_gpt_4o":   pqs_gpt,
        "opus_4_7_vs_gpt_4o": opus_gpt,
        "ship_threshold_pqs_opus": 0.61,
        "pqs_opus_ship": (pqs_opus is not None and pqs_opus >= 0.61),
    }

    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n")
    print()
    print(f"Written: {RESULTS_PATH.relative_to(ROOT)}")
    print()
    print("Ship criterion (PQS ↔ Opus 4.7 ≥ 0.61 substantial):", results["summary"]["pqs_opus_ship"])


if __name__ == "__main__":
    main()
