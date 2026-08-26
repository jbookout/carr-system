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
const PLAN_FIELDS = new Set(["idempotency_key","human_ref","base_version","scope_summary","runbook_ref","dependency_refs","recovery_ref","observability_ref","caps","heavy_build"]);
const ACCEPT_PLAN_FIELDS = new Set(["idempotency_key","human_ref","base_version","plan_hash"]);
const HEAVY_REVIEW_FIELDS = new Set(["idempotency_key","human_ref","plan_hash","admission_hash","verdict","reviewer_session_ref","review_summary","evidence_refs","gaps"]);
const OUTCOME_PROPOSAL_FIELDS = new Set(["idempotency_key","human_ref","base_version","plan_hash","criterion_results","evidence_refs","blocker_code","result_summary","observed_minutes","interaction_surface","heavy_session_used","manual_context_transfers"]);
const ACCEPT_OUTCOME_FIELDS = new Set(["idempotency_key","human_ref","base_version","feedback_hash"]);
const SAFE_REF = /^safe:[a-z0-9][a-z0-9:_./-]*$/;
const CRITERION_RESULT = new Set(["met", "not_met", "not_observed"]);
const INTERACTION_SURFACE = new Set(["workspace", "control_room", "mcp", "codex", "claude_code", "other"]);
const OUTCOME_BLOCKER = new Set(["none", "evidence_missing", "criterion_not_met", "external_dependency", "system_error"]);
const SESSION_REF = /^session:[a-z0-9][a-z0-9:._/-]{8,199}$/;
const SHA256 = /^sha256:[0-9a-f]{64}$/;
const HEAVY_RESEARCH_FIELDS = ["primary_sources", "maintained_repositories", "practitioner_evidence", "current_baseline", "failure_modes"];
const HEAVY_MASTER_PLAN_FIELDS = ["product_goal", "non_goals", "architecture", "authority_boundaries", "dependency_dag", "planned_checks", "baseline_comparison", "release_strategy", "rollback_strategy", "observability_strategy", "fully_shipped_definition", "prerequisite_policy"];
const EVIDENCE_CLASS = Object.freeze({ primary_sources: "primary_source", maintained_repositories: "maintained_repository",
  practitioner_evidence: "practitioner_evidence", current_baseline: "current_baseline", failure_modes: "failure_mode" });

function text(value) { return typeof value === "string" ? value.trim() : ""; }
function exactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}
function boundedStrings(value, min, max, minLength = 10, maxLength = 1000) {
  return Array.isArray(value) && value.length >= min && value.length <= max &&
    value.every(item => text(item).length >= minLength && text(item).length <= maxLength);
}

function validateHeavyBuildContract(raw, ToolError) {
  const fail = (field, detail) => { throw new ToolError({ error: "invalid_heavy_build_contract", field, detail }); };
  if (!exactKeys(raw, ["builder_session_ref", "research_manifest", "master_plan"])) fail("heavy_build", "closed typed contract required");
  if (!SESSION_REF.test(raw.builder_session_ref || "")) fail("builder_session_ref", "use session:<stable-fresh-context-ref>");
  const research = raw.research_manifest;
  if (!exactKeys(research, [...HEAVY_RESEARCH_FIELDS, "unresolved_contradictions", "conclusion"])) fail("research_manifest", "all research classes are required");
  const seen = new Set();
  for (const field of HEAVY_RESEARCH_FIELDS) {
    const items = research[field];
    const minimum = field === "maintained_repositories" ? 2 : 1;
    if (!Array.isArray(items) || items.length < minimum || items.length > 12) fail(`research_manifest.${field}`, `requires ${minimum}-12 evidence items`);
    for (const item of items) {
      if (!exactKeys(item, ["source_ref", "source_class", "locator", "observed_at", "content_digest", "finding"])) fail(`research_manifest.${field}`, "evidence item shape is not exact");
      if (!SAFE_REF.test(item.source_ref || "") || seen.has(item.source_ref)) fail(`research_manifest.${field}.source_ref`, "safe refs must be unique");
      seen.add(item.source_ref);
      if (item.source_class !== EVIDENCE_CLASS[field]) fail(`research_manifest.${field}.source_class`, `expected ${EVIDENCE_CLASS[field]}`);
      let locator;
      try { locator = new URL(String(item.locator || "")); } catch { fail(`research_manifest.${field}.locator`, "HTTPS source required"); }
      if (locator.protocol !== "https:") fail(`research_manifest.${field}.locator`, "HTTPS source required");
      const observed = new Date(String(item.observed_at || ""));
      if (Number.isNaN(observed.getTime()) || observed.getTime() > Date.now() + 300000) fail(`research_manifest.${field}.observed_at`, "non-future timestamp required");
      if (!SHA256.test(item.content_digest || "")) fail(`research_manifest.${field}.content_digest`, "exact sha256 digest required");
      if (text(item.finding).length < 20 || text(item.finding).length > 1000) fail(`research_manifest.${field}.finding`, "20-1000 character finding required");
    }
  }
  if (!boundedStrings(research.unresolved_contradictions, 0, 12, 10, 500) || text(research.conclusion).length < 20 || text(research.conclusion).length > 1000)
    fail("research_manifest", "contradictions[] and a substantive conclusion are required");

  const plan = raw.master_plan;
  if (!exactKeys(plan, HEAVY_MASTER_PLAN_FIELDS)) fail("master_plan", "complete target-product plan required");
  for (const field of ["product_goal", "baseline_comparison", "release_strategy", "rollback_strategy", "observability_strategy", "fully_shipped_definition", "prerequisite_policy"])
    if (text(plan[field]).length < 20 || text(plan[field]).length > 2000) fail(`master_plan.${field}`, "20-2000 character statement required");
  if (!boundedStrings(plan.non_goals, 1, 12) || !boundedStrings(plan.architecture, 2, 20) || !boundedStrings(plan.authority_boundaries, 1, 12))
    fail("master_plan", "non-goals, architecture, and authority boundaries must be explicit");
  if (!Array.isArray(plan.dependency_dag) || !plan.dependency_dag.length || plan.dependency_dag.length > 20) fail("master_plan.dependency_dag", "1-20 typed steps required");
  const steps = new Set();
  for (const step of plan.dependency_dag) {
    if (!exactKeys(step, ["step_ref", "depends_on"]) || !/^step:[a-z0-9][a-z0-9:._/-]*$/.test(step.step_ref || "") || steps.has(step.step_ref) ||
        !Array.isArray(step.depends_on) || step.depends_on.some(ref => !/^step:[a-z0-9][a-z0-9:._/-]*$/.test(ref) || ref === step.step_ref))
      fail("master_plan.dependency_dag", "unique step refs and non-self dependencies required");
    steps.add(step.step_ref);
  }
  if (plan.dependency_dag.some(step => step.depends_on.some(ref => !steps.has(ref)))) fail("master_plan.dependency_dag", "every dependency must name a declared step");
  if (!Array.isArray(plan.planned_checks) || !plan.planned_checks.length || plan.planned_checks.length > 20 || plan.planned_checks.some(check =>
      !exactKeys(check, ["artifact", "comparator", "failure_condition"]) || [check.artifact, check.comparator, check.failure_condition].some(value => text(value).length < 5 || text(value).length > 500)))
    fail("master_plan.planned_checks", "each check must name artifact, comparator, and failure condition");
  return raw;
}

function validateHeavyReview(args, ToolError) {
  if (Object.keys(args).some(key => !HEAVY_REVIEW_FIELDS.has(key))) throw new ToolError({ error: "invalid_heavy_build_review_fields" });
  if (!UUID.test(args.idempotency_key || "") || !/^WR-[0-9]{1,12}$/.test(args.human_ref || "") || !SHA256.test(args.plan_hash || "") ||
      !SHA256.test(args.admission_hash || "") || !new Set(["pass", "fail"]).has(args.verdict) || !SESSION_REF.test(args.reviewer_session_ref || "") ||
      text(args.review_summary).length < 20 || text(args.review_summary).length > 1000 ||
      !Array.isArray(args.evidence_refs) || !args.evidence_refs.length || args.evidence_refs.length > 12 || args.evidence_refs.some(ref => !SAFE_REF.test(ref || "")) ||
      new Set(args.evidence_refs).size !== args.evidence_refs.length || !boundedStrings(args.gaps, 0, 12, 10, 500) ||
      (args.verdict === "pass" && args.gaps.length) || (args.verdict === "fail" && !args.gaps.length))
    throw new ToolError({ error: "invalid_heavy_build_review" });
}

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

function pendingOutcomeFeedbackProjection(row) {
  if (!row) return null;
  return { feedback_ref: row.feedback_ref, feedback_hash: row.feedback_hash,
    proposed_outcome: row.outcome, criterion_results: row.criterion_results || [],
    evidence_refs: row.evidence_refs || [], blocker_code: row.blocker_code,
    result_summary: row.result_summary, observed_minutes: Number(row.observed_minutes),
    interaction_surface: row.interaction_surface, heavy_session_used: row.heavy_session_used,
    manual_context_transfers: Number(row.manual_context_transfers), proposed_at: row.proposed_at,
    status: "pending_human_acceptance" };
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
  if (args.heavy_build !== undefined) validateHeavyBuildContract(args.heavy_build, ToolError);
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

const HEAVY_EVIDENCE_SCHEMA = { type: "object", additionalProperties: false,
  required: ["source_ref", "source_class", "locator", "observed_at", "content_digest", "finding"], properties: {
    source_ref: { type: "string", minLength: 6, maxLength: 300 }, source_class: { type: "string" },
    locator: { type: "string", minLength: 8, maxLength: 1000 }, observed_at: { type: "string" },
    content_digest: { type: "string", pattern: "^sha256:[0-9a-f]{64}$" }, finding: { type: "string", minLength: 20, maxLength: 1000 },
  } };
function heavyEvidenceArray(minItems = 1) { return { type: "array", minItems, maxItems: 12, items: HEAVY_EVIDENCE_SCHEMA }; }
const HEAVY_BUILD_SCHEMA = { type: "object", additionalProperties: false,
  required: ["builder_session_ref", "research_manifest", "master_plan"], properties: {
    builder_session_ref: { type: "string", minLength: 17, maxLength: 207 },
    research_manifest: { type: "object", additionalProperties: false,
      required: [...HEAVY_RESEARCH_FIELDS, "unresolved_contradictions", "conclusion"], properties: {
        primary_sources: heavyEvidenceArray(), maintained_repositories: heavyEvidenceArray(2), practitioner_evidence: heavyEvidenceArray(),
        current_baseline: heavyEvidenceArray(), failure_modes: heavyEvidenceArray(),
        unresolved_contradictions: { type: "array", maxItems: 12, items: { type: "string", minLength: 10, maxLength: 500 } },
        conclusion: { type: "string", minLength: 20, maxLength: 1000 },
      } },
    master_plan: { type: "object", additionalProperties: false, required: HEAVY_MASTER_PLAN_FIELDS, properties: {
      product_goal: { type: "string", minLength: 20, maxLength: 2000 },
      non_goals: { type: "array", minItems: 1, maxItems: 12, items: { type: "string", minLength: 10, maxLength: 1000 } },
      architecture: { type: "array", minItems: 2, maxItems: 20, items: { type: "string", minLength: 10, maxLength: 1000 } },
      authority_boundaries: { type: "array", minItems: 1, maxItems: 12, items: { type: "string", minLength: 10, maxLength: 1000 } },
      dependency_dag: { type: "array", minItems: 1, maxItems: 20, items: { type: "object", additionalProperties: false,
        required: ["step_ref", "depends_on"], properties: { step_ref: { type: "string" }, depends_on: { type: "array", items: { type: "string" } } } } },
      planned_checks: { type: "array", minItems: 1, maxItems: 20, items: { type: "object", additionalProperties: false,
        required: ["artifact", "comparator", "failure_condition"], properties: { artifact: { type: "string" }, comparator: { type: "string" }, failure_condition: { type: "string" } } } },
      baseline_comparison: { type: "string", minLength: 20, maxLength: 2000 }, release_strategy: { type: "string", minLength: 20, maxLength: 2000 },
      rollback_strategy: { type: "string", minLength: 20, maxLength: 2000 }, observability_strategy: { type: "string", minLength: 20, maxLength: 2000 },
      fully_shipped_definition: { type: "string", minLength: 20, maxLength: 2000 }, prerequisite_policy: { type: "string", minLength: 20, maxLength: 2000 },
    } },
  } };

export function workRequestIntakeTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "current-work-requests": {
      write: false,
      description: "Read at most 20 current, shared, sourced Program 6 Work Requests that still admit a bounded human next action. It never accepts a caller-selected tenant, filter, state, source, or database identifier.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c, actor, args) => {
        if (Object.keys(args).length) throw new ToolError({ error: "invalid_current_work_requests_fields" });
        const tenant = organizationTenantForActor(actor);
        const result = await c.query(
          `select * from ops.current_sourced_work_requests($1::text)
             /* work-request-intake:current */`, [tenant]);
        return { ok: true, items: result.rows.map(row => ({
          human_ref: row.ref, title: row.title, state: row.state,
          source: { label: row.source_label, freshness: row.source_freshness },
          next_human_action: row.next_human_action,
        })) };
      },
    },
    "current-work-item": {
      write: false,
      description:
        "What is being worked right now: every Work Request a person or session is actually holding, " +
        "each with its owner, the predicate that would make it done, and its blocker or null. Answers in " +
        "one call the question that previously needed a read of the engineering suite, a read of the " +
        "sourced queue, and a guess. Also reports the work-in-progress limit and whether it is exceeded, " +
        "because a queue that silently runs six-wide is the condition this verb exists to make visible. " +
        "Takes no arguments: it never accepts a caller-selected tenant, owner, state or filter.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c, actor, args) => {
        if (Object.keys(args).length) throw new ToolError({ error: "invalid_current_work_item_fields" });
        const tenant = organizationTenantForActor(actor);
        // HELD, not merely open. claimed/in_progress/verification is somebody's
        // hands on the work; needs_joe and blocked are held too — the council was
        // explicit that a blocked row stays current and can never be skipped,
        // which is exactly why they belong in an answer to "what is current".
        // ready and captured are the queue, not the current item.
        const held = await c.query(
          `select ref, title, state, owner_actor, executor_actor,
                  acceptance_criteria, project_context, blocker_code, blocker_detail,
                  program_key, program_ordinal, claimed_at, started_at, updated_at,
                  extract(epoch from (now() - updated_at))/3600.0 as hours_since_change
             from ops.work_request
            where state in ('claimed','in_progress','verification','needs_joe','blocked')
              and (organization_tenant_id is null or organization_tenant_id = $1::text)
            order by (state in ('needs_joe','blocked')) desc, updated_at asc
             /* work-request-intake:current-item */`, [tenant]);

        const items = held.rows.map(row => {
          const ctx = row.project_context || {};
          const criteria = Array.isArray(row.acceptance_criteria) ? row.acceptance_criteria : [];
          return {
            human_ref: row.ref,
            title: row.title,
            state: row.state,
            // Owner is who answers for it; executor is who has their hands on it.
            // They are different questions and a single "assignee" field loses one.
            owner: row.owner_actor || null,
            executor: row.executor_actor || null,
            // THE DONE PREDICATE, in the order the council asked for it: the
            // acceptance predicates if the row carries them, else the written
            // completion definition, else null — never an invented one.
            done_predicate: criteria.length
              ? criteria.map(c => (typeof c === "string" ? c : (c && c.text) || null)).filter(Boolean)
              : (ctx.completion_definition ? [ctx.completion_definition] : null),
            // Blocker OR NULL, never absent: a row with no blocker has to say so
            // out loud, because "no blocker field" and "not blocked" read the
            // same in a payload and mean different things.
            blocker: row.blocker_code
              ? { code: row.blocker_code, detail: row.blocker_detail || null }
              : null,
            program: row.program_key ? { key: row.program_key, sequence: Number(row.program_ordinal) } : null,
            held_since: row.claimed_at || row.started_at || null,
            hours_since_last_change: Math.round(Number(row.hours_since_change || 0) * 10) / 10,
          };
        });

        // The council set the limit at two system-wide and one per executor. This
        // verb REPORTS it; enforcing it belongs in the claim path, and saying so
        // here keeps the two from being confused for each other.
        const inFlight = items.filter(i => i.state === "claimed" || i.state === "in_progress");
        // WHO A ROW BELONGS TO IS DECIDED THE SAME WAY THE TRIGGER DECIDES IT —
        // coalesce(executor_actor, owner_actor), and rows with NOBODY named are
        // skipped, exactly as ops.enforce_work_in_progress_limit()'s `if who is
        // not null` skips them. This used to bucket unnamed rows together under a
        // single "unassigned" pseudo-executor, so two in-flight rows nobody owned
        // were reported as one executor over their limit — a breach the database
        // does not refuse and nobody could act on. A limit reported but not
        // enforced is worse than either alone: it teaches the reader the number
        // is decorative, which is how a real breach later gets ignored.
        const perExecutor = {};
        let unattributed = 0;
        for (const i of inFlight) {
          const who = i.executor || i.owner;
          if (!who) { unattributed += 1; continue; }
          perExecutor[who] = (perExecutor[who] || 0) + 1;
        }
        const overCommitted = Object.entries(perExecutor)
          .filter(([, n]) => n > 1).map(([who, n]) => ({ executor: who, in_flight: n }));

        // An item nobody has touched for two days is not current, whatever its
        // state says. The council's rule is that it gets forced to blocked, split
        // or closed; surfacing it is the half this read owes.
        const stale = items.filter(i => i.hours_since_last_change >= 48)
          .map(i => ({ human_ref: i.human_ref, hours_since_last_change: i.hours_since_last_change }));

        return {
          ok: true,
          current: items,
          count: items.length,
          wip: {
            limit_system_wide: 2,
            limit_per_executor: 1,
            in_flight: inFlight.length,
            over_system_limit: inFlight.length > 2,
            executors_over_limit: overCommitted,
            // COUNTED, NOT SILENTLY DROPPED. These rows are in flight and do
            // consume the system-wide limit, so they are already inside
            // in_flight above; they are named separately because in-flight work
            // with nobody's name on it is worth seeing, and because dropping
            // them without saying so is how a number stops meaning anything.
            in_flight_unattributed: unattributed,
            note: "reported here, enforced in the claim path; per-executor counts "
                + "resolve the responsible party exactly as the trigger does, and "
                + "skip rows with nobody named rather than pooling them",
          },
          unchanged_over_48h: stale,
          say: items.length
            ? "these are held right now; a blocked or needs-Joe row is still current and is never skipped"
            : "nothing is held — the queue may still have ready work, which this verb deliberately does not show",
        };
      },
    },
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
        let pendingOutcomeFeedback = null;
        if (row.state === "ready") {
          const pending = await c.query(
            `select * from ops.pending_sourced_work_request_outcome_feedback($1::text,$2::text)
               /* work-request-intake:pending-outcome-feedback */`, [args.work_request, tenant]);
          pendingOutcomeFeedback = pendingOutcomeFeedbackProjection(pending.rows[0]);
        }
        return { ok: true, human_ref: row.ref, title: row.title, desired_outcome: row.desired_outcome,
          acceptance_criteria: row.acceptance_criteria, state: row.state, version: Number(row.version),
          projection_state: "queued", source: sourceProjection(row),
          triage: triaged ? { classification: row.triage_classification, human_actor_slug: row.triaged_by_actor_slug,
            triaged_at: row.triaged_at } : null,
          plan: row.plan_hash ? { plan_ref: row.plan_ref || null, plan_hash: row.plan_hash, runbook_ref: row.runbook_ref || null,
            scope_summary: row.scope_summary || null,
            runbook_revision_id: row.runbook_revision_id || null, runbook_content_hash: row.runbook_content_hash || null,
            caps: row.plan_caps || null, dependency_refs: row.dependency_refs || [], recovery_ref: row.recovery_ref || null,
            observability_ref: row.observability_ref || null,
            accepted_by_actor_slug: row.accepted_by_actor_slug || null,
            accepted_at: row.accepted_at || null } : null,
          outcome_feedback: row.outcome_feedback && typeof row.outcome_feedback === "object" ? outcomeFeedbackProjection(row.outcome_feedback) : null,
          pending_outcome_feedback: pendingOutcomeFeedback,
          outcome_feedback_history: Array.isArray(row.outcome_feedback_history) ? row.outcome_feedback_history.map(outcomeFeedbackProjection) : [],
          accepted_feedback_count: Number(row.accepted_feedback_count || 0),
          shape: row.shape_disposition ? { disposition: row.shape_disposition, fixed_surface_ref: row.shape_fixed_surface_ref || null } : null,
          next_human_action: row.state === "ready" ? (pendingOutcomeFeedback ? { label: "Review outcome feedback", effect: "none" } : row.outcome_feedback ? { label: "Outcome feedback accepted", effect: "none" } : { label: "Plan accepted", effect: "none" }) : triaged ? { label: "Prepare scope and acceptance", effect: "none" } : { label: "Review and triage", effect: "none" },
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
      description: "Append one immutable, bounded ready-plan proposal to a triaged Work Request. The database derives whether the work is heavy from the request and plan size. Heavy work is refused until a current Work Shape plus a typed research manifest and complete master plan are bound to the proposal. It does not change request state, assign work, dispatch, execute, or approve.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" }, human_ref: { type: "string" }, base_version: { type: "integer", minimum: 1 },
        scope_summary: { type: "string", minLength: 1, maxLength: 1000 }, runbook_ref: { type: "string" },
        dependency_refs: { type: "array", maxItems: 12, items: { type: "string" } }, recovery_ref: { type: "string" },
        observability_ref: { type: "string" }, caps: { type: "object", additionalProperties: false,
          required: ["max_steps", "max_duration_minutes"], properties: {
            max_steps: { type: "integer", minimum: 1, maximum: 20 },
            max_duration_minutes: { type: "integer", minimum: 1, maximum: 120 },
          } },
        heavy_build: HEAVY_BUILD_SCHEMA,
      }, required:["idempotency_key","human_ref","base_version","scope_summary","runbook_ref","dependency_refs","recovery_ref","observability_ref","caps"] },
      handler: async (c, actor, args) => {
        validatePlan(args, ToolError);
        return withEnvelope(c, actor, "propose-ready-plan", { ...args, _server_actor_id: actor.id }, async () => {
          const classified = (await c.query(
            `select * from ops.classify_sourced_work_request_build($1::text,$2::integer,$3::text,$4::jsonb,$5::jsonb)
               /* work-request-intake:classify-ready-plan */`,
            [args.human_ref, args.base_version, text(args.scope_summary), JSON.stringify(args.dependency_refs), JSON.stringify(args.caps)])).rows[0];
          if (!classified) throw new ToolError({ error: "version_conflict", human_ref: args.human_ref,
            resolution: "re-read the exact current triaged Work Request before proposing its plan" });
          const reasons = Array.isArray(classified.reasons) ? classified.reasons : [];
          const heavy = classified.tier === "heavy" || args.heavy_build !== undefined;
          if (heavy && !classified.shape_ready) throw new ToolError({ error: "heavy_build_shape_required", human_ref: args.human_ref,
            classification_reasons: reasons, shape_disposition: classified.shape_disposition || null,
            resolution: "set Work Shape disposition to required and write the current evidence-backed Work Shape before proposing a heavy plan" });
          if (heavy && !args.heavy_build) throw new ToolError({ error: "heavy_build_admission_required", human_ref: args.human_ref,
            classification_reasons: reasons, missing: ["research_manifest", "master_plan", "builder_session_ref"],
            resolution: "finish the research manifest and complete target-product master plan, then retry the same planning step" });

          const result = await c.query(
            `select * from ops.propose_sourced_work_request_plan($1::text,$2::integer,$3::text,$4::text,$5::jsonb,$6::text,$7::text,$8::jsonb,$9::uuid)
               /* work-request-intake:propose-ready-plan */`,
            [args.human_ref, args.base_version, text(args.scope_summary), args.runbook_ref, JSON.stringify(args.dependency_refs),
             args.recovery_ref, args.observability_ref, JSON.stringify(args.caps), args.idempotency_key]);
          const row = result.rows[0];
          if (!row) throw new ToolError({ error: "version_conflict" });
          let admission = null;
          if (heavy) {
            const effectiveReasons = reasons.length ? reasons : ["caller:explicit-heavy-contract"];
            admission = (await c.query(
              `select * from ops.record_sourced_heavy_build_admission($1::uuid,$2::text,$3::integer,$4::jsonb,$5::jsonb,$6::uuid,$7::uuid)
                 /* work-request-intake:record-heavy-admission */`,
              [row.plan_id, args.human_ref, args.base_version, JSON.stringify(effectiveReasons), JSON.stringify(args.heavy_build), actor.id, args.idempotency_key])).rows[0];
            if (!admission) throw new ToolError({ error: "heavy_build_admission_not_recorded" });
          }
          await writeEvent(c, actor, "propose-ready-plan", "ops_work_request", row.work_request_id, {
            field: "ready_plan", new: { plan_ref: row.plan_ref, plan_hash: row.plan_hash, runbook_ref: row.runbook_ref,
              build_tier: heavy ? "heavy" : "standard", admission_hash: admission?.admission_hash || null }, idempotency_key: args.idempotency_key,
          });
          return { ok: true, human_ref: row.ref, state: row.state, version: Number(row.version), plan_ref: row.plan_ref,
            plan_hash: row.plan_hash, scope_summary: row.scope_summary, runbook_ref: row.runbook_ref,
            runbook_revision_id: row.runbook_revision_id, runbook_content_hash: row.runbook_content_hash,
            build_admission: heavy ? { tier: "heavy", reasons: admission.classifier_reasons || reasons, required: true,
              admission_ref: admission.admission_ref, admission_hash: admission.admission_hash,
              next_required_action: "fresh independent plan review" } : { tier: "standard", reasons, required: false } };
        });
      },
    },
    "review-heavy-build-plan": {
      write: true,
      description: "Record a fresh-context independent review of one exact heavy-build admission and immutable plan. A passing receipt is required before human plan acceptance; this verb never accepts, dispatches, executes, or changes Work Request state.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" }, human_ref: { type: "string", pattern: "^WR-[0-9]{1,12}$" },
        plan_hash: { type: "string", pattern: "^sha256:[0-9a-f]{64}$" }, admission_hash: { type: "string", pattern: "^sha256:[0-9a-f]{64}$" },
        verdict: { type: "string", enum: ["pass", "fail"] }, reviewer_session_ref: { type: "string", minLength: 17, maxLength: 207 },
        review_summary: { type: "string", minLength: 20, maxLength: 1000 },
        evidence_refs: { type: "array", minItems: 1, maxItems: 12, items: { type: "string", minLength: 6, maxLength: 300 } },
        gaps: { type: "array", maxItems: 12, items: { type: "string", minLength: 10, maxLength: 500 } },
      }, required: ["idempotency_key", "human_ref", "plan_hash", "admission_hash", "verdict", "reviewer_session_ref", "review_summary", "evidence_refs", "gaps"] },
      handler: async (c, actor, args) => {
        validateHeavyReview(args, ToolError);
        return withEnvelope(c, actor, "review-heavy-build-plan", { ...args, _server_actor_id: actor.id }, async () => {
          const target = (await c.query(
            `select * from ops.sourced_heavy_build_review_target($1::text,$2::text,$3::text)
               /* work-request-intake:heavy-review-target */`,
            [args.human_ref, args.plan_hash, args.admission_hash])).rows[0];
          if (!target) throw new ToolError({ error: "heavy_build_review_target_not_found" });
          if (target.builder_session_ref === args.reviewer_session_ref) throw new ToolError({ error: "heavy_build_review_context_not_fresh",
            builder_session_ref: target.builder_session_ref,
            resolution: "run the review in a genuinely fresh session and pass that session's distinct stable reference" });
          const row = (await c.query(
            `select * from ops.review_sourced_heavy_build_plan($1::text,$2::text,$3::text,$4::uuid,$5::text,$6::text,$7::text,$8::jsonb,$9::jsonb,$10::uuid)
               /* work-request-intake:review-heavy-plan */`,
            [args.human_ref, args.plan_hash, args.admission_hash, actor.id, args.verdict, args.reviewer_session_ref,
             text(args.review_summary), JSON.stringify(args.evidence_refs), JSON.stringify(args.gaps), args.idempotency_key])).rows[0];
          if (!row) throw new ToolError({ error: "heavy_build_review_not_recorded" });
          await writeEvent(c, actor, "review-heavy-build-plan", "ops_work_request", row.work_request_id, {
            field: "heavy_build_plan_review", new: { plan_hash: args.plan_hash, admission_hash: args.admission_hash,
              review_ref: row.review_ref, review_hash: row.review_hash, verdict: row.verdict }, idempotency_key: args.idempotency_key,
          });
          return { ok: true, human_ref: row.ref, plan_hash: args.plan_hash, admission_ref: row.admission_ref,
            admission_hash: row.admission_hash, review_ref: row.review_ref, review_hash: row.review_hash,
            verdict: row.verdict, reviewer_session_ref: row.reviewer_session_ref,
            status: row.verdict === "pass" ? "ready_for_human_plan_acceptance" : "revision_required" };
        });
      },
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
