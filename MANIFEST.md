# PQS Atlas: Submission manifest

**Built with Opus 4.7 hackathon (April 21-26, 2026)**

## Positioning

PQS is a kappa-calibrated pre-flight quality gate for LLM prompts, built on x402 payment rails. Agents pay per call, get scored per dimension, and know before inference whether the prompt is worth sending.

## The artifacts

Four concrete, shipped deliverables anchor the submission:

1. **500-row deterministic source corpus.** 4 files × 500 rows = 2000 prompts, drawn from 5 public HuggingFace datasets (LMSYS, WildChat, OpenAssistant/oasst2, HuggingFaceH4/no_robots, Open-Orca/OpenOrca). Deterministic and seeded-sampled variants, with and without CC-BY-NC. Located at `data/source-prompts-*.jsonl`.

2. **Rubric-ceiling finding and the F→B Lift.** Documented in `findings/rubric-ceiling.md`. Empirical result: PQS's 8-dimension rubric correctly rejects unengineered prompts (0/20 messy-or-mid rows scored above F; corpus capped at 33/80). The same closed-loop PQS optimize endpoint then lifts 5/5 WildChat mid seeds from F (avg 22.0/80) to B (avg 66.4/80), an average +44.4 point improvement, 76% relative. Evidence: `data/pilots/path2-scoping.jsonl`.

3. **15-row anchor set for inter-rater kappa calibration.** Deterministic 5F/5D/5B selection feeding PQS's reliability calibration (Pipeline 5, Gate A + Gate B). F-band from raw Pipeline 4 corpus, D-band from Anthropic prompt-library paraphrases and Awesome ChatGPT Prompts, B-band from the F→B Lift. [Exact kappa values pending Pipeline 5 completion.]

4. **Five Claude Skills packaged at Thariq depth.** Discrete, composable skills wrapping the PQS score, optimize, and grade paths for use across Claude Code, Claude Desktop, and the Anthropic SDK. [Skill names and canonical location pending final packaging; tracked in the OnChainAIIntel organization.]

## Repo layout

| Path | What's there |
|------|--------------|
| `data/` | 500-row source corpora (4 files, 2000 rows total), pilot evidence, Path-2 scoping (15 rows), mid-grade verification (20 rows) |
| `findings/rubric-ceiling.md` | Core research narrative: why polished=0, why the F→B Lift matters, per-source gradient table |
| `docs/pipeline-4-extraction-notes.md` | Operational record for corpus extraction |
| `docs/methodology.md` | Scoring methodology, dimension lists, canonical grade thresholds |
| `scripts/generate-atlas-row.ts` | Single-prompt atlas row generator (pre-score, Opus output, post-score) |
| `scripts/generate-atlas-batch.ts` | Batch runner over a source corpus JSONL |
| `scripts/pipeline-4/` | Corpus extractor, bucket classifier, Path-2 scoping, mid-grade verification |
| `scripts/pipeline-5/` | Kappa calibration anchor selection + rubric (on `feat/pipeline-5-kappa-calibration`) |
| `schemas/atlas-row.ts` | Canonical AtlasRow type and grade thresholds (A≥70, B≥60, C≥50, D≥35) |

## How a judge sees it

- **Deployed scoring tool:** `https://pqs.onchainintel.net`. Paste a prompt, get a score, optimize it, watch the lift.
- **Demo video:** [link added with Saturday submission]
- **GitHub repo:** `https://github.com/OnChainAIIntel/pqs-atlas-agent`
- **Local reproduction:** `cp .env.example .env.local`, fill `PQS_API_KEY` and `PQS_INTERNAL_TOKEN`, `npm install`, `npm run generate-atlas-row -- --prompt "your prompt"`.

## Scope boundary

This submission ships PQS v1, the SaaS scoring layer. The autonomous atlas agent referenced in earlier positioning is a v2 roadmap item and is not demoed here.
