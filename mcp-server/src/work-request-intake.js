// The deliberately small Program 6 bridge: source a problem from the current
// deterministic situation index, capture it, and expose a safe read card. It
// does not own a lifecycle transition, an executor, or an approval.
import { searchDoctrineSituations } from "./situation-retrieval.js";
import { organizationTenantForActor } from "./identity.js";
import { deriveRuleDeliverySource, normalizeRuleMapDigest } from "./rule-delivery-source.js";

const FIELDS = new Set(["idempotency_key", "situation", "title", "desired_outcome", "acceptance_criteria"]);
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const CRITERION_ID = /^[A-Z][A-Z0-9-]{1,63}$/;
const TRIAGE_FIELDS = new Set(["idempotency_key", "human_ref", "base_version", "classification"]);
const TRIAGE_CLASSES = new Set(["operational", "needs_judgment", "safety_review"]);
const DECLINE_FIELDS = new Set(["idempotency_key", "human_ref", "base_version", "exit_reason"]);
const SUPERSEDE_FIELDS = new Set(["idempotency_key", "human_ref", "base_version", "exit_reason", "superseded_by"]);
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

async function deliverySourceForPlan(c, actor, row, args, classified, admission) {
  await c.query("savepoint siep02_rule_delivery_source");
  let registry;
  try {
    registry = await c.query(
      `with registry as (
         select count(distinct map_digest)::integer as map_versions,
                min(map_digest) as map_digest,
                count(*)::integer as tagged_rules
           from ops.rule_load_layer
       ), packs as (
         select coalesce(jsonb_agg(to_jsonb(p) order by p.pack),'[]'::jsonb) as pack_index
           from ops.rule_pack_index() p
       )
       select registry.*,packs.pack_index,w.title as work_request_title,
              w.desired_outcome,w.acceptance_criteria
         from registry cross join packs
         join ops.work_request w on w.id=$1::uuid and w.ref=$2::text
          and w.version=$3::integer and w.organization_tenant_id=$4::text
         /* work-request-intake:rule-delivery-source-snapshot */`,
      [row.work_request_id, row.ref, Number(row.version), organizationTenantForActor(actor)]);
    await c.query("release savepoint siep02_rule_delivery_source");
  } catch (error) {
    await c.query("rollback to savepoint siep02_rule_delivery_source");
    await c.query("release savepoint siep02_rule_delivery_source");
    return { schema_version: "rule-delivery-source.v1", status: "unavailable",
      reason: "rule_delivery_registry_unavailable", detail: String(error?.message || error).slice(0, 160) };
  }
  try {
    const state = registry.rows[0] || {};
    const digest = normalizeRuleMapDigest(state.map_digest);
    if (Number(state.map_versions) !== 1 || Number(state.tagged_rules) < 1 || !digest) {
      return { schema_version: "rule-delivery-source.v1", status: "unavailable",
        reason: "installed_rule_map_is_not_one_coherent_nonempty_version",
        map_versions: Number(state.map_versions || 0), tagged_rules: Number(state.tagged_rules || 0) };
    }
    return await deriveRuleDeliverySource({
      plan: {
        work_request_ref: row.ref,
        work_request_title: state.work_request_title,
        desired_outcome: state.desired_outcome,
        acceptance_criteria: state.acceptance_criteria,
        base_version: Number(row.version),
        plan_ref: row.plan_ref,
        plan_hash: row.plan_hash,
        scope_summary: row.scope_summary,
        runbook_ref: row.runbook_ref,
        dependency_refs: args.dependency_refs,
        recovery_ref: args.recovery_ref,
        observability_ref: args.observability_ref,
        caps: args.caps,
      },
      heavyClassification: {
        tier: admission ? "heavy" : classified.tier,
        reasons: admission?.classifier_reasons || classified.reasons || [],
      },
      admittedHeavyContract: admission ? {
        admission_ref: admission.admission_ref,
        admission_hash: admission.admission_hash,
        builder_session_ref: admission.builder_session_ref,
        master_plan: args.heavy_build.master_plan,
      } : null,
      packIndex: state.pack_index,
      mapDigest: digest,
    });
  } catch (error) {
    return { schema_version: "rule-delivery-source.v1", status: "unavailable",
      reason: "rule_delivery_source_invalid", detail: String(error?.message || error).slice(0, 160) };
  }
}
function exactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}
function boundedStrings(value, min, max, minLength = 10, maxLength = 1000) {
  return Array.isArray(value) && value.length >= min && value.length <= max &&
    value.every(item => text(item).length >= minLength && text(item).length <= maxLength);
}

// A REFUSAL THAT NAMES NO FIELD COSTS THE CALLER A READ OF THIS FILE.
//
// Measured 2026-09-02: filing ONE outcome record on WR-000040 took five
// attempts, and each attempt came back as a bare {"error":"invalid_outcome_
// feedback"} naming nothing. The five causes — criterion_results sent as an
// object instead of an array, a "not_assessed" result, a "capability" blocker,
// plain file paths where safe: refs are required, and a 607-character summary
// against an undocumented 500 cap — were every one of them knowable only by
// reading the constants and the validator below. A session that cannot read
// this file cannot file the record, so it writes the narrative into a loop's
// blocker_detail instead, which is exactly why "what is actually done?" needs
// archaeology to answer. The recording surface cost more than the work.
//
// So every refusal in this file now names the failing field and what that field
// wanted. The error NAME is unchanged and stays the contract callers and tests
// switch on; `field` and `detail` are additive.
//
// This is a different question from the one tools.js's assertRequiredArgs
// answers. That one fires when a required argument is ABSENT and reports
// {error, missing, hint}. These fire when an argument is PRESENT and wrong, so
// the useful answer is which field and what shape it wanted — the same {error,
// field, detail} shape validateHeavyBuildContract has always thrown.
function refuser(ToolError, error) {
  return (field, detail) => { throw new ToolError({ error, field, detail }); };
}

// The unknown-key refusal every closed schema here shares. Echoing the keys it
// did not accept is the whole value: the observed failure is a caller who sent
// a plausible synonym and read the bare refusal as "this verb is broken".
function assertClosed(args, allowed, ToolError, error) {
  const unrecognised = Object.keys(args).filter(key => !allowed.has(key));
  if (unrecognised.length)
    throw new ToolError({ error, unrecognised,
      detail: `this verb accepts only ${[...allowed].join(", ")}` });
}

// idempotency_key / human_ref / base_version are the same three arguments on
// six verbs, and a caller gets them wrong the same three ways.
function assertRequestIdentity(args, fail) {
  if (!UUID.test(args.idempotency_key || "")) fail("idempotency_key", "a fresh UUID per intended action, never a reused one");
  if (!/^WR-[0-9]{1,12}$/.test(args.human_ref || "")) fail("human_ref", "a work request ref such as WR-000040");
  if (!Number.isInteger(args.base_version) || args.base_version < 1) fail("base_version", "the integer version from a fresh read of the request");
}

function assertSafeRefs(value, field, fail, { min, max }) {
  if (!Array.isArray(value)) fail(field, `an ARRAY of ${min}-${max} safe: references` + (min ? "" : " (send [] when there are none)"));
  if (value.length < min || value.length > max) fail(field, `${min}-${max} items, received ${value.length}`);
  value.forEach((ref, index) => {
    if (typeof ref !== "string" || ref.length > 300) fail(`${field}[${index}]`, "a string of at most 300 characters");
    if (!SAFE_REF.test(ref)) fail(`${field}[${index}]`,
      `must match ^safe:[a-z0-9][a-z0-9:_./-]*$ — a bare path such as ${JSON.stringify(ref.slice(0, 60))} is refused; write safe:<path>`);
  });
  if (new Set(value).size !== value.length) fail(field, "references must be unique");
}

function validateHeavyBuildContract(raw, ToolError) {
  const fail = refuser(ToolError, "invalid_heavy_build_contract");
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
  assertClosed(args, HEAVY_REVIEW_FIELDS, ToolError, "invalid_heavy_build_review_fields");
  const fail = refuser(ToolError, "invalid_heavy_build_review");
  if (!UUID.test(args.idempotency_key || "")) fail("idempotency_key", "a fresh UUID per intended action, never a reused one");
  if (!/^WR-[0-9]{1,12}$/.test(args.human_ref || "")) fail("human_ref", "a work request ref such as WR-000040");
  if (!SHA256.test(args.plan_hash || "")) fail("plan_hash", "the exact sha256:<64 hex> the proposal returned");
  if (!SHA256.test(args.admission_hash || "")) fail("admission_hash", "the exact sha256:<64 hex> recorded at heavy-build admission");
  if (!new Set(["pass", "fail"]).has(args.verdict)) fail("verdict", "exactly pass or fail");
  if (!SESSION_REF.test(args.reviewer_session_ref || "")) fail("reviewer_session_ref", "session:<stable-fresh-context-ref>, 9-200 characters after the prefix");
  if (text(args.review_summary).length < 20 || text(args.review_summary).length > 1000)
    fail("review_summary", `20-1000 characters, received ${text(args.review_summary).length}`);
  assertSafeRefs(args.evidence_refs, "evidence_refs", fail, { min: 1, max: 12 });
  if (!boundedStrings(args.gaps, 0, 12, 10, 500)) fail("gaps", "an array of 0-12 statements, each 10-500 characters");
  if (args.verdict === "pass" && args.gaps.length) fail("gaps", "a pass records no gaps");
  if (args.verdict === "fail" && !args.gaps.length) fail("gaps", "a fail must name at least one gap");
}

function validate(args, ToolError) {
  assertClosed(args, FIELDS, ToolError, "invalid_report_problem_fields");
  const fail = refuser(ToolError, "invalid_report_problem");
  if (!UUID.test(args.idempotency_key || "")) fail("idempotency_key", "a fresh UUID per intended action, never a reused one");
  for (const [field, cap] of [["situation", 1000], ["title", 200], ["desired_outcome", 2000]]) {
    if (!text(args[field])) fail(field, `required; 1-${cap} characters`);
    if (text(args[field]).length > cap) fail(field, `${cap} characters maximum, received ${text(args[field]).length}`);
  }
  if (!Array.isArray(args.acceptance_criteria)) fail("acceptance_criteria", "an ARRAY of {id, text} — an object keyed by criterion id is refused");
  if (!args.acceptance_criteria.length || args.acceptance_criteria.length > 12)
    fail("acceptance_criteria", `1-12 items, received ${args.acceptance_criteria.length}`);
  const criteria = refuser(ToolError, "invalid_acceptance_criteria");
  const ids = new Set();
  args.acceptance_criteria.forEach((criterion, index) => {
    const where = `acceptance_criteria[${index}]`;
    if (!criterion || typeof criterion !== "object" || Array.isArray(criterion)) criteria(where, "each item is an object {id, text}");
    const extra = Object.keys(criterion).filter(key => key !== "id" && key !== "text");
    if (extra.length) criteria(where, `unrecognised ${extra.join(", ")}; each item is exactly {id, text}`);
    if (!text(criterion.id) || !CRITERION_ID.test(criterion.id.trim()))
      criteria(`${where}.id`, "SCREAMING-KEBAB, 2-64 characters, matching ^[A-Z][A-Z0-9-]{1,63}$");
    if (ids.has(criterion.id.trim())) criteria(`${where}.id`, `duplicate id ${criterion.id.trim()}`);
    if (!text(criterion.text)) criteria(`${where}.text`, "required; 1-500 characters");
    if (text(criterion.text).length > 500) criteria(`${where}.text`, `500 characters maximum, received ${text(criterion.text).length}`);
    ids.add(criterion.id.trim());
  });
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
  assertClosed(args, TRIAGE_FIELDS, ToolError, "invalid_triage_fields");
  const fail = refuser(ToolError, "invalid_triage");
  assertRequestIdentity(args, fail);
  if (!TRIAGE_CLASSES.has(args.classification)) fail("classification", `exactly one of ${[...TRIAGE_CLASSES].join(" / ")}`);
}

// WITHDRAWAL IS TWO VALIDATORS, not one with an optional successor. See the two
// verbs below for why the split is contractual rather than cosmetic.
//
// 500 characters for the reason. The database requires only btrim(exit_reason)
// <> '' — it will take an essay — so the bound is this layer's, chosen to match
// result_summary, the other short human sentence this file accepts. The empty
// case is refused here AND by the function; duplicating it costs nothing and
// stops a blank reason before the idempotency lock turns it into I/O.
function validateDecline(args, ToolError) {
  assertClosed(args, DECLINE_FIELDS, ToolError, "invalid_decline_work_request_fields");
  const fail = refuser(ToolError, "invalid_decline_work_request");
  assertRequestIdentity(args, fail);
  if (!text(args.exit_reason)) fail("exit_reason", "required; 1-500 characters saying why this request is being withdrawn");
  if (text(args.exit_reason).length > 500) fail("exit_reason", `500 characters maximum, received ${text(args.exit_reason).length}`);
}

function validateSupersede(args, ToolError) {
  assertClosed(args, SUPERSEDE_FIELDS, ToolError, "invalid_supersede_work_request_fields");
  const fail = refuser(ToolError, "invalid_supersede_work_request");
  assertRequestIdentity(args, fail);
  if (!text(args.exit_reason)) fail("exit_reason", "required; 1-500 characters saying why this request is being superseded");
  if (text(args.exit_reason).length > 500) fail("exit_reason", `500 characters maximum, received ${text(args.exit_reason).length}`);
  if (!/^WR-[0-9]{1,12}$/.test(args.superseded_by || "")) fail("superseded_by", "the successor request's ref, such as WR-000041");
  // Self-supersession is refused by the function too, and that refusal is the
  // one that counts. This copy exists only so the obvious typo never reaches
  // a row lock. The three refusals that need a SECOND row — a withdrawn
  // successor, a non-sourced successor, a two-row cycle — are deliberately
  // NOT re-implemented here: they cannot be decided without the database.
  if (args.superseded_by.trim() === args.human_ref.trim())
    fail("superseded_by", "a request cannot supersede itself; name the successor request");
}

// The source-merge scope is its own refusal name because it is its own
// contract: an authorized_paths list is a grant of write reach, and a caller
// that gets it wrong needs to know WHICH path and WHY, not that "the plan" was
// invalid.
function validateSourceMergeScope(merge, ToolError) {
  if (merge === undefined) return;
  const fail = refuser(ToolError, "invalid_source_merge_scope");
  if (!merge || typeof merge !== "object" || Array.isArray(merge) ||
      Object.keys(merge).sort().join(",") !== "authorized_paths,base_branch,repository,schema_version")
    fail("caps.source_merge", "exactly {schema_version, repository, base_branch, authorized_paths}");
  if (merge.schema_version !== "source-merge-scope.v1") fail("caps.source_merge.schema_version", "must be source-merge-scope.v1");
  if (merge.repository !== "jbookout/carr-system") fail("caps.source_merge.repository", "must be jbookout/carr-system");
  if (merge.base_branch !== "main") fail("caps.source_merge.base_branch", "must be main");
  if (!Array.isArray(merge.authorized_paths) || !merge.authorized_paths.length || merge.authorized_paths.length > 100)
    fail("caps.source_merge.authorized_paths", "1-100 repository-relative paths");
  merge.authorized_paths.forEach((path, index) => {
    const where = `caps.source_merge.authorized_paths[${index}]`;
    if (typeof path !== "string" || path.length > 500) fail(where, "a string of 1-500 characters");
    if (!/^[!-~]+$/.test(path)) fail(where, "printable ASCII with no spaces");
    if (path.startsWith("/") || path.endsWith("/")) fail(where, "repository-relative; no leading or trailing slash");
    if (path.includes("\\") || /[*?\[\]{}!]/.test(path)) fail(where, "a literal path; globs and backslashes are refused");
    if (path.split("/").some(part => !part || part === "." || part === "..")) fail(where, "no empty, . or .. path segments");
  });
  const lowered = merge.authorized_paths.map(path => path.toLowerCase());
  if (new Set(lowered).size !== lowered.length) fail("caps.source_merge.authorized_paths", "paths must be unique, compared case-insensitively");
  const sorted = [...merge.authorized_paths].sort((left, right) => {
    const lowerLeft = left.toLowerCase();
    const lowerRight = right.toLowerCase();
    return lowerLeft < lowerRight ? -1 : lowerLeft > lowerRight ? 1 : left < right ? -1 : left > right ? 1 : 0;
  });
  if (JSON.stringify(merge.authorized_paths) !== JSON.stringify(sorted))
    fail("caps.source_merge.authorized_paths", "must be sorted case-insensitively, ties broken by exact value");
}

function validatePlan(args, ToolError) {
  assertClosed(args, PLAN_FIELDS, ToolError, "invalid_ready_plan_fields");
  const fail = refuser(ToolError, "invalid_ready_plan");
  assertRequestIdentity(args, fail);
  if (!text(args.scope_summary)) fail("scope_summary", "required; 1-1000 characters");
  if (text(args.scope_summary).length > 1000) fail("scope_summary", `1000 characters maximum, received ${text(args.scope_summary).length}`);
  if (!/^doctrine:runbook#[a-z0-9][a-z0-9-]*$/.test(args.runbook_ref || "")) fail("runbook_ref", "looks like doctrine:runbook#<kebab-slug>");
  for (const field of ["recovery_ref", "observability_ref"]) {
    if (typeof args[field] !== "string" || args[field].length > 300) fail(field, "a string of at most 300 characters");
    if (!SAFE_REF.test(args[field])) fail(field, "must match ^safe:[a-z0-9][a-z0-9:_./-]*$ — a bare path is refused; write safe:<path>");
  }
  assertSafeRefs(args.dependency_refs, "dependency_refs", fail, { min: 0, max: 12 });
  const capKeys = args.caps && typeof args.caps === "object" && !Array.isArray(args.caps) ? Object.keys(args.caps).sort().join(",") : null;
  if (!["max_duration_minutes,max_steps", "max_duration_minutes,max_steps,source_merge"].includes(capKeys))
    fail("caps", "exactly {max_steps, max_duration_minutes}, plus the optional source_merge");
  if (!Number.isInteger(args.caps.max_steps) || args.caps.max_steps < 1 || args.caps.max_steps > 20) fail("caps.max_steps", "an integer 1-20");
  if (!Number.isInteger(args.caps.max_duration_minutes) || args.caps.max_duration_minutes < 1 || args.caps.max_duration_minutes > 120)
    fail("caps.max_duration_minutes", "an integer 1-120");
  validateSourceMergeScope(args.caps.source_merge, ToolError);
  if (args.heavy_build !== undefined) validateHeavyBuildContract(args.heavy_build, ToolError);
}

function validateAcceptPlan(args, ToolError) {
  assertClosed(args, ACCEPT_PLAN_FIELDS, ToolError, "invalid_accept_plan_fields");
  const fail = refuser(ToolError, "invalid_accept_plan");
  assertRequestIdentity(args, fail);
  if (!SHA256.test(args.plan_hash || "")) fail("plan_hash", "the exact sha256:<64 hex> the proposal returned, copied verbatim");
}

function validateOutcomeProposal(args, ToolError) {
  assertClosed(args, OUTCOME_PROPOSAL_FIELDS, ToolError, "invalid_outcome_feedback_fields");
  const fail = refuser(ToolError, "invalid_outcome_feedback");
  assertRequestIdentity(args, fail);
  if (!SHA256.test(args.plan_hash || "")) fail("plan_hash", "the exact sha256:<64 hex> the accepted plan returned, copied verbatim");
  if (!Array.isArray(args.criterion_results))
    fail("criterion_results", "an ARRAY of {id, result} — an object keyed by criterion id is refused");
  if (!args.criterion_results.length || args.criterion_results.length > 12)
    fail("criterion_results", `1-12 items, received ${args.criterion_results.length}`);
  assertSafeRefs(args.evidence_refs, "evidence_refs", fail, { min: 1, max: 12 });
  if (!OUTCOME_BLOCKER.has(args.blocker_code)) fail("blocker_code", `exactly one of ${[...OUTCOME_BLOCKER].join(" / ")}`);
  if (!text(args.result_summary)) fail("result_summary", "required; 1-500 characters");
  if (text(args.result_summary).length > 500) fail("result_summary", `500 characters maximum, received ${text(args.result_summary).length}`);
  if (!Number.isInteger(args.observed_minutes) || args.observed_minutes < 1 || args.observed_minutes > 1440) fail("observed_minutes", "an integer 1-1440");
  if (!INTERACTION_SURFACE.has(args.interaction_surface)) fail("interaction_surface", `exactly one of ${[...INTERACTION_SURFACE].join(" / ")}`);
  if (typeof args.heavy_session_used !== "boolean") fail("heavy_session_used", "a boolean");
  if (!Number.isInteger(args.manual_context_transfers) || args.manual_context_transfers < 0 || args.manual_context_transfers > 100)
    fail("manual_context_transfers", "an integer 0-100");

  const criteria = refuser(ToolError, "invalid_outcome_criteria");
  const ids = new Set();
  args.criterion_results.forEach((criterion, index) => {
    const where = `criterion_results[${index}]`;
    if (!criterion || typeof criterion !== "object" || Array.isArray(criterion)) criteria(where, "each item is an object {id, result}");
    const extra = Object.keys(criterion).filter(key => key !== "id" && key !== "result");
    if (extra.length) criteria(where, `unrecognised ${extra.join(", ")}; each item is exactly {id, result}`);
    if (!text(criterion.id) || !CRITERION_ID.test(criterion.id.trim()))
      criteria(`${where}.id`, "an acceptance criterion id from the request, matching ^[A-Z][A-Z0-9-]{1,63}$");
    if (!CRITERION_RESULT.has(criterion.result))
      criteria(`${where}.result`, `exactly one of ${[...CRITERION_RESULT].join(" / ")}, received ${JSON.stringify(criterion.result)}`);
    if (ids.has(criterion.id.trim())) criteria(`${where}.id`, `duplicate id ${criterion.id.trim()}`);
    ids.add(criterion.id.trim());
  });

  // The outcome is DERIVED, never sent. A caller therefore cannot see why its
  // blocker_code was refused without being told which derivation it collided
  // with, so each refusal below names the derived outcome and the codes it admits.
  const results = args.criterion_results.map(criterion => criterion.result);
  const outcome = results.includes("not_met") ? "criteria_not_met" : results.every(result => result === "met") ? "criteria_met" : "inconclusive";
  const consistency = refuser(ToolError, "inconsistent_outcome_feedback");
  if (outcome === "criteria_met" && args.blocker_code !== "none")
    consistency("blocker_code", `every criterion is met, so the outcome derives to criteria_met, which takes blocker_code none — received ${args.blocker_code}`);
  if (outcome === "criteria_not_met" && args.blocker_code === "none")
    consistency("blocker_code", "a not_met criterion derives the outcome to criteria_not_met, which needs a blocker_code other than none");
  if (outcome === "inconclusive" && !new Set(["evidence_missing", "external_dependency", "system_error"]).has(args.blocker_code))
    consistency("blocker_code", `a not_observed criterion derives the outcome to inconclusive, which takes evidence_missing / external_dependency / system_error — received ${args.blocker_code}`);
}

function validateOutcomeAcceptance(args, ToolError) {
  assertClosed(args, ACCEPT_OUTCOME_FIELDS, ToolError, "invalid_accept_outcome_fields");
  const fail = refuser(ToolError, "invalid_accept_outcome");
  assertRequestIdentity(args, fail);
  if (!SHA256.test(args.feedback_hash || "")) fail("feedback_hash", "the exact sha256:<64 hex> the proposal returned, copied verbatim");
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


// WHO ACTUALLY PERFORMED THE AUTHORITY ACTS ON THIS REQUEST.
//
// ops.authority_actor_slug() maps the Postgres session role to 'joe' or 'dell'
// and can return nothing else, and every authority receipt column is constrained
// to a human actor — so an act performed by an agent sponsored by Joe is
// structurally unrecordable as anything but Joe. Once Joe's ruling made agent
// acts the normal case for internal system work rather than the exception, a
// card that says 'joe' and cannot say more became a card that misleads.
//
// Nothing new is captured to fix it. public.tool_call already stores actor_id,
// authorization_class and via under the SAME idempotency key each receipt
// stores; the two were simply never joined. This is that join.
//
// A row is reported only where the call ledger actually has the key. Acts
// predating the ledger, or performed by a path that does not write it, come back
// as null rather than as a guess.
const ACTING_IDENTITY = `
  select act, recorded_slug, acted_at, actor_slug, authorization_class, via from (
    select 'review-and-triage' as act, ha.slug as recorded_slug, r.triaged_at as acted_at,
           a.slug as actor_slug, t.authorization_class, t.via
      from ops.work_request_triage_receipt r
      join ops.work_request w on w.id = r.work_request_id
      join public.actor ha on ha.id = r.triaged_by_actor_id
      left join public.tool_call t on t.idempotency_key = r.idempotency_key::text
      left join public.actor a on a.id = t.actor_id
     where w.ref = $1
    union all
    select 'accept-ready-plan', ha.slug, r.accepted_at, a.slug, t.authorization_class, t.via
      from ops.sourced_work_request_plan_acceptance_receipt r
      join ops.work_request w on w.id = r.work_request_id
      join public.actor ha on ha.id = r.accepted_by_actor_id
      left join public.tool_call t on t.idempotency_key = r.idempotency_key::text
      left join public.actor a on a.id = t.actor_id
     where w.ref = $1
    union all
    select 'accept-outcome-feedback', ha.slug, r.accepted_at, a.slug, t.authorization_class, t.via
      from ops.sourced_work_request_outcome_feedback_acceptance_receipt r
      join ops.work_request w on w.id = r.work_request_id
      join public.actor ha on ha.id = r.accepted_by_actor_id
      left join public.tool_call t on t.idempotency_key = r.idempotency_key::text
      left join public.actor a on a.id = t.actor_id
     where w.ref = $1
  ) acts order by acted_at`;

export function actingIdentityProjection(rows) {
  return rows.map((row) => ({
    act: row.act,
    // The slug the receipt records. Always a human; that is the constraint.
    recorded_as: row.recorded_slug,
    // Who actually made the call, when the call ledger knows. null means the
    // ledger has no row for that key — say so rather than guess.
    performed_by: row.actor_slug || null,
    authorization_class: row.authorization_class || null,
    via: row.via || null,
    // The one field a reader needs: did a human do this, or an agent under a
    // human's sponsorship? Unknown is a real answer and is not smoothed away.
    hand: row.actor_slug
      ? (row.authorization_class === "sponsored_agent" ? "agent" : "human")
      : "unknown",
    acted_at: row.acted_at,
  }));
}

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
      description: "Capture one operational problem from the current deterministic situation source. It only creates a captured Work Request; it never triages, assigns, dispatches, approves, executes, or changes an existing request. CAPS the refusals enforce: situation 1-1000 characters, title 1-200, desired_outcome 1-2000, and acceptance_criteria an ARRAY of 1-12 items, each exactly {id, text}, with unique ids matching ^[A-Z][A-Z0-9-]{1,63}$ and text of 1-500 characters. Every refusal names the failing field and what that field wanted.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string", pattern: "^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" },
        situation: { type: "string", minLength: 1, maxLength: 1000 }, title: { type: "string", minLength: 1, maxLength: 200 },
        desired_outcome: { type: "string", minLength: 1, maxLength: 2000 }, acceptance_criteria: { type: "array", minItems: 1, maxItems: 12,
          items: { type: "object", additionalProperties: false, required: ["id", "text"], properties: {
            id: { type: "string", minLength: 2, maxLength: 64, pattern: "^[A-Z][A-Z0-9-]{1,63}$" }, text: { type: "string", minLength: 1, maxLength: 500 },
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
      description: "Read one same-tenant safe Work Request card, live or withdrawn. The card names the current or stale source, one human review label, and — for a request withdrawn in error — why it was withdrawn, when it closed, and the request that replaced it. It offers no executable actions.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        work_request: { type: "string", pattern: "^WR-[0-9]{1,12}$", minLength: 4, maxLength: 15 },
      }, required: ["work_request"] },
      handler: async (c, actor, args) => {
        const tenant = organizationTenantForActor(actor);
        const result = await c.query(
          `select * from ops.work_request_card($1::text, $2::text)
             /* work-request-intake:card */`, [args.work_request, tenant]);
        const row = result.rows[0];
        // A WITHDRAWN REQUEST IS STILL A RECORD. Refusing it here as
        // work_request_not_found would make the withdrawal capability erase the
        // one thing it exists to write, and it would say "not found" about a row
        // the database returned. ops.work_request_card admits the two terminal
        // states as of 0426, so this list has to as well or the SQL widening is
        // dead code.
        if (!row || !["captured", "triaged", "ready", "declined", "superseded"].includes(row.state)) throw new ToolError({ error: "work_request_not_found" });
        const triaged = ["triaged", "ready"].includes(row.state);
        // Withdrawal is CAPTURED-ONLY: branch 3 of work_request_sourced_capture_
        // shape requires triage_classification, triaged_by_actor_id and
        // triaged_at to all be null in these two states, and the immutability
        // trigger admits the transition only from 'captured'. So a withdrawn row
        // carries no triage, no plan and no shape, and every block below that is
        // keyed on those columns correctly reads null. Nothing here has to
        // special-case them; the constraint already did.
        const withdrawn = ["declined", "superseded"].includes(row.state);
        let pendingOutcomeFeedback = null;
        if (row.state === "ready") {
          const pending = await c.query(
            `select * from ops.pending_sourced_work_request_outcome_feedback($1::text,$2::text)
               /* work-request-intake:pending-outcome-feedback */`, [args.work_request, tenant]);
          pendingOutcomeFeedback = pendingOutcomeFeedbackProjection(pending.rows[0]);
        }
        const acting = actingIdentityProjection(
          (await c.query(ACTING_IDENTITY + " /* work-request-intake:acting-identity */",
                         [args.work_request])).rows);
        return { ok: true, human_ref: row.ref, title: row.title, desired_outcome: row.desired_outcome,
          acting_identity: acting,
          acceptance_criteria: row.acceptance_criteria, state: row.state, version: Number(row.version),
          // DERIVED FROM THE CROSSWALK, not hardcoded. work-request-projection
          // .v1.json maps captured/triaged/ready to queued and BOTH terminals to
          // declined — superseded-projects-as-declined is that contract's
          // declared judgment call, made because doctrine grants the projection
          // seven states and superseded is not one of them. A withdrawn card that
          // still read "queued" would tell a requester their closed record is
          // waiting in line.
          projection_state: withdrawn ? "declined" : "queued", source: sourceProjection(row),
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
          // REASON SURVIVES COLLAPSE. Both terminals project to one label, so the
          // crosswalk's own invariant obliges the card to carry what actually
          // happened alongside it. superseded_by_ref is the successor's durable
          // human ref, resolved by the card function's self-join; it is null on a
          // decline by the receipt's own check constraint.
          withdrawal: withdrawn ? { exit_reason: row.exit_reason, closed_at: row.closed_at,
            superseded_by_ref: row.superseded_by_ref || null } : null,
          // A withdrawn request asks nothing of anybody. Falling through to
          // "Review and triage" would put a record closed in error back in front
          // of a human as work, which is the queue-pollution the withdrawal verbs
          // exist to end. The two terminals keep separate labels because the
          // canonical record keeps them separate even where the projection does
          // not.
          next_human_action: withdrawn ? { label: row.state === "superseded" ? "Superseded" : "Declined", effect: "none" } : row.state === "ready" ? (pendingOutcomeFeedback ? { label: "Review outcome feedback", effect: "none" } : row.outcome_feedback ? { label: "Outcome feedback accepted", effect: "none" } : { label: "Plan accepted", effect: "none" }) : triaged ? { label: "Prepare scope and acceptance", effect: "none" } : { label: "Review and triage", effect: "none" },
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
    // WITHDRAWING A REQUEST CAPTURED IN ERROR — TWO VERBS, NEVER ONE.
    //
    // state-machines.v1.json is phase0_frozen and declares "* -> declined" and
    // "* -> superseded" as SEPARATE transitions with different guards
    // ("authorized disposition recorded" and "replacement request linked"), and
    // work-request-projection.v1.json rests its declared judgment call
    // superseded-projects-as-declined on the canonical record keeping the two
    // apart. A single verb inferring the state from whether a successor argument
    // was passed collapses exactly the distinction the frozen contract says must
    // survive, and it does so silently: a caller who forgets the successor gets a
    // decline instead of an error. 0426 splits the database functions for the
    // same reason; this is that split reaching the verb layer.
    //
    // CAPTURED ONLY, and the verbs do not enforce it — ops.decline_/supersede_
    // sourced_work_request select "for update" on state='captured' and version=
    // p_base_version, so a triaged request raises and surfaces here as a version
    // conflict. Withdrawing a triaged row would strand its triage receipt and may
    // strand a shape disposition; that is the migration's reasoning, not a
    // restriction this layer invented.
    //
    // NOT authorityOnly, unlike review-and-triage and accept-ready-plan. 0426
    // grants execute on both functions to carr_writer and nothing else, and the
    // authority login roles are REFUSED if they hold carr_writer at all
    // (ops/control-plane-authority-runtime-preflight-selftest.py, "writer
    // membership refuses"), so routing these down authorityDsnForActor would be a
    // guaranteed permission-denied on every call. That is also why the functions
    // take p_actor_slug: on the writer connection ops.authority_actor_slug() has
    // no session role to map, so who acted is PASSED rather than inferred — and
    // passing actor.slug records the agent that actually acted rather than
    // flattening every act to 'joe', which is the gap ACTING_IDENTITY above
    // exists to report.
    //
    // humanOnly is not declared either: the label stopped being read by
    // executeRegisteredTool on 2026-08-26 (decision dc57f62d) and WR-000019
    // slice S1 is removing the stale declarations. A new one would be dead on
    // arrival and would misreport this verb's protection in the derived
    // action-risk registry. base_version is the live guard, as it is for
    // propose-ready-plan and propose-outcome-feedback.
    "decline-work-request": {
      write: true,
      description: "Withdraw one sourced Work Request captured in error by declining it, recording why. It is the only backward move a captured request has, it works from captured and no later state, and it never triages, assigns, dispatches, approves, executes, or names a successor.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string", pattern: "^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" },
        human_ref: { type: "string", pattern: "^WR-[0-9]{1,12}$", minLength: 4, maxLength: 15 },
        base_version: { type: "integer", minimum: 1 },
        exit_reason: { type: "string", minLength: 1, maxLength: 500 },
      }, required: ["idempotency_key", "human_ref", "base_version", "exit_reason"] },
      handler: async (c, actor, args) => {
        validateDecline(args, ToolError);
        // Bind replays to the authenticated actor without admitting actor data
        // into the closed client schema.
        return withEnvelope(c, actor, "decline-work-request", { ...args, _server_actor_id: actor.id }, async () => {
          const result = await c.query(
            `select * from ops.decline_sourced_work_request($1::text, $2::integer, $3::text, $4::text, $5::uuid)
               /* work-request-intake:decline */`,
            [args.human_ref, args.base_version, text(args.exit_reason), actor.slug, args.idempotency_key]);
          const row = result.rows[0];
          if (!row) throw new ToolError({ error: "version_conflict", human_ref: args.human_ref,
            resolution: "re-read the Work Request card; only its current captured version may be withdrawn" });
          // The function returns the card fields, not the row id, and event
          // .subject_id is NOT NULL — so the id is read back by ref rather than
          // the audit link being dropped.
          const subject = await c.query(
            `select id from ops.work_request where ref = $1
               /* work-request-intake:withdrawn-subject */`, [row.ref]);
          await writeEvent(c, actor, "decline-work-request", "ops_work_request", subject.rows[0].id, {
            field: "state", old: { state: "captured", version: args.base_version },
            new: { state: "declined", version: Number(row.version), exit_reason: row.exit_reason },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, human_ref: row.ref, state: row.state, version: Number(row.version),
            exit_reason: row.exit_reason, closed_at: row.closed_at };
        });
      },
    },
    "supersede-work-request": {
      write: true,
      description: "Withdraw one sourced Work Request captured in error by superseding it into the request that replaces it, recording why. It works from captured and no later state; it never triages, assigns, dispatches, approves, executes, or changes the successor.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string", pattern: "^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" },
        human_ref: { type: "string", pattern: "^WR-[0-9]{1,12}$", minLength: 4, maxLength: 15 },
        base_version: { type: "integer", minimum: 1 },
        exit_reason: { type: "string", minLength: 1, maxLength: 500 },
        superseded_by: { type: "string", pattern: "^WR-[0-9]{1,12}$", minLength: 4, maxLength: 15 },
      }, required: ["idempotency_key", "human_ref", "base_version", "exit_reason", "superseded_by"] },
      handler: async (c, actor, args) => {
        validateSupersede(args, ToolError);
        return withEnvelope(c, actor, "supersede-work-request", { ...args, _server_actor_id: actor.id }, async () => {
          const result = await c.query(
            `select * from ops.supersede_sourced_work_request($1::text, $2::integer, $3::text, $4::text, $5::text, $6::uuid)
               /* work-request-intake:supersede */`,
            [args.human_ref, args.base_version, text(args.exit_reason), args.superseded_by, actor.slug, args.idempotency_key]);
          const row = result.rows[0];
          if (!row) throw new ToolError({ error: "version_conflict", human_ref: args.human_ref,
            resolution: "re-read the Work Request card; only its current captured version may be withdrawn" });
          const subject = await c.query(
            `select id from ops.work_request where ref = $1
               /* work-request-intake:withdrawn-subject */`, [row.ref]);
          await writeEvent(c, actor, "supersede-work-request", "ops_work_request", subject.rows[0].id, {
            field: "state", old: { state: "captured", version: args.base_version },
            new: { state: "superseded", version: Number(row.version), exit_reason: row.exit_reason,
              superseded_by_ref: row.superseded_by_ref },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, human_ref: row.ref, state: row.state, version: Number(row.version),
            exit_reason: row.exit_reason, closed_at: row.closed_at, superseded_by_ref: row.superseded_by_ref };
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
            source_merge: { type: "object", additionalProperties: false,
              required: ["schema_version", "repository", "base_branch", "authorized_paths"], properties: {
                schema_version: { type: "string", const: "source-merge-scope.v1" },
                repository: { type: "string", const: "jbookout/carr-system" },
                base_branch: { type: "string", const: "main" },
                authorized_paths: { type: "array", minItems: 1, maxItems: 100,
                  uniqueItems: true, items: { type: "string", minLength: 1, maxLength: 500 } },
              },
            },
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
          const ruleDeliverySource = await deliverySourceForPlan(c, actor, row, args, classified, admission);
          await writeEvent(c, actor, "propose-ready-plan", "ops_work_request", row.work_request_id, {
            field: "ready_plan", new: { plan_ref: row.plan_ref, plan_hash: row.plan_hash, runbook_ref: row.runbook_ref,
              build_tier: heavy ? "heavy" : "standard", admission_hash: admission?.admission_hash || null,
              rule_delivery_contract_digest: ruleDeliverySource.contract_digest || null }, idempotency_key: args.idempotency_key,
          });
          return { ok: true, human_ref: row.ref, state: row.state, version: Number(row.version), plan_ref: row.plan_ref,
            plan_hash: row.plan_hash, scope_summary: row.scope_summary, runbook_ref: row.runbook_ref,
            runbook_revision_id: row.runbook_revision_id, runbook_content_hash: row.runbook_content_hash,
            build_admission: heavy ? { tier: "heavy", reasons: admission.classifier_reasons || reasons, required: true,
              admission_ref: admission.admission_ref, admission_hash: admission.admission_hash,
              next_required_action: "fresh independent plan review" } : { tier: "standard", reasons, required: false },
            rule_delivery_source: ruleDeliverySource };
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
      description: "Propose evidence-bound outcome feedback for one accepted sourced ready plan. It creates no success claim, state transition, execution, assignment, dispatch, approval, or completion. SHAPES AND CAPS, so the call is composed once rather than discovered by refusal: criterion_results is an ARRAY of {id, result}, never an object keyed by criterion id, 1-12 items with unique ids matching ^[A-Z][A-Z0-9-]{1,63}$ and result exactly met | not_met | not_observed; evidence_refs is 1-12 unique strings each matching ^safe:[a-z0-9][a-z0-9:_./-]*$, so a bare file path is refused and safe:<path> is the form; blocker_code is exactly none | evidence_missing | criterion_not_met | external_dependency | system_error; result_summary is 1-500 characters; observed_minutes 1-1440; manual_context_transfers 0-100. The outcome is DERIVED from criterion_results, never sent, and blocker_code must agree with it: any not_met derives criteria_not_met and needs a blocker other than none, all met derives criteria_met and takes only none, otherwise it derives inconclusive and takes evidence_missing, external_dependency or system_error. Every refusal names the failing field and what that field wanted.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string", pattern: "^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" },
        human_ref: { type: "string", pattern: "^WR-[0-9]{1,12}$" }, base_version: { type: "integer", minimum: 1 },
        plan_hash: { type: "string", pattern: "^sha256:[0-9a-f]{64}$" },
        criterion_results: { type: "array", minItems: 1, maxItems: 12, items: { type: "object", additionalProperties: false, required: ["id", "result"], properties: { id: { type: "string", minLength: 2, maxLength: 64, pattern: "^[A-Z][A-Z0-9-]{1,63}$" }, result: { type: "string", enum: ["met", "not_met", "not_observed"] } } } },
        // The safe: format is stated in this verb's description and in every
        // refusal it throws, NOT here. inputSchema is what the SCAC mutation
        // registry digests (mutation-registry.js assertRegisteredOperation), so
        // adding the pattern here would demand a registry reseal across versions
        // 1-10 and their migrations to document a rule the caller already reads.
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
