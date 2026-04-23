/**
 * atlas-agent-buy — buyer script for targeted Virtuals ACP v2 jobs.
 *
 * Initiates a job against the pqs-atlas-agent seller wallet with a prompt
 * string in the requirement schema, then grade-gates payment based on the
 * AtlasRow the seller surfaces in its NEGOTIATION requirement memo.
 *
 * Grade gate: the brief says "post_score.grade >= B (>= 60/80)". The
 * post-score uses a 0-60 scale and has no defined grade thresholds yet
 * (post_grade_placeholder is null in the AtlasRow schema). The 60/80 hint in
 * the brief aligns with the pre-score B threshold (A>=70, B>=60, C>=50,
 * D>=35). So we gate on pre_score.total >= 60 — i.e. the *prompt* is at
 * least B-grade quality. Documented here so the choice is auditable.
 *
 * Escrow: $0.10 USDC on Base mainnet.
 *
 * Usage:
 *   tsx scripts/atlas-agent-buy.ts --prompt "write a production-ready ..."
 *   tsx scripts/atlas-agent-buy.ts --prompt "..." --vertical software
 */

import { parseArgs } from "node:util";
import {
  AcpJob,
  AcpJobPhases,
  AcpMemo,
  FareAmount,
  Fare,
} from "@virtuals-protocol/acp-node";
import {
  buildBuyer,
  getSellerWallet,
  BASE_USDC_ADDRESS,
  USDC_DECIMALS,
} from "../src/virtuals-client.js";
import type { AtlasRow } from "../schemas/atlas-row.js";

// ---------- config ----------

const ESCROW_USDC = 0.1; // $0.10 demo escrow
const GRADE_GATE_MIN_TOTAL = 60; // pre_score.total >= 60 == grade B

// ---------- helpers ----------

function tryParseAtlasRow(raw: unknown): AtlasRow | null {
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw) as AtlasRow;
    } catch {
      return null;
    }
  }
  if (raw && typeof raw === "object") {
    // Already parsed; trust its shape (server-side did JSON.stringify).
    return raw as AtlasRow;
  }
  return null;
}

function shouldPay(row: AtlasRow | null): { pay: boolean; reason: string } {
  if (!row) {
    return {
      pay: false,
      reason: "seller requirement did not contain a parseable AtlasRow",
    };
  }
  const total = row.pre_score?.total;
  const grade = row.pre_grade;
  if (typeof total !== "number") {
    return { pay: false, reason: "AtlasRow missing pre_score.total" };
  }
  if (total >= GRADE_GATE_MIN_TOTAL) {
    return {
      pay: true,
      reason: `pre_score.total=${total}/80 grade=${grade} — meets B+ gate`,
    };
  }
  return {
    pay: false,
    reason: `pre_score.total=${total}/80 grade=${grade} — below B gate (${GRADE_GATE_MIN_TOTAL}/80)`,
  };
}

// ---------- main ----------

async function main(): Promise<void> {
  const { values } = parseArgs({
    options: {
      prompt: { type: "string" },
      vertical: { type: "string", default: "general" },
    },
    allowPositionals: false,
    strict: true,
  });

  if (!values.prompt) {
    console.error(
      'Usage: tsx scripts/atlas-agent-buy.ts --prompt "..." [--vertical general]',
    );
    process.exit(2);
  }

  const prompt = values.prompt;
  const vertical = values.vertical ?? "general";
  const sellerWallet = getSellerWallet();

  console.log(`[buy] buyer wallet initialising...`);

  // Track whether we've already settled this job so we don't accidentally
  // pay twice if callbacks fire multiple times.
  let decided = false;
  let jobFinal: "paid" | "rejected" | "completed" | "seller-rejected" | null =
    null;

  const acpClient = await buildBuyer({
    onNewTask: async (job: AcpJob, memoToSign?: AcpMemo) => {
      try {
        if (
          job.phase === AcpJobPhases.NEGOTIATION &&
          memoToSign?.nextPhase === AcpJobPhases.TRANSACTION
        ) {
          if (decided) {
            console.log(`[buy] job ${job.id}: already decided; skipping.`);
            return;
          }
          decided = true;

          const requirement = job.requirement ?? memoToSign?.content;
          const atlasRow = tryParseAtlasRow(requirement);
          const verdict = shouldPay(atlasRow);
          console.log(`[buy] job ${job.id}: evaluation → ${verdict.reason}`);

          if (verdict.pay) {
            console.log(`[buy] job ${job.id}: paying & accepting requirement...`);
            const res = await job.payAndAcceptRequirement(verdict.reason);
            const tx = res?.txnHash ?? "<no-tx>";
            console.log(
              `[buy] job ${job.id}: PAID. userOpHash=${res?.userOpHash ?? "?"} txnHash=${tx}`,
            );
            console.log(`[buy] Basescan: https://basescan.org/tx/${tx}`);
            jobFinal = "paid";
          } else {
            console.log(`[buy] job ${job.id}: rejecting — ${verdict.reason}`);
            await job.reject(verdict.reason);
            jobFinal = "rejected";
          }
        } else if (
          job.phase === AcpJobPhases.TRANSACTION &&
          memoToSign?.nextPhase === AcpJobPhases.REJECTED
        ) {
          // Seller rejected post-payment (rare in this flow).
          console.log(
            `[buy] job ${job.id}: seller rejection memo: ${memoToSign?.content}`,
          );
          await memoToSign.sign(true, "buyer accepts seller rejection");
          jobFinal = "seller-rejected";
        } else if (job.phase === AcpJobPhases.COMPLETED) {
          console.log(`[buy] job ${job.id}: COMPLETED.`);
          const deliverable = await job.getDeliverable();
          console.log(`[buy] job ${job.id}: deliverable:`, deliverable);
          jobFinal = "completed";
        } else if (job.phase === AcpJobPhases.REJECTED) {
          console.log(`[buy] job ${job.id}: REJECTED.`);
          if (!jobFinal) jobFinal = "rejected";
        } else {
          console.log(
            `[buy] job ${job.id}: observed phase=${job.phase} nextPhase=${memoToSign?.nextPhase ?? "<none>"}`,
          );
        }
      } catch (e) {
        console.error(
          `[buy] unhandled error for job ${job.id}:`,
          (e as Error).message,
        );
      }
    },
  });

  console.log(`[buy] buyer online. Targeting seller ${sellerWallet}.`);

  // Build the $0.10 USDC FareAmount.
  const fare = new Fare(BASE_USDC_ADDRESS, USDC_DECIMALS);
  const fareAmount = new FareAmount(ESCROW_USDC, fare);

  const requirement = {
    prompt,
    vertical,
  };

  // 1 hour expiry for the initial request memo.
  const expiredAt = new Date(Date.now() + 60 * 60 * 1000);

  const jobId = await acpClient.initiateJob(
    sellerWallet,
    requirement,
    fareAmount,
    undefined, // evaluatorAddress: undefined → skip-evaluation pattern
    expiredAt,
  );

  console.log(
    `[buy] job ${jobId} initiated against ${sellerWallet}. escrow=$${ESCROW_USDC} USDC. awaiting NEGOTIATION memo...`,
  );

  // Wait for terminal state. The callbacks above flip `jobFinal`.
  // Poll with a generous timeout — the seller has to run the PQS pipeline
  // (~30s) before sending the NEGOTIATION memo.
  const deadline = Date.now() + 5 * 60 * 1000; // 5 min hard cap
  while (!jobFinal && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
  }

  if (!jobFinal) {
    console.error("[buy] timeout — no terminal state reached in 5 minutes.");
    process.exit(1);
  }

  console.log(`[buy] final: ${jobFinal}`);
  process.exit(jobFinal === "paid" || jobFinal === "completed" ? 0 : 1);
}

main().catch((e) => {
  console.error("[buy] fatal:", e);
  process.exit(1);
});
