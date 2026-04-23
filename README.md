# pqs-atlas-agent

PQS as a scoring layer for AI prompts, deployed on two rails where economic value moves: SaaS (humans) and x402 (agents).

Built for Anthropic's "Built with Opus 4.7" hackathon, April 21-26, 2026.

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

**What's demo-scoped:** the x402 rail in this repo runs as a CLI-triggered batch. The Virtuals ACP v2 marketplace integration (below) extends this into a live provider that a buyer can transact with for real USDC on Base mainnet. The fully autonomous polling loop is v2 roadmap.

### Virtuals ACP v2 marketplace integration (Beat 2)

This repo also ships the pqs-atlas-agent as a self-hosted service provider on the [Virtuals ACP](https://app.virtuals.io/acp/join) marketplace. A buyer initiates a targeted job carrying a `{ prompt, vertical }` schema; the seller runs the PQS pipeline synchronously, surfaces the resulting `AtlasRow` in the NEGOTIATION requirement memo so the buyer can grade-gate payment, and the buyer calls `payAndAcceptRequirement()` only when `pre_score.total >= 60` (grade B or above). Settlement is a real on-chain USDC transfer on Base mainnet via the x402 payment route; the tx hash is captured in the buyer script's output and viewable on Basescan.

**Protocol flow (skip-evaluation pattern, evaluator = `undefined`):**

1. `REQUEST → NEGOTIATION`: buyer initiates job with `{ prompt, vertical }` schema and $0.10 USDC escrow. Seller's `onNewTask` handler runs `generateAtlasRow()`, calls `job.accept()`, then `job.createRequirement()` with the full `AtlasRow` JSON as the memo content.
2. `NEGOTIATION → TRANSACTION`: buyer's handler parses the `AtlasRow` from the requirement, checks `pre_score.total`, and calls `payAndAcceptRequirement()` on B+ or `reject()` otherwise.
3. `TRANSACTION → EVALUATION`: seller re-emits the `AtlasRow` via `job.deliver()` as the canonical on-chain deliverable.
4. `EVALUATION → COMPLETED`: auto-completes under the skip-evaluation pattern.

**CLI:**

```bash
cp .env.example .env.local
# Fill the VIRTUALS_* vars (seller + buyer wallet, private key, entity ID).
npm install

# Start the seller listener (agent ONLINE on the Virtuals dashboard).
npm run serve

# In a second terminal, initiate a targeted grade-gated job.
npm run buy -- "write a production-ready onboarding email for a B2B SaaS"

# Print resolved env + wallets without touching the network.
npm run status
```

The underlying scripts (`scripts/atlas-agent-serve.ts`, `scripts/atlas-agent-buy.ts`) and the thin SDK wrapper (`src/virtuals-client.ts`) implement the protocol flow above. The CLI wrapper (`bin/atlas-agent.ts`) forwards to them.

**Implementation note on payment semantics.** The raw brief described the pipeline prepaying PQS $0.025 via an existing x402 MCP tool, but the production pipeline uses a Bearer API key (`PQS_API_KEY`) that represents pre-funded PQS credit — there is no per-call x402 step on the seller side. The real on-chain USDC transaction captured by this integration is the buyer → seller settlement via Virtuals ACP's x402 route, which is what lands on Basescan.

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

- Autonomous polling loop on top of the Virtuals ACP integration: agent self-selects inbound prompts, scores, routes payments, acts without CLI invocation.
- Persisted session state and an observable trace surface for judges or auditors.
- `marketplace-agent` Skill wrapping the discovery and action path.
- `managed-agent-session` Skill wrapping durable multi-prompt sessions.

## License

MIT
