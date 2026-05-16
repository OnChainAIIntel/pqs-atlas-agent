# Pipeline 6 — Output-Scoring Atlas Executor

Generates the PQS output-scoring atlas: for each prompt in the Pipeline 4
source corpus, score the prompt (8-dim pre-flight), generate an
output with Haiku 4.5, score that output on the 6-dimension post-flight
rubric, and write one atlas row per prompt to JSONL.

The post-flight rubric is scored by a **hybrid judge**: a local Gemma 4 9B
model (`gemma2:9b` on the CRYPTOMINER box) handles the 4 structural
dimensions; Haiku 4.5 handles the 2 knowledge dimensions. A Phase 0 kappa
calibration validates that the local judge agrees with Haiku before the full
run commits to it.

## Purpose

Answer one question with data: **does the PQS pre-flight prompt score predict
output quality?** Pipeline 6 produces the atlas; `correlation.py` produces the
headline correlation number.

## Files

| File | Role |
|---|---|
| `config.py` | Judge routing (`JUDGE_CONFIG`), model IDs, endpoint URLs, paths, the 6-dim rubric |
| `local_judge.py` | Ollama Gemma 4 9B client — one structural dimension per call |
| `cloud_judge.py` | Haiku 4.5 — generator, single-dim knowledge judge, full `/api/score-output` call |
| `kappa_phase_0.py` | Phase 0 calibration — must pass before the full run |
| `score_outputs.py` | Main executor — builds the atlas |
| `correlation.py` | Post-run analysis — Pearson/Spearman + grade matrix |

## Prerequisites

1. **Output-scoring endpoint deployed.** Pipeline 6's full `/api/score-output`
   path is delivered by `prompt-optimization-engine` PR #73 (merged), which
   imports its rubric from `lib/pqs-output-rubric.js`. For local dev, point
   `PQS_OUTPUT_SCORE_URL` at `http://localhost:3000/api/score-output`. Note:
   `score_output_full()` is used for observability only — atlas rows do **not**
   depend on this endpoint (per-dimension judge attribution comes from the
   direct judge calls).
2. **`PQS_INTERNAL_BYPASS_KEY`** set — the internal bypass key sent as the
   single `x-pqs-internal-bypass` header on `/api/score` calls only.
   Production `middleware.js` validates it against the `PQS_PARTNER_BYPASS_KEYS`
   partner-key registry (with `PQS_INTERNAL_BYPASS_KEY` as the legacy
   single-key fallback); on a match it sets `x-pqs-bypass-verified`, which the
   route handler reads to skip the x402 paywall on `/api/score`.
3. **`PQS_API_KEY`** set — a `PQS_*` API key sent as an `Authorization: Bearer`
   header on `/api/score-output` calls. That endpoint does **not** honor the
   middleware bypass; it validates the Bearer key against the `pqs_api_keys`
   Supabase table (route comment: "No x402 payment path — this endpoint is
   internal-use"). The bypass header returns HTTP 401 here.
4. **`ANTHROPIC_API_KEY`** set — for the Haiku 4.5 generator and cloud judge.
5. **Ollama running on CRYPTOMINER** at `OLLAMA_HOST` (default
   `http://192.168.1.205:11434`) with `gemma2:9b` pulled (`ollama pull gemma2:9b`).
6. **Python deps:** `pip install -r scripts/pipeline-6/requirements.txt`
   (`anthropic`, `scikit-learn`, `scipy`).

Set environment variables in a repo-root `.env.atlas` file or export them
directly. Pipeline 6 uses **two auth paths** for the two scoring endpoints:
`/api/score` (pre-flight prompt scoring) takes the middleware-bypass path —
`PQS_INTERNAL_BYPASS_KEY` sent as `x-pqs-internal-bypass`, which `middleware.js`
validates against the partner-key registry to admit the request past the x402
paywall. `/api/score-output` (post-flight output scoring) takes the Bearer
path — `PQS_API_KEY` sent as `Authorization: Bearer`, validated against the
`pqs_api_keys` table. The output endpoint does not honor the bypass header.

### Configurable env vars

| Var | Default | Purpose |
|---|---|---|
| `PQS_INTERNAL_BYPASS_KEY` | — (required) | Bypass key sent as `x-pqs-internal-bypass` header on `/api/score`; middleware validates against `PQS_PARTNER_BYPASS_KEYS` registry or legacy `PQS_INTERNAL_BYPASS_KEY` env var |
| `PQS_API_KEY` | — (required) | API key sent as `Authorization: Bearer` header to `/api/score-output`; validated against `pqs_api_keys` Supabase table |
| `ANTHROPIC_API_KEY` | — (required) | Haiku generator + cloud judge |
| `PQS_PROMPT_SCORE_URL` | `https://pqs.onchainintel.net/api/score` | Pre-flight prompt scoring |
| `PQS_OUTPUT_SCORE_URL` | `https://pqs.onchainintel.net/api/score-output` | Full output scoring (observability) |
| `OLLAMA_HOST` | `http://192.168.1.205:11434` | CRYPTOMINER Ollama host |
| `OLLAMA_MODEL` | `gemma2:9b` | Local judge model |
| `PQS_SOURCE_CORPUS` | `data/source-prompts-clean-deterministic.jsonl` | Input corpus |
| `PQS_ATLAS_OUTPUT` | `scripts/pipeline-6/outputs/atlas-software.jsonl` | Atlas output |

> **Source corpus note.** `PQS_SOURCE_CORPUS` defaults to
> `data/source-prompts-clean-deterministic.jsonl` — the tracked 500-row
> Pipeline 4 deliverable. Override it to target a different corpus.

## Execution order

```
1. python scripts/pipeline-6/kappa_phase_0.py       # Phase 0 calibration
2. (Ken reviews outputs/kappa-phase-0.md)           # amend JUDGE_CONFIG if FAIL
3. python scripts/pipeline-6/score_outputs.py       # build the atlas
4. python scripts/pipeline-6/correlation.py         # post-run analysis
```

Run every script from the repo root.

**Step 1 — Phase 0 kappa calibration.** Scores 50 prompts on the 4 structural
dimensions with both judges and computes Cohen's weighted kappa per dimension.
Exit 0 if all 4 dimensions clear kappa ≥ 0.70; exit 1 if any fail. The report
lands at `outputs/kappa-phase-0.md`.

**Step 2 — review.** If Phase 0 exits 1, edit `JUDGE_CONFIG` in `config.py`
and set each failed dimension to `"haiku"`, then re-run nothing — proceed to
step 3. If Phase 0 exits 0, no change is needed.

**Step 3 — build the atlas.** Scores all 500 prompts and writes atlas rows to
`outputs/atlas-software.jsonl`.

**Step 4 — analysis.** Computes the prompt-score ↔ output-score correlation
and writes `findings/output-correlation.md` with the headline number.

## Cost + wall-time expectations

| Phase | Cost target | Wall-time target |
|---|---|---|
| Phase 0 (kappa, n=50) | < $15 | < 90 min |
| Full atlas (n=500) | < $50 | < 12 h |
| `correlation.py` | $0 (local) | seconds |

Local Gemma judge calls have no API cost. Cost is Haiku generation plus Haiku
cloud-judge calls. Phase 0 cost and wall time are recorded in
`outputs/kappa-phase-0.md`.

## Resuming after failure

Both executors are resumable — re-run the same command:

- `kappa_phase_0.py` caches per-prompt judge results to
  `outputs/kappa-phase-0-raw.jsonl`. A re-run reuses cached prompts and only
  scores the rest, then recomputes kappa over the full set.
- `score_outputs.py` skips any `prompt_id` already present in
  `outputs/atlas-software.jsonl`. A re-run only processes the remainder.
  Failed prompts within a batch are logged and left for the next run.

## Output / where the dataset publishes

- `outputs/kappa-phase-0.md` — Phase 0 calibration report (committed after the
  first run for review).
- `outputs/atlas-software.jsonl` — the atlas dataset. Generated output;
  gitignored by default. The selected final dataset is committed separately
  once a run is reviewed — link it here after the first successful run.
- `findings/output-correlation.md` — the correlation analysis and headline
  number.

### Atlas row schema

```json
{
  "prompt_id": "string (= source_row_id)",
  "prompt_text": "string (= prompt)",
  "vertical": "string (= vertical_source_label)",
  "generator_model": "claude-haiku-4-5-20251001",
  "output_text": "string",
  "prompt_score": { "total": 0, "out_of": 80, "grade": "A", "dimensions": {} },
  "output_score": {
    "total": 0, "out_of": 60, "grade": "A",
    "dimensions": { "<dim>": { "score": 0, "reasoning": "", "judge_model": "" } }
  },
  "timestamp": "ISO-8601",
  "pipeline_version": "pipeline-6-v1.0",
  "provenance": {
    "source_dataset": "string",
    "source_split": "string",
    "quality_bucket": "string",
    "word_count": 0,
    "license_flag": "string",
    "sampling_method": "string",
    "sampling_seed": 0
  }
}
```

`provenance` carries the Pipeline 4 corpus columns straight through for
downstream analysis. Rows where the generator refuses carry `output_score:
null` and a `skipped_reason` field so they stay auditable; `correlation.py`
excludes them.

---

## Optimize-lift atlas — measuring PQS optimization impact

The first atlas answers "does the pre-flight prompt score predict output
quality?" The answer on Haiku 4.5 was r=0.02 — a finding about Haiku's
robustness, not about PQS. PQS's actual product is the **optimize transform**.
The optimize-lift atlas tests that product thesis head-on: **does running a
prompt through PQS optimization produce a better output?**

| File | Role |
|---|---|
| `optimize_lift.py` | Executor — builds the optimize-lift atlas |
| `optimize_lift_correlation.py` | Analysis — mean lift, transition matrix, headline |

### What it measures

For every prompt in the first atlas (`outputs/atlas-software.jsonl`):

1. POST the original prompt to **`/api/score/full`** (`x-pqs-internal-bypass`
   auth). The endpoint returns the original prompt + score + output **and** the
   Sonnet-4.6-rewritten optimized prompt + score + output (both outputs
   generated by Haiku 4.5), plus `improvement_pct` and an `explanation`.
2. POST the optimized **output** to **`/api/score-output`**
   (`Authorization: Bearer` auth) for a 6-dimension post-flight output score.
3. Reuse the **original** output and original output score straight from the
   first atlas row — no recompute.
4. Compute the lift:
   - `output_lift_points = optimized_output_score.total - original_output_score.total`
   - `prompt_lift_points = optimized_prompt_score.total - original_prompt_score.total`

Headline: _"Across n=500 prompts, PQS optimization lifts output quality by X
points on average."_

The executor never modifies a first-atlas file. It reuses `load_env()` from
`score_outputs.py`, `score_output_full()` from `cloud_judge.py`, and the
endpoint / auth / grade helpers from `config.py`. First-atlas rows whose
`output_score` is null (a first-atlas generator refusal) cannot yield an output
lift — they are skipped, logged, and counted, never written as a row.

### How to run

```
1. python scripts/pipeline-6/optimize_lift.py              # build the atlas
2. python scripts/pipeline-6/optimize_lift_correlation.py  # analysis + headline
```

Run both from the repo root, after `score_outputs.py` has produced
`outputs/atlas-software.jsonl`. The executor is resumable — `prompt_id`s
already present in `outputs/atlas-optimize-lift.jsonl` are skipped on re-run,
and failed prompts are logged and left for the next run. Prompts are processed
in parallel batches of `BATCH_SIZE` (10).

`/api/score/full` uses the same `x-pqs-internal-bypass` auth as `/api/score`.
`/api/score-output` requires `PQS_API_KEY` as an `Authorization: Bearer` header
(it does not honor the bypass) — set it in `.env.atlas` alongside the other
keys before running.

| Var | Default | Purpose |
|---|---|---|
| `PQS_SCORE_FULL_URL` | `<PQS_PROMPT_SCORE_URL>/full` | Optimize-transform endpoint |

### Cost expectations

| Phase | Cost target | Wall-time target |
|---|---|---|
| Optimize-lift atlas (n=500) | ~$25 | < 6 h |
| `optimize_lift_correlation.py` | $0 (local) | seconds |

One `/api/score/full` call per prompt (~$0.005) drives ~$2.50 of PQS-side
cost; the rest is the Anthropic-side Haiku generation the endpoint runs
server-side. `/api/score-output` is free under internal auth.

### Outputs

- `outputs/atlas-optimize-lift.jsonl` — the optimize-lift atlas. Generated
  output; gitignored by default.
- `findings/output-optimize-lift.md` — the optimize-lift analysis and headline.

### Optimize-lift row schema

```json
{
  "prompt_id": "string",
  "prompt_text": "string (original)",
  "vertical": "string",
  "original_prompt_score": { "total": 0, "out_of": 80, "grade": "F", "dimensions": {} },
  "original_output_text": "string",
  "original_output_score": { "total": 0, "out_of": 60, "grade": "A", "dimensions": {} },
  "optimized_prompt": "string",
  "optimized_prompt_score": { "total": 0, "out_of": 80, "grade": "A", "percentile": 0, "dimensions": {} },
  "optimized_output_text": "string",
  "optimized_output_score": { "total": 0, "out_of": 60, "grade": "A", "dimensions": {}, "rationales": {} },
  "prompt_lift_points": 0,
  "output_lift_points": 0,
  "improvement_pct": 0,
  "explanation": "string",
  "timestamp": "ISO-8601",
  "pipeline_version": "pipeline-6-optimize-lift-v1.0"
}
```
