# Pipeline 6 — Output-Scoring Atlas Executor

Generates the PQS output-scoring atlas: for each prompt in the Pipeline 4
software source corpus, score the prompt (8-dim pre-flight), generate an
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

1. **Session A endpoint deployed.** Pipeline 6's full `/api/score-output` path
   depends on the `prompt-optimization-engine` Session A PR being merged and
   deployed. Until then, point `PQS_OUTPUT_SCORE_URL` at a local dev URL
   (`http://localhost:3000/api/score-output`). Note: `score_output_full()` is
   used for observability only — atlas rows do **not** depend on this endpoint
   (per-dimension judge attribution comes from the direct judge calls).
2. **`PQS_INTERNAL_BEARER`** set — the internal PQS API key sent as
   `Authorization: Bearer <token>`. Bypasses the x402 paywall on `/api/score`
   and authenticates the internal `/api/score-output` endpoint.
3. **`ANTHROPIC_API_KEY`** set — for the Haiku 4.5 generator and cloud judge.
4. **Ollama running on CRYPTOMINER** at `OLLAMA_HOST` (default
   `http://192.168.1.205:11434`) with `gemma2:9b` pulled (`ollama pull gemma2:9b`).
5. **Python deps:** `pip install -r pipelines/pipeline-6/requirements.txt`
   (`anthropic`, `scikit-learn`, `scipy`).

Set environment variables in a repo-root `.env.atlas` file (see
`.env.atlas.example`) or export them directly. Optionally set
`PQS_INTERNAL_TOKEN` — when present it is sent as the `X-PQS-Internal`
observability header so atlas traffic is flagged `is_internal=true` in
`pqs_api_calls`.

### Configurable env vars

| Var | Default | Purpose |
|---|---|---|
| `PQS_INTERNAL_BEARER` | — (required) | Bearer token for `/api/score` + `/api/score-output` |
| `ANTHROPIC_API_KEY` | — (required) | Haiku generator + cloud judge |
| `PQS_PROMPT_SCORE_URL` | `https://pqs.onchainintel.net/api/score` | Pre-flight prompt scoring |
| `PQS_OUTPUT_SCORE_URL` | `https://pqs.onchainintel.net/api/score-output` | Full output scoring (observability) |
| `OLLAMA_HOST` | `http://192.168.1.205:11434` | CRYPTOMINER Ollama host |
| `OLLAMA_MODEL` | `gemma2:9b` | Local judge model |
| `PQS_SOURCE_CORPUS` | `pipelines/pipeline-4/outputs/source-corpus-software.jsonl` | Input corpus |
| `PQS_ATLAS_OUTPUT` | `pipelines/pipeline-6/outputs/atlas-software.jsonl` | Atlas output |
| `PQS_INTERNAL_TOKEN` | — (optional) | `X-PQS-Internal` observability flag |

> **Source corpus note.** The default `PQS_SOURCE_CORPUS` is the spec path.
> Pipeline 4's current deliverables sit under `data/` (e.g.
> `data/source-prompts-clean-sampled.jsonl`). Set `PQS_SOURCE_CORPUS` to the
> real software-vertical corpus before running.

## Execution order

```
1. python pipelines/pipeline-6/kappa_phase_0.py     # Phase 0 calibration
2. (Ken reviews outputs/kappa-phase-0.md)           # amend JUDGE_CONFIG if FAIL
3. python pipelines/pipeline-6/score_outputs.py     # build the atlas
4. python pipelines/pipeline-6/correlation.py       # post-run analysis
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
  "prompt_id": "string",
  "prompt_text": "string",
  "vertical": "software",
  "generator_model": "claude-haiku-4-5-20251001",
  "output_text": "string",
  "prompt_score": { "total": 0, "out_of": 80, "grade": "A", "dimensions": {} },
  "output_score": {
    "total": 0, "out_of": 60, "grade": "A",
    "dimensions": { "<dim>": { "score": 0, "reasoning": "", "judge_model": "" } }
  },
  "timestamp": "ISO-8601",
  "pipeline_version": "pipeline-6-v1.0"
}
```

Rows where the generator refuses carry `output_score: null` and a
`skipped_reason` field so they stay auditable; `correlation.py` excludes them.
