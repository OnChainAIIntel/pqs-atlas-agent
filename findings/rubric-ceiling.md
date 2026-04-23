# Rubric Ceiling — Why Pipeline 4 has 0 polished rows

**Status:** Finding  •  **Pipeline:** 4 (Atlas source corpus)  •  **Date:** 2026-04-23

## TL;DR

The Pipeline 4 spec required 100 polished rows per 500-row file, gated by
PQS's 8-dim pre-flight rubric (A/B/C grade required). Two pilots on
five HuggingFace public prompt corpora produced **1 passing polished row
out of 27 candidates (~4%)**. Final corpus shipped with polished=0. This
is not a bucket-classifier bug, not a dataset-selection bug, not an
insurance-threshold bug — it is a structural mismatch between what the
bucket classifier measures (textual surface features) and what PQS's
rubric measures (prompt-engineering quality).

A post-shipment 20-row verification diagnostic confirmed the ceiling is
stronger than originally diagnosed: **0/20 messy-or-mid rows score
above F**, with the entire corpus capped at **33/80** (2 points below
D-grade threshold). Pipeline 5's calibration cannot draw PQS anchors
from this corpus. See "Mid-bucket verification" below for per-source
gradient data.

A follow-up three-arm Path-2 scoping diagnostic settled where anchors
actually exist: Anthropic-library paraphrases cluster at D (1×C, 4×D),
Awesome-ChatGPT curated prompts cluster at D/F (4×D, 1×F), and
**PQS-optimized WildChat mid rows lift 5/5 from F to B (avg +44.4
points, 76% improvement)**. The closed-loop optimize path IS
Pipeline 5's B-band anchor source. See "Path-2 scoping results" below.

## Measurements

### Pilot-level pass rates

| Pilot | Candidates | Passed (A/B/C) | Rate |
|-------|-----------:|---------------:|-----:|
| v3 (original polished target 100) | 16 | 0 | 0% |
| v4 (fallback polished target 50)  | 11 | 1 | 9% |
| **Combined** | **27** | **1** | **~4%** |

Both pilots used identical insurance rule: send candidate prompt to
`/api/score/full` (8-dim pre-flight, 0–80 scale), keep if `pre_grade ∈
{A, B, C}`. Both pilots preserved the A/B/C threshold; D-grade
relaxation was considered and explicitly rejected to preserve this
finding.

### Source-level pass rates

| Source | v3+v4 candidates | Passed | Rate |
|--------|-----------------:|-------:|-----:|
| HuggingFaceH4/no_robots | 1 | **1** | 100%* |
| OpenAssistant/oasst2    | 2 | 0 | 0% |
| Open-Orca/OpenOrca      | 24 | 0 | 0% |

*single-observation — no robust rate estimate

### Rejection score pattern (pilot v4 insurance log)

All 10 rejections graded **F** with totals **12–25 out of 80**:

| Source | source_row_id | Total | Grade |
|--------|---------------|------:|:-----:|
| OpenOrca | flan.1021430 | 21 | F |
| OpenOrca | flan.753708  | 15 | F |
| OpenOrca | t0.404629    | 20 | F |
| OpenOrca | flan.1079763 | 25 | F |
| OpenOrca | flan.1526030 | 12 | F |
| OpenOrca | flan.1887132 | 13 | F |
| OpenOrca | flan.160889  | 19 | F |
| OpenOrca | flan.2339205 | 12 | F |
| OpenOrca | flan.1520303 | 12 | F |
| oasst2   | 654d4221-8d0…| 20 | F |

D-grade threshold is 35 — even the highest-scoring reject (25) is 10
points below D. This is not borderline rejection; it is deep rejection
on dimensions the bucket classifier does not measure.

### Dimension-level pattern (from full dim breakdowns captured for 4 rejections)

| Dim | flan.1021430 | flan.753708 | t0.404629 | flan.1079763 |
|-----|:---:|:---:|:---:|:---:|
| clarity          | 3 | 3 | 3 | 3 |
| specificity      | 2 | 2 | 4 | — |
| context          | 4 | 1 | 5 | — |
| constraints      | 2 | 4 | 2 | — |
| output_format    | 2 | 2 | 2 | — |
| **role_definition** | **1** | **1** | **1** | — |
| examples         | 6 | 1 | 1 | — |
| **cot_structure**   | **1** | **1** | **2** | — |
| **Total / 80**   | 21 | 15 | 20 | 25 |

The dimensions that consistently score 1/10 across rejections are
`role_definition` and `cot_structure` — the two dimensions that measure
**explicit prompt-engineering constructs** (did the user set a persona?
did they scaffold a reasoning chain?). The bucket classifier's "role"
regex matches phrases like *"as an expert"* embedded anywhere in the
prompt, which surfaces many rows that do not actually assign a role to
the model.

## Why the pilots failed

The bucket classifier (`scripts/pipeline-4/buckets.py`) promotes a row
to `polished` when: `word_count >= 100 AND role_regex AND format_regex
AND example_regex AND criteria_regex`. Those regexes match **textual
surface features anywhere in the prompt**. A long OpenOrca row
containing a passage to summarize will frequently trip the classifier
because the passage itself happens to contain formatting words, example
phrases, criteria-like phrases.

PQS's rubric, by contrast, evaluates the prompt as a *prompt*: does it
assign a role to the model, specify output format for the model, give
the model few-shot examples, structure a chain of thought for the
model? Passage-carrying instruction-tuning rows from public HF datasets
fail nearly all of these constructs.

The two measurements are orthogonal. Real-world users in public HF
datasets do not prompt-engineer — they ask questions, paste passages,
and expect a helpful response. That is the ceiling.

## What this means for PQS

1. **The rubric is working as designed.** Rejection of these rows is
   correct behavior — they *are not* well-engineered prompts.

2. **The bucket classifier is not a PQS-score proxy.** Future work
   should not use the 4-signal regex as a pre-filter for PQS quality.
   A bucket named "polished" needs a name that reflects what it
   actually measures (textual surface signals), not what PQS
   measures (prompt-engineering quality).

3. **Public HF datasets are not a source for Grade-A/B/C prompts at
   volume.** Any future polished-bucket work will need either
   hand-curated prompts, LLM-regenerated rewrites with explicit role
   and CoT scaffolding, or prompts from an actively prompt-engineered
   source (e.g., Anthropic's own prompt library, Awesome ChatGPT
   Prompts curated sets).

4. **Relaxing the insurance threshold would void the finding, not
   solve the problem.** A D-grade row (35–49 total) would still
   score 1 on `role_definition` and `cot_structure` — it would just
   be slightly less broken on the other six dimensions.

## Mid-bucket verification (post-Gate-C diagnostic)

After Gate C shipped the 300/200/0 corpus, a follow-up concern surfaced:
if Pipeline 5's kappa calibration draws from this corpus, does the mid
bucket actually contain any Grade-A anchors? (The rubric ceiling was
only *proven* for polished candidates — mid rows were never scored.)

### Design

Stratified spot-check of 20 rows through `/api/score/full`, balanced
across source to surface per-source signal:
- **10 mid rows**: WildChat 3, oasst2 3, no_robots 2, OpenOrca 2
- **10 messy rows**: LMSYS 4, WildChat 3, oasst2 3

Script: `scripts/pipeline-4/verify-mid-grades.py` (deterministic,
`SEED=4242`).  Evidence: `data/pilots/mid-grade-verification.jsonl`.

### Result

**0/10 mid rows scored A, B, C, or even D.** Every single row (mid and
messy) scored F. The entire corpus lives below the D-grade threshold
(35/80).

| Bucket | n | avg | median | min | max | A | B | C | D | F |
|--------|--:|----:|------:|----:|----:|--:|--:|--:|--:|--:|
| messy  | 10 | 12.7 | 12.0 | 10 | 20 | 0 | 0 | 0 | 0 | **10** |
| mid    | 10 | 21.6 | 21.0 | 12 | 33 | 0 | 0 | 0 | 0 | **10** |

Messy → mid gradient: **+8.9**. There IS a real gradient (mid rows
score ~70% higher than messy), but the ceiling is **33/80** — 2 points
below even D-grade territory.

### Per-source gradient (the reusable artifact)

Ordered by mid-bucket ceiling — this table is the evidence any future
Pipeline 5 source-weighting work should start from:

| Source | Mid n | Mid avg | Mid max | Messy avg | Interpretation |
|--------|------:|--------:|--------:|----------:|----------------|
| allenai/WildChat-1M     | 3 | **26.0** | **33** | 13.7 | **Widest dynamic range.** Closest approach to D-grade in the whole corpus. Best candidate for optimize-based lift. |
| HuggingFaceH4/no_robots | 2 | 23.5 | 27 | — (messy:0)  | Decent ceiling; small n — revisit with larger sample if Path 2 explores it. |
| Open-Orca/OpenOrca      | 2 | 21.0 | 22 | — (messy:0)  | Middling. Consistent with Gate-B polished failures (same source). |
| OpenAssistant/oasst2    | 3 | **16.3** | 20 | 13.3 | **Worst.** Essentially messy-equivalent. 3-point lift over its own messy floor — negligible. |
| lmsys/lmsys-chat-1m     | — (mid:0) | — | — | 11.5 | Most disciplined messy floor. Likely the cleanest "low-grade anchor" source. |

### Implication for Pipeline 5

**Zero calibration anchors above F exist in Pipeline 4's output.** A
kappa calibration drawing 15 rows from this corpus would see a single-
grade distribution (all F) and produce an undefined/meaningless kappa.
This is a *coverage* problem, not a tuning problem.

**Path forward ("Path 2"):** curate D/C/B/A exemplars outside
Pipeline 4. Three arms evaluated in the scoping diagnostic
(`scripts/pipeline-4/scope-path2.py` → `data/pilots/path2-scoping.jsonl`):
- Anthropic prompt library (reference-class prompt-engineered)
- Awesome ChatGPT Prompts (curated, non-rubric-targeted)
- LLM-regenerated rewrites of WildChat mid-top-quartile rows via
  `/api/optimize` (closed-loop)

The scoping diagnostic settles which arm(s) yield anchors at rate.

**For source weighting**, if Pipeline 5 does decide to pull any messy
or mid anchors from Pipeline 4's output: WildChat > no_robots > OpenOrca
>> oasst2. LMSYS is pure messy (no mid rows in the corpus).

## Path-2 scoping results (three-arm diagnostic)

Script: `scripts/pipeline-4/scope-path2.py` + 5 mcp-tool calls for Arm 3.
Evidence: `data/pilots/path2-scoping.jsonl` (15 rows total).

### Arm 1 — Anthropic prompt library (5 prompts, paraphrased from docs)

| Prompt | Total | Grade |
|--------|------:|:-----:|
| `cite-sources`          | 44 | D |
| `code-clarifier`        | 42 | D |
| `email-extractor`       | 50 | C |
| `socratic-tutor`        | 47 | D |
| `function-fabricator`   | 48 | D |
| **avg** | **46.2** | 4×D, 1×C |

### Arm 2 — Awesome ChatGPT Prompts (5 canonical prompts)

| Prompt | Total | Grade |
|--------|------:|:-----:|
| `linux-terminal`        | 37 | D |
| `english-translator`    | 35 | D |
| `interviewer`           | 38 | D |
| `javascript-console`    | 39 | D |
| `travel-guide`          | 30 | F |
| **avg** | **35.8** | 4×D, 1×F |

### Arm 3 — WildChat mid rewrites via `mcp__pqs__optimize_prompt`

The HTTP endpoint `/api/optimize` is origin-locked to
`pqs.onchainintel.net` (HTTP 403 for API-key callers). The MCP tool
exposes the same optimization path for programmatic use. Five WildChat
mid seeds were optimized; one original seed (`3fa9b68e…`) was skipped
for NSFW content and replaced with `8a912051…` (data-science-teen
prompt — see NSFW note below). Each MCP call costs $0.025 USDC via
x402.

| Seed | Orig | Optimized | Δ | Improvement |
|------|:----:|:---------:|:-:|:-----------:|
| `8662da0…` books-SQL task (general)     | 27/F | 66/B | +39 | 74% |
| `2c2064e…` SEO guru (general)           | 23/F | 66/B | +43 | 75% |
| `07ad16b…` compiler assignment (sw)     | 19/F | 63/B | +44 | 72% |
| `8a91205…` data-science teen (edu)      | 29/F | 69/B | +40 | 78% |
| `45503aa…` ffmpeg error (sw)            | 12/F | 68/B | +56 | 82% |
| **avg** | **22.0/F** | **66.4/B** | **+44.4** | **76%** |

**5/5 F→B lift.** Optimized-prompt dimension profile is balanced
(clarity 9, specificity 9, context 8–9, constraints 8–9, output_format
8–9, role_definition 8–9, examples 6–8, cot_structure 6–9) — this is
precisely the dimension spread Pipeline 5 kappa calibration needs.

### Cross-arm verdict

| Arm | A/B/C rate | Grade distribution | Notes |
|-----|:---------:|--------------------|-------|
| 1 Anthropic lib              | 1/5  | 1×C, 4×D            | Borderline — closest human-written source to rubric spec. |
| 2 Awesome ChatGPT            | 0/5  | 4×D, 1×F            | Below threshold — curated for usefulness, not prompt-engineering depth. |
| 3 WildChat + PQS optimize    | 5/5  | **5×B** (63–69)     | **Closed-loop anchor source.** |

Arms 1 and 2 fail the original A/B/C bar but cluster in the D band
(35–49), which is still above the entire Pipeline 4 corpus ceiling of
33 — so they remain useful as **D-grade anchors** for a multi-band
kappa calibration.

Arm 3 is the definitive win: PQS can **lift its own corpus** from F to
B at rate. Pipeline 5 should treat the optimize-loop as the B-band
anchor source of record, with Arms 1+2 as D-band floor references and
raw Pipeline 4 mid/messy rows as the F-band anchors.

### NSFW signal (flagged for Pipeline 5 source-selection)

While sampling 5 WildChat mid replacement seeds to substitute for
`3fa9b68e…` (an NSFW Italian Romantic Comedy prompt with scatological
content), 3 of 5 alternates surfaced as variants of the same NSFW
prompt family. This is not a statistically rigorous measurement but
suggests WildChat-1M has non-trivial NSFW density in the mid bucket.
Pipeline 5 should either apply a content filter upstream or use
LMSYS/no_robots as the preferred messy/mid source where quality
allows.

### Closed-loop demo narrative (unblocked)

The three-arm result unlocks the PQS story: *"PQS identifies weak
prompts in the wild (F-grade WildChat mid rows at 12–29/80), optimizes
them to strong prompts (B-grade at 63–69/80), and produces a
dimension-balanced anchor set Pipeline 5 can calibrate against."*
Single corpus, single tool, closed loop, measurable lift (avg +44.4
points / 76% improvement).

## Final shipped distribution

500 rows per file × 4 files = 2000 rows total.

```
300 messy / 200 mid / 0 polished
```

- `data/source-prompts-full-deterministic.jsonl`
- `data/source-prompts-full-sampled.jsonl`
- `data/source-prompts-clean-deterministic.jsonl`  (no CC-BY-NC)
- `data/source-prompts-clean-sampled.jsonl`        (no CC-BY-NC)

Source mix — full files:

| Source | messy | mid | Total |
|--------|------:|----:|------:|
| lmsys/lmsys-chat-1m      | 160 |   0 | 160 |
| allenai/WildChat-1M      |  90 |  40 | 130 |
| OpenAssistant/oasst2     |  50 |  80 | 130 |
| HuggingFaceH4/no_robots  |   0 |  50 |  50 |
| Open-Orca/OpenOrca       |   0 |  30 |  30 |
| **Total**                | **300** | **200** | **500** |

Clean files drop `no_robots` (CC-BY-NC-4.0) and redistribute its 50 mid
slots to `oasst2` (+50 mid).

## References

- Spec: `docs/pipeline-4-spec.md` (if it exists; otherwise the
  hackathon brief in this session's transcript)
- Classifier: `scripts/pipeline-4/buckets.py`
- Extractor: `scripts/pipeline-4/extract.py`
- Pilot v4 log: `/tmp/pipeline4-pilot-v4.log`
- Gate C log: `/tmp/pipeline4-full-v1.log`
- Pilot rows: `data/pipeline-4-pilot.jsonl` (46 rows from v4)
- Mid-bucket verification script: `scripts/pipeline-4/verify-mid-grades.py`
- Mid-bucket verification evidence: `data/pilots/mid-grade-verification.jsonl` (20 rows)
- Mid-bucket verification log: `/tmp/pipeline4-verify-mid.log`
- Path-2 scoping script: `scripts/pipeline-4/scope-path2.py`
- Path-2 scoping evidence: `data/pilots/path2-scoping.jsonl` (15 rows: 5 Anthropic lib + 5 Awesome ChatGPT + 5 WildChat-optimize-mcp)
- Path-2 scoping log (Arms 1+2 + Arm 3 HTTP 403 before MCP pivot): `/tmp/pipeline4-scope-path2.log`
