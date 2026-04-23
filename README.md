# pqs-atlas-agent

PQS v2: kappa-calibrated pre-flight quality gate for LLM prompts, built on x402 payment rails.

Built for Anthropic's "Built with Opus 4.7" hackathon, April 21-26, 2026.

## The problem

The AI input quality problem is that most prompts hitting LLMs in production are underspecified. They lack role, context, output format, and scaffolding. Models burn tokens recovering from ambiguity that could have been fixed upstream, and nobody has a reliable way to grade a prompt before inference.

A 20-row stratified sample of messy-and-mid prompts from five public HuggingFace corpora scored 0/20 above F-grade on PQS's 8-dimension rubric, with the entire sample capped at 33 out of 80 (2 points below the D-grade floor). In the wild, prompt quality is not graded because there is no grader. PQS is the grader.

## The solution

PQS scores any LLM prompt on eight dimensions: clarity, specificity, context, constraints, output format, role definition, examples, chain-of-thought structure. It returns a grade (A through F), a total out of 80, per-dimension scores, and rationales. The same API then optimizes weak prompts end-to-end via a closed loop.

On a five-seed demonstration, WildChat mid-bucket prompts that initially graded F (average 22.0/80) lifted to B (average 66.4/80) after one optimize call. Average +44.4 point improvement. 76% relative. 5/5 passing the B threshold. That closed loop is the submission: identify a weak prompt, optimize it, verify the lift, and produce calibration-ready anchor rows along the way.

The calibration half closes the other end. A 15-row anchor set (5F, 5D, 5B) feeds inter-rater kappa calibration so that scores reported on the deployed tool carry a known reliability floor, not a vibes check.

## Architecture

- **PQS scoring engine** (deployed at `pqs.onchainintel.net`): 8-dimension pre-flight rubric via `POST /api/score/full`, 6-dimension post-flight rubric via `POST /api/atlas/score/output`, and closed-loop prompt optimization via `/api/optimize` (accessed programmatically through the `mcp__pqs__optimize_prompt` MCP tool at $0.025 USDC per call via x402).
- **Atlas corpus**: 500-row deterministic source corpora (4 files, 2000 rows total) drawn from LMSYS, WildChat, OpenAssistant/oasst2, HuggingFaceH4/no_robots, and Open-Orca/OpenOrca. Stored as JSONL at `data/source-prompts-*.jsonl`.
- **Rubric calibration**: 15-row anchor set (5F + 5D + 5B) for inter-rater kappa. F-band from the raw Pipeline 4 corpus, D-band from Anthropic prompt-library paraphrases and Awesome ChatGPT Prompts, B-band from the F→B Lift.
- **Skills layer**: five Claude Skills wrapping score, optimize, and grade paths for Claude Code, Claude Desktop, and the Anthropic SDK.

## Quickstart

```bash
cp .env.example .env.local
# Fill PQS_API_KEY (format: PQS_<base64>, pqs_live_<base64>, or orbis_<hex>).
# Fill PQS_INTERNAL_TOKEN so atlas traffic is flagged is_internal=true in
# analytics (not a customer key; the scoring endpoints ignore it for auth).

npm install

# Score a single prompt and generate one atlas row (pre-score, Opus output, post-score).
npm run generate-atlas-row -- --prompt "your prompt here" --vertical general

# Batch-score a source corpus.
npm run generate-atlas-batch -- --input data/source-prompts-full-deterministic.jsonl
```

No local model deploy required. All scoring and optimization routes through `pqs.onchainintel.net`.

## Demo

[Video link added with Saturday submission.]

Live deployed tool for judges: `https://pqs.onchainintel.net`. Paste a prompt, watch it get scored, run the optimize loop, see the lift.

## Built with

- Claude Opus 4.7 for scoring, optimization, and all LLM-in-the-loop work
- Claude Code with auto mode + five PQS Skills
- PQS v2 scoring infrastructure at `pqs.onchainintel.net`
- x402 payment rails for programmatic optimize calls
- Python 3.12 for corpus extraction, TypeScript 5 for atlas row generation
- PostgreSQL + Supabase for the scoring backend (infrastructure, not in this repo)

## Origin / build process

We used PQS to score the prompt we used to audit our own agent, and the audit told us to drop the agent. The SaaS scoring layer is what you see here. The agent is v2.

## License

MIT
