# Kappa Calibration: 15-Row Cross-Rater Agreement on PQS v2.0 Rubric

Last updated: 2026-04-23
Status: **SHIP CRITERION FAILED**. PQS production scoring shows fair agreement (κ = 0.37) with Opus 4.7 on the same 8-dim rubric applied to the same 15 anchors. External LLM raters agree with each other almost perfectly (κ = 0.89). The disagreement is between PQS and the outside world, not between the outside-world raters.

---

## TL;DR

- **15 anchors** (5 F-band, 5 D-band, 5 B-band) selected deterministically from Pipeline 4 outputs and the Arm 3 closed-loop F→B Lift corpus, SEED=42.
- **3 raters** scored every anchor on the canonical 8-dimension PQS v2.0 rubric:
  1. PQS production `/api/score/full` (Sonnet 4 under the hood, black-box reference).
  2. Anthropic Claude Opus 4.7 direct, same rubric as the system prompt.
  3. OpenAI GPT-4o direct, byte-identical rubric as the system prompt.
- **Cohen's weighted kappa (quadratic weights)** computed pairwise on the 5-category grade scale and per-dim on the 10-category 1-10 scale.
- **Result**: Opus 4.7 ↔ GPT-4o agree almost perfectly (κ = 0.89). PQS ↔ Opus 4.7 comes in at κ = 0.37 (fair). PQS ↔ GPT-4o at κ = 0.47 (moderate). Ship threshold of 0.61 (substantial) not met on the PQS-involving pairs.
- **What this means**: the rubric itself is learnable and reproducible. Two independent frontier models applying the same text-form rubric converge. PQS production scoring diverges systematically from both of them, concentrated on the D-band and B-band anchors where PQS consistently under-scores compared to external rating.

---

## Methodology

### Anchors

15 anchors, 5 per target band (F, D, B):

| Band | Source | Method |
|------|--------|--------|
| F | `data/source-prompts-clean-deterministic.jsonl` | WildChat-first priority, `messy`/`mid` buckets, lowest rank-tie IDs |
| D | Pipeline 4 Path-2 scoping Arms 1+2 (Anthropic + awesome-chatgpt libraries) | D-graded rows, full prompt text recovered via AST parsing |
| B | Pipeline 4 Path-2 scoping Arm 3 (closed-loop F→B Lift via `/api/optimize`) | 5 WildChat mid seeds optimized by PQS MCP, cached in-script for reproducibility |

Deterministic selection (SEED=42) verified reproducible by running `scripts/pipeline-5/select-anchors.py` twice and diffing output.

Anchor file: `data/pipeline-5-anchors.jsonl` (15 rows, committed).

### Rubric

8 dimensions each on an integer 1-10 scale, total in [8, 80], grade cutoffs A≥70, B≥60, C≥50, D≥35, F<35. The dimensions and cutoffs are canonical from `prompt-optimization-engine/lib/pqs-schemas.js` and `lib/pqs-grading.js`.

Rubric text assembled in `scripts/pipeline-5/rubric.py`. SHA256 checksum `0dfa088cb5d2253fb2793b3dcc17da72ecda229b7f55bdc02cbcd690b38315cc` asserted at runtime before every Rater 2 and Rater 3 call. Rater 1 (PQS production) uses its own internal system prompt; we treat it as a black box.

### Raters

| # | Rater | Model | Prompt path |
|---|-------|-------|-------------|
| 1 | PQS production | `claude-sonnet-4-20250514` (internal) | black box; POST to `/api/score/full` |
| 2 | Opus direct | `claude-opus-4-7` | RUBRIC_PROMPT as `system`, anchor text as user |
| 3 | GPT-4o direct | `gpt-4o-2024-08-06` | RUBRIC_PROMPT as `system`, anchor text as user |

Raters 2 and 3 request minified JSON output on the 8-dim + total + grade shape and coerce to the canonical grade mapping in post-processing. Rater 1 returns PQS's own 8-dim object.

One refusal: Opus 4.7 declined to score F-01 (`stop_reason: refusal`), which is a 4323-character SHA512-encrypted blob with a "decode this" instruction. Surfaced as a structured row with `status: "refused"` and null dimensions. Treated as missing data in all pair computations that include Opus; the (PQS, GPT-4o) pair retains all 15 anchors.

### Kappa formula

Cohen's weighted kappa with quadratic weights on the ordinal scale. For K categories with category indices i, j ∈ {0, ..., K-1}:

```
w_ij = (i - j)² / (K - 1)²
κ    = 1 - [Σ w_ij · p_ij^observed] / [Σ w_ij · p_ij^expected]
```

Grade-level: K=5 (F=0, D=1, C=2, B=3, A=4).
Per-dim: K=10 (1..10 mapped to 0..9).

Interpretation bands (Landis & Koch 1977): ≥0.81 almost perfect, ≥0.61 substantial, ≥0.41 moderate, ≥0.21 fair, below 0.21 slight.

Full implementation: `scripts/pipeline-5/compute-kappa.py`. Raw output: `data/pipeline-5-kappa-results.json`.

### Cost

Total Gate C scoring cost: **$0.1640** (vs. $2.00 budget cap).

- Opus 4.7: 15 calls (including 1 refusal), ~$0.110 total.
- GPT-4o: 15 calls, ~$0.037 total.
- PQS production: 15 calls, server-side cost not exposed; logged as $0.

---

## Results

### Pairwise grade-level kappa

| Pair | n | κ (weighted) | Landis-Koch label |
|------|---|--------------|-------------------|
| Opus 4.7 ↔ GPT-4o | 14 | **+0.8916** | almost perfect |
| PQS ↔ GPT-4o | 15 | +0.4677 | moderate |
| PQS ↔ Opus 4.7 | 14 | +0.3715 | fair |

n = 14 on pairs involving Opus because F-01 is excluded (refusal).

### Confusion matrices (rows indexed F, D, C, B, A; columns same)

**PQS (rows) vs. Opus 4.7 (cols)**, n=14:

```
         F  D  C  B  A
    F    4  0  0  0  0
    D    0  1  3  4  1
    C    0  0  0  1  0
    B    0  0  0  0  0
    A    0  0  0  0  0
```

PQS calls 5 anchors F (Opus agrees on 4, refused on 1), 9 anchors D (Opus puts 1 of those at D, 3 at C, 4 at B, 1 at A), and 1 anchor C (Opus calls B). PQS never issues a B or A grade across all 15 anchors.

**PQS (rows) vs. GPT-4o (cols)**, n=15:

```
         F  D  C  B  A
    F    5  0  0  0  0
    D    0  0  5  4  0
    C    0  0  0  1  0
    B    0  0  0  0  0
    A    0  0  0  0  0
```

Same pattern: F perfectly agreed, PQS-D gets split 5/4 between GPT-4o's C and B. PQS never agrees with GPT-4o above F.

**Opus 4.7 (rows) vs. GPT-4o (cols)**, n=14:

```
         F  D  C  B  A
    F    4  0  0  0  0
    D    0  0  1  0  0
    C    0  0  2  1  0
    B    0  0  2  3  0
    A    0  0  0  1  0
```

Strong diagonal bias. The two external raters land in the same or adjacent grade cell on 13 of 14 anchors.

### Per-dimension kappa (10 categories)

| Dimension | PQS ↔ Opus | PQS ↔ GPT-4o | Opus ↔ GPT-4o |
|-----------|-----------:|-------------:|--------------:|
| clarity | +0.165 (slight) | +0.477 (moderate) | +0.599 (moderate) |
| specificity | +0.480 (moderate) | +0.523 (moderate) | +0.821 (almost perfect) |
| context | +0.601 (moderate) | +0.597 (moderate) | +0.784 (substantial) |
| constraints | +0.735 (substantial) | +0.760 (substantial) | +0.918 (almost perfect) |
| output_format | +0.818 (almost perfect) | +0.839 (almost perfect) | +0.986 (almost perfect) |
| role_definition | +0.750 (substantial) | +0.869 (almost perfect) | +0.948 (almost perfect) |
| examples | +0.662 (substantial) | +0.600 (moderate) | +0.826 (almost perfect) |
| cot_structure | +0.523 (moderate) | +0.511 (moderate) | +0.974 (almost perfect) |

Observed pattern:
- Opus ↔ GPT-4o is at or above 0.78 on every dimension except clarity (0.60).
- PQS ↔ Opus is substantial/almost-perfect on the "syntactic" dimensions (output_format, role_definition, constraints, examples) and drops into moderate/slight on the "interpretive" ones (clarity 0.17, specificity 0.48, cot_structure 0.52, context 0.60).
- The dimension where PQS diverges most from both external raters is `clarity`, where PQS ↔ Opus is 0.17 (slight). External raters peg clarity higher than PQS does on D-band and B-band prompts.

---

## Ship claim

**The ship criterion is not met.**

Requirement from the Pipeline 5 spec: PQS ↔ Opus 4.7 ≥ 0.61 weighted kappa (substantial agreement per Landis-Koch).

Observed: 0.3715. Margin of miss: 0.24 kappa points.

What makes the result interpretable (rather than inconclusive) is that the *other* pair involving an external rater, Opus ↔ GPT-4o, lands at 0.89 almost-perfect. Two independent frontier models applying the same text rubric to the same 15 prompts converge. The rubric is well-formed and learnable. The disagreement is between PQS and that convergent external reading.

---

## What this tells us

### The rubric is fine

Opus 4.7 and GPT-4o trained by different labs on different data reach nearly identical grades on 13 of 14 shared anchors. Per-dim agreement between them is at or above 0.78 on 7 of 8 dimensions. The rubric text in `rubric.py`, the 1-10 scale, and the A/B/C/D/F cutoffs produce reproducible ordinal judgments when a frontier LLM applies them directly.

### PQS production is systematically stricter than external raters

Across all 15 anchors, PQS issued zero B and zero A grades. The external raters, scoring the same anchors against the same rubric (in rubric form; PQS uses its own internal prompt), issued B or A on 5 of 14 anchors (Opus) and 5 of 15 (GPT-4o).

The gap concentrates on the 10 non-F anchors:
- **D-band (n=5)**: PQS calls all 5 D. Opus scores them as (D, C, B, B, C). GPT-4o scores them as (B, C, C, C, C). External raters place these prompts one to two grade steps above PQS.
- **B-band (n=5)**, the Pipeline 4 Arm 3 outputs, prompts optimized by PQS's own `/api/optimize` MCP endpoint with Δtotal ≥ 25 on PQS's own scoring: PQS's production scorer re-grades them as (D, C, D, D, D). Opus grades them (D, B, C, A, B). GPT-4o grades them (C, B, B, B, B).

The cleanest case is B-01 through B-05. These are the Arm 3 closed-loop artifacts documented in `findings/rubric-ceiling.md`. Those artifacts were explicitly lifted from F to B by the PQS optimize endpoint, producing prompts that the optimizer's own model believed to be B-grade. Run those same prompts back through PQS production scoring and 4 of 5 come out D. Run them through Opus or GPT-4o and 3 to 4 of 5 come out B. The external view of "what B looks like" tracks the optimize endpoint's intent, not the score endpoint's measurement.

### Where PQS and external raters align

The per-dim table shows PQS ↔ external kappa at or above 0.74 on four dimensions: output_format, role_definition, constraints, examples. These are the four dimensions where "present or absent" is relatively clear from the prompt text. A persona is in or out. A format directive is in or out. These are the easiest to score.

### Where they diverge

The four dimensions under 0.74 PQS ↔ external are: clarity (0.17), specificity (0.48), cot_structure (0.52), context (0.60). These are the dimensions that require an interpretive judgment about how well the prompt will work, not whether a feature is present. "Is this clear?" is not a syntactic question.

One plausible reading: PQS's internal scoring prompt may over-index on "weak clarity" signals (short prompts, casual tone, conversational phrasing) in ways that external LLMs interpret as "clear but informal" rather than "unclear." This is a prompt-engineering question about the PQS internal system prompt, not about the rubric text.

### What to do next

The whitepaper methodology should not assert PQS-on-PQS kappa. The calibrated agreement number to cite for any external audience is Opus ↔ GPT-4o at 0.89 on the rubric text published in this repo, not PQS-on-PQS. That number supports "the rubric is reliable" as a claim. It does not support "PQS's scoring endpoint reproduces the rubric." The latter is what just failed here.

Two concrete work items that this finding surfaces for follow-up (not for this pipeline):
1. Audit the PQS `/api/score/full` internal system prompt for drift from the canonical rubric. If PQS is implicitly applying a "harsher rubric than it publishes," that is the underlying cause.
2. Consider whether "PQS production grade" and "rubric grade" should be separate labels in the atlas dataset. Right now they are assumed to be the same thing; this evidence suggests they are not.

---

## Reproducibility

All artifacts are committed:

- `scripts/pipeline-5/select-anchors.py`: anchor selection (SEED=42, reproducible).
- `scripts/pipeline-5/rubric.py`: canonical RUBRIC_PROMPT with SHA256 self-check.
- `scripts/pipeline-5/run-raters.py`: 45 scoring calls, resume-safe, records per-call latency and cost.
- `scripts/pipeline-5/compute-kappa.py`: Cohen's weighted kappa pairwise + per-dim.
- `data/pipeline-5-anchors.jsonl`: 15 anchors.
- `data/pipeline-5-rater-outputs.jsonl`: 45 rows, 1 refusal flag.
- `data/pipeline-5-kappa-results.json`: full results including confusion matrices.

Re-running from scratch (with a valid `ANTHROPIC_KEY`, `OPENAI_API_KEY`, `PQS_API_KEY`, `PQS_INTERNAL_TOKEN` in `~/Desktop/prompt-optimization-engine/.env.local`):

```bash
python scripts/pipeline-5/select-anchors.py
python scripts/pipeline-5/run-raters.py
python scripts/pipeline-5/compute-kappa.py
```

The rubric SHA is asserted pre-flight. Any future rubric edit surfaces immediately as a mismatch and forces a full re-run of the 45 scoring calls.
