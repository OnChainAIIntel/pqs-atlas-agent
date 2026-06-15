/**
 * atlas-agent-serve — seller listener for Virtuals ACP v2 targeted jobs.
 *
 * Wraps the existing pqs-atlas-agent pipeline (scripts/generate-atlas-row.ts)
 * and exposes it as a marketplace service under the agent wallet.
 *
 * Flow (ACP v2 phases are inverted vs. the raw brief — this is the reconciled
 * interpretation that preserves both protocol semantics and the brief's
 * "grade-gated payment" intent):
 *
 *   REQUEST → NEGOTIATION
 *     - Buyer initiates a job carrying { prompt, vertical? } in the schema.
 *     - Seller (this script) runs generateAtlasRow() synchronously.
 *     - Seller calls job.accept() and job.createRequirement() with the
 *       AtlasRow serialized in the memo content. This surfaces the score
 *       to the buyer *before* payment is settled so the buyer can decide
 *       whether to pay (grade >= B) or reject.
 *
 *   NEGOTIATION → TRANSACTION
 *     - Buyer evaluates the requirement memo (parses AtlasRow), calls
 *       payAndAcceptRequirement() if pre_score.total >= 60, else reject().
 *       This is handled in atlas-agent-buy.ts, not here.
 *
 *   TRANSACTION → EVALUATION
 *     - Seller calls job.deliver(atlasRow) with the same AtlasRow, which
 *       becomes the canonical on-chain deliverable memo.
 *
 *   EVALUATION → COMPLETED
 *     - Skip-evaluation pattern: evaluator = undefined, so delivery
 *       auto-completes the job.
 *
 * Running:
 *   tsx scripts/atlas-agent-serve.ts
 *
 * Env vars: see src/virtuals-client.ts. PQS_API_KEY + PQS_INTERNAL_TOKEN
 * are also required since generateAtlasRow() calls the PQS production API.
 *
 * Note on PQS prepay: the original brief mentions prepaying PQS $0.025
 * via an existing x402 MCP tool. The actual generate-atlas-row.ts uses a
 * Bearer API key (PQS_API_KEY) that represents pre-funded credit; there is
 * no per-call x402 step in the existing pipeline. The real on-chain USDC
 * tx captured by this Beat 2 integration is the buyer → seller settlement
 * via Virtuals ACP's x402 payment route, which is what gets recorded on
 * Basescan.
 */

import {
  AcpJob,
  AcpJobPhases,
  AcpMemo,
  DeliverablePayload,
} from "@virtuals-protocol/acp-node";
import { buildSeller } from "../src/virtuals-client.js";
import { generateAtlasRow } from "./generate-atlas-row.js";
import type { Vertical } from "../schemas/atlas-row.js";

// ---------- job requirement schema ----------

interface JobRequirement {
  prompt?: string;
  vertical?: Vertical;
}

function parseRequirement(raw: unknown): JobRequirement {
  // Virtuals surfaces the buyer's schema either as an object (parsed JSON) or
  // a string (raw JSON). Handle both.
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw) as JobRequirement;
    } catch {
      // Fall through: treat the string itself as the prompt.
      return { prompt: raw };
    }
  }
  if (raw && typeof raw === "object") {
    return raw as JobRequirement;
  }
  return {};
}

function extractPromptAndVertical(
  job: AcpJob,
): { prompt: string; vertical: Vertical } {
  // Try job.requirement first (populated once buyer's requirement memo is
  // visible). Fall back to the latest memo content.
  const reqFields = parseRequirement(job.requirement);
  const memoFields = parseRequirement(job.latestMemo?.content);
  const prompt = reqFields.prompt ?? memoFields.prompt;
  const vertical: Vertical = (reqFields.vertical ?? memoFields.vertical ?? "general") as Vertical;

  if (!prompt || typeof prompt !== "string" || !prompt.trim()) {
    throw new Error(
      `[atlas-agent-serve] job ${job.id} has no resolvable prompt in requirement or latest memo`,
    );
  }
  return { prompt, vertical };
}

// ---------- phase handlers ----------

async function handleRequestToNegotiation(job: AcpJob): Promise<void> {
  console.log(`[serve] job ${job.id}: REQUEST phase, computing AtlasRow...`);

  let prompt: string;
  let vertical: Vertical;
  try {
    ({ prompt, vertical } = extractPromptAndVertical(job));
  } catch (e) {
    const reason = (e as Error).message;
    console.error(`[serve] rejecting job ${job.id}: ${reason}`);
    await job.reject(reason);
    return;
  }

  // Run the existing PQS pipeline. The Bearer API key (PQS_API_KEY) handles
  // payment for the underlying scoring calls.
  let atlasRow;
  try {
    atlasRow = await generateAtlasRow(prompt, vertical, "synthetic");
  } catch (e) {
    const reason = `PQS pipeline failed: ${(e as Error).message}`;
    console.error(`[serve] ${reason}`);
    await job.reject(reason);
    return;
  }

  console.log(
    `[serve] job ${job.id}: AtlasRow produced. pre_grade=${atlasRow.pre_grade}, pre_total=${atlasRow.pre_score.total}/80`,
  );

  // Accept the job, then send the AtlasRow as the requirement so the buyer
  // can grade-gate payment.
  await job.accept("pqs-atlas-agent: AtlasRow computed; see requirement");
  const requirementContent = JSON.stringify(atlasRow);
  await job.createRequirement(requirementContent);
  console.log(
    `[serve] job ${job.id}: NEGOTIATION memo sent (${requirementContent.length} chars)`,
  );
}

async function handleTransactionToEvaluation(job: AcpJob): Promise<void> {
  console.log(
    `[serve] job ${job.id}: TRANSACTION phase, buyer paid. Delivering AtlasRow...`,
  );

  // At this point the buyer has paid and the AtlasRow is already in the
  // requirement memo. Re-emit it as the canonical deliverable so it lands in
  // an EVALUATION/COMPLETED memo on-chain.
  let deliverable: DeliverablePayload;
  try {
    const fields = parseRequirement(job.requirement);
    // Requirement at this phase holds the AtlasRow JSON we sent earlier.
    deliverable =
      job.requirement && typeof job.requirement === "object"
        ? (job.requirement as Record<string, unknown>)
        : fields && Object.keys(fields).length > 0
          ? (fields as unknown as Record<string, unknown>)
          : { status: "delivered", note: "see job.requirement for AtlasRow" };
  } catch {
    deliverable = { status: "delivered", note: "see job.requirement for AtlasRow" };
  }

  await job.deliver(deliverable);
  console.log(`[serve] job ${job.id}: delivered.`);
}

// ---------- main ----------

async function main(): Promise<void> {
  console.log("[serve] starting pqs-atlas-agent seller listener...");

  await buildSeller({
    onNewTask: async (job: AcpJob, memoToSign?: AcpMemo) => {
      try {
        if (
          job.phase === AcpJobPhases.REQUEST &&
          memoToSign?.nextPhase === AcpJobPhases.NEGOTIATION
        ) {
          await handleRequestToNegotiation(job);
        } else if (
          job.phase === AcpJobPhases.TRANSACTION &&
          memoToSign?.nextPhase === AcpJobPhases.EVALUATION
        ) {
          await handleTransactionToEvaluation(job);
        } else {
          // Other transitions (COMPLETED, REJECTED) don't require seller action.
          console.log(
            `[serve] job ${job.id}: observed phase=${job.phase} nextPhase=${memoToSign?.nextPhase ?? "<none>"}`,
          );
        }
      } catch (e) {
        console.error(
          `[serve] unhandled error for job ${job.id}:`,
          (e as Error).message,
        );
      }
    },
  });

  console.log(
    "[serve] seller listener initialised. Agent is ONLINE. Awaiting jobs...",
  );
  // The AcpClient keeps a websocket open; keep the process alive.
  // Graceful shutdown on Ctrl+C.
  process.on("SIGINT", () => {
    console.log("\n[serve] SIGINT received. Shutting down.");
    process.exit(0);
  });
}

main().catch((e) => {
  console.error("[serve] fatal:", e);
  process.exit(1);
});
