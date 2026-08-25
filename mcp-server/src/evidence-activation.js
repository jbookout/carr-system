// Server-only front door for the existing Work Request/accepted-plan path.
// Callers name a Work Request and accepted plan; the database derives the
// tenant, canonical refs, required/advisory classification, and timestamps.
import { organizationTenantForActor } from "./identity.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const WR = /^WR-[0-9]{1,12}$/;
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const RAW_RECEIPT_KEYS = new Set(["raw_prompt", "raw_transcript", "tool_payload", "raw_output", "prompt", "transcript", "expected_output", "expected_answer", "held_out_expected_output", "held_out_answer"]);
const BOUNDED_SCALAR = /^[^\s]{1,127}$/;
const RECEIPT_REQUIRED = new Set(["schema_version", "attempt_id", "envelope_digest", "attempt_ordinal", "adapter", "lifecycle", "result", "attestation", "negative_knowledge", "telemetry", "tool_event_summaries", "observation", "interventions", "handoff_proposal", "visual_artifacts", "evaluation_binding", "knowledge_activation", "reliability"]);
const RECEIPT_ALLOWED = new Set([...RECEIPT_REQUIRED]);
const KNOWLEDGE_REQUIRED = new Set(["bundle_digest", "item_dispositions", "closure", "mode", "canonical_binding"]);
const RELIABILITY_REQUIRED = new Set(["route_digest", "topology_digest", "evaluation_plan_digest", "grounding_sufficiency", "deterministic_checks", "model_judgement", "human_acceptance", "trajectory", "evaluator_results", "corrections", "defects", "incidents", "downstream_outcome", "outcome_horizon", "process_metrics", "eval_candidates", "shadow_comparisons", "learning_disposition", "telemetry", "closure"]);
const RELIABILITY_ALLOWED = RELIABILITY_REQUIRED;

/** The DB read is payload-only; every panel fact uses this one MCP wrapper. */
export function activationReliabilityWire(payload) {
  return { job_passport: { schema_version: "job-passport-wire.v1", kind: "activation_reliability_projection", payload } };
}

function containsRawReceiptContent(value) {
  if (typeof value === "string") return !BOUNDED_SCALAR.test(value);
  if (Array.isArray(value)) return value.some(containsRawReceiptContent);
  if (!value || typeof value !== "object") return false;
  return Object.entries(value).some(([key, nested]) => RAW_RECEIPT_KEYS.has(key) || containsRawReceiptContent(nested));
}

function exactObject(value, required, allowed = required) {
  return !!value && typeof value === "object" && !Array.isArray(value)
    && [...required].every((key) => Object.hasOwn(value, key))
    && Object.keys(value).every((key) => allowed.has(key));
}

// This is intentionally a structural admission check, duplicated by the
// security-definer database trigger.  It keeps malformed nested facts from
// reaching either persistence path while the database derives tenant, plan,
// closure and timestamp facts itself.
export function strictAttemptReceiptShape(receipt) {
  if (!exactObject(receipt, RECEIPT_REQUIRED, RECEIPT_ALLOWED) || containsRawReceiptContent(receipt)) return false;
  const knowledge = receipt.knowledge_activation;
  if (!exactObject(knowledge, KNOWLEDGE_REQUIRED) || !Array.isArray(knowledge.item_dispositions)
    || !exactObject(knowledge.closure, new Set(["state", "unresolved_required_item_refs", "derived_by"]))) return false;
  if (!exactObject(knowledge.canonical_binding, new Set(["work_request_id", "work_request_version", "accepted_plan_digest", "envelope_digest", "activation_binding_ref"]))
    || !WR.test(knowledge.canonical_binding.work_request_id) || !Number.isInteger(knowledge.canonical_binding.work_request_version) || knowledge.canonical_binding.work_request_version < 1
    || !DIGEST.test(knowledge.canonical_binding.accepted_plan_digest) || !DIGEST.test(knowledge.canonical_binding.envelope_digest)
    || typeof knowledge.canonical_binding.activation_binding_ref !== "string") return false;
  if (knowledge.closure.derived_by !== "server" || !Array.isArray(knowledge.closure.unresolved_required_item_refs)) return false;
  if (knowledge.item_dispositions.some((row) => !exactObject(row, new Set(["item_ref", "disposition", "evidence_refs", "reason_ref"]), new Set(["item_ref", "disposition", "evidence_refs", "reason_ref", "stage_ref", "tool_ref"]))
    || !/^[A-Za-z][A-Za-z0-9._:-]{2,127}$/.test(row.item_ref) || !/^[A-Za-z][A-Za-z0-9._:-]{2,127}$/.test(row.reason_ref) || !Array.isArray(row.evidence_refs) || row.evidence_refs.some((ref) => !/^[A-Za-z][A-Za-z0-9._:-]{2,127}$/.test(ref)) || (row.disposition === "applied" && (!row.evidence_refs.length || (!row.stage_ref && !row.tool_ref))))) return false;
  const reliability = receipt.reliability;
  const refs = (value) => Array.isArray(value) && value.every((row) => /^[A-Za-z][A-Za-z0-9._:-]{2,127}$/.test(row));
  const groundingFields = new Set(["state", "evidence_refs", "required_supplied", "required_used", "required_missing", "advisory_supplied", "advisory_used", "freshness_failures", "retrieval_failures"]);
  const trajectoryFields = new Set(["sequence", "stage_ref", "parent_event_ref", "decision_class", "tool_class", "result_state", "fallback_state", "guardrail_state", "latency_ms", "evidence_refs"]);
  const evaluatorFields = new Set(["kind", "evaluator_ref", "rubric_ref", "evaluator_version", "evaluator_digest", "status", "confidence", "critical", "independence_state", "held_out_case_count", "check_refs", "dimension_refs", "evidence_refs", "judge_provenance", "calibration_evidence_refs"]);
  const horizonFields = new Set(["state", "ends_at", "as_of", "evidence_refs"]);
  const humanFields = new Set(["state", "actor_ref", "evidence_refs", "outcome_feedback_ref", "outcome_feedback_hash"]);
  const outcomeFields = new Set(["state", "brokerage_ref", "evidence_refs", "outcome_feedback_ref", "outcome_feedback_hash"]);
  const metricFields = new Set(["latency_ms", "cost_usd", "input_tokens", "output_tokens", "cached_input_tokens", "retry_count", "recovery_count", "context_reconstruction_ms", "human_intervention_count", "security_event_refs"]);
  const canonicalFactFields = new Set(["event_ref", "kind", "evidence_refs", "summary"]);
  if (!exactObject(reliability, RELIABILITY_REQUIRED, RELIABILITY_ALLOWED)
    || !DIGEST.test(reliability.route_digest) || !DIGEST.test(reliability.topology_digest) || !DIGEST.test(reliability.evaluation_plan_digest)
    || !exactObject(reliability.grounding_sufficiency, groundingFields) || !["sufficient", "insufficient", "unknown"].includes(reliability.grounding_sufficiency.state)
    || !["required_supplied", "required_used", "required_missing", "advisory_supplied", "advisory_used", "freshness_failures", "retrieval_failures", "evidence_refs"].every((field) => refs(reliability.grounding_sufficiency[field]))
    || !Array.isArray(reliability.deterministic_checks) || !Array.isArray(reliability.telemetry) || reliability.telemetry.length !== 0 || !Array.isArray(reliability.trajectory) || !Array.isArray(reliability.evaluator_results) || reliability.evaluator_results.length === 0
    || !Array.isArray(reliability.corrections) || !Array.isArray(reliability.defects) || !Array.isArray(reliability.incidents) || !Array.isArray(reliability.eval_candidates) || reliability.eval_candidates.length !== 0 || !Array.isArray(reliability.shadow_comparisons) || reliability.shadow_comparisons.length !== 0
    || !exactObject(reliability.human_acceptance, humanFields) || !["accepted", "rejected", "absent", "unknown"].includes(reliability.human_acceptance.state) || !refs(reliability.human_acceptance.evidence_refs)
    || !exactObject(reliability.downstream_outcome, outcomeFields) || !["observed", "not_observed", "unknown"].includes(reliability.downstream_outcome.state) || !refs(reliability.downstream_outcome.evidence_refs)
    || !exactObject(reliability.outcome_horizon, horizonFields) || !["mature", "immature", "unavailable", "stale", "unknown"].includes(reliability.outcome_horizon.state) || !refs(reliability.outcome_horizon.evidence_refs)
    || !exactObject(reliability.process_metrics, metricFields) || !Object.entries(reliability.process_metrics).every(([key, value]) => key === "cost_usd" ? typeof value === "number" && Number.isFinite(value) && value >= 0 : key === "security_event_refs" ? refs(value) : Number.isInteger(value) && value >= 0)
    || !exactObject(reliability.closure, new Set(["state", "reasons", "derived_by"])) || reliability.closure.derived_by !== "server" || !["blocked", "insufficient_evidence", "eligible_for_human_review"].includes(reliability.closure.state) || !refs(reliability.closure.reasons)) return false;
  if (reliability.deterministic_checks.some((row) => !exactObject(row, new Set(["check_id", "state", "critical", "evidence_refs"])) || !Array.isArray(row.evidence_refs))
    || reliability.trajectory.some((row, index) => !exactObject(row, trajectoryFields) || !Number.isInteger(row.sequence) || row.sequence !== index + 1 || !refs(row.evidence_refs) || (row.parent_event_ref !== null && typeof row.parent_event_ref !== "string") || !Number.isInteger(row.latency_ms) || row.latency_ms < 0)
    || reliability.evaluator_results.some((row) => !exactObject(row, evaluatorFields) || !["deterministic", "judge", "human_acceptance"].includes(row.kind) || !["passed", "failed", "blocked", "unknown", "not_run"].includes(row.status) || !["not_independent", "unknown"].includes(row.independence_state) || !Number.isInteger(row.held_out_case_count) || row.held_out_case_count < 0 || !DIGEST.test(row.evaluator_digest) || !refs(row.check_refs) || !refs(row.dimension_refs) || !refs(row.evidence_refs) || !refs(row.calibration_evidence_refs))) return false;
  for (const [lane, kind] of [[reliability.corrections, "correction"], [reliability.defects, "defect"], [reliability.incidents, "incident"]]) {
    if (lane.some((row) => !exactObject(row, canonicalFactFields) || row.kind !== kind || typeof row.event_ref !== "string" || !/^[A-Za-z][A-Za-z0-9._:-]{2,127}$/.test(row.event_ref) || typeof row.summary !== "string" || !/^[A-Za-z][A-Za-z0-9._:-]{2,127}$/.test(row.summary) || !refs(row.evidence_refs) || row.evidence_refs.length === 0)) return false;
  }
  for (const [row, accepted] of [[reliability.human_acceptance, reliability.human_acceptance.state === "accepted"], [reliability.downstream_outcome, reliability.downstream_outcome.state === "observed"]]) {
    if (accepted ? (typeof row.outcome_feedback_ref !== "string" || !/^[A-Za-z][A-Za-z0-9._:-]{2,127}$/.test(row.outcome_feedback_ref) || typeof row.outcome_feedback_hash !== "string" || !DIGEST.test(row.outcome_feedback_hash)) : (row.outcome_feedback_ref !== null || row.outcome_feedback_hash !== null)) return false;
  }
  return true;
}

export function evidenceActivationTools({ withEnvelope, ToolError }) {
  return {
    "assign-execution-route": {
      description: "HUMAN-ONLY: record one authority-approved immutable profile/environment/policy route for a ready Work Request. It does not execute work, issue capability, or promote a feature.",
      write: true, humanOnly: true, authorityOnly: true,
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { human_ref: { type: "string" }, profile_key: { type: "string", pattern: "^[a-z][a-z0-9-]{1,63}$" }, environment: { type: "string", enum: ["local", "rehearsal", "staging", "production"] }, policy_ref: { type: "string" }, policy_digest: { type: "string", pattern: "^sha256:[0-9a-f]{64}$" }, idempotency_key: { type: "string" } },
        required: ["human_ref", "profile_key", "environment", "policy_ref", "policy_digest", "idempotency_key"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "assign-execution-route", args, async () => {
        if (!WR.test(String(args.human_ref || "")) || !UUID.test(String(args.idempotency_key || ""))) throw new ToolError({ error: "execution_route_reference_invalid" });
        await c.query("select set_config('carr.organization_tenant_id',$1::text,true) /* assign-execution-route:tenant */", [organizationTenantForActor(actor)]);
        const row = (await c.query(
          "select * from ops.assign_execution_profile($1::text,$2::text,$3::text,$4::text,$5::text,$6::uuid) /* assign-execution-route */",
          [args.human_ref, args.profile_key, args.environment, args.policy_ref, args.policy_digest, args.idempotency_key],
        )).rows[0];
        if (!row) throw new ToolError({ error: "execution_route_refused" });
        return { ok: true, human_ref: args.human_ref, assignment_id: row.assignment_id, replayed: row.replayed };
      }),
    },
    "activate-context-bundle": {
      description: "Compile and bind bounded canonical knowledge to one accepted plan. The caller supplies no artifact bodies, refs, revisions, required flags, tenant, or authority. Recording an AttemptReceipt remains a separate strict admission path.",
      write: true,
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { human_ref: { type: "string" }, plan_ref: { type: "string" }, idempotency_key: { type: "string" } },
        required: ["human_ref", "plan_ref", "idempotency_key"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "activate-context-bundle", args, async () => {
        if (!WR.test(String(args.human_ref || "")) || !/^PLAN-[0-9a-f]{12}-v[1-9][0-9]*$/.test(String(args.plan_ref || "")) || !UUID.test(String(args.idempotency_key || "")))
          throw new ToolError({ error: "activation_reference_invalid" });
        const tenant = organizationTenantForActor(actor);
        const compiled = await c.query("select ops.compile_context_bundle($1::text,$2::text,$3::text) as bundle /* activate-context-bundle:compile */", [args.human_ref, args.plan_ref, tenant]);
        const bundle = compiled.rows[0]?.bundle;
        if (!bundle) throw new ToolError({ error: "activation_plan_not_ready" });
        const result = await c.query("select * from ops.activate_context_bundle($1::text,$2::text,$3::jsonb,$4::uuid) /* activate-context-bundle:activate */", [args.human_ref, args.plan_ref, JSON.stringify(bundle), args.idempotency_key]);
        const row = result.rows[0];
        if (!row) throw new ToolError({ error: "activation_refused" });
        return { ok: true, human_ref: args.human_ref, plan_ref: args.plan_ref, binding_id: row.binding_id, bundle_digest: row.bundle_digest, replayed: row.replayed };
      }),
    },
    "read-context-activation": {
      description: "Read the redacted, exact context activation projection for one Work Request and binding. Tenant scope is derived from the authenticated actor.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { human_ref: { type: "string" }, binding_id: { type: "string" } },
        required: ["human_ref", "binding_id"],
      },
      handler: async (c, actor, args) => {
        if (!WR.test(String(args.human_ref || "")) || !/^ctx-[0-9a-f]{16}$/.test(String(args.binding_id || "")))
          throw new ToolError({ error: "activation_reference_invalid" });
        await c.query("select set_config('carr.organization_tenant_id',$1::text,true) /* read-context-activation:tenant */", [organizationTenantForActor(actor)]);
        const row = (await c.query(
          "select ops.read_context_activation($1::text,$2::text) as activation /* read-context-activation */",
          [args.human_ref, args.binding_id],
        )).rows[0]?.activation;
        if (!row) throw new ToolError({ error: "activation_not_found" });
        return { ok: true, human_ref: args.human_ref, binding_id: args.binding_id, activation: row };
      },
    },
    "render-context-activation": {
      description: "Render ephemeral, exact frozen context revisions for a fresh governed session. Required missing, stale, or digest-mismatched revisions refuse; bodies are never persisted in the activation bundle or receipt.",
      inputSchema: { type: "object", additionalProperties: false, properties: { human_ref: { type: "string" }, binding_id: { type: "string" } }, required: ["human_ref", "binding_id"] },
      handler: async (c, actor, args) => {
        if (!WR.test(String(args.human_ref || "")) || !/^ctx-[0-9a-f]{16}$/.test(String(args.binding_id || ""))) throw new ToolError({ error: "activation_reference_invalid" });
        await c.query("select set_config('carr.organization_tenant_id',$1::text,true) /* render-context-activation:tenant */", [organizationTenantForActor(actor)]);
        const rows = (await c.query("select ops.render_context_activation_for_brief($1::text,$2::text) as items /* render-context-activation */", [args.human_ref, args.binding_id])).rows[0]?.items;
        if (!Array.isArray(rows)) throw new ToolError({ error: "context_render_refused" });
        return { ok: true, human_ref: args.human_ref, binding_id: args.binding_id, ephemeral: true, items: rows };
      },
    },
    "issue-execution-envelope": {
      description: "Issue and persist the existing ExecutionEnvelope v1 for one exact activation binding. Runtime, topology, evaluation, tenant, identity, authority, and configuration are server-derived bounded metadata; this grants no new capability.",
      write: true,
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { human_ref: { type: "string" }, binding_id: { type: "string" }, idempotency_key: { type: "string" } },
        required: ["human_ref", "binding_id", "idempotency_key"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "issue-execution-envelope", args, async () => {
        if (!WR.test(String(args.human_ref || "")) || !/^ctx-[0-9a-f]{16}$/.test(String(args.binding_id || "")) || !UUID.test(String(args.idempotency_key || "")))
          throw new ToolError({ error: "execution_envelope_reference_invalid" });
        const row = (await c.query(
          "select * from ops.issue_execution_envelope_v1($1::text,$2::text,$3::uuid) /* issue-execution-envelope */",
          [args.human_ref, args.binding_id, args.idempotency_key],
        )).rows[0];
        if (!row) throw new ToolError({ error: "execution_envelope_refused" });
        return { ok: true, human_ref: args.human_ref, binding_id: args.binding_id, envelope_digest: row.envelope_digest, replayed: row.replayed };
      }),
    },
    "record-attempt-receipt": {
      description: "Persist one redacted existing AttemptReceipt v1 against an existing activation binding. Tenant, Work Request plan hash, and timestamps are derived by the server; this does not promote an outcome or learning candidate.",
      write: true,
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { human_ref: { type: "string" }, binding_id: { type: "string" }, receipt: { type: "object" }, idempotency_key: { type: "string" } },
        required: ["human_ref", "binding_id", "receipt", "idempotency_key"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-attempt-receipt", args, async () => {
        if (!WR.test(String(args.human_ref || "")) || !/^ctx-[0-9a-f]{16}$/.test(String(args.binding_id || ""))
          || !UUID.test(String(args.idempotency_key || "")) || !args.receipt || typeof args.receipt !== "object" || Array.isArray(args.receipt))
          throw new ToolError({ error: "attempt_receipt_reference_invalid" });
        if (args.receipt.schema_version !== "attempt-receipt.v1" || typeof args.receipt.envelope_digest !== "string"
          || typeof args.receipt.attempt_id !== "string" || !strictAttemptReceiptShape(args.receipt))
          throw new ToolError({ error: "attempt_receipt_redaction_or_shape_invalid" });
        await c.query("select set_config('carr.organization_tenant_id',$1::text,true) /* record-attempt-receipt:tenant */", [organizationTenantForActor(actor)]);
        const binding = (await c.query(
          "select * from ops.context_activation_receipt_binding($1::text,$2::text) /* record-attempt-receipt:binding */",
          [args.human_ref, args.binding_id],
        )).rows[0];
        if (!binding) throw new ToolError({ error: "activation_not_found" });
        const result = await c.query(
          "select * from ops.record_attempt_receipt($1::text,$2::text,$3::text,$4::uuid,$5::jsonb,$6::uuid) /* record-attempt-receipt */",
          [args.human_ref, binding.plan_hash, args.receipt.envelope_digest, binding.binding_pk, JSON.stringify(args.receipt), args.idempotency_key],
        );
        const row = result.rows[0];
        if (!row) throw new ToolError({ error: "attempt_receipt_refused" });
        return { ok: true, human_ref: args.human_ref, binding_id: args.binding_id, attempt_id: row.attempt_id, replayed: row.replayed };
      }),
    },
    "propose-evaluation-case": {
      description: "Create or replay one proposed evaluation case from an already-admitted redacted correction, defect, or incident. The caller selects only the AttemptReceipt fact; case, root cause, lane, risk, split, evidence basis, and golden-set target are server-derived. This does not add a golden case or promote a route.",
      write: true,
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { attempt_id: { type: "string", pattern: "^[A-Za-z][A-Za-z0-9._:-]{2,127}$" }, fact_event_ref: { type: "string", pattern: "^[A-Za-z][A-Za-z0-9._:-]{2,127}$" }, idempotency_key: { type: "string" } },
        required: ["attempt_id", "fact_event_ref", "idempotency_key"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "propose-evaluation-case", args, async () => {
        if (!UUID.test(String(args.idempotency_key || ""))) throw new ToolError({ error: "evaluation_case_reference_invalid" });
        await c.query("select set_config('carr.organization_tenant_id',$1::text,true) /* propose-evaluation-case:tenant */", [organizationTenantForActor(actor)]);
        const row = (await c.query(
          "select * from ops.propose_eval_candidate($1::text,$2::text,$3::uuid) /* propose-evaluation-case */",
          [args.attempt_id, args.fact_event_ref, args.idempotency_key],
        )).rows[0];
        if (!row) throw new ToolError({ error: "evaluation_case_proposal_refused" });
        return { ok: true, candidate_ref: row.candidate_ref, lifecycle: row.lifecycle, promotion_state: row.promotion_state, replayed: row.replayed };
      }),
    },
    "attest-attempt-evaluation": {
      description: "HUMAN-ONLY: append one authority-observed evaluation result bound to an existing AttemptReceipt and its frozen evaluation plan. This derives review eligibility only; it never activates or promotes a model or route.",
      write: true, humanOnly: true, authorityOnly: true,
      inputSchema: { type: "object", additionalProperties: false, properties: { attempt_id: { type: "string", pattern: "^[A-Za-z][A-Za-z0-9._:-]{2,127}$" }, evaluator_kind: { type: "string", enum: ["deterministic", "judge", "human_acceptance", "outcome_horizon"] }, check_ref: { type: "string", pattern: "^[A-Za-z][A-Za-z0-9._:-]{0,127}$" }, dimension_refs: { type: "array", minItems: 1, items: { type: "string", pattern: "^[A-Za-z][A-Za-z0-9._:-]{2,127}$" } }, status: { type: "string" }, independent: { type: "boolean" }, evidence_refs: { type: "array", minItems: 1, items: { type: "string", pattern: "^[A-Za-z][A-Za-z0-9._:-]{2,127}$" } }, evaluation_metadata: { type: "object", additionalProperties: false, properties: { evaluator_ref: { type: "string", pattern: "^[A-Za-z][A-Za-z0-9._:-]{2,127}$" }, rubric_ref: { type: "string", pattern: "^[A-Za-z][A-Za-z0-9._:-]{2,127}$" }, evaluator_version: { type: "string", pattern: "^[A-Za-z][A-Za-z0-9._:-]{2,127}$" }, evaluator_digest: { type: "string", pattern: "^sha256:[a-f0-9]{64}$" }, confidence: { type: "string", enum: ["high", "medium", "low", "unknown"] }, held_out_case_count: { type: "integer", minimum: 0 }, calibration_refs: { type: "array", items: { type: "string", pattern: "^[A-Za-z][A-Za-z0-9._:-]{2,127}$" } }, lower_bound_ref: { type: ["string", "null"], pattern: "^[A-Za-z][A-Za-z0-9._:-]{2,127}$" } }, required: ["evaluator_ref", "rubric_ref", "evaluator_version", "evaluator_digest", "confidence", "held_out_case_count", "calibration_refs", "lower_bound_ref"] }, outcome_feedback_ref: { type: ["string", "null"], pattern: "^[A-Za-z][A-Za-z0-9._:-]{2,127}$" }, outcome_feedback_hash: { type: ["string", "null"], pattern: "^sha256:[a-f0-9]{64}$" }, idempotency_key: { type: "string" } }, required: ["attempt_id", "evaluator_kind", "check_ref", "dimension_refs", "status", "independent", "evidence_refs", "evaluation_metadata", "outcome_feedback_ref", "outcome_feedback_hash", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "attest-attempt-evaluation", args, async () => {
        if (!UUID.test(String(args.idempotency_key || "")) || !Array.isArray(args.dimension_refs) || !Array.isArray(args.evidence_refs) || containsRawReceiptContent(args)) throw new ToolError({ error: "evaluation_attestation_reference_or_redaction_invalid" });
        await c.query("select set_config('carr.organization_tenant_id',$1::text,true) /* attest-attempt-evaluation:tenant */", [organizationTenantForActor(actor)]);
        const row = (await c.query("select * from ops.attest_attempt_receipt_evaluation($1::text,$2::text,$3::text,$4::jsonb,$5::text,$6::boolean,$7::jsonb,$8::jsonb,$9::text,$10::text,$11::uuid) /* attest-attempt-evaluation */", [args.attempt_id, args.evaluator_kind, args.check_ref, JSON.stringify(args.dimension_refs), args.status, args.independent, JSON.stringify(args.evidence_refs), JSON.stringify(args.evaluation_metadata), args.outcome_feedback_ref, args.outcome_feedback_hash, args.idempotency_key])).rows[0];
        if (!row) throw new ToolError({ error: "evaluation_attestation_refused" });
        return { ok: true, attestation_id: row.attestation_id, replayed: row.replayed };
      }),
    },
    "read-attempt-reliability": {
      description: "Read canonical authority-derived reliability posture for one tenant-scoped AttemptReceipt. Executor claims are not promotion evidence.",
      inputSchema: { type: "object", additionalProperties: false, properties: { attempt_id: { type: "string" } }, required: ["attempt_id"] },
      handler: async (c, actor, args) => {
        await c.query("select set_config('carr.organization_tenant_id',$1::text,true) /* read-attempt-reliability:tenant */", [organizationTenantForActor(actor)]);
        const reliability = (await c.query("select ops.read_attempt_receipt_reliability($1::text) as reliability /* read-attempt-reliability */", [args.attempt_id])).rows[0]?.reliability;
        if (!reliability) throw new ToolError({ error: "attempt_reliability_not_found" });
        return { ok: true, reliability, ...activationReliabilityWire(reliability) };
      },
    },
    "transition-evaluation-case": {
      description: "HUMAN-ONLY: advance one proposed evaluation case exactly proposed→triaged→accepted→retired. Acceptance adds a lane/risk/split golden membership projection; retirement leaves its append-only history intact and makes it inactive. This cannot promote a model, provider, or workflow.",
      write: true, humanOnly: true, authorityOnly: true,
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { human_ref: { type: "string" }, candidate_ref: { type: "string", pattern: "^[A-Za-z][A-Za-z0-9._:-]{2,127}$" }, next_state: { type: "string", enum: ["triaged", "accepted", "retired"] }, decision_basis: { type: "object" }, idempotency_key: { type: "string" } },
        required: ["human_ref", "candidate_ref", "next_state", "decision_basis", "idempotency_key"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "transition-evaluation-case", args, async () => {
        if (!WR.test(String(args.human_ref || "")) || !UUID.test(String(args.idempotency_key || "")) || containsRawReceiptContent(args.decision_basis))
          throw new ToolError({ error: "evaluation_case_transition_reference_or_redaction_invalid" });
        await c.query("select set_config('carr.organization_tenant_id',$1::text,true) /* transition-evaluation-case:tenant */", [organizationTenantForActor(actor)]);
        const row = (await c.query(
          "select * from ops.transition_proposed_eval_candidate($1::text,$2::text,$3::text,$4::jsonb,$5::uuid) /* transition-evaluation-case */",
          [args.human_ref, args.candidate_ref, args.next_state, JSON.stringify(args.decision_basis), args.idempotency_key],
        )).rows[0];
        if (!row) throw new ToolError({ error: "evaluation_case_transition_refused" });
        return { ok: true, candidate_ref: args.candidate_ref, lifecycle: row.lifecycle, golden_member: row.golden_member };
      }),
    },
  };
}
