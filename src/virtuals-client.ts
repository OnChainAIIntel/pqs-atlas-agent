/**
 * virtuals-client — thin wrapper around @virtuals-protocol/acp-node for the
 * pqs-atlas-agent Beat 2 marketplace integration.
 *
 * Two roles:
 *   - seller: listens on the agent wallet for incoming jobs, runs the PQS
 *     pipeline, returns an AtlasRow.
 *   - buyer: initiates targeted jobs against the seller and grade-gates
 *     payment based on the score surfaced in the requirement memo.
 *
 * Env vars (loaded from .env.local via dotenv):
 *   VIRTUALS_ATLAS_AGENT_WALLET                 seller smart-wallet address (0x...)
 *   VIRTUALS_ATLAS_AGENT_PRIVATE_KEY             seller whitelisted signing key (0x...)
 *   VIRTUALS_ATLAS_AGENT_SESSION_ENTITY_KEY_ID   seller session entity id (integer)
 *   VIRTUALS_BUYER_AGENT_WALLET                 buyer smart-wallet address (0x...)
 *   VIRTUALS_BUYER_AGENT_PRIVATE_KEY             buyer whitelisted signing key (0x...)
 *   VIRTUALS_BUYER_AGENT_SESSION_ENTITY_KEY_ID   buyer session entity id (integer)
 *
 * Private-key format note: Virtuals session entity keys are P-256 (EC), not
 * secp256k1 Ethereum keys. AcpContractClientV2.build() accepts them as-is
 * (raw 32-byte hex, 0x-prefixed). No format conversion on our end.
 *
 * Chain: Base mainnet. Payment route: x402 (routes through ACP's v2 x402
 * config so the buyer's payAndAcceptRequirement lands as a real on-chain
 * USDC transfer recorded on Basescan).
 */

import AcpClient, {
  AcpContractClientV2,
  AcpJob,
  AcpMemo,
  baseAcpX402ConfigV2,
} from "@virtuals-protocol/acp-node";
import type { Address } from "viem";
import * as dotenv from "dotenv";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

// Load .env.local from the repo root. virtuals-client.ts lives at
// src/virtuals-client.ts so the repo root is one dir up.
const __dirname = dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: resolve(__dirname, "..", ".env.local") });

// ---------- env helpers ----------

function required(name: string): string {
  const v = process.env[name];
  if (!v || v.trim() === "") {
    throw new Error(
      `[virtuals-client] env var ${name} is not set. Add it to .env.local.`,
    );
  }
  return v.trim();
}

function requiredInt(name: string): number {
  const raw = required(name);
  const n = parseInt(raw, 10);
  if (!Number.isFinite(n)) {
    throw new Error(
      `[virtuals-client] env var ${name} must be an integer (got ${raw}).`,
    );
  }
  return n;
}

// USDC on Base mainnet. Used for FareAmount construction when initiating a job.
export const BASE_USDC_ADDRESS: Address =
  "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
export const USDC_DECIMALS = 6;

// ---------- seller / buyer factories ----------

export interface SellerOptions {
  onNewTask: (job: AcpJob, memoToSign?: AcpMemo) => void | Promise<void>;
}

export interface BuyerOptions {
  onNewTask: (job: AcpJob, memoToSign?: AcpMemo) => void | Promise<void>;
}

/**
 * Build the seller-side AcpClient. The seller listens on the agent wallet
 * (VIRTUALS_ATLAS_AGENT_WALLET) and handles REQUEST → NEGOTIATION (accept
 * + put AtlasRow preview in requirement) and TRANSACTION → EVALUATION
 * (deliver full AtlasRow) transitions inside the supplied onNewTask.
 */
export async function buildSeller(opts: SellerOptions): Promise<AcpClient> {
  const agentWallet = required("VIRTUALS_ATLAS_AGENT_WALLET") as Address;
  const agentKey = required("VIRTUALS_ATLAS_AGENT_PRIVATE_KEY") as Address;
  // SDK types sessionEntityKeyId as `number` and runtime forwards it to
  // Alchemy Modular Account V2's signerEntity.entityId (the on-chain
  // entity ID registered when the EOA was whitelisted for this smart
  // wallet). Virtuals dashboard doesn't expose this — trial the default
  // `1` first; if the SDK throws an entity-related error, increment.
  const entityId = requiredInt(
    "VIRTUALS_ATLAS_AGENT_SESSION_ENTITY_KEY_ID",
  );

  const contractClient = await AcpContractClientV2.build(
    agentKey,
    entityId,
    agentWallet,
    baseAcpX402ConfigV2,
  );

  return new AcpClient({
    acpContractClient: contractClient,
    onNewTask: opts.onNewTask,
  });
}

/**
 * Build the buyer-side AcpClient. The buyer initiates a targeted job against
 * the seller wallet and handles NEGOTIATION → TRANSACTION (grade-gated pay
 * or reject) and COMPLETED/REJECTED terminal phases.
 */
export async function buildBuyer(opts: BuyerOptions): Promise<AcpClient> {
  const buyerWallet = required("VIRTUALS_BUYER_AGENT_WALLET") as Address;
  const buyerKey = required("VIRTUALS_BUYER_AGENT_PRIVATE_KEY") as Address;
  // See buildSeller() for entity-ID note.
  const entityId = requiredInt("VIRTUALS_BUYER_AGENT_SESSION_ENTITY_KEY_ID");

  const contractClient = await AcpContractClientV2.build(
    buyerKey,
    entityId,
    buyerWallet,
    baseAcpX402ConfigV2,
  );

  return new AcpClient({
    acpContractClient: contractClient,
    onNewTask: opts.onNewTask,
  });
}

/**
 * Read-only helper: return resolved seller wallet from env. Useful for the
 * buyer script to target the correct provider without duplicating env-read
 * logic.
 */
export function getSellerWallet(): Address {
  return required("VIRTUALS_ATLAS_AGENT_WALLET") as Address;
}

/**
 * Read-only helper: return resolved buyer wallet from env.
 */
export function getBuyerWallet(): Address {
  return required("VIRTUALS_BUYER_AGENT_WALLET") as Address;
}
