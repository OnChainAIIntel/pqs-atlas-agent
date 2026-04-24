/**
 * handlers.ts — openclaw-acp seller handler for offering "pqs_atlas_score".
 *
 * This file is copied (verbatim) into the openclaw-acp offering directory at:
 *   ~/Desktop/openclaw-acp/src/seller/offerings/<agent-name>/pqs_atlas_score/handlers.ts
 *
 * It's a thin adapter — all real logic lives in
 * ~/Desktop/pqs-atlas-agent/scripts/virtuals-handler.ts so the PQS
 * pipeline stays authoritative in pqs-atlas-agent and we don't fork code.
 *
 * If you ever relocate pqs-atlas-agent, update the absolute import path
 * below. Using an absolute path (file:// or expanded ~) avoids the
 * fragility of deep relative paths like ../../../../../.
 *
 * Runtime: openclaw-acp uses tsx, so TS imports resolve natively.
 * Env: pqs-atlas-agent's virtuals-handler loads its own .env.local via
 * dotenv, so PQS_API_KEY and PQS_INTERNAL_TOKEN must be set in
 * pqs-atlas-agent/.env.local (not openclaw-acp/config.json).
 */

import type { ExecuteJobResult, ValidationResult } from "../../../runtime/offeringTypes.js";
import {
  executeJobHandler,
  validateRequirementsHandler,
  requestPaymentHandler,
} from "/Users/kenburbary/Desktop/pqs-atlas-agent/scripts/virtuals-handler.ts";

// Required: runs AFTER the buyer has paid. Produces the AtlasRow.
export async function executeJob(request: any): Promise<ExecuteJobResult> {
  return executeJobHandler(request);
}

// Optional: runs BEFORE acceptance. Reject malformed prompts early.
export function validateRequirements(request: any): ValidationResult {
  return validateRequirementsHandler(request);
}

// Optional: custom NEGOTIATION-memo message shown to the buyer.
export function requestPayment(request: any): string {
  return requestPaymentHandler(request);
}
