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

This repo ships the pqs-atlas-agent as a self-hosted service provider on the [Virtuals ACP](https://app.virtuals.io/acp/join) marketplace. A buyer initiates a targeted job carrying a `{ prompt, vertical }` schema; the seller runs the PQS pipeline synchronously, returns the resulting `AtlasRow` as the deliverable, and settlement is a real on-chain USDC transfer on Base mainnet captured via Virtuals' x402 payment route.

**Architecture: openclaw-acp CLI (not raw @virtuals-protocol/acp-node SDK).** Beat 2 routes through the Virtuals `openclaw-acp` CLI tool (github.com/Virtual-Protocol/openclaw-acp), not the raw Node SDK. The raw SDK requires a pre-deployed Modular Account V2 smart wallet on Base mainnet, and Virtuals' dashboard-side deploy step is no longer documented (whitepaper URLs 404). The CLI auto-provisions the wallet via Virtuals' backend API (`acpx.virtuals.io`), which is the supported self-hosted path today. This pivot is documented in `openclaw-templates/README.md`.

**Layout:**

- `openclaw-templates/pqs_atlas_score/` — copy-in `offering.json` + `handlers.ts` for the openclaw-acp CLI. `handlers.ts` is a thin adapter that re-exports `executeJobHandler` / `validateRequirementsHandler` / `requestPaymentHandler` from this repo.
- `scripts/virtuals-handler.ts` — the source-of-truth handler logic. Wraps `generateAtlasRow()` behind the openclaw-acp `ExecuteJobResult` contract. Any update to the PQS pipeline lands here, not in the CLI repo.
- `scripts/atlas-agent-resume.sh` — one-shot setup + buy driver. Walks through `acp setup` (browser-blocked), `acp sell init`, template copy, `acp sell create`, `acp serve start` in the background, and finally `acp job create` + poll + pay.
- `src/grade-gate.ts` — reusable grade-gate logic (pre_score.total ≥ 60). Unit-tested via `npm test`. Used by the buyer to decide whether the AtlasRow deliverable was worth paying for.
- `src/virtuals-client.ts`, `scripts/atlas-agent-{serve,buy,doctor}.ts`, `bin/atlas-agent.ts` — retained as a **reference implementation** of the raw-SDK path. They typecheck and can be invoked once Virtuals exposes a wallet-deploy step, but are not the live path.

**Protocol flow via openclaw-acp:**

1. Buyer: `acp job create <seller-wallet> pqs_atlas_score --requirements '{"prompt":"...","vertical":"software"}'` — creates a $0.10 USDC fixed-fee job.
2. Seller runtime (`acp serve start`) receives the request, calls `validateRequirementsHandler` to reject malformed prompts early, then emits the payment request with the message from `requestPaymentHandler`.
3. Buyer: `acp job pay <jobId> --accept true` — settles $0.10 USDC on Base mainnet via ACP's x402 route.
4. Seller runtime calls `executeJobHandler` which runs `generateAtlasRow()` (pre-score + post-score + Opus 4.7 output). The AtlasRow JSON is delivered.
5. Buyer applies `shouldPay` from `src/grade-gate.ts` client-side to audit whether the returned AtlasRow met the B+ gate; rejection → reputational signal, not a refund (payment already settled on-chain in step 3). This is a documented tradeoff of the openclaw-acp fixed-fee flow vs the raw-SDK `payAndAcceptRequirement` pattern.

**CLI.** `scripts/atlas-agent-ship.sh` is the canonical driver. (Earlier
`atlas-agent-resume.sh` is retained for reference; it had two bugs —
bash-portable `read` syntax and underscore-vs-hyphen agent-slug derivation
— that `atlas-agent-ship.sh` fixes.)

```bash
# One-time:
git clone https://github.com/Virtual-Protocol/openclaw-acp ~/Desktop/openclaw-acp
cd ~/Desktop/openclaw-acp && npm install && npm link
acp setup                     # browser OAuth. Creates pqs-atlas-seller agent.
acp agent create pqs-buyer    # second agent for the buyer side.

# From pqs-atlas-agent repo root:
./scripts/atlas-agent-ship.sh seller-up   # copy templates + sell create + serve
./scripts/atlas-agent-ship.sh check       # on-chain balance check via Base RPC
./scripts/atlas-agent-ship.sh buy "write a production-ready onboarding email for a B2B SaaS"
./scripts/atlas-agent-ship.sh status      # active agent + serve + sell list + balances
```

Single-machine flip note: `acp agent switch` stops the seller runtime (the
runtime is bound to the current agent's API key). The `buy` subcommand
handles this automatically: stop-seller → switch-to-buyer → `acp job create
--isAutomated true` → switch-back-to-seller → restart serve → poll until
COMPLETED. `--isAutomated true` means the ACP server auto-accepts payment
on the buyer's behalf when the seller emits the NEGOTIATION memo, so we
don't need to flip back to the buyer for `acp job pay`.

**Implementation note on payment semantics.** The raw brief described the pipeline prepaying PQS $0.025 via an existing x402 MCP tool, but the production pipeline uses a Bearer API key (`PQS_API_KEY`) that represents pre-funded PQS credit — there is no per-call x402 step on the seller side. The real on-chain USDC transaction captured by this integration is the buyer → seller settlement via Virtuals ACP's x402 route, which is what lands on Basescan.

**Live proof of a completed buy.** Job `1003481624` executed end-to-end on Base mainnet (offering `pqs_atlas_score`, pre_score 15/80, post_score 47/60, deliverable emitted, phase=COMPLETED). Settlement is a two-tx flow through the ACP escrow contract (`0xef4364fe4487353df46eb7c811d4fac78b856c7f`):

| Direction | Amount | Tx hash |
|-----------|-------:|---------|
| Buyer → escrow | 0.10 USDC | [`0x90faf432…87d7dc`](https://basescan.org/tx/0x90faf432bffd08b1eb4b7d5a9b4e760f94a42ec7b7faae7d0d83de848c87d7dc) |
| Escrow → seller | 0.08 USDC | [`0x2af9ad30…3bce2efa`](https://basescan.org/tx/0x2af9ad30310f5c4711cb2703368c534853ce0a20a6b87f1b4f0925b93bce2efa) |

The 0.02 USDC delta is Virtuals' 20% protocol fee retained by the escrow. Both txs confirmed in Base blocks 45043695 and 45043732 on 2026-04-24.

**Edge case found during shipping (documented because judges may hit it).** `acp agent switch` kills the seller runtime, so the obvious "switch → create job → switch back" sequence orphans the new job — it lands server-side while the seller socket is offline and no `onNewTask` event is replayed on reconnect. Workaround: POST directly to `https://claw-api.virtuals.io/acp/jobs` with the buyer's `x-api-key` from `~/Desktop/openclaw-acp/config.json`. Seller stays up, job fires live through the socket, deliverable settles immediately. `atlas-agent-ship.sh`'s `buy` subcommand performs the flip-dance for convenience; the direct-API path is recommended for reliability and is the one used for the proven tx above.

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
