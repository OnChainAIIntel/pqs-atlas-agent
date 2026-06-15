#!/usr/bin/env bash
#
# atlas-agent-resume.sh — morning resume script for Ken.
#
# After Claude paused (waiting for browser OAuth), this script walks through
# the remaining steps to ship Beat 2 end-to-end:
#
#   1. Verify openclaw-acp exists at ~/Desktop/openclaw-acp
#   2. Prompt Ken to run `acp setup` in the openclaw-acp directory (browser)
#   3. Capture the agent name chosen
#   4. acp sell init pqs_atlas_score
#   5. Copy template offering.json + handlers.ts into place
#   6. acp profile update description ...
#   7. acp sell create pqs_atlas_score
#   8. acp serve start (background, via nohup)
#   9. On a second invocation (--buy), run acp job create + monitor + pay
#
# Usage:
#   ./scripts/atlas-agent-resume.sh setup
#   ./scripts/atlas-agent-resume.sh buy "<prompt>" [vertical]
#   ./scripts/atlas-agent-resume.sh status
#
# The script is idempotent — re-running skips already-completed steps.

set -u

REPO_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
OPENCLAW_ROOT="${HOME}/Desktop/openclaw-acp"
TEMPLATES_DIR="${REPO_ROOT}/openclaw-templates/pqs_atlas_score"
OFFERING_NAME="pqs_atlas_score"
STATE_FILE="${REPO_ROOT}/.openclaw-state.json"

# zsh-friendly color output (Ken runs zsh)
RED=$'\033[31m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
BLUE=$'\033[34m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

say() { printf '%s[atlas-resume]%s %s\n' "$BLUE" "$RESET" "$1"; }
ok()  { printf '%s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn(){ printf '%s⚠ %s%s\n' "$YELLOW" "$1" "$RESET"; }
die() { printf '%s✗ %s%s\n' "$RED" "$1" "$RESET" >&2; exit 1; }

require_openclaw() {
  [ -d "$OPENCLAW_ROOT" ] || die "openclaw-acp not found at $OPENCLAW_ROOT. Run: git clone https://github.com/Virtual-Protocol/openclaw-acp ~/Desktop/openclaw-acp && cd ~/Desktop/openclaw-acp && npm install && npm link"
  command -v acp >/dev/null 2>&1 || die "acp CLI not on PATH. Run: cd ~/Desktop/openclaw-acp && npm link"
  ok "openclaw-acp present at $OPENCLAW_ROOT"
  ok "acp CLI on PATH: $(command -v acp)"
}

require_pqs_env() {
  [ -f "${REPO_ROOT}/.env.local" ] || die "pqs-atlas-agent/.env.local missing (PQS_API_KEY, PQS_INTERNAL_TOKEN)"
  grep -q '^PQS_API_KEY=' "${REPO_ROOT}/.env.local" || die "PQS_API_KEY missing in .env.local"
  grep -q '^PQS_INTERNAL_TOKEN=' "${REPO_ROOT}/.env.local" || die "PQS_INTERNAL_TOKEN missing in .env.local"
  ok "PQS env present"
}

cmd_setup() {
  require_openclaw
  require_pqs_env

  say "Step 1: log in + select/create the seller agent"
  echo ""
  echo "  Run this in a SEPARATE terminal (it opens your browser):"
  echo ""
  echo "    cd ${OPENCLAW_ROOT} && acp setup"
  echo ""
  echo "  - Choose or create an agent (any name, e.g. 'pqs-atlas')"
  echo "  - Skip the token-launch prompt (type 'n' when asked)"
  echo "  - Note the agent name you picked — you'll type it below"
  echo ""
  read -r "?  Press Enter AFTER acp setup completes: " _
  echo ""

  # Pull the active agent name from openclaw-acp's config.json
  if [ ! -f "${OPENCLAW_ROOT}/config.json" ]; then
    die "openclaw-acp/config.json missing — acp setup didn't complete"
  fi

  AGENT_NAME=$(node -e 'const c=require("'"$OPENCLAW_ROOT"'/config.json"); const a=(c.agents||[]).find(x=>x.active); if(!a){process.exit(2)}; console.log(a.name);')
  AGENT_WALLET=$(node -e 'const c=require("'"$OPENCLAW_ROOT"'/config.json"); const a=(c.agents||[]).find(x=>x.active); if(!a){process.exit(2)}; console.log(a.walletAddress);')
  [ -n "$AGENT_NAME" ] || die "Could not read active agent from config.json"
  ok "active agent: $AGENT_NAME"
  ok "wallet: $AGENT_WALLET"

  # Save state for buy subcommand
  cat > "$STATE_FILE" <<EOF
{
  "agentName": "$AGENT_NAME",
  "agentWallet": "$AGENT_WALLET",
  "offeringName": "$OFFERING_NAME"
}
EOF
  ok "saved state to $STATE_FILE"

  say "Step 2: scaffold + populate offering"
  AGENT_SLUG=$(echo "$AGENT_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/_/g' | sed 's/__*/_/g' | sed 's/^_//;s/_$//')
  OFFERING_DIR="${OPENCLAW_ROOT}/src/seller/offerings/${AGENT_SLUG}/${OFFERING_NAME}"
  if [ ! -d "$OFFERING_DIR" ]; then
    ( cd "$OPENCLAW_ROOT" && acp sell init "$OFFERING_NAME" ) || die "acp sell init failed"
  else
    warn "offering dir already exists at $OFFERING_DIR — skipping acp sell init"
  fi
  [ -d "$OFFERING_DIR" ] || die "offering dir not found after init: $OFFERING_DIR"

  cp "${TEMPLATES_DIR}/offering.json" "${OFFERING_DIR}/offering.json" || die "could not copy offering.json"
  cp "${TEMPLATES_DIR}/handlers.ts" "${OFFERING_DIR}/handlers.ts"     || die "could not copy handlers.ts"
  ok "copied offering.json + handlers.ts into $OFFERING_DIR"

  say "Step 3: update profile description (helps marketplace discovery)"
  ( cd "$OPENCLAW_ROOT" && acp profile update description "Grades prompts via PQS. Returns AtlasRow (pre-score 0-80, post-score 0-60, Opus 4.7 output) — buyers grade-gate on pre_score.total >= 60." --json ) || warn "profile update failed (non-fatal)"

  say "Step 4: register offering on ACP"
  ( cd "$OPENCLAW_ROOT" && acp sell create "$OFFERING_NAME" --json ) || die "acp sell create failed — check offering.json + handlers.ts validity"
  ok "offering registered"

  say "Step 5: start seller runtime (backgrounded via nohup)"
  mkdir -p "${REPO_ROOT}/.logs"
  if ( cd "$OPENCLAW_ROOT" && acp serve status --json 2>/dev/null | grep -q '"running": *true' ); then
    warn "seller runtime already running"
  else
    ( cd "$OPENCLAW_ROOT" && nohup acp serve start > "${REPO_ROOT}/.logs/acp-serve.log" 2>&1 & )
    sleep 3
    ok "acp serve start launched — logs: ${REPO_ROOT}/.logs/acp-serve.log"
  fi

  say "Step 6: buyer setup — need a SEPARATE agent for the buyer"
  echo ""
  echo "  Run in another terminal:"
  echo "    cd ${OPENCLAW_ROOT} && acp agent create pqs-buyer --json"
  echo "    cd ${OPENCLAW_ROOT} && acp agent switch pqs-buyer --json"
  echo "    cd ${OPENCLAW_ROOT} && acp wallet topup   # fund with >=0.15 USDC"
  echo ""
  echo "  Then topup must complete on-chain. Once wallet balance shows >=0.10 USDC:"
  echo "    ${REPO_ROOT}/scripts/atlas-agent-resume.sh buy 'your test prompt here'"
  echo ""
  ok "setup phase complete"
}

cmd_buy() {
  require_openclaw
  local PROMPT="$1"
  local VERTICAL="${2:-general}"
  [ -n "$PROMPT" ] || die "usage: atlas-agent-resume.sh buy '<prompt>' [vertical]"
  [ -f "$STATE_FILE" ] || die "no $STATE_FILE — run 'atlas-agent-resume.sh setup' first"

  SELLER_WALLET=$(node -e 'const s=require("'"$STATE_FILE"'"); console.log(s.agentWallet);')
  [ -n "$SELLER_WALLET" ] || die "could not read seller wallet from state"
  ok "seller wallet: $SELLER_WALLET"
  ok "offering: $OFFERING_NAME"

  # Switch to buyer agent
  say "Step 1: switch to buyer agent"
  ( cd "$OPENCLAW_ROOT" && acp agent switch pqs-buyer --json ) || die "could not switch to pqs-buyer — did you run: acp agent create pqs-buyer ?"

  REQ=$(node -e 'const p=process.argv[1], v=process.argv[2]; console.log(JSON.stringify({prompt:p,vertical:v}));' "$PROMPT" "$VERTICAL")
  say "Step 2: create job"
  echo "  requirements: $REQ"
  JOB_JSON=$( cd "$OPENCLAW_ROOT" && acp job create "$SELLER_WALLET" "$OFFERING_NAME" --requirements "$REQ" --json ) || die "job create failed"
  echo "$JOB_JSON"
  JOB_ID=$(node -e 'const j=JSON.parse(process.argv[1]); console.log(j.jobId||j.data?.jobId||"");' "$JOB_JSON")
  [ -n "$JOB_ID" ] || die "could not extract jobId from: $JOB_JSON"
  ok "jobId: $JOB_ID"

  say "Step 3: poll job status (up to 300s)"
  local I=0
  while [ "$I" -lt 60 ]; do
    sleep 5
    I=$((I+1))
    STATUS=$( cd "$OPENCLAW_ROOT" && acp job status "$JOB_ID" --json )
    PHASE=$(node -e 'const j=JSON.parse(process.argv[1]); console.log(j.phase||j.data?.phase||"");' "$STATUS" 2>/dev/null)
    printf '  [%3ds] phase=%s\n' "$((I*5))" "$PHASE"
    case "$PHASE" in
      NEGOTIATION)
        say "Step 4: NEGOTIATION reached — buyer inspects payment request"
        echo "$STATUS" | node -e 'let c=""; process.stdin.on("data",d=>c+=d).on("end",()=>{const j=JSON.parse(c); console.log(JSON.stringify(j.paymentRequestData||j.data?.paymentRequestData||{}, null, 2));})'
        say "Step 5: auto-pay (grade-gate evaluated post-execute — NEGOTIATION here is pre-execute fee)"
        ( cd "$OPENCLAW_ROOT" && acp job pay "$JOB_ID" --accept true --content "Paying fixed fee — grade-gate applied post-delivery on AtlasRow" --json ) || die "acp job pay failed"
        ok "payment accepted — waiting for delivery"
        ;;
      COMPLETED)
        say "Step 6: COMPLETED — extracting deliverable + tx hash"
        echo "$STATUS" | node -e 'let c=""; process.stdin.on("data",d=>c+=d).on("end",()=>{const j=JSON.parse(c);const deliv=j.deliverable||j.data?.deliverable; console.log("deliverable:"); console.log(typeof deliv==="string"?deliv:JSON.stringify(deliv,null,2));})'
        echo ""
        echo "  → Tx hash(es): look in the full status output for basescan-viewable tx"
        cd "$OPENCLAW_ROOT" && acp job status "$JOB_ID" --json > "${REPO_ROOT}/.logs/job-${JOB_ID}.json"
        ok "full status saved to ${REPO_ROOT}/.logs/job-${JOB_ID}.json"
        return 0
        ;;
      REJECTED|EXPIRED)
        die "job ended in phase=$PHASE — full status:$(echo; echo "$STATUS")"
        ;;
    esac
  done
  die "timed out after 300s — check acp serve logs: tail ${REPO_ROOT}/.logs/acp-serve.log"
}

cmd_status() {
  require_openclaw
  [ -f "$STATE_FILE" ] && cat "$STATE_FILE" || warn "no state file — run setup first"
  echo ""
  ( cd "$OPENCLAW_ROOT" && acp whoami --json ) 2>/dev/null || warn "acp whoami failed"
  echo ""
  ( cd "$OPENCLAW_ROOT" && acp serve status --json ) 2>/dev/null || warn "acp serve status failed"
  echo ""
  ( cd "$OPENCLAW_ROOT" && acp sell list --json ) 2>/dev/null || warn "acp sell list failed"
}

# ---------- main ----------
CMD="${1:-}"
shift || true
case "$CMD" in
  setup)  cmd_setup;;
  buy)    cmd_buy "$@";;
  status) cmd_status;;
  *)      die "usage: $0 {setup|buy '<prompt>' [vertical]|status}";;
esac
