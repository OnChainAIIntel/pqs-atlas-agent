# pqs-atlas-agent

PQS as a scoring layer for AI prompts, deployed on two rails where economic value moves: SaaS (humans) and x402 (agents).

Built for Anthropic's "Built with Opus 4.7" hackathon, April 21-26, 2026.

## Findings

Open research backing the Synapse hackathon submission (synapse.promptqualityscore.com).

- **`findings/kappa-calibration.md`** — Inter-rater reliability across Opus 4.7, Sonnet, and GPT-4o on adversarial prompts. Headline result: Opus–Sonnet κ=0.79 (substantial agreement, Claude-family alignment); Opus–GPT4o κ=0.06 (slight, near-collapse on adversarial input).
- **`findings/fb-lift-comparison.md`** — F→B Lift comparison between Opus 4.7 and Sonnet on PQS rewrites. Opus 4.7 mean lift +38.4 vs Sonnet +32.4 (n=5, directional).
- **`findings/rubric-ceiling.md`** — Analysis of scoring ceiling effects.
- **`fb-lift-opus.jsonl`** / **`fb-lift-sonnet.jsonl`** — Raw per-prompt experimental data for the F→B Lift comparison.

These findings inform the design of [Synapse](https://synapse.promptqualityscore.com), our entry to Built with Opus 4.7 (Cerebral Valley × Anthropic, April 2026).

## The thesis

Bad prompts are the root cause of AI underperformance wherever AI touches economic value, but the blame lands on models, orchestration, or tooling because the input is the one signal no one is measuring. A 20-row stratified sample across five public HuggingFace corpora (LMSYS, WildChat, OpenAssistant/oasst2, HuggingFaceH4/no_robots, Open-Orca/OpenOrca) scored 0/20 above F-grade on PQS's 8-dimension rubric, with the entire sample capped at 33 out of 80 (2 points below the D-grade floor). The prompts going into production are underspecified in the same way almost every time, and there is no grader between the human intent and the bill. PQS is the grader.

This submission ships PQS as a scoring layer on two rails where economic value moves.

## The two rails

### SaaS rail: atlas-optimize

**Who it serves:** humans paying for LLM outputs.

**What it does:** a web surface at `pqs.onchainintel.net` that scores a pasted prompt against the 8-dimension rubric, shows the per-dimension breakdown, runs the `/api/optimize` loop to rewrite weak prompts, and re-scores. The judge-facing story beat is the F→B Lift: five WildChat mid-bucket prompts graded F before optimize (avg 22.0/80) and B after (avg 66.4/80), an average +44.4-point improvement, 76% relative, 5/5 passing the B threshold. Full evidence in `findings/rubric-ceiling.md` and `data/pilots/path2-scoping.jsonl`.

**Infrastructure:** hosted in the sibling repo `OnChainAIIntel/prompt-optimization-engine`.

### x402 rail: atlas-agent

**Who it serves:** agents paying for LLM outputs.

**What it does:** this repo. `scripts/generate-atlas-row.ts` and `scripts/generate-atlas-batch.ts` exercise the agent-side rail end-to-end. Every prompt is scored pre-flight via `POST /api/score/full` before it hits a model, and the optimize call routes as a $0.025 USDC x402 payment on Base mainnet via the `mcp__pqs__optimize_prompt` MCP tool (the `/api/optimize` HTTP endpoint is origin-locked). Output is a graded `AtlasRow` with pre-score, Opus 4.7 output, post-score, and dimension-level rationales.

**What's demo-scoped:** the x402 rail in this repo runs as a CLI-triggered batch, not a fully autonomous polling loop. The marketplace-polling, decision-making, action-taking agent is v2 roadmap and is not demoed here.

## The scoring substrate

PQS grades any LLM prompt on eight dimensions: clarity, specificity, context, constraints, output format, role definition, examples, chain-of-thought structure. Each is scored 1-10. Total in [8, 80], grade cutoffs A≥70, B≥60, C≥50, D≥35, F<35. The rubric cites five academic frameworks (PEEM, RAGAS, MT-Bench, G-Eval, ROUGE).

**Rubric calibration.** Fifteen anchors (5 F-band, 5 D-band, 5 B-band), deterministic selection (SEED=42), scored by two independent frontier raters under a byte-identical rubric with a SHA256 self-check before every call:

| Pair | Weighted κ | Landis-Koch label |
|------|-----------:|-------------------|
| Opus 4.7 ↔ GPT-4o | **0.89** | almost perfect |

Two independent frontier models applying the same text rubric to the same 15 prompts converged at κ = 0.89. The rubric is reliable and rater-agnostic. Full per-dimension table in `findings/kappa-calibration.md`.

**F-01 refusal footnote.** One of the 15 anchors is a 4323-character encrypted SHA512 blob with a "decode this" instruction. Opus 4.7 declined to score it (`stop_reason: refusal`); GPT-4o proceeded to grade it. The rater infrastructure surfaced the refusal as a structured row with null dimensions rather than hiding the call. The pair computation uses n=14.

## Opus 4.7's role in the product (not in the calibration)

The kappa calibration uses Opus 4.7 as one of two independent auditors to validate that the rubric is learnable. That part is rater-infrastructure. Where Opus 4.7 earns its place in the submission itself:

- **Optimization lift on atlas-optimize.** Every F→B Lift generation routes through Opus 4.7 as the rewriter. A judge pasting a weak prompt on `pqs.onchainintel.net` is watching Opus 4.7 produce the optimized version in real time. 5/5 measured lift on the Arm 3 pilot set.
- **Task reasoning on atlas-agent.** The agent rail uses Opus 4.7 for the task-execution half of each AtlasRow (the `opus_output` field). Extended thinking traces are captured in the row when the judge drills in.

## Quickstart

```bash
cp .env.example .env.local
# Fill PQS_API_KEY (format: PQS_<base64>, pqs_live_<base64>, or orbis_<hex>).
# Fill PQS_INTERNAL_TOKEN so atlas traffic is flagged is_internal=true
# in analytics. The scoring endpoints ignore this token for auth.

npm install

# Score one prompt, call Opus 4.7, post-score, emit an AtlasRow.
npm run generate-atlas-row -- --prompt "your prompt here" --vertical general

# Batch over a source corpus.
npm run generate-atlas-batch -- --input data/source-prompts-full-deterministic.jsonl
```

No local model deploy. All scoring, optimization, and model calls route through `pqs.onchainintel.net`.

## Demo path

Two beats, 2-4 minutes of video, one deployed URL for the judge to click.

**Beat 1 (SaaS rail, atlas-optimize).** Judge pastes a weak prompt into `pqs.onchainintel.net`. PQS scores it F. Judge triggers optimize. Opus 4.7 rewrites. PQS re-scores the rewrite at B. F→B lift visible on screen in real time.

**Beat 2 (x402 rail, atlas-agent).** Terminal recording. Script pulls a task, scores the inbound prompt via `/api/score/full`. If the score clears threshold, the $0.025 USDC x402 optimize payment routes and the agent proceeds. If it does not clear, the payment does not clear either. Gate function visible.

**Deployed URL (post-submission):** `https://pqs.onchainintel.net`.

## Built with

- Claude Opus 4.7 for optimize generations, agent task reasoning, and one of two independent calibration raters
- OpenAI GPT-4o as the second independent calibration rater (rubric validation only, not product path)
- Claude Code with auto mode, plus three PQS Skills: `/pqs-score`, `/pqs-optimize`, `/pqs-batch`
- PQS v2 scoring infrastructure at `pqs.onchainintel.net`
- x402 payment rails for programmatic optimize calls on Base mainnet
- Python 3.12 for corpus extraction and calibration, TypeScript 5 for atlas row generation

## Origin / build process

We used PQS to score the prompt we used to audit our own agent during the build. The audit told us to drop the agent as primary surface and reframe the submission around two rails. The scoring layer you see here is what the audit preserved. The full autonomous agent is v2.

## Roadmap (v2, not demoed)

- Marketplace integration for the x402 rail via a Daydreams-style adapter: agents discover listings, score inbound prompts, route payments, act.
- Full autonomous polling loop with persisted session state and an observable trace surface for judges or auditors.
- `marketplace-agent` Skill wrapping the discovery and action path.
- `managed-agent-session` Skill wrapping durable multi-prompt sessions.

## License

MIT
