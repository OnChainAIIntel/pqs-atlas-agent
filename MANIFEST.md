# PQS Atlas: Submission manifest

**Built with Opus 4.7 hackathon (April 21-26, 2026)**

## The problem

Bad prompts are the root cause of AI underperformance wherever AI touches economic value, but the blame lands on models, orchestration, or tooling because the input is the one signal no one is measuring. A 20-row stratified sample across five public HuggingFace corpora (LMSYS, WildChat, OpenAssistant/oasst2, HuggingFaceH4/no_robots, Open-Orca/OpenOrca) scored 0/20 above F-grade on PQS's 8-dimension rubric, with the entire sample capped at 33 out of 80 (2 points below the D-grade floor). The prompts going into production are underspecified in the same way almost every time, and there is no grader between the human intent and the bill. PQS is the grader.

## The product

PQS is a scoring layer that measures prompt quality before money moves. It runs on two rails in this submission:

- **SaaS rail (atlas-optimize):** humans paying for LLM outputs. Paste a prompt into `pqs.onchainintel.net`, get a score, run the optimize loop, watch the lift, keep the rewritten prompt.
- **x402 rail (atlas-agent):** agents paying for LLM outputs. This repo's `generate-atlas-row` path scores a prompt pre-flight against the PQS rubric, routes the optimize call as a $0.025 USDC x402 payment, and produces a graded AtlasRow (pre-score, Opus 4.7 output, post-score).

Same scoring engine. Same kappa-validated rubric: Opus 4.7 and GPT-4o independently applied the rubric to the same 15 anchors and agreed at weighted κ = 0.89 (full methodology and per-dimension breakdown in `findings/kappa-calibration.md` on `feat/pipeline-5-kappa-calibration`, PR #3). Two populations served. One input quality problem solved in both places.

## Artifacts

1. **500-row deterministic source corpus.** 4 JSONL files × 500 rows = 2000 prompts, drawn from the five HuggingFace sources listed above. Deterministic and seeded-sampled variants, with and without CC-BY-NC. Located at `data/source-prompts-*.jsonl`. Operational record in `docs/pipeline-4-extraction-notes.md`.

2. **Rubric-ceiling finding and the F→B Lift.** Documented in `findings/rubric-ceiling.md`. PQS's 8-dimension rubric correctly rejects unengineered prompts in the wild. The closed-loop `/api/optimize` endpoint then lifts 5/5 WildChat mid seeds from F (avg 22.0/80) to B (avg 66.4/80), an average +44.4-point improvement (76% relative per the findings doc). Evidence: `data/pilots/path2-scoping.jsonl`.

3. **15-anchor kappa calibration.** Deterministic 5F/5D/5B selection, scored by two independent frontier raters (Opus 4.7 direct, GPT-4o direct) under a byte-identical rubric with SHA256 self-check. Opus ↔ GPT-4o = 0.89 weighted kappa (almost perfect) validates the rubric text as rater-agnostic. One anchor (F-01, an encrypted SHA512 blob) triggered an Opus 4.7 refusal at the safety layer and is recorded as a structured `status: "refused"` row rather than being hidden.

4. **Three Claude Skills that ship today.** `/pqs-score`, `/pqs-optimize`, `/pqs-batch`, committed in the sibling `OnChainAIIntel/pqs-claude-commands` repo (installable via `npx skills add`). `/pqs-score` grades a prompt and prints an 8-dimension bar chart plus top fixes. `/pqs-optimize` rewrites below-60 prompts and re-scores until clearance. `/pqs-batch` scores every prompt in a file and emits aggregate metrics.

5. **atlas-optimize deployed surface.** `https://pqs.onchainintel.net`, the SaaS rail. Score, optimize, and view the F→B Lift interactively. Infrastructure hosted in `OnChainAIIntel/prompt-optimization-engine` (not in this repo).

6. **atlas-agent loop (demo-scoped).** This repo. `scripts/generate-atlas-row.ts` and `scripts/generate-atlas-batch.ts` exercise the x402 rail end-to-end with real PQS API calls, real Opus 4.7 generations, and real `/api/optimize` x402 payments on the optimize half of the loop. The full autonomous marketplace-polling agent is a v2 roadmap item (see footer) and is not demoed in this submission.

## Repo layout

| Path | What's there |
|------|--------------|
| `data/` | 500-row source corpora (4 files), pilot evidence, Path-2 scoping, mid-grade verification. Pipeline 5 anchors + rater outputs live on `feat/pipeline-5-kappa-calibration` (PR #3). |
| `findings/rubric-ceiling.md` | Pipeline 4 narrative: why polished=0, the F→B Lift, per-source gradient. |
| `findings/kappa-calibration.md` | Pipeline 5 narrative: two-rater external calibration, Opus ↔ GPT-4o κ = 0.89. |
| `docs/methodology.md` | Scoring methodology, dimensions, grade thresholds. |
| `docs/pipeline-4-extraction-notes.md` | Operational record for corpus extraction. |
| `scripts/generate-atlas-row.ts` | Single-prompt atlas row generator (pre-score, Opus output, post-score). |
| `scripts/generate-atlas-batch.ts` | Batch runner over a source corpus JSONL. |
| `scripts/pipeline-4/` | Corpus extractor, bucket classifier, Path-2 scoping, mid-grade verification. |
| `scripts/pipeline-5/` | Anchor selection, rubric with SHA256 self-check, rater orchestration, kappa computation. |
| `schemas/atlas-row.ts` | Canonical AtlasRow type and grade thresholds (A≥70, B≥60, C≥50, D≥35). |

Skills repo: `https://github.com/OnChainAIIntel/pqs-claude-commands`.

## How a judge sees it

- **Demo video:** [link added with Saturday submission]
- **SaaS rail, deployed:** `https://pqs.onchainintel.net`. Paste a prompt, watch it get scored, run optimize, see the lift.
- **x402 rail, in-repo:** `cp .env.example .env.local`, fill `PQS_API_KEY` and `PQS_INTERNAL_TOKEN`, `npm install`, `npm run generate-atlas-row -- --prompt "your prompt"`. The optimize half routes via x402 at $0.025 USDC per call.
- **GitHub:** `https://github.com/OnChainAIIntel/pqs-atlas-agent`.

## Roadmap (v2, not demoed)

- Marketplace integration for the x402 rail (Daydreams-style adapter so agents discover listings, score inbound prompts, route payments).
- Full autonomous polling loop with persisted state and observable trace.
- `marketplace-agent` Skill: wrap the discovery and action path.
- `managed-agent-session` Skill: wrap durable sessions across multi-prompt tasks.
