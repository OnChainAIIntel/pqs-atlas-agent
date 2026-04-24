# openclaw-acp offering templates

Pre-built `offering.json` + `handlers.ts` for the `pqs_atlas_score` Virtuals ACP v2 offering, ready to copy into the openclaw-acp CLI repo.

## Why this exists

Beat 2 (the Virtuals marketplace integration) routes through `openclaw-acp` (github.com/Virtual-Protocol/openclaw-acp) instead of the raw `@virtuals-protocol/acp-node` SDK. The raw SDK requires a pre-deployed Modular Account V2 smart wallet on Base mainnet, and Virtuals' deploy step is no longer documented in the whitepaper (URLs 404). The CLI auto-provisions the wallet via Virtuals' backend API, so it's the supported self-hosted path today.

These template files cannot be committed into a pre-initialized offering directory in the openclaw-acp repo because `acp sell init` writes into `src/seller/offerings/<agent-name>/<offering>/`, and `<agent-name>` is only known after `acp setup` picks/creates an agent. So the files live here, and the morning resume script (`scripts/atlas-agent-resume.sh`) copies them over.

## Files

- `pqs_atlas_score/offering.json` — offering descriptor. `jobFee: 0.1` USDC, `jobFeeType: "fixed"`, `requiredFunds: false`. `requirement` schema accepts `{ prompt: string, vertical?: Vertical }`.
- `pqs_atlas_score/handlers.ts` — adapter. Re-exports `executeJob`, `validateRequirements`, `requestPayment` from `pqs-atlas-agent/scripts/virtuals-handler.ts` (absolute path).

## Resume flow

See `scripts/atlas-agent-resume.sh` in the repo root. It walks through `acp setup` (browser-blocked, Ken runs), `acp sell init pqs_atlas_score`, file copy, `acp sell create`, `acp serve start`, `acp job create`, `acp job pay`.
