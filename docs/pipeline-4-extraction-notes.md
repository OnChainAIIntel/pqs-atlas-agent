# Pipeline 4 — Extraction Notes

Operational record for the Atlas source-prompt corpus extraction. For the
research finding on why polished=0, see
[`findings/rubric-ceiling.md`](../findings/rubric-ceiling.md).

## Deliverable

4 JSONL files, 500 rows each, at `data/source-prompts-*.jsonl`:

| File | License scope | Sampling |
|------|---------------|----------|
| `source-prompts-full-deterministic.jsonl` | All 5 HF sources incl. CC-BY-NC | Deterministic (sorted by source_row_id asc) |
| `source-prompts-full-sampled.jsonl` | All 5 HF sources incl. CC-BY-NC | `random.Random(seed=42)` sampled |
| `source-prompts-clean-deterministic.jsonl` | No CC-BY-NC (no `no_robots`) | Deterministic |
| `source-prompts-clean-sampled.jsonl` | No CC-BY-NC | `random.Random(seed=42)` sampled |

All four files hold 500 unique `source_row_id`s each, 300 `messy` + 200 `mid`
+ 0 `polished`, with `sampling_method` matching the file name.

## Row schema

Each line is a single JSON object with the following fields:

```
prompt                 — the user's prompt text, verbatim from source
source_dataset         — HF identifier (e.g., "lmsys/lmsys-chat-1m")
source_row_id          — stable ID scoped to source (see below)
source_split           — HF split the row was pulled from (e.g., "train")
vertical_source_label  — short label for corpus filters
license_flag           — SPDX-ish string (CC-BY-NC-4.0, Apache-2.0, ODC-BY, etc.)
quality_bucket         — "messy" | "mid"  (no polished in this corpus)
sampling_method        — "deterministic" | "sampled"
word_count             — whitespace-split count on `prompt`
```

### Stable source_row_ids

| Source | source_row_id format |
|--------|----------------------|
| HuggingFaceH4/no_robots | `prompt_id` |
| OpenAssistant/oasst2    | `message_id` of root prompter turn |
| Open-Orca/OpenOrca      | `id` (e.g., `flan.1021430`, `t0.404629`) |
| allenai/WildChat-1M     | `f"{conv_hash}:{turn_identifier}"` |
| lmsys/lmsys-chat-1m     | `conversation_id` |

OpenOrca rows with the `niv.*` prefix (NI distillations) are excluded —
see pilot v2 below.

## Final source mix

### Full files (500 rows)
| Source | messy | mid | Total |
|--------|------:|----:|------:|
| lmsys/lmsys-chat-1m      | 160 |   0 | 160 |
| allenai/WildChat-1M      |  90 |  40 | 130 |
| OpenAssistant/oasst2     |  50 |  80 | 130 |
| HuggingFaceH4/no_robots  |   0 |  50 |  50 |
| Open-Orca/OpenOrca       |   0 |  30 |  30 |
| **Total**                | **300** | **200** | **500** |

### Clean files (500 rows — drops `no_robots` CC-BY-NC)
| Source | messy | mid | Total |
|--------|------:|----:|------:|
| lmsys/lmsys-chat-1m      | 160 |   0 | 160 |
| allenai/WildChat-1M      |  90 |  40 | 130 |
| OpenAssistant/oasst2     |  50 | 130 | 180 |
| Open-Orca/OpenOrca       |   0 |  30 |  30 |
| **Total**                | **300** | **200** | **500** |

## Pilot timeline (why polished=0)

| Pilot | Target | Polished result | Key change from prior |
|-------|--------|-----------------|-----------------------|
| v1 | 50 rows, 100 polished target | 0/3 (NI content, all F) | Initial run with `natural-instructions` as polished source |
| v2 | 50 rows, 100 polished target | 0/N (all `niv.*`-prefixed OpenOrca, all F + HTTP 400) | Swapped NI → OpenOrca per Ken's Option B; added `MAX_PROMPT_CHARS=9500` cap to avoid HTTP 400 |
| v3 | 50 rows, 100 polished target | 0/16 (F, totals 12–25) | Excluded `niv.*` prefix from OpenOrca loader; 3× polished overshoot |
| v4 | 50 rows, **50 polished target** (fallback) | 1/11 (no_robots passed; OpenOrca 0/9, oasst2 0/1) | Fallback distribution: 250 messy / 200 mid / 50 polished |

v3 and v4 together: **1 pass / 27 candidates (~4%)** — well below the
≥70% threshold for a useful polished bucket.

Pilot auto-approve rule per brief:
- ≥70% polished pass → auto Gate C
- 50–70% → stop and report
- <50% → stop and report (triggered by v3 and v4)

Ken's decision after v4 report: **Option 1** — ship 4 × 500-row files at
300 messy / 200 mid / 0 polished. Do not relax insurance threshold. See
[`findings/rubric-ceiling.md`](../findings/rubric-ceiling.md) for the full
rubric-ceiling analysis.

## Gate C run

- Command: `python3 scripts/pipeline-4/extract.py` (no `--pilot`)
- Runtime: **82.7s** (no polished insurance API calls; source-loading
  dominated by oasst2 at 38.3s and WildChat at 25.7s)
- Output: 4 files, 500 rows each, all `source_row_id`s unique within a
  file, bucket distribution matches spec exactly
- Log: `/tmp/pipeline4-full-v1.log`

## Judge review

Dispatched post-Gate-C. 120 rows spot-checked across the 4 files (30/file
re-classified through `buckets.py`, 10/file content-inspected).

```
VERDICT: ship
ANOMALIES: none
SAMPLE CHECKS: 120 rows spot-checked, 0 issues
```

Checks performed:
- Row count (500 each) ✓
- Bucket distribution (300/200/0) ✓
- Sampling method matches filename ✓
- `source_row_id` uniqueness within file ✓
- Clean-file license purity (no CC-BY-NC leak) ✓
- Source distribution matches spec ✓
- Bucket-vs-classifier re-consistency on 30 rows/file ✓
- Word-count sanity per bucket ✓
- No model-output leakage into prompt column ✓
- Non-empty `source_split` on every row ✓

## Non-shipping artifacts (local only, gitignored)

- `data/pipeline-4-pilot.jsonl` — last pilot's 46 rows; development
  artifact, not a deliverable
- `data/pipeline-4-polished-rejections.jsonl` — insurance rejection log
  (clobbered on each run); empty after Gate C because polished=0
- `/tmp/pipeline4-pilot-v4.log` — pilot v4 stdout
- `/tmp/pipeline4-full-v1.log` — Gate C stdout

## Reproducing the extraction

```sh
cd pqs-atlas-agent
python3 -m venv .venv-pipeline4
source .venv-pipeline4/bin/activate
pip install -r scripts/pipeline-4/requirements.txt   # (create if needed)
export HF_TOKEN=...          # required for LMSYS (gated dataset)
export PQS_API_KEY=...       # required if re-running polished insurance
export PQS_INTERNAL_TOKEN=...

# Pilot (50-row smoke test; not part of the deliverable)
python3 scripts/pipeline-4/extract.py --pilot

# Full run (writes the 4 deliverable files)
python3 scripts/pipeline-4/extract.py
```

Seeds: sampled variant uses `random.Random(seed=42)`. Deterministic
variant sorts candidate pools by `source_row_id` ascending and slices
the first N.

## Open questions for follow-up

1. **Where do Grade-A/B/C prompts actually come from at volume?** The
   finding rules out public HF instruction-tuning corpora. Next
   candidates to investigate: Awesome ChatGPT Prompts, Anthropic's
   published prompt library, hand-curated prompt-engineering tutorials.

2. **Should the `polished` bucket be renamed?** The regex-based
   classifier measures textual surface features, not PQS-rubric
   quality. A name like `surface-rich` would be more honest.

3. **Does Pipeline 4's messy/mid corpus reflect what Atlas needs?**
   Atlas's intended use is pre/post-flight scoring research — if the
   downstream signal depends on having polished exemplars, this corpus
   is only 60% of the intended input surface.
