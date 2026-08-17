// The deliberately small Program 6 bridge: source a problem from the current
// deterministic situation index, capture it, and expose a safe read card. It
// does not own a lifecycle transition, an executor, or an approval.
import { searchDoctrineSituations } from "./situation-retrieval.js";
import { organizationTenantForActor } from "./identity.js";

const FIELDS = new Set(["idempotency_key", "situation", "title", "desired_outcome", "acceptance_criteria"]);
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const CRITERION_ID = /^[A-Z][A-Z0-9-]{1,63}$/;
const TRIAGE_FIELDS = new Set(["idempotency_key", "human_ref", "base_version", "classification"]);
const TRIAGE_CLASSES = new Set(["operational", "needs_judgment", "safety_review"]);
const PLAN_FIELDS = new Set(["idempotency_key","human_ref","base_version","scope_summary","runbook_ref","dependency_refs","recovery_ref","observability_ref","caps"]);
const ACCEPT_PLAN_FIELDS = new Set(["idempotency_key","human_ref","base_version","plan_hash"]);
const OUTCOME_PROPOSAL_FIELDS = new Set(["idempotency_key","human_ref","base_version","plan_hash","criterion_results","evidence_refs","blocker_code","result_summary","observed_minutes","interaction_surface","heavy_session_used","manual_context_transfers"]);
const ACCEPT_OUTCOME_FIELDS = new Set(["idempotency_key","human_ref","base_version","feedback_hash"]);
const SAFE_REF = /^safe:[a-z0-9][a-z0-9:_./-]*$/;
const CRITERION_RESULT = new Set(["met", "not_met", "not_observed"]);
const INTERACTION_SURFACE = new Set(["workspace", "control_room", "mcp", "codex", "claude_code", "other"]);
const OUTCOME_BLOCKER = new Set(["none", "evidence_missing", "criterion_not_met", "external_dependency", "system_error"]);

function text(value) { return typeof value === "string" ? value.trim() : ""; }

function validate(args, ToolError) {
  if (Object.keys(args).some(key => !FIELDS.has(key)))
    throw new ToolError({ error: "invalid_report_problem_fields" });
  if (!UUID.test(args.idempotency_key || "") || !text(args.situation) || text(args.situation).length > 1000 ||
      !text(args.title) || text(args.title).length > 200 || !text(args.desired_outcome) || text(args.desired_outcome).length > 2000 ||
      !Array.isArray(args.acceptance_criteria) || !args.acceptance_criteria.length || args.acceptance_criteria.length > 12)
    throw new ToolError({ error: "invalid_report_problem" });
  const ids = new Set();
  for (const criterion of args.acceptance_criteria) {
    if (!criterion || typeof criterion !== "object" || Object.keys(criterion).some(key => key !== "id" && key !== "text") ||
        !text(criterion.id) || !CRITERION_ID.test(criterion.id.trim()) || !text(criterion.text) || text(criterion.text).length > 500 ||
        ids.has(criterion.id.trim()))
      throw new ToolError({ error: "invalid_acceptance_criteria" });
    ids.add(criterion.id.trim());
  }
}

function sourceProjection(row) {
  return { label: row.doctrine_source_label || row.source_ref || null,
    freshness: row.source_current === false ? "stale" : "current",
    provenance: row.source_provenance || {
      doctrine_section_id: row.doctrine_section_id || null,
      doctrine_revision_id: row.doctrine_revision_id || null,
    } };
}

function outcomeFeedbackProjection(row) {
  return { feedback_ref: row.feedback_ref || null, feedback_hash: row.feedback_hash,
    outcome: row.outcome, criterion_results: row.criterion_results || [], evidence_refs: row.evidence_refs || [],
    blocker_code: row.blocker_code, result_summary: row.result_summary,
    observed_minutes: Number(row.observed_minutes), interaction_surface: row.interaction_surface,
    heavy_session_used: row.heavy_session_used, manual_context_transfers: Number(row.manual_context_transfers),
    accepted_by_actor_slug: row.accepted_by_actor_slug, accepted_at: row.accepted_at };
}

function validateTriage(args, ToolError) {
  if (Object.keys(args).some(key => !TRIAGE_FIELDS.has(key))) throw new ToolError({ error: "invalid_triage_fields" });
  if (!UUID.test(args.idempotency_key || "") || !/^WR-[0-9]{1,12}$/.test(args.human_ref || "") ||
      !Number.isInteger(args.base_version) || args.base_version < 1 || !TRIAGE_CLASSES.has(args.classification))
    throw new ToolError({ error: "invalid_triage" });
}

function validatePlan(args, ToolError) {
  if (Object.keys(args).some(k => !PLAN_FIELDS.has(k))) throw new ToolError({ error: "invalid_ready_plan_fields" });
  if (!UUID.test(args.idempotency_key || "") || !/^WR-[0-9]{1,12}$/.test(args.human_ref || "") || !Number.isInteger(args.base_version) || args.base_version < 1 ||
      !text(args.scope_summary) || text(args.scope_summary).length > 1000 || !/^doctrine:runbook#[a-z0-9][a-z0-9-]*$/.test(args.runbook_ref || "") ||
      typeof args.recovery_ref !== "string" || args.recovery_ref.length > 300 || typeof args.observability_ref !== "string" || args.observability_ref.length > 300 || !SAFE_REF.test(args.recovery_ref || "") || !SAFE_REF.test(args.observability_ref || "") || !args.caps || Object.keys(args.caps).sort().join(",") !== "max_duration_minutes,max_steps" ||
      !Number.isInteger(args.caps.max_steps) || args.caps.max_steps < 1 || args.caps.max_steps > 20 || !Number.isInteger(args.caps.max_duration_minutes) || args.caps.max_duration_minutes < 1 || args.caps.max_duration_minutes > 120 ||
      !Array.isArray(args.dependency_refs) || args.dependency_refs.length > 12 || args.dependency_refs.some(x => typeof x !== "string" || x.length > 300 || !SAFE_REF.test(x)) || new Set(args.dependency_refs).size !== args.dependency_refs.length)
    throw new ToolError({ error: "invalid_ready_plan" });
}
function validateAcceptPlan(args, ToolError) {
  if (Object.keys(args).some(k => !ACCEPT_PLAN_FIELDS.has(k))) throw new ToolError({ error: "invalid_accept_plan_fields" });
  if (!UUID.test(args.idempotency_key || "") || !/^WR-[0-9]{1,12}$/.test(args.human_ref || "") || !Number.isInteger(args.base_version) || args.base_version < 1 || !/^sha256:[0-9a-f]{64}$/.test(args.plan_hash || "")) throw new ToolError({ error: "invalid_accept_plan" });
}

function validateOutcomeProposal(args, ToolError) {
  if (Object.keys(args).some(k => !OUTCOME_PROPOSAL_FIELDS.has(k))) throw new ToolError({ error: "invalid_outcome_feedback_fields" });
  if (!UUID.test(args.idempotency_key || "") || !/^WR-[0-9]{1,12}$/.test(args.human_ref || "") ||
      !Number.isInteger(args.base_version) || args.base_version < 1 || !/^sha256:[0-9a-f]{64}$/.test(args.plan_hash || "") ||
      !Array.isArray(args.criterion_results) || !args.criterion_results.length || args.criterion_results.length > 12 ||
      !Array.isArray(args.evidence_refs) || !args.evidence_refs.length || args.evidence_refs.length > 12 ||
      args.evidence_refs.some(x => typeof x !== "string" || x.length > 300 || !SAFE_REF.test(x)) ||
      new Set(args.evidence_refs).size !== args.evidence_refs.length ||
      !OUTCOME_BLOCKER.has(args.blocker_code) || !text(args.result_summary) || text(args.result_summary).length > 500 ||
      !Number.isInteger(args.observed_minutes) || args.observed_minutes < 1 || args.observed_minutes > 1440 ||
      !INTERACTION_SURFACE.has(args.interaction_surface) || typeof args.heavy_session_used !== "boolean" ||
      !Number.isInteger(args.manual_context_transfers) || args.manual_context_transfers < 0 || args.manual_context_transfers > 100)
    throw new ToolError({ error: "invalid_outcome_feedback" });
  const ids = new Set();
  for (const criterion of args.criterion_results) {
    if (!criterion || typeof criterion !== "object" || Object.keys(criterion).some(k => k !== "id" && k !== "result") ||
        !text(criterion.id) || !CRITERION_ID.test(criterion.id.trim()) || !CRITERION_RESULT.has(criterion.result) ||
        ids.has(criterion.id.trim()))
      throw new ToolError({ error: "invalid_outcome_criteria" });
    ids.add(criterion.id.trim());
  }
  const results = args.criterion_results.map(criterion => criterion.result);
  const outcome = results.includes("not_met") ? "criteria_not_met" : results.every(result => result === "met") ? "criteria_met" : "inconclusive";
  if ((outcome === "criteria_met" && args.blocker_code !== "none") ||
      (outcome === "criteria_not_met" && args.blocker_code === "none") ||
      (outcome === "inconclusive" && !new Set(["evidence_missing", "external_dependency", "system_error"]).has(args.blocker_code)))
    throw new ToolError({ error: "inconsistent_outcome_feedback" });
}

function validateOutcomeAcceptance(args, ToolError) {
  if (Object.keys(args).some(k => !ACCEPT_OUTCOME_FIELDS.has(k))) throw new ToolError({ error: "invalid_accept_outcome_fields" });
  if (!UUID.test(args.idempotency_key || "") || !/^WR-[0-9]{1,12}$/.test(args.human_ref || "") ||
      !Number.isInteger(args.base_version) || args.base_version < 1 || !/^sha256:[0-9a-f]{64}$/.test(args.feedback_hash || ""))
    throw new ToolError({ error: "invalid_accept_outcome" });
}

export function workRequestIntakeTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "report-problem": {
      write: true,
      description: "Capture one operational problem from the current deterministic situation source. It only creates a captured Work Request; it never triages, assigns, dispatches, approves, executes, or changes an existing request.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string", pattern: "^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" },
        situation: { type: "string", minLength: 1, maxLength: 1000 }, title: { type: "string", minLength: 1, maxLength: 200 },
        desired_outcome: { type: "string", minLength: 1, maxLength: 2000 }, acceptance_criteria: { type: "array", minItems: 1, maxItems: 12,
          items: { type: "object", additionalProperties: false, required: ["id", "text"], properties: {
            id: { type: "string", minLength: 1, maxLength: 64 }, text: { type: "string", minLength: 1, maxLength: 500 },
          } } },
      }, additionalProperties: false, required: ["idempotency_key", "situation", "title", "desired_outcome", "acceptance_criteria"] },
      handler: async (c, actor, args) => {
        // Reject non-contract fields before the idempotency lock/read: first-call
        // serialization must not turn an invalid authority/source claim into I/O.
        validate(args, ToolError);
        // Bind this verb's replay hash to the authenticated actor. The shared
        // envelope predates actor-bound request hashes, so add the derived
        // identity only to its private hash input, never to the client schema.
        return withEnvelope(c, actor, "report-problem", { ...args, _server_actor_id: actor.id }, async () => {
        const retrieval = await searchDoctrineSituations(c, actor, { q: args.situation, limit: 20 });
        // Retrieval may legitimately include personal doctrine visible to this
        // actor. Program 6 cannot bind personal scope, so choose the first
        // ranked source that the database proves is shared, active, and current.
        const hits = retrieval.hits.filter(hit => hit.section_id);
        if (!hits.length) throw new ToolError({ error: "current_situation_source_not_found" });
        const shared = await c.query(
          `select ranked.section_id, s.current_revision_id
             from unnest($1::uuid[]) with ordinality as ranked(section_id, ordinal)
             join doctrine_section s on s.id=ranked.section_id
             join doctrine_document d on d.id=s.document_id
            where d.visibility='shared' and s.status='active' and s.current_revision_id is not null
            order by ranked.ordinal limit 1
             /* work-request-intake:highest-shared-source */`, [hits.map(hit => hit.section_id)]);
        const selected = shared.rows[0];
        const source = selected && hits.find(hit => hit.section_id === selected.section_id);
        if (!source || !selected.current_revision_id)
          throw new ToolError({ error: "current_situation_source_not_found" });
        const revisionId = selected.current_revision_id;
        const captured = await c.query(
          `select * from ops.capture_sourced_work_request($1::text, $2::text, $3::text,
             $4::jsonb, $5::uuid, $6::uuid, $7::uuid)
             /* work-request-intake:capture */`,
          [`doctrine:${source.doc_slug}#${source.section_key}`, args.title.trim(), args.desired_outcome.trim(),
            JSON.stringify(args.acceptance_criteria), source.section_id, revisionId, args.idempotency_key]);
        const row = captured.rows[0];
        if (!row) throw new ToolError({ error: "work_request_capture_refused" });
        await writeEvent(c, actor, "report-problem", "ops_work_request", row.id, {
          field: "state", new: { state: "captured", source_ref: `doctrine:${source.doc_slug}#${source.section_key}` }, idempotency_key: args.idempotency_key,
        });
        return { ok: true, human_ref: row.ref, state: row.state, version: Number(row.version),
          captured_at: row.captured_at || null, source: sourceProjection(row) };
        });
      },
    },
    "work-request-card": {
      write: false,
      description: "Read one same-tenant safe captured Work Request card. The card names the current or stale source and one human review label; it offers no executable actions.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        work_request: { type: "string", pattern: "^WR-[0-9]{1,12}$", minLength: 4, maxLength: 15 },
      }, required: ["work_request"] },
      handler: async (c, actor, args) => {
        const tenant = organizationTenantForActor(actor);
        const result = await c.query(
          `select * from ops.work_request_card($1::text, $2::text)
             /* work-request-intake:card */`, [args.work_request, tenant]);
        const row = result.rows[0];
        if (!row || !["captured", "triaged", "ready"].includes(row.state)) throw new ToolError({ error: "work_request_not_found" });
        const triaged = ["triaged", "ready"].includes(row.state);
        return { ok: true, human_ref: row.ref, title: row.title, desired_outcome: row.desired_outcome,
          acceptance_criteria: row.acceptance_criteria, state: row.state, projection_state: "queued", source: sourceProjection(row),
          triage: triaged ? { classification: row.triage_classification, human_actor_slug: row.triaged_by_actor_slug,
            triaged_at: row.triaged_at } : null,
          plan: row.plan_hash ? { plan_ref: row.plan_ref || null, plan_hash: row.plan_hash, runbook_ref: row.runbook_ref || null,
            scope_summary: row.scope_summary || null,
            runbook_revision_id: row.runbook_revision_id || null, runbook_content_hash: row.runbook_content_hash || null,
            caps: row.plan_caps || null, dependency_refs: row.dependency_refs || [], recovery_ref: row.recovery_ref || null,
            observability_ref: row.observability_ref || null } : null,
          outcome_feedback: row.outcome_feedback && typeof row.outcome_feedback === "object" ? outcomeFeedbackProjection(row.outcome_feedback) : null,
          outcome_feedback_history: Array.isArray(row.outcome_feedback_history) ? row.outcome_feedback_history.map(outcomeFeedbackProjection) : [],
          accepted_feedback_count: Number(row.accepted_feedback_count || 0),
          shape: row.shape_disposition ? { disposition: row.shape_disposition, fixed_surface_ref: row.shape_fixed_surface_ref || null } : null,
          next_human_action: row.state === "ready" ? (row.outcome_feedback ? { label: "Outcome feedback accepted", effect: "none" } : { label: "Plan accepted", effect: "none" }) : triaged ? { label: "Prepare scope and acceptance", effect: "none" } : { label: "Review and triage", effect: "none" },
          actions: [] };
      },
    },
    "review-and-triage": {
      write: true, humanOnly: true, authorityOnly: true,
      description: "HUMAN-ONLY: classify one sourced captured Work Request and make its sole allowed transition, captured to triaged. It never assigns, dispatches, approves, executes, or advances any later state.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string", pattern: "^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" },
        human_ref: { type: "string", pattern: "^WR-[0-9]{1,12}$", minLength: 4, maxLength: 15 },
        base_version: { type: "integer", minimum: 1 },
        classification: { type: "string", enum: ["operational", "needs_judgment", "safety_review"] },
      }, required: ["idempotency_key", "human_ref", "base_version", "classification"] },
      handler: async (c, actor, args) => {
        validateTriage(args, ToolError);
        // Bind replays to the authenticated actor without admitting actor data
        // into the closed client schema.
        return withEnvelope(c, actor, "review-and-triage", { ...args, _server_actor_id: actor.id }, async () => {
          const result = await c.query(
            `select * from ops.triage_sourced_work_request($1::text, $2::integer, $3::text, $4::uuid)
               /* work-request-intake:triage */`, [args.human_ref, args.base_version, args.classification, args.idempotency_key]);
          const row = result.rows[0];
          if (!row) throw new ToolError({ error: "version_conflict", human_ref: args.human_ref,
            resolution: "re-read the Work Request card; only its current captured version may be triaged" });
          await writeEvent(c, actor, "review-and-triage", "ops_work_request", row.id, {
            field: "state", old: { state: "captured", version: args.base_version },
            new: { state: "triaged", version: Number(row.version), classification: row.classification },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, human_ref: row.ref, state: row.state, version: Number(row.version),
            classification: row.classification, triaged_by_actor_slug: row.triaged_by_actor_slug,
            triaged_at: row.triaged_at };
        });
      },
    },
    "propose-ready-plan": {
      write: true,
      description: "Append one immutable, bounded ready-plan proposal to a triaged Work Request. It does not change request state, assign work, dispatch, execute, or approve.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key:{type:"string"}, human_ref:{type:"string"}, base_version:{type:"integer",minimum:1}, scope_summary:{type:"string",minLength:1,maxLength:1000}, runbook_ref:{type:"string"}, dependency_refs:{type:"array",maxItems:12,items:{type:"string"}}, recovery_ref:{type:"string"}, observability_ref:{type:"string"}, caps:{type:"object",additionalProperties:false,required:["max_steps","max_duration_minutes"],properties:{max_steps:{type:"integer",minimum:1,maximum:20},max_duration_minutes:{type:"integer",minimum:1,maximum:120}}}
      }, required:["idempotency_key","human_ref","base_version","scope_summary","runbook_ref","dependency_refs","recovery_ref","observability_ref","caps"] },
      handler: async (c,actor,args) => { validatePlan(args,ToolError); return withEnvelope(c,actor,"propose-ready-plan",{...args,_server_actor_id:actor.id},async()=>{
        const r=await c.query(`select * from ops.propose_sourced_work_request_plan($1::text,$2::integer,$3::text,$4::text,$5::jsonb,$6::text,$7::text,$8::jsonb,$9::uuid) /* work-request-intake:propose-ready-plan */`,[args.human_ref,args.base_version,args.scope_summary.trim(),args.runbook_ref,JSON.stringify(args.dependency_refs),args.recovery_ref,args.observability_ref,JSON.stringify(args.caps),args.idempotency_key]); const row=r.rows[0]; if(!row) throw new ToolError({error:"version_conflict"}); await writeEvent(c,actor,"propose-ready-plan","ops_work_request",row.work_request_id,{field:"ready_plan",new:{plan_ref:row.plan_ref,plan_hash:row.plan_hash,runbook_ref:row.runbook_ref},idempotency_key:args.idempotency_key}); return {ok:true,human_ref:row.ref,state:row.state,version:Number(row.version),plan_ref:row.plan_ref,plan_hash:row.plan_hash,scope_summary:row.scope_summary,runbook_ref:row.runbook_ref,runbook_revision_id:row.runbook_revision_id,runbook_content_hash:row.runbook_content_hash}; }); },
    },
    "accept-ready-plan": {
      write:true,humanOnly:true,authorityOnly:true,
      description:"HUMAN-ONLY: accept one exact immutable ready-plan hash and make the sole triaged-to-ready transition. It never dispatches, executes, assigns, or grants approval authority.",
      inputSchema:{type:"object",additionalProperties:false,properties:{idempotency_key:{type:"string"},human_ref:{type:"string"},base_version:{type:"integer",minimum:1},plan_hash:{type:"string",pattern:"^sha256:[0-9a-f]{64}$"}},required:["idempotency_key","human_ref","base_version","plan_hash"]},
      handler:async(c,actor,args)=>{validateAcceptPlan(args,ToolError);return withEnvelope(c,actor,"accept-ready-plan",{...args,_server_actor_id:actor.id},async()=>{const r=await c.query(`select * from ops.accept_sourced_work_request_plan($1::text,$2::integer,$3::text,$4::uuid) /* work-request-intake:accept-ready-plan */`,[args.human_ref,args.base_version,args.plan_hash,args.idempotency_key]);const row=r.rows[0];if(!row)throw new ToolError({error:"version_conflict"});await writeEvent(c,actor,"accept-ready-plan","ops_work_request",row.work_request_id,{field:"state",old:{state:"triaged",version:args.base_version},new:{state:"ready",plan_ref:row.plan_ref,plan_hash:row.plan_hash},idempotency_key:args.idempotency_key});return {ok:true,human_ref:row.ref,state:row.state,version:Number(row.version),plan_ref:row.plan_ref,plan_hash:row.plan_hash,accepted_by_actor_slug:row.accepted_by_actor_slug,accepted_at:row.accepted_at,shape_disposition:row.shape_disposition,shape_fixed_surface_ref:row.shape_fixed_surface_ref};});},
    },
    "propose-outcome-feedback": {
      write: true,
      description: "Propose evidence-bound outcome feedback for one accepted sourced ready plan. It creates no success claim, state transition, execution, assignment, dispatch, approval, or completion.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string", pattern: "^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" },
        human_ref: { type: "string", pattern: "^WR-[0-9]{1,12}$" }, base_version: { type: "integer", minimum: 1 },
        plan_hash: { type: "string", pattern: "^sha256:[0-9a-f]{64}$" },
        criterion_results: { type: "array", minItems: 1, maxItems: 12, items: { type: "object", additionalProperties: false, required: ["id", "result"], properties: { id: { type: "string", minLength: 1, maxLength: 64 }, result: { type: "string", enum: ["met", "not_met", "not_observed"] } } } },
        evidence_refs: { type: "array", minItems: 1, maxItems: 12, items: { type: "string", minLength: 6, maxLength: 300 } },
        blocker_code: { type: "string", enum: ["none", "evidence_missing", "criterion_not_met", "external_dependency", "system_error"] },
        result_summary: { type: "string", minLength: 1, maxLength: 500 },
        observed_minutes: { type: "integer", minimum: 1, maximum: 1440 },
        interaction_surface: { type: "string", enum: ["workspace", "control_room", "mcp", "codex", "claude_code", "other"] },
        heavy_session_used: { type: "boolean" }, manual_context_transfers: { type: "integer", minimum: 0, maximum: 100 },
      }, required: ["idempotency_key", "human_ref", "base_version", "plan_hash", "criterion_results", "evidence_refs", "blocker_code", "result_summary", "observed_minutes", "interaction_surface", "heavy_session_used", "manual_context_transfers"] },
      handler: async (c, actor, args) => {
        validateOutcomeProposal(args, ToolError);
        return withEnvelope(c, actor, "propose-outcome-feedback", { ...args, _server_actor_id: actor.id }, async () => {
          const result = await c.query(
            `select * from ops.propose_sourced_work_request_outcome_feedback($1::text,$2::integer,$3::text,$4::jsonb,$5::jsonb,$6::text,$7::text,$8::integer,$9::text,$10::boolean,$11::integer,$12::uuid)
               /* work-request-intake:propose-outcome-feedback */`,
            [args.human_ref, args.base_version, args.plan_hash, JSON.stringify(args.criterion_results), JSON.stringify(args.evidence_refs), args.blocker_code, text(args.result_summary), args.observed_minutes, args.interaction_surface, args.heavy_session_used, args.manual_context_transfers, args.idempotency_key]);
          const row = result.rows[0];
          if (!row) throw new ToolError({ error: "version_conflict" });
          await writeEvent(c, actor, "propose-outcome-feedback", "ops_work_request", row.work_request_id, {
            field: "outcome_feedback_proposed", new: { feedback_ref: row.feedback_ref, feedback_hash: row.feedback_hash, proposed_outcome: row.outcome, blocker_code: row.blocker_code }, idempotency_key: args.idempotency_key,
          });
          return { ok: true, human_ref: row.ref, state: row.state, version: Number(row.version), plan_ref: row.plan_ref,
            plan_hash: row.plan_hash, feedback_ref: row.feedback_ref, feedback_hash: row.feedback_hash,
            status: "pending_human_acceptance", proposed_outcome: row.outcome, criterion_results: row.criterion_results,
            evidence_refs: row.evidence_refs, blocker_code: row.blocker_code, result_summary: row.result_summary,
            observed_minutes: Number(row.observed_minutes), interaction_surface: row.interaction_surface,
            heavy_session_used: row.heavy_session_used, manual_context_transfers: Number(row.manual_context_transfers) };
        });
      },
    },
    "accept-outcome-feedback": {
      write: true, humanOnly: true, authorityOnly: true,
      description: "HUMAN-ONLY: accept one exact immutable ready-plan outcome-feedback hash. It records human-verified observation only; it never self-attests success, changes state, executes, dispatches, assigns, approves, or completes work.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string", pattern: "^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" },
        human_ref: { type: "string", pattern: "^WR-[0-9]{1,12}$" }, base_version: { type: "integer", minimum: 1 },
        feedback_hash: { type: "string", pattern: "^sha256:[0-9a-f]{64}$" },
      }, required: ["idempotency_key", "human_ref", "base_version", "feedback_hash"] },
      handler: async (c, actor, args) => {
        validateOutcomeAcceptance(args, ToolError);
        return withEnvelope(c, actor, "accept-outcome-feedback", { ...args, _server_actor_id: actor.id }, async () => {
          const result = await c.query(
            `select * from ops.accept_sourced_work_request_outcome_feedback($1::text,$2::integer,$3::text,$4::uuid)
               /* work-request-intake:accept-outcome-feedback */`, [args.human_ref, args.base_version, args.feedback_hash, args.idempotency_key]);
          const row = result.rows[0];
          if (!row) throw new ToolError({ error: "version_conflict" });
          await writeEvent(c, actor, "accept-outcome-feedback", "ops_work_request", row.work_request_id, {
            field: "outcome_feedback_accepted", new: { feedback_ref: row.feedback_ref, feedback_hash: row.feedback_hash, outcome: row.outcome, blocker_code: row.blocker_code }, idempotency_key: args.idempotency_key,
          });
          return { ok: true, human_ref: row.ref, state: row.state, version: Number(row.version), plan_ref: row.plan_ref,
            plan_hash: row.plan_hash, feedback_ref: row.feedback_ref, feedback_hash: row.feedback_hash, outcome: row.outcome,
            criterion_results: row.criterion_results, evidence_refs: row.evidence_refs, blocker_code: row.blocker_code,
            result_summary: row.result_summary, observed_minutes: Number(row.observed_minutes), interaction_surface: row.interaction_surface,
            heavy_session_used: row.heavy_session_used, manual_context_transfers: Number(row.manual_context_transfers),
            accepted_by_actor_slug: row.accepted_by_actor_slug, accepted_at: row.accepted_at };
        });
      },
    },
  };
}
