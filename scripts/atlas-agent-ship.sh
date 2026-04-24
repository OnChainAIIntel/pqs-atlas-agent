#!/usr/bin/env bash
#
# atlas-agent-ship.sh — clean, idempotent driver for Beat 2.
#
# Replaces atlas-agent-resume.sh (bugs: invalid bash-portable `read` syntax
# and agent-slug derivation that used underscores instead of hyphens).
#
# Two-agent, single-machine workflow for Virtuals ACP v2 via openclaw-acp:
#
#   pqs-atlas-seller (id 42077, 0x405662430fB92DF1fB7830dfe4a1AbE0BBF023Ac)
#     The "offering" is pqs_atlas_score. Seller runtime calls
#     generateAtlasRow() via scripts/virtuals-handler.ts.
#
#   pqs-buyer        (id 42078, 0x705e1beFc7aDF9a216A886Ae45c055a852F7c832)
#     Fires an ACP job at the seller's wallet. Pays $0.10 USDC on Base.
#
# NOTE: openclaw-acp's agent-switch stops the seller runtime (the runtime
# is bound to the current agent's API key). So the `buy` subcommand does
# a flip: stop-seller → switch-to-buyer → create-job → switch-to-seller →
# restart-seller → poll. --isAutomated true means the server auto-accepts
# payment on the buyer's behalf, so we don't need to flip back to the
# buyer to run `acp job pay`.
#
# Usage:
#   ./scripts/atlas-agent-ship.sh seller-up    # ensure seller+offering+serve
#   ./scripts/atlas-agent-ship.sh check        # on-chain balances via Base RPC
#   ./scripts/atlas-agent-ship.sh buy "<prompt>" [vertical]
#   ./scripts/atlas-agent-ship.sh status
#
set -u

# ---------- constants ----------
REPO_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
OPENCLAW_ROOT="${HOME}/Desktop/openclaw-acp"
OFFERING_NAME="pqs_atlas_score"
SELLER_AGENT="pqs-atlas-seller"
BUYER_AGENT="pqs-buyer"
SELLER_WALLET="0x405662430fB92DF1fB7830dfe4a1AbE0BBF023Ac"
BUYER_WALLET="0x705e1beFc7aDF9a216A886Ae45c055a852F7c832"
BASE_RPC="https://mainnet.base.org"
USDC_ADDR="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
TEMPLATES_DIR="${REPO_ROOT}/openclaw-templates/${OFFERING_NAME}"
OFFERING_DIR="${OPENCLAW_ROOT}/src/seller/offerings/${SELLER_AGENT}/${OFFERING_NAME}"
LOGS_DIR="${REPO_ROOT}/.logs"
SERVE_LOG="${LOGS_DIR}/acp-serve.log"

# ---------- output helpers ----------
R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[34m'; D=$'\033[0m'
say()  { printf '%s[ship]%s %s\n' "$B" "$D" "$1"; }
ok()   { printf '%s✓%s %s\n' "$G" "$D" "$1"; }
warn() { printf '%s⚠ %s%s\n' "$Y" "$1" "$D"; }
die()  { printf '%s✗ %s%s\n' "$R" "$1" "$D" >&2; exit 1; }

# ---------- preconditions ----------
preflight() {
  [ -d "$OPENCLAW_ROOT" ] || die "openclaw-acp not found at $OPENCLAW_ROOT"
  command -v acp >/dev/null 2>&1 || die "acp CLI not on PATH (cd openclaw-acp && npm link)"
  [ -d "$TEMPLATES_DIR" ] || die "templates missing at $TEMPLATES_DIR"
  [ -f "${REPO_ROOT}/.env.local" ] || die "pqs-atlas-agent/.env.local missing"
  grep -q '^PQS_API_KEY=' "${REPO_ROOT}/.env.local" || die "PQS_API_KEY not set in .env.local"
  mkdir -p "$LOGS_DIR"
}

# ---------- RPC helpers (balance check without switching agents) ----------
rpc_eth_balance() {
  local addr="$1"
  curl -s -X POST "$BASE_RPC" -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"eth_getBalance\",\"params\":[\"$addr\",\"latest\"],\"id\":1}" \
    | python3 -c 'import json,sys; r=json.load(sys.stdin).get("result","0x0"); print(int(r,16))'
}
rpc_usdc_balance() {
  local addr="$1"
  local padded=$(printf '%064s' "${addr#0x}" | tr ' ' '0')
  local data="0x70a08231${padded}"
  curl -s -X POST "$BASE_RPC" -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"eth_call\",\"params\":[{\"to\":\"$USDC_ADDR\",\"data\":\"$data\"},\"latest\"],\"id\":1}" \
    | python3 -c 'import json,sys; r=json.load(sys.stdin).get("result","0x0"); print(int(r,16))'
}

# ---------- serve helpers ----------
serve_running() {
  ( cd "$OPENCLAW_ROOT" && acp serve status --json 2>/dev/null ) \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("running") else 1)' 2>/dev/null
}
serve_start() {
  if serve_running; then
    warn "serve already running — skipping start"
    return 0
  fi
  ( cd "$OPENCLAW_ROOT" && nohup acp serve start > "$SERVE_LOG" 2>&1 & )
  sleep 4
  if serve_running; then
    ok "serve started — log: $SERVE_LOG"
  else
    die "serve failed to start — tail $SERVE_LOG"
  fi
}
serve_stop() {
  if serve_running; then
    ( cd "$OPENCLAW_ROOT" && acp serve stop >/dev/null 2>&1 ) || true
    sleep 1
    ok "serve stopped"
  fi
}

# ---------- agent helpers (handle the switch-stops-seller prompt) ----------
active_agent() {
  ( cd "$OPENCLAW_ROOT" && acp whoami --json 2>/dev/null ) \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))'
}
switch_to() {
  local target="$1"
  local cur=$(active_agent)
  if [ "$cur" = "$target" ]; then
    return 0
  fi
  # The prompt "Stop the seller runtime process and continue? (Y/n): " gets
  # a 'y' answer via stdin. Default is also Y so empty stdin works too,
  # but we pipe 'y' explicitly to be safe.
  ( cd "$OPENCLAW_ROOT" && printf 'y\n' | acp agent switch "$target" ) >/dev/null 2>&1 || true
  sleep 1
  local now=$(active_agent)
  [ "$now" = "$target" ] || die "failed to switch to $target (still on $now)"
  ok "switched to $target"
}

# ---------- subcommand: seller-up ----------
cmd_seller_up() {
  preflight
  say "Step 1: ensure active agent is $SELLER_AGENT"
  switch_to "$SELLER_AGENT"

  say "Step 2: copy real templates into offering dir"
  [ -d "$OFFERING_DIR" ] || die "offering dir missing: $OFFERING_DIR (run: cd $OPENCLAW_ROOT && acp sell init $OFFERING_NAME)"
  cp "${TEMPLATES_DIR}/offering.json" "${OFFERING_DIR}/offering.json" || die "copy offering.json failed"
  cp "${TEMPLATES_DIR}/handlers.ts"   "${OFFERING_DIR}/handlers.ts"   || die "copy handlers.ts failed"
  ok "templates copied → $OFFERING_DIR"

  say "Step 3: register offering on ACP (idempotent)"
  ( cd "$OPENCLAW_ROOT" && acp sell create "$OFFERING_NAME" --json ) >/dev/null 2>&1 || \
    warn "sell create returned non-zero (likely already listed — acceptable)"
  local listed=$( ( cd "$OPENCLAW_ROOT" && acp sell list --json ) | python3 -c '
import json,sys
d=json.load(sys.stdin)
for o in d:
  if o.get("name")==sys.argv[1]:
    print("true" if o.get("listed") else "false"); sys.exit(0)
print("missing")' "$OFFERING_NAME")
  [ "$listed" = "true" ] || die "offering not listed after create (got: $listed)"
  ok "offering listed on ACP"

  say "Step 4: start seller runtime"
  serve_start
  ok "seller is up + listed + serving"
}

# ---------- subcommand: check (on-chain balances) ----------
cmd_check() {
  preflight
  say "Checking on-chain balances via Base RPC ($BASE_RPC)"
  local eth_buyer=$(rpc_eth_balance "$BUYER_WALLET")
  local usdc_buyer=$(rpc_usdc_balance "$BUYER_WALLET")
  local eth_seller=$(rpc_eth_balance "$SELLER_WALLET")
  local usdc_seller=$(rpc_usdc_balance "$SELLER_WALLET")
  printf '\n  %-10s %-44s %20s %20s\n' "agent" "wallet" "ETH (wei)" "USDC (6-decimal)"
  printf '  %-10s %-44s %20s %20s\n' "buyer"  "$BUYER_WALLET"  "$eth_buyer"  "$usdc_buyer"
  printf '  %-10s %-44s %20s %20s\n' "seller" "$SELLER_WALLET" "$eth_seller" "$usdc_seller"
  echo ""
  # Minimum thresholds for a $0.10 USDC job + gas: 0.15 USDC, 0.001 ETH
  local min_usdc=150000      # 0.15 USDC (6 decimals)
  local min_eth=1000000000000000  # 0.001 ETH (1e15 wei)
  if [ "$usdc_buyer" -ge "$min_usdc" ] && [ "$eth_buyer" -ge "$min_eth" ]; then
    ok "buyer funded — ready to run: $0 buy \"<prompt>\""
    return 0
  else
    warn "buyer unfunded. Minimums: >= 0.15 USDC ($min_usdc raw) and >= 0.001 ETH ($min_eth wei)."
    echo ""
    echo "  Topup via: cd $OPENCLAW_ROOT && acp agent switch $BUYER_AGENT && acp wallet topup"
    echo "  (will print a URL to send USDC + ETH to $BUYER_WALLET on Base mainnet)"
    return 2
  fi
}

# ---------- subcommand: buy ----------
cmd_buy() {
  preflight
  local prompt="${1:-}"
  local vertical="${2:-general}"
  [ -n "$prompt" ] || die "usage: $0 buy '<prompt>' [vertical]"

  cmd_check > /dev/null || die "buyer not funded — run: $0 check"
  ok "buyer funding verified"

  # Build requirements JSON (node to get proper JSON escaping)
  local req
  req=$(node -e 'const p=process.argv[1], v=process.argv[2]; process.stdout.write(JSON.stringify({prompt:p,vertical:v}));' "$prompt" "$vertical")
  say "requirements: $req"

  # --- flip: stop seller → switch to buyer → create job → switch back → restart seller ---
  say "Step 1: stop seller runtime (required before switching active agent)"
  serve_stop

  say "Step 2: switch to $BUYER_AGENT"
  switch_to "$BUYER_AGENT"

  say "Step 3: create ACP job ($SELLER_WALLET / $OFFERING_NAME / --isAutomated true)"
  local job_json
  job_json=$( ( cd "$OPENCLAW_ROOT" && acp job create "$SELLER_WALLET" "$OFFERING_NAME" \
      --requirements "$req" --isAutomated true --json ) 2>&1 ) || die "job create failed:$(printf '\n%s' "$job_json")"
  echo "$job_json"
  local job_id
  job_id=$(printf '%s' "$job_json" | python3 -c '
import json,sys
try:
  d=json.loads(sys.stdin.read())
  print(d.get("data",{}).get("jobId") or d.get("jobId") or "")
except Exception:
  print("")')
  [ -n "$job_id" ] || die "could not extract jobId from response"
  ok "jobId=$job_id"

  say "Step 4: switch back to $SELLER_AGENT and restart serve"
  switch_to "$SELLER_AGENT"
  serve_start
  ok "seller back up — runtime will pick up job $job_id via socket"

  say "Step 5: poll job status every 5s (timeout 300s)"
  local i=0
  while [ "$i" -lt 60 ]; do
    sleep 5
    i=$((i+1))
    local status
    status=$( ( cd "$OPENCLAW_ROOT" && acp job status "$job_id" --json ) 2>/dev/null )
    local phase
    phase=$(printf '%s' "$status" | python3 -c '
import json,sys
try:
  d=json.loads(sys.stdin.read())
  print(d.get("data",{}).get("phase") or d.get("phase") or "")
except Exception:
  print("")')
    printf '  [%3ds] phase=%s\n' "$((i*5))" "$phase"
    case "$phase" in
      COMPLETED)
        echo "$status" > "${LOGS_DIR}/job-${job_id}.json"
        ok "COMPLETED — full status saved to ${LOGS_DIR}/job-${job_id}.json"
        printf '%s' "$status" | python3 -c '
import json,sys
d=json.loads(sys.stdin.read()).get("data",{})
deliv = d.get("deliverable")
print("\n--- Deliverable ---")
print(deliv if isinstance(deliv,str) else json.dumps(deliv, indent=2))
'
        # Basescan tx hashes may appear in memo history or paymentRequestData
        printf '%s' "$status" | python3 -c '
import json,sys,re
raw = sys.stdin.read()
hashes = sorted(set(re.findall(r"0x[0-9a-fA-F]{64}", raw)))
if hashes:
  print("\n--- Tx hash candidates (review on basescan.org) ---")
  for h in hashes: print(f"  https://basescan.org/tx/{h}")
else:
  print("\n(no 0x{64} tx hash candidates found in status blob — check acp serve logs)")
' | tee "${LOGS_DIR}/basescan-tx.txt"
        return 0
        ;;
      REJECTED|EXPIRED|FAILED)
        echo "$status" > "${LOGS_DIR}/job-${job_id}.json"
        die "job ended in phase=$phase — see ${LOGS_DIR}/job-${job_id}.json"
        ;;
    esac
  done
  die "timed out after 300s — acp serve logs: $SERVE_LOG"
}

# ---------- subcommand: status ----------
cmd_status() {
  preflight
  echo "active agent:"
  ( cd "$OPENCLAW_ROOT" && acp whoami --json ) | python3 -m json.tool 2>/dev/null | head -6 || true
  echo ""
  echo "serve status:"
  ( cd "$OPENCLAW_ROOT" && acp serve status --json ) 2>/dev/null || true
  echo ""
  echo "sell list:"
  ( cd "$OPENCLAW_ROOT" && acp sell list --json ) 2>/dev/null || true
  echo ""
  echo "agents:"
  ( cd "$OPENCLAW_ROOT" && acp agent list --json ) 2>/dev/null || true
  echo ""
  cmd_check || true
}

# ---------- main ----------
CMD="${1:-}"
shift || true
case "$CMD" in
  seller-up) cmd_seller_up;;
  check)     cmd_check;;
  buy)       cmd_buy "$@";;
  status)    cmd_status;;
  *)         die "usage: $0 {seller-up|check|buy '<prompt>' [vertical]|status}";;
esac
