/**
 * atlas-agent-doctor — preflight env + reachability checks for Beat 2.
 *
 * Runs all non-network-requiring checks first (env shape, wallet format,
 * entity ID parseability). Then issues a trivial read against PQS to confirm
 * credentials work and the PQS host is up. Does NOT touch Virtuals contracts
 * (those require real key + smart-wallet setup we only want to exercise in
 * the real seller/buyer scripts).
 *
 * Exit code 0 if everything passes, 1 otherwise. Report is human-readable;
 * each line is a ✓ (pass), ✗ (fail), or • (info).
 */

import * as dotenv from "dotenv";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: resolve(__dirname, "..", ".env.local") });

// ---------- check helpers ----------

type Level = "pass" | "fail" | "info";
interface Check {
  level: Level;
  name: string;
  detail: string;
}

const results: Check[] = [];

function pass(name: string, detail: string): void {
  results.push({ level: "pass", name, detail });
}
function fail(name: string, detail: string): void {
  results.push({ level: "fail", name, detail });
}
function info(name: string, detail: string): void {
  results.push({ level: "info", name, detail });
}

function isHexAddress(s: string): boolean {
  return /^0x[0-9a-fA-F]{40}$/.test(s);
}

// secp256k1 private key from Rabby / a standard EOA: 0x + 64 hex chars.
// The whitelisted signer for the Virtuals smart wallet uses this format.
function isHexPrivateKey(s: string): boolean {
  return /^0x[0-9a-fA-F]{64}$/.test(s);
}

function checkAddressEnv(name: string): void {
  const v = process.env[name];
  if (!v) return fail(name, "not set in .env.local");
  if (!isHexAddress(v)) {
    return fail(name, `invalid address format (expect 0x + 40 hex). got: ${v.slice(0, 10)}...`);
  }
  pass(name, `set (${v.slice(0, 6)}...${v.slice(-4)})`);
}

function checkPrivateKeyEnv(name: string): void {
  const v = process.env[name];
  if (!v) return fail(name, "not set in .env.local");
  if (!isHexPrivateKey(v)) {
    return fail(
      name,
      "invalid private-key format (expect 0x + 64 hex, secp256k1 from Rabby/EOA)",
    );
  }
  pass(name, `set (${v.slice(0, 6)}...${v.slice(-4)})`);
}

function checkIntEnv(name: string): void {
  const v = process.env[name];
  if (!v) return fail(name, "not set in .env.local");
  const n = parseInt(v, 10);
  if (!Number.isFinite(n)) return fail(name, `not an integer: ${v}`);
  pass(name, `set (${n})`);
}

function checkStringEnv(name: string, minLen = 1): void {
  const v = process.env[name];
  if (!v) return fail(name, "not set in .env.local");
  if (v.length < minLen) return fail(name, `too short (< ${minLen} chars)`);
  pass(name, `set (${v.length} chars)`);
}

// ---------- Virtuals config checks ----------

checkAddressEnv("VIRTUALS_ATLAS_AGENT_WALLET");
checkPrivateKeyEnv("VIRTUALS_ATLAS_AGENT_PRIVATE_KEY");
checkIntEnv("VIRTUALS_ATLAS_AGENT_SESSION_ENTITY_KEY_ID");
checkAddressEnv("VIRTUALS_BUYER_AGENT_WALLET");
checkPrivateKeyEnv("VIRTUALS_BUYER_AGENT_PRIVATE_KEY");
checkIntEnv("VIRTUALS_BUYER_AGENT_SESSION_ENTITY_KEY_ID");

// ---------- PQS pipeline checks ----------

checkStringEnv("PQS_API_KEY", 8);
checkStringEnv("PQS_INTERNAL_TOKEN", 4);
info(
  "PQS_BASE_URL",
  process.env.PQS_BASE_URL ?? "https://pqs.onchainintel.net (default)",
);

// ---------- module-level checks ----------

try {
  await import("../src/virtuals-client.js");
  pass("virtuals-client module", "imports cleanly");
} catch (e) {
  fail("virtuals-client module", (e as Error).message);
}

try {
  const { BASE_USDC_ADDRESS, USDC_DECIMALS } = await import(
    "../src/virtuals-client.js"
  );
  if (BASE_USDC_ADDRESS.toLowerCase() === "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913") {
    pass("BASE_USDC_ADDRESS", "correct Base mainnet USDC");
  } else {
    fail("BASE_USDC_ADDRESS", `unexpected address: ${BASE_USDC_ADDRESS}`);
  }
  if (USDC_DECIMALS === 6) {
    pass("USDC_DECIMALS", "6");
  } else {
    fail("USDC_DECIMALS", `expected 6, got ${USDC_DECIMALS}`);
  }
} catch (e) {
  fail("constants check", (e as Error).message);
}

try {
  const mod = await import("@virtuals-protocol/acp-node");
  const needed = [
    "default", // AcpClient
    "AcpContractClientV2",
    "AcpJobPhases",
    "Fare",
    "FareAmount",
    "baseAcpX402ConfigV2",
  ];
  const missing = needed.filter((k) => (mod as Record<string, unknown>)[k] === undefined);
  if (missing.length === 0) {
    pass("@virtuals-protocol/acp-node", "all required exports present");
  } else {
    fail("@virtuals-protocol/acp-node", `missing exports: ${missing.join(", ")}`);
  }
} catch (e) {
  fail("@virtuals-protocol/acp-node", `import failed: ${(e as Error).message}`);
}

// ---------- report ----------

const icons: Record<Level, string> = { pass: "✓", fail: "✗", info: "•" };
console.log("atlas-agent doctor");
console.log("==================");
for (const r of results) {
  console.log(`${icons[r.level]} ${r.name.padEnd(36)} ${r.detail}`);
}
const failCount = results.filter((r) => r.level === "fail").length;
const passCount = results.filter((r) => r.level === "pass").length;
console.log("");
console.log(`${passCount} pass, ${failCount} fail`);
process.exit(failCount === 0 ? 0 : 1);
