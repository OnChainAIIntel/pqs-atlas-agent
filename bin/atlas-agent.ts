#!/usr/bin/env -S tsx
/**
 * atlas-agent — unified CLI wrapper for the Virtuals ACP v2 integration.
 *
 * Subcommands:
 *   serve                                  Start the seller listener. Keeps the
 *                                          agent ONLINE on Virtuals. Wraps
 *                                          scripts/atlas-agent-serve.ts.
 *
 *   buy <prompt> [--vertical <v>]          Initiate a $0.10 USDC targeted job
 *                                          against the seller. Grade-gates
 *                                          payment on pre_score >= 60. Wraps
 *                                          scripts/atlas-agent-buy.ts.
 *
 *   status                                 Print resolved env + wallets so Ken
 *                                          can sanity-check the .env.local
 *                                          without launching a listener.
 *
 * Examples:
 *   tsx bin/atlas-agent.ts serve
 *   tsx bin/atlas-agent.ts buy "write a prod-ready onboarding email" --vertical marketing
 *   tsx bin/atlas-agent.ts status
 */

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..");
const SERVE_SCRIPT = resolve(REPO_ROOT, "scripts", "atlas-agent-serve.ts");
const BUY_SCRIPT = resolve(REPO_ROOT, "scripts", "atlas-agent-buy.ts");
const DOCTOR_SCRIPT = resolve(REPO_ROOT, "scripts", "atlas-agent-doctor.ts");

function usage(): void {
  console.error(`Usage: atlas-agent <command> [args]

Commands:
  serve                              Start the seller listener (agent ONLINE).
  buy <prompt> [--vertical <v>]      Initiate a grade-gated targeted job.
  status                             Print resolved env vars and wallets.
  doctor                             Full preflight check — env, SDK, constants.

Environment: loads .env.local from repo root.`);
}

function runTsx(scriptPath: string, args: string[]): never {
  // Inherit stdio so logs stream live and SIGINT/SIGTERM propagate.
  const child = spawn("tsx", [scriptPath, ...args], {
    stdio: "inherit",
    cwd: REPO_ROOT,
    env: process.env,
  });
  child.on("exit", (code) => process.exit(code ?? 0));
  child.on("error", (err) => {
    console.error(`[atlas-agent] failed to spawn tsx: ${err.message}`);
    process.exit(1);
  });
  // Spawn is async; keep the process alive until child exits.
  // Use a never-resolving promise to satisfy the `never` return type.
  return undefined as never;
}

async function status(): Promise<void> {
  // Dynamically import so env loading (.env.local) happens inside the module.
  const mod = await import("../src/virtuals-client.js");
  const { getSellerWallet, getBuyerWallet, BASE_USDC_ADDRESS, USDC_DECIMALS } =
    mod;

  console.log("atlas-agent status");
  console.log("==================");
  try {
    console.log(`seller wallet:       ${getSellerWallet()}`);
  } catch (e) {
    console.log(`seller wallet:       <unset> (${(e as Error).message})`);
  }
  try {
    console.log(`buyer wallet:        ${getBuyerWallet()}`);
  } catch (e) {
    console.log(`buyer wallet:        <unset> (${(e as Error).message})`);
  }
  console.log(`base USDC address:   ${BASE_USDC_ADDRESS}`);
  console.log(`USDC decimals:       ${USDC_DECIMALS}`);
  console.log(
    `PQS_API_KEY set:     ${process.env.PQS_API_KEY ? "yes" : "no"}`,
  );
  console.log(
    `PQS_INTERNAL_TOKEN:  ${process.env.PQS_INTERNAL_TOKEN ? "set" : "unset"}`,
  );
  console.log(
    `PQS_BASE_URL:        ${process.env.PQS_BASE_URL ?? "https://pqs.onchainintel.net (default)"}`,
  );
}

async function main(): Promise<void> {
  const [, , cmd, ...rest] = process.argv;

  switch (cmd) {
    case "serve": {
      runTsx(SERVE_SCRIPT, rest);
      break;
    }
    case "buy": {
      // First positional after "buy" is the prompt. Passthrough any --vertical
      // flag untouched. If no positional, assume user passed --prompt already.
      const args: string[] = [];
      const first = rest[0];
      if (first && !first.startsWith("--")) {
        args.push("--prompt", first, ...rest.slice(1));
      } else {
        args.push(...rest);
      }
      runTsx(BUY_SCRIPT, args);
      break;
    }
    case "status": {
      await status();
      break;
    }
    case "doctor": {
      runTsx(DOCTOR_SCRIPT, rest);
      break;
    }
    case "-h":
    case "--help":
    case "help":
    case undefined: {
      usage();
      process.exit(cmd ? 0 : 2);
      break;
    }
    default: {
      console.error(`atlas-agent: unknown command "${cmd}"\n`);
      usage();
      process.exit(2);
    }
  }
}

main().catch((e) => {
  console.error("[atlas-agent] fatal:", e);
  process.exit(1);
});
