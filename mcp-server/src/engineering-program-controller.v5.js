// V5-F02 — the deterministic program controller for the isolated engineering
// factory.
//
// This module answers four questions and nothing else:
//
//   1. May the program run this many slices at once?      evaluateProgramWidth
//   2. May this slice touch the tree at all?              evaluateSliceAdmission
//   3. May this slice resume from its checkpoint?         evaluateCheckpointResume
//   4. Is this slice released?                            evaluateReleaseEligibility
//
// WHAT THIS FILE IS NOT, said first because "controller" invites the wrong
// reading. It creates no worktree, runs no git command, opens no shell, holds
// no lock, claims no lease and merges nothing. It is a PURE EVALUATOR over
// typed observations the caller supplies, in the shape of
// command-supervisor-admission.v5.js: every result is a frozen answer carrying
// `effects: V5_NO_EFFECTS`, and an `allow` says the registered negatives did
// not fire on the facts as reported.
//
// NOTHING IN THIS REPOSITORY CALLS THESE FOUR FUNCTIONS YET, AND THAT IS THE
// HONEST STATE, not an oversight to be papered over with a fabricated caller.
// The independent review of PR 980 named it, and the seam census below is the
// answer, one decision at a time. The test is: (a) does a code path here
// PERFORM the action being gated, (b) does that path hold the facts the
// decision consumes from a source other than the party being gated, and (c) can
// the call be made without a new migration, table, entrypoint or registry seal?
//
//   * SLICE ADMISSION. (a) YES — admitEngineeringSlice in engineering-runtime.js
//     is the door that issues the execution envelope authorizing a slice to be
//     worked, and a refusal there is a hard stop before any edit. (b) NO. That
//     door decides over Work Requests, accepted slice plans, envelopes and agent
//     sessions. It has no source-path lease, no worktree path, no per-slice base
//     commit (it writes a sentinel, ENGINEERING_SESSION_SOURCE) and no
//     width-evidence record. No relation in db/schema.sql stores a live lease
//     census. Building that request from what the door holds would mean
//     inventing the very facts this module refuses to let a caller invent.
//     MISSING SEAM: a live worktree/source-path lease census and an earned-width
//     evidence ledger.
//   * CHECKPOINT RESUME. (a) NO. There is no resume-admission door. The nearest
//     relative, isCanonicalResetReconstruction in engineering-runtime.js, checks
//     a receipt AFTER the work, which is a different decision at a different
//     time. MISSING SEAM: a resume door that reads a durable checkpoint record.
//   * RELEASE ELIGIBILITY. (a) NO. There is no Deployment Controller in this
//     repository — the only occurrence of the phrase is this file. (b) NO. No
//     store here holds hosted-CI conclusions, a merge slot or a post-merge
//     readback. MISSING SEAM: a Deployment Controller and the receipt store this
//     module's state-holder port is written against.
//
// So the decision layer is built, closed and proved against its own contract,
// and it is not yet load-bearing. Saying so is the point: a controller claimed
// to gate work it does not gate is worse than one that says it gates nothing.
//
// TWO KINDS OF NO, inherited from global-boundaries.v5.js and unchanged here:
//   * A POLICY ANSWER is RETURNED — `decision` is "allow" or "refuse" with a
//     stable `reason_id`. An observation that does not state a fact BLOCKS;
//     silence is never an allow.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Unknown fields, open
//     schemas, unknown enum members and malformed digests are not policy
//     questions — the module cannot read the request, so it fails closed.
//
// THE RULES MODELLED HERE ARE NOT INVENTED. They are the accepted parallel
// execution contract of the v5 reviewed implementation catalog (doctrine
// document `doctorcre-v5-astra-integration-review`, section
// `v5-reviewed-implementation-slice-catalog-and-parallel-groups-2026-09-09`),
// clause by clause:
//
//   earned_width         "Width starts at one and may grow to two then three
//                         only after current accepted isolation, race,
//                         stale-base, WIP recovery, merge serialization and
//                         failure-containment evidence."
//                        -> V5_WIDTH_EVIDENCE_CLASSES, one ladder step at a
//                           time, every class accepted AND bound to the current
//                           base.
//   exclusive_ownership  "Each slice binds exact source paths, primary
//                         module/interface and exact database
//                         tables/schemas/migration/fixture resources. No two
//                         active writers may overlap."
//                        -> the overlap checks, by path SEGMENT containment.
//   work_surface         "Every admitted source slice uses its own worktree and
//                         branch from fresh origin/main; never share a writer
//                         tree."
//                        -> owned_worktree + fresh_base.
//   dispatch_time_rule   "Do not claim path or database disjointness from this
//                         catalog. Compute it from the current repository,
//                         current migration/registry frontier and live lease
//                         census immediately before admission."
//                        -> census_provenance: a catalog-derived disjointness
//                           claim is refused by its own declared source, and a
//                           census older than V5_MAX_CENSUS_AGE_SECONDS is
//                           refused as an observation, not accepted as a fact.
//   serialized_surfaces  "One designated owner at a time for shared
//                         interfaces/contracts, migration frontier,
//                         SCAC/mutation registry, generated schema/seals,
//                         shared inventory fixtures and deployment/activation/
//                         retirement."
//                        -> V5_SERIALIZED_SURFACES, single-owner regardless of
//                           path disjointness.
//   integration          "Merge one eligible slice at a time through Deployment
//                         Controller; after each merge, rebase and revalidate
//                         every affected descendant before its delivery."
//                        -> serialized_merge_slot + descendant_revalidation.
//
// THE CATALOG'S EXCLUDED SCOPE IS ENFORCED, NOT DOCUMENTED. "shared-root
// editing", "generic shell", "raw Production data fork" and "duplicate queue"
// each have a named refusal below, and each is a KNOWN-BUT-REFUSED vocabulary
// member rather than an unreadable request — so the refusal is an answer a
// caller can record, which is what makes it checkable.
//
// THE MODEL JUDGMENT BOUNDARY IS STRUCTURAL. The catalog's boundary for this
// slice reads: "Models occupy declared roles; deterministic controller owns
// admission, leases, concurrency, merge eligibility and terminal transitions."
// A request may DECLARE which role a model occupied (V5_MODEL_ROLES); it may
// not carry a model's verdict, confidence, recommendation or override, because
// those are unknown fields and an unknown field is unreadable. Every result
// says `decided_by: "deterministic_controller"` in its own fields.
//
// WHAT IS HONESTLY UNAVAILABLE. The catalog's included scope lists "eligible
// compatible auto-release". Auto-release is gated on two upstream receipts the
// catalog names as this slice's runtime/acceptance evidence inputs:
// `step:gate-zero-read-only-outcome` and
// `step:foundation-control-plane-preactivation-contract-receipt`. NEITHER IS
// PRODUCED ANYWHERE IN THIS REPOSITORY — benchmark-minimum.v5.js says so in its
// own words about the first ("the dependency step r7 references but never
// registers"), and the second exists only as a required MEMBER of the
// foundation-assurance minimum gate, i.e. as something waited on. So auto-
// release is answered `unavailable` with the missing steps named, and the two
// step refs are IMPORTED from benchmark-minimum.v5.js rather than retyped, so
// the day A00 registers them this module follows instead of drifting. A caller
// cannot assert those receipts here: there is no field for them, by design.
//
// DECISION BINDING. The catalog maps nine decisions to this slice — Q012.D2,
// Q025.D1, Q026.D1, Q037.D1, Q038.D1, Q039.D1, Q044.D1, Q111.D1 and Q112.D1.
// Only Q012's settled text is readable: the v5 design-basis register carries
// Q001-Q015 in full and compacts everything after it to routing pointers whose
// own canonicalization note says to "recompile full question, recommendation,
// and user response from source item IDs" in the originating Codex thread,
// which is not reachable from here. Q012's readable text is why this module
// exists at all — Joe: "approvals and permissions should be removed as much as
// possible bc the design is where we made those decisions. having me
// micromanage the build after a rigorous design process is just duplicated work
// and it slows us down." A deterministic controller is how a decision made at
// design time gets enforced at build time without a human in the loop. The
// other eight ids are carried in V5_PROGRAM_CONTROLLER_DECISION_IDS as a
// binding, and this file claims no knowledge of their text.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { ENGINEERING_REPOSITORY_ACTIONS } from "./engineering-runtime.js";
import { GATE_ZERO_STEP_REF, MINIMUM_REQUIRED_MEMBERS } from "./benchmark-minimum.v5.js";

export const V5_PROGRAM_CONTROLLER_SCHEMA_VERSION =
  "doctorcre-v5-engineering-program-controller.v1";
export const V5_PROGRAM_CONTROLLER_POLICY_VERSION = 1;

const SHA1_HEX = /^[0-9a-f]{40}$/;
const REF = /^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;
const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
}

function object(value, path) {
  if (!isPlainObject(value)) fail("not_an_object", `${path} must be a plain object`, { path });
  return value;
}

/** An open schema is an unenforced one: every request object is closed. */
function closed(value, allowed, path) {
  object(value, path);
  for (const key of Object.keys(value))
    if (!allowed.includes(key))
      fail("unknown_field", `${path}.${key} is not a field this module reads`, { path, key, allowed: [...allowed].sort() });
}

function required(value, keys, path) {
  for (const key of keys)
    if (!Object.hasOwn(value, key))
      fail("missing_field", `${path}.${key} is required`, { path, key });
}

function exact(value, allowed, path) {
  closed(value, allowed, path);
  required(value, allowed, path);
  return value;
}

function str(value, path) {
  if (typeof value !== "string" || value.length === 0)
    fail("not_a_string", `${path} must be a non-empty string`, { path });
  return value;
}

function ref(value, path) {
  str(value, path);
  if (!REF.test(value)) fail("malformed_ref", `${path} must be a typed reference`, { path, value });
  return value;
}

function commitSha(value, path) {
  str(value, path);
  if (!SHA1_HEX.test(value)) fail("malformed_commit_sha", `${path} must be 40 lowercase hex characters`, { path });
  return value;
}

function instant(value, path) {
  str(value, path);
  if (!ISO_INSTANT.test(value) || Number.isNaN(Date.parse(value)))
    fail("malformed_instant", `${path} must be a UTC ISO-8601 instant`, { path, value });
  return Date.parse(value);
}

function member(value, allowed, path) {
  str(value, path);
  if (!allowed.includes(value))
    fail("unknown_enum_member", `${path} is not a member this module knows`, { path, value, allowed: [...allowed].sort() });
  return value;
}

function bool(value, path) {
  if (typeof value !== "boolean") fail("not_a_boolean", `${path} must be a boolean`, { path });
  return value;
}

function list(value, path) {
  if (!Array.isArray(value)) fail("not_an_array", `${path} must be an array`, { path });
  return value;
}

/** Width is an integer on a closed ladder, so it gets its own member check. */
function widthMember(value, path) {
  if (!Number.isInteger(value) || !V5_PROGRAM_WIDTH_LADDER.includes(value))
    fail("unknown_enum_member", `${path} is not a width on the ladder`,
      { path, value: value ?? null, allowed: [...V5_PROGRAM_WIDTH_LADDER] });
  return value;
}

/**
 * A C-sorted, duplicate-free list of exact strings.
 *
 * Sort order and uniqueness are REQUEST SHAPE, not policy: the ownership
 * template says "FILL_EXACT_C_SORTED_NON_OVERLAPPING_PATH_LEASE_AT_DISPATCH",
 * and two callers who disagree about the order of a lease are describing two
 * different leases. So an unsorted or repeating list throws rather than being
 * silently sorted into an identity its producer never wrote — the same reason
 * engineering-runtime.js refuses a padded identifier instead of trimming it.
 */
function sortedUnique(values, path, validate) {
  list(values, path);
  values.forEach((value, index) => validate(value, `${path}[${index}]`));
  for (let index = 1; index < values.length; index += 1) {
    if (values[index] === values[index - 1])
      fail("duplicate_member", `${path} repeats ${values[index]}`, { path, value: values[index] });
    if (values[index] < values[index - 1])
      fail("unsorted_list", `${path} must be C-sorted`, { path, at: index });
  }
  return values;
}

// ---------------------------------------------------------------------------
// The closed vocabularies. Every one of these is in the policy preimage, so a
// vocabulary that changes moves the policy digest and a pinned consumer is
// refused rather than silently reading a policy that now decides more.
// ---------------------------------------------------------------------------

/** The nine decisions the catalog maps to V5-F02. Carried, not interpreted. */
export const V5_PROGRAM_CONTROLLER_DECISION_IDS = deepFreeze([
  "Q012.D2", "Q025.D1", "Q026.D1", "Q037.D1", "Q038.D1",
  "Q039.D1", "Q044.D1", "Q111.D1", "Q112.D1",
]);

/** Width starts at one and may grow to two then three. Never 1 -> 3. */
export const V5_PROGRAM_WIDTH_LADDER = deepFreeze([1, 2, 3]);

/** The six evidence classes named in the earned_width clause, C-sorted. */
export const V5_WIDTH_EVIDENCE_CLASSES = deepFreeze([
  "failure_containment", "isolation", "merge_serialization",
  "race", "stale_base", "wip_recovery",
]);

export const V5_EVIDENCE_STATES = deepFreeze(["accepted", "failed", "pending"]);

/** The serialized_if_touched list from the shared dispatch ownership template. */
export const V5_SERIALIZED_SURFACES = deepFreeze([
  "deployment_activation_retirement_controller",
  "generated_schema_or_seal",
  "migration_number_frontier",
  "scac_mutation_registry",
  "shared_interface_or_command_contract",
  "shared_source_inventory_fixture",
]);

/**
 * The data_boundary clause: "Each slice records exact environment and uses no
 * DB, an equal-trust branch, schema-only/hermetic fixture or approved sanitized
 * parent."
 */
export const V5_DATA_DISPOSITIONS = deepFreeze([
  "approved_sanitized_parent", "equal_trust_branch", "hermetic_fixture",
  "no_database", "schema_only_fixture",
]);

/**
 * Known dispositions that are REFUSED rather than unreadable. "raw Production
 * data fork" is the catalog's own excluded-scope wording; a caller that asks
 * for it gets a recordable refusal naming it, not a schema error that reads as
 * a typo.
 */
export const V5_REFUSED_DATA_DISPOSITIONS = deepFreeze(["raw_production_fork"]);

/** R5: "Record per-slice reuse/extend/replace decision". Unskippable below. */
export const V5_REUSE_DISPOSITIONS = deepFreeze(["extend", "replace", "reuse"]);

/** Declared roles a model may occupy. A role is carried; it decides nothing. */
export const V5_MODEL_ROLES = deepFreeze(["author", "observer", "reviewer"]);

/** The only admissible provenance for a disjointness claim. */
export const V5_DISJOINTNESS_SOURCES = deepFreeze(["live_lease_census"]);

/** Known-but-refused provenance, named by the dispatch_time_rule. */
export const V5_REFUSED_DISJOINTNESS_SOURCES = deepFreeze(["candidate_group_catalog"]);

/** A census observed longer ago than this is an old story, not a fact. */
export const V5_MAX_CENSUS_AGE_SECONDS = 300;

export const V5_MERGE_SLOT_STATES = deepFreeze(["free", "held"]);
export const V5_REVIEW_STATES = deepFreeze(["absent", "accepted", "requested_changes"]);
export const V5_CHECK_CONCLUSIONS = deepFreeze(["cancelled", "failure", "pending", "success"]);
export const V5_READBACK_STATES = deepFreeze(["absent", "pending", "verified"]);

/** Ordered. The first check that does not pass decides; the rest are not reached. */
export const V5_WIDTH_CHECKS = deepFreeze(["ladder_step", "evidence_complete", "evidence_current"]);

export const V5_ADMISSION_CHECKS = deepFreeze([
  "census_provenance",
  "fresh_base",
  "owned_worktree",
  "explicit_path_staging",
  "source_path_overlap",
  "database_disposition",
  "database_resource_overlap",
  "serialized_surface_ownership",
  "repository_action_scope",
  "program_width",
]);

export const V5_CHECKPOINT_CHECKS = deepFreeze([
  "census_provenance",
  "checkpoint_record_evidence",
  "checkpoint_base_current",
  "checkpoint_lease_held",
  "checkpoint_next_step",
]);

/**
 * Release, in phase order. The first four are decidable BEFORE a merge and are
 * exactly what `merge_admitted` reports; readback is only decidable after, so a
 * pre-merge caller correctly gets "you may merge, you may not call this
 * released".
 */
export const V5_PRE_MERGE_RELEASE_CHECKS = deepFreeze([
  "independent_review", "green_required_checks", "exact_head", "serialized_merge_slot",
  "descendant_revalidation",
]);
export const V5_RELEASE_CHECKS = deepFreeze([...V5_PRE_MERGE_RELEASE_CHECKS, "merged_readback"]);

export const V5_CHECK_STATES = deepFreeze(["not_reached", "refused", "satisfied"]);

/** The two upstream receipts auto-release waits on, derived, not retyped. */
const FOUNDATION_PREACTIVATION_MEMBER = MINIMUM_REQUIRED_MEMBERS
  .find(entry => entry.gate_id === "foundation-control-plane-child-accepted");
if (!FOUNDATION_PREACTIVATION_MEMBER)
  throw new Error("V5-F02: benchmark-minimum.v5.js no longer registers the foundation-control-plane preactivation member");

export const V5_AUTO_RELEASE_UPSTREAM_STEPS = deepFreeze(
  [GATE_ZERO_STEP_REF, FOUNDATION_PREACTIVATION_MEMBER.step_ref].sort());

export const V5_AUTO_RELEASE_STATES = deepFreeze(["available", "unavailable"]);

export const V5_PROGRAM_CONTROLLER_REASON_IDS = deepFreeze([
  "admitted_for_edit_within_earned_width",
  "auto_release_upstream_receipt_absent",
  "census_observation_stale",
  "checkpoint_base_moved_revalidation_required",
  "checkpoint_lease_not_held",
  "checkpoint_next_step_already_complete",
  "checkpoint_resume_requires_record_evidence",
  "checkpoint_worktree_not_held",
  "database_disposition_refused",
  "database_resource_overlap_denied",
  "descendant_revalidation_required",
  "disjointness_not_computed_from_live_census",
  "explicit_path_staging_required",
  "generic_shell_action_refused",
  "program_width_exceeded",
  "release_complete_after_serialized_merge_and_readback",
  "release_head_moved",
  "release_readback_digest_mismatch",
  "release_readback_not_verified",
  "release_receipt_bound_to_other_head",
  "release_receipt_bound_to_other_slice",
  "release_receipt_digest_mismatch",
  "release_receipt_kind_mismatch",
  "release_receipt_not_content_addressed",
  "release_receipt_unresolvable",
  "release_required_check_bound_to_other_head",
  "release_required_check_not_green",
  "release_review_bound_to_other_head",
  "release_review_not_accepted",
  "release_reviewer_not_independent",
  "release_state_holder_unavailable",
  "resumes_from_recorded_checkpoint",
  "serialized_merge_slot_held",
  "serialized_surface_single_owner_required",
  "shared_root_editing_refused",
  "source_path_overlap_denied",
  "stale_base_refused_before_edit",
  "width_at_or_below_current_earned",
  "width_evidence_incomplete",
  "width_evidence_stale_base",
  "width_granted_on_current_accepted_evidence",
  "width_ladder_step_skipped",
  "writer_tree_shared_with_active_lease",
]);

function reason(id) {
  if (!V5_PROGRAM_CONTROLLER_REASON_IDS.includes(id))
    throw new Error(`V5-F02: unregistered reason_id ${id}`);
  return id;
}

function satisfied(check, note) {
  return deepFreeze({ check, state: "satisfied", reason_id: null, note: note ?? null, detail: null });
}

function refused(check, reasonId, note, detail = null) {
  return deepFreeze({ check, state: "refused", reason_id: reason(reasonId), note, detail });
}

/**
 * Run an ordered check list, first failure deciding. Everything after the
 * blocking check reports `not_reached` and names what blocked it, so an answer
 * never implies a check ran that did not.
 */
function runChecks(order, evaluators) {
  const states = {};
  const satisfiedChecks = [];
  const notReached = [];
  let blocking = null;
  for (const check of order) {
    if (blocking !== null) {
      states[check] = deepFreeze({
        check, state: "not_reached", reason_id: null,
        note: `not reached: ${blocking} refused first`, detail: { blocked_by: blocking },
      });
      notReached.push(check);
      continue;
    }
    const state = evaluators[check]();
    states[check] = state;
    if (state.state === "satisfied") satisfiedChecks.push(check);
    else blocking = check;
  }
  return { states, satisfiedChecks, notReached, blocking };
}

// ---------------------------------------------------------------------------
// Path containment.
//
// "No two active writers may overlap" is decided by PATH SEGMENT, never by
// string prefix. "mcp-server/src" does not contain "mcp-server/srcache", and a
// controller that used startsWith would refuse that pair and quietly let
// "a/b" and "a/b/c" through in the reverse order. Both directions are checked:
// a directory lease contains a file lease and a file lease is contained by a
// directory lease, and either way that is one resource with two writers.
// ---------------------------------------------------------------------------

function pathsOverlap(left, right) {
  if (left === right) return true;
  return left.startsWith(`${right}/`) || right.startsWith(`${left}/`);
}

const GLOB = /[*?[\]{}]/;

function stagingViolation(path) {
  if (path === "." || path === "..") return "whole-tree staging is not an explicit path";
  if (path.startsWith("/")) return "an absolute path is outside the owned worktree";
  if (GLOB.test(path)) return "a glob is not an exact path";
  if (path.split("/").includes("..")) return "a parent traversal is not an exact path";
  if (path.endsWith("/")) return "a trailing slash leaves the boundary unstated";
  return null;
}

// ---------------------------------------------------------------------------
// 1. Earned width.
// ---------------------------------------------------------------------------

const WIDTH_REQUEST_FIELDS = ["program_ref", "current_width", "requested_width", "base", "evidence"];
const WIDTH_BASE_FIELDS = ["origin_main_sha", "observed_at"];
const WIDTH_EVIDENCE_FIELDS = ["state", "evidence_ref", "base_sha", "observed_at"];

function normalizeWidthRequest(request) {
  exact(request, WIDTH_REQUEST_FIELDS, "request");
  ref(request.program_ref, "request.program_ref");
  widthMember(request.current_width, "request.current_width");
  widthMember(request.requested_width, "request.requested_width");
  const base = exact(request.base, WIDTH_BASE_FIELDS, "request.base");
  commitSha(base.origin_main_sha, "request.base.origin_main_sha");
  instant(base.observed_at, "request.base.observed_at");
  // The evidence object's keys are closed to the six classes, but a MISSING
  // class is a policy fact ("no WIP-recovery evidence yet"), not a schema
  // error — that is the ordinary state of a program at width one.
  closed(request.evidence, V5_WIDTH_EVIDENCE_CLASSES, "request.evidence");
  for (const [cls, entry] of Object.entries(request.evidence)) {
    const path = `request.evidence.${cls}`;
    exact(entry, WIDTH_EVIDENCE_FIELDS, path);
    member(entry.state, V5_EVIDENCE_STATES, `${path}.state`);
    ref(entry.evidence_ref, `${path}.evidence_ref`);
    commitSha(entry.base_sha, `${path}.base_sha`);
    instant(entry.observed_at, `${path}.observed_at`);
  }
  return request;
}

/**
 * Decide the width this program has EARNED, never the width it asked for.
 *
 * A refusal still returns a granted_width — the current one — because "you may
 * not go to two" is not "you must stop". That is the difference between a
 * controller and a brake.
 */
export function evaluateProgramWidth(request) {
  normalizeWidthRequest(request);
  const { current_width: current, requested_width: requested, base, evidence } = request;

  const missing = V5_WIDTH_EVIDENCE_CLASSES
    .filter(cls => !Object.hasOwn(evidence, cls) || evidence[cls].state !== "accepted");
  const stale = V5_WIDTH_EVIDENCE_CLASSES
    .filter(cls => Object.hasOwn(evidence, cls) && evidence[cls].state === "accepted"
      && evidence[cls].base_sha !== base.origin_main_sha);
  const anIncrease = requested > current;

  const { states, satisfiedChecks, notReached, blocking } = runChecks(V5_WIDTH_CHECKS, {
    ladder_step: () => (!anIncrease || requested === current + 1)
      ? satisfied("ladder_step", anIncrease ? "one step up the ladder" : "at or below the current earned width")
      : refused("ladder_step", "width_ladder_step_skipped",
        `width may grow one step at a time; ${current} -> ${requested} skips ${current + 1}`,
        { current_width: current, requested_width: requested }),
    evidence_complete: () => (!anIncrease || missing.length === 0)
      ? satisfied("evidence_complete", anIncrease ? "all six evidence classes accepted" : "no increase requested")
      : refused("evidence_complete", "width_evidence_incomplete",
        "every width evidence class must be accepted before width increases",
        { missing_evidence_classes: missing }),
    evidence_current: () => (!anIncrease || stale.length === 0)
      ? satisfied("evidence_current", anIncrease ? "every accepted class is bound to the current base" : "no increase requested")
      : refused("evidence_current", "width_evidence_stale_base",
        "accepted evidence bound to an older base is not current evidence",
        { stale_evidence_classes: stale, origin_main_sha: base.origin_main_sha }),
  });

  const allowed = blocking === null;
  return deepFreeze({
    schema_version: V5_PROGRAM_CONTROLLER_SCHEMA_VERSION,
    policy_version: V5_PROGRAM_CONTROLLER_POLICY_VERSION,
    answer: "program_width",
    decision: allowed ? "allow" : "refuse",
    reason_id: allowed
      ? (anIncrease ? reason("width_granted_on_current_accepted_evidence") : reason("width_at_or_below_current_earned"))
      : states[blocking].reason_id,
    program_ref: request.program_ref,
    current_width: current,
    requested_width: requested,
    // Never above what is earned. A refusal leaves the program running.
    granted_width: allowed ? requested : current,
    evidence_classes_required: [...V5_WIDTH_EVIDENCE_CLASSES],
    missing_evidence_classes: missing,
    stale_evidence_classes: stale,
    checks_required: [...V5_WIDTH_CHECKS],
    checks_satisfied: satisfiedChecks,
    checks_not_reached: notReached,
    blocking_check: blocking,
    check_states: states,
    decided_by: "deterministic_controller",
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The live lease census, shared by admission and checkpoint resume.
// ---------------------------------------------------------------------------

const CENSUS_FIELDS = ["source", "observed_at", "origin_main_sha", "repository_root", "active_leases"];
// A lease names its worktree TWICE, and the two are not interchangeable.
// worktree_path is where the tree is on one machine; worktree_ref is the stable
// identity the checkpoint recorded. Resume compares the REF, because a slice
// that lost its tree and was re-admitted into a new one still holds a lease
// under the same slice_ref -- and "some lease exists for this slice" was never
// the question. See evaluateCheckpointResume's checkpoint_lease_held.
const ACTIVE_LEASE_FIELDS = ["slice_ref", "worktree_ref", "worktree_path", "source_paths", "database_resources", "serialized_surfaces"];

function normalizeCensus(census, path) {
  exact(census, CENSUS_FIELDS, path);
  member(census.source, [...V5_DISJOINTNESS_SOURCES, ...V5_REFUSED_DISJOINTNESS_SOURCES], `${path}.source`);
  instant(census.observed_at, `${path}.observed_at`);
  commitSha(census.origin_main_sha, `${path}.origin_main_sha`);
  str(census.repository_root, `${path}.repository_root`);
  list(census.active_leases, `${path}.active_leases`);
  census.active_leases.forEach((lease, index) => {
    const at = `${path}.active_leases[${index}]`;
    exact(lease, ACTIVE_LEASE_FIELDS, at);
    ref(lease.slice_ref, `${at}.slice_ref`);
    ref(lease.worktree_ref, `${at}.worktree_ref`);
    str(lease.worktree_path, `${at}.worktree_path`);
    sortedUnique(lease.source_paths, `${at}.source_paths`, str);
    sortedUnique(lease.database_resources, `${at}.database_resources`, ref);
    sortedUnique(lease.serialized_surfaces, `${at}.serialized_surfaces`,
      (value, valuePath) => member(value, V5_SERIALIZED_SURFACES, valuePath));
  });
  return census;
}

/**
 * `observedAtMs` is EPOCH MILLISECONDS, not the instant string. The first
 * version of this function took the string straight off the request, and
 * `string - number` is NaN: every comparison against NaN is false, so a census
 * observed a week ago and a census observed in the future both passed. The
 * suite's two staleness cases caught it. Callers pass instant()'s return value,
 * which is the parsed number, so the type is enforced by construction.
 */
function censusProvenance(check, census, observedAtMs) {
  if (!V5_DISJOINTNESS_SOURCES.includes(census.source))
    return refused(check, "disjointness_not_computed_from_live_census",
      "disjointness must be computed from a live lease census immediately before admission, never from a candidate grouping",
      { source: census.source, admissible_sources: [...V5_DISJOINTNESS_SOURCES] });
  const ageSeconds = (observedAtMs - Date.parse(census.observed_at)) / 1000;
  if (ageSeconds < 0 || ageSeconds > V5_MAX_CENSUS_AGE_SECONDS)
    return refused(check, "census_observation_stale",
      "a census observed outside the admission window is an old story, not a current fact",
      { census_age_seconds: ageSeconds, max_census_age_seconds: V5_MAX_CENSUS_AGE_SECONDS });
  return satisfied(check, "computed from a live lease census inside the admission window");
}

// ---------------------------------------------------------------------------
// 2. Slice admission — the decision that happens BEFORE any edit.
// ---------------------------------------------------------------------------

const ADMISSION_REQUEST_FIELDS = [
  "slice_ref", "observed_at", "reuse_disposition", "model_roles", "lease", "census", "width",
];
const LEASE_FIELDS = [
  "worktree_ref", "worktree_path", "branch_ref", "base_sha", "source_paths",
  "database_disposition", "database_resources", "serialized_surfaces", "repository_actions",
];

/**
 * Width is DERIVED at admission, never accepted from the caller.
 *
 * The first version of this took a width ANSWER -- an object the caller said
 * evaluateProgramWidth had produced -- and checked its schema version, policy
 * version, answer name and granted_width. Every one of those four is a field
 * the caller writes, so a request carrying `granted_width: 3` and the three
 * correct labels bought three lanes with no evidence at all. The independent
 * review of PR 980 proved exactly that, and the test named
 * "admission: a forged width answer with every checked field correct is not an
 * answer" is the one that would have caught it.
 *
 * So admission takes the width REQUEST -- the program's current and requested
 * width and the six evidence classes -- and calls evaluateProgramWidth itself.
 * There is no field through which a granted width can arrive. The base is not
 * the caller's either: it is the census's origin/main, so "current evidence"
 * means current against the same head this admission is computed on, and a
 * caller cannot pair stale evidence with a base that flatters it.
 */
const ADMISSION_WIDTH_FIELDS = ["program_ref", "current_width", "requested_width", "evidence"];

function admissionWidthAnswer(request) {
  const width = exact(request.width, ADMISSION_WIDTH_FIELDS, "request.width");
  return evaluateProgramWidth({
    program_ref: width.program_ref,
    current_width: width.current_width,
    requested_width: width.requested_width,
    base: {
      origin_main_sha: request.census.origin_main_sha,
      observed_at: request.census.observed_at,
    },
    evidence: width.evidence,
  });
}

function normalizeAdmissionRequest(request) {
  exact(request, ADMISSION_REQUEST_FIELDS, "request");
  ref(request.slice_ref, "request.slice_ref");
  const observedAtMs = instant(request.observed_at, "request.observed_at");
  // R5 applied as a field nobody can skip: a slice that never said whether it
  // reuses, extends or replaces is not admissible, and the answer carries it.
  member(request.reuse_disposition, V5_REUSE_DISPOSITIONS, "request.reuse_disposition");
  sortedUnique(request.model_roles, "request.model_roles",
    (value, path) => member(value, V5_MODEL_ROLES, path));
  const lease = exact(request.lease, LEASE_FIELDS, "request.lease");
  ref(lease.worktree_ref, "request.lease.worktree_ref");
  str(lease.worktree_path, "request.lease.worktree_path");
  ref(lease.branch_ref, "request.lease.branch_ref");
  commitSha(lease.base_sha, "request.lease.base_sha");
  sortedUnique(lease.source_paths, "request.lease.source_paths", str);
  member(lease.database_disposition,
    [...V5_DATA_DISPOSITIONS, ...V5_REFUSED_DATA_DISPOSITIONS], "request.lease.database_disposition");
  sortedUnique(lease.database_resources, "request.lease.database_resources", ref);
  sortedUnique(lease.serialized_surfaces, "request.lease.serialized_surfaces",
    (value, path) => member(value, V5_SERIALIZED_SURFACES, path));
  sortedUnique(lease.repository_actions, "request.lease.repository_actions", str);
  normalizeCensus(request.census, "request.census");
  object(request.width, "request.width");
  return observedAtMs;
}

/** Every active lease except this slice's own previous entry. */
function peerLeases(census, sliceRef) {
  return census.active_leases.filter(lease => lease.slice_ref !== sliceRef);
}

function firstPathConflict(lease, peers) {
  for (const peer of peers)
    for (const mine of lease.source_paths)
      for (const theirs of peer.source_paths)
        if (pathsOverlap(mine, theirs))
          return { conflicting_slice_ref: peer.slice_ref, path: mine, conflicting_path: theirs };
  return null;
}

function firstResourceConflict(lease, peers) {
  for (const peer of peers)
    for (const mine of lease.database_resources)
      if (peer.database_resources.includes(mine))
        return { conflicting_slice_ref: peer.slice_ref, database_resource: mine };
  return null;
}

function firstSurfaceConflict(lease, peers) {
  for (const peer of peers)
    for (const mine of lease.serialized_surfaces)
      if (peer.serialized_surfaces.includes(mine))
        return { conflicting_slice_ref: peer.slice_ref, serialized_surface: mine };
  return null;
}

/**
 * Decide whether a slice may touch the tree. Every refusal here is a refusal
 * BEFORE an edit exists, which is the point: a conflict discovered at merge is
 * a conflict two writers already paid for.
 */
export function evaluateSliceAdmission(request) {
  const observedAtMs = normalizeAdmissionRequest(request);
  const { lease, census, slice_ref: sliceRef } = request;
  const peers = peerLeases(census, sliceRef);
  const widthAnswer = admissionWidthAnswer(request);
  const grantedWidth = widthAnswer.granted_width;

  const stagingProblem = lease.source_paths
    .map(path => ({ path, problem: stagingViolation(path) }))
    .find(entry => entry.problem !== null) ?? null;
  const pathConflict = firstPathConflict(lease, peers);
  const resourceConflict = firstResourceConflict(lease, peers);
  const surfaceConflict = firstSurfaceConflict(lease, peers);
  const foreignActions = lease.repository_actions
    .filter(action => !ENGINEERING_REPOSITORY_ACTIONS.includes(action));
  // Either identity being shared is the same defect: one tree, two writers.
  const sharedTree = peers.find(peer =>
    peer.worktree_path === lease.worktree_path || peer.worktree_ref === lease.worktree_ref) ?? null;
  const activeAfterAdmission = peers.length + 1;

  const { states, satisfiedChecks, notReached, blocking } = runChecks(V5_ADMISSION_CHECKS, {
    census_provenance: () => censusProvenance("census_provenance", census, observedAtMs),
    fresh_base: () => lease.base_sha === census.origin_main_sha
      ? satisfied("fresh_base", "the lease is based on the observed origin/main head")
      : refused("fresh_base", "stale_base_refused_before_edit",
        "a worktree must be cut from fresh origin/main; this base is behind the observed head",
        { lease_base_sha: lease.base_sha, origin_main_sha: census.origin_main_sha }),
    owned_worktree: () => {
      if (lease.worktree_path === census.repository_root)
        return refused("owned_worktree", "shared_root_editing_refused",
          "the shared repository root is not an owned writer tree",
          { worktree_path: lease.worktree_path, repository_root: census.repository_root });
      if (sharedTree)
        return refused("owned_worktree", "writer_tree_shared_with_active_lease",
          "never share a writer tree",
          { worktree_ref: lease.worktree_ref, worktree_path: lease.worktree_path,
            conflicting_slice_ref: sharedTree.slice_ref });
      return satisfied("owned_worktree", "an owned worktree no other active lease holds");
    },
    explicit_path_staging: () => stagingProblem === null
      ? satisfied("explicit_path_staging", "every leased path is exact")
      : refused("explicit_path_staging", "explicit_path_staging_required",
        `staging must name exact paths: ${stagingProblem.problem}`,
        { path: stagingProblem.path }),
    source_path_overlap: () => pathConflict === null
      ? satisfied("source_path_overlap", "no active writer holds an overlapping path")
      : refused("source_path_overlap", "source_path_overlap_denied",
        "no two active writers may overlap on a source path", pathConflict),
    database_disposition: () => V5_DATA_DISPOSITIONS.includes(lease.database_disposition)
      ? satisfied("database_disposition", `recorded environment: ${lease.database_disposition}`)
      : refused("database_disposition", "database_disposition_refused",
        "a slice uses no DB, an equal-trust branch, a schema-only or hermetic fixture, or an approved sanitized parent",
        { database_disposition: lease.database_disposition, admissible: [...V5_DATA_DISPOSITIONS] }),
    database_resource_overlap: () => resourceConflict === null
      ? satisfied("database_resource_overlap", "no active writer holds an overlapping database resource")
      : refused("database_resource_overlap", "database_resource_overlap_denied",
        "no two active writers may overlap on a database resource", resourceConflict),
    serialized_surface_ownership: () => surfaceConflict === null
      ? satisfied("serialized_surface_ownership",
        lease.serialized_surfaces.length ? "this slice is the only owner of the serialized surfaces it names" : "no serialized surface touched")
      : refused("serialized_surface_ownership", "serialized_surface_single_owner_required",
        "a serialized surface has one designated owner at a time, path disjointness notwithstanding", surfaceConflict),
    repository_action_scope: () => foreignActions.length === 0
      ? satisfied("repository_action_scope", "every action is a registered repository action")
      : refused("repository_action_scope", "generic_shell_action_refused",
        "a slice executes registered repository actions, never a generic shell",
        { refused_actions: foreignActions, registered_actions: [...ENGINEERING_REPOSITORY_ACTIONS] }),
    program_width: () => activeAfterAdmission <= grantedWidth
      ? satisfied("program_width", `${activeAfterAdmission} of ${grantedWidth} earned lanes in use`)
      : refused("program_width", "program_width_exceeded",
        "admitting this slice would run the program wider than it has earned",
        { active_after_admission: activeAfterAdmission, granted_width: grantedWidth }),
  });

  const admitted = blocking === null;
  return deepFreeze({
    schema_version: V5_PROGRAM_CONTROLLER_SCHEMA_VERSION,
    policy_version: V5_PROGRAM_CONTROLLER_POLICY_VERSION,
    answer: "slice_admission",
    decision: admitted ? "allow" : "refuse",
    reason_id: admitted ? reason("admitted_for_edit_within_earned_width") : states[blocking].reason_id,
    slice_ref: sliceRef,
    // Said in the answer's own fields, because "refuse before edit" is the
    // claim this slice has to be able to prove.
    admitted_for_edit: admitted,
    refused_before_edit: !admitted,
    reuse_disposition: request.reuse_disposition,
    model_roles_declared: [...request.model_roles],
    lease_binding: {
      worktree_ref: lease.worktree_ref,
      branch_ref: lease.branch_ref,
      base_sha: lease.base_sha,
      source_paths: [...lease.source_paths],
      database_disposition: lease.database_disposition,
      database_resources: [...lease.database_resources],
      serialized_surfaces: [...lease.serialized_surfaces],
    },
    census_binding: {
      source: census.source,
      observed_at: census.observed_at,
      origin_main_sha: census.origin_main_sha,
      active_peer_slice_refs: peers.map(peer => peer.slice_ref).sort(),
    },
    // The width decision this admission MADE, not one it was handed. A reader
    // can see which evidence was missing or stale without a second call.
    width_binding: {
      program_ref: widthAnswer.program_ref,
      current_width: widthAnswer.current_width,
      requested_width: widthAnswer.requested_width,
      decision: widthAnswer.decision,
      reason_id: widthAnswer.reason_id,
      missing_evidence_classes: [...widthAnswer.missing_evidence_classes],
      stale_evidence_classes: [...widthAnswer.stale_evidence_classes],
    },
    width_derived_by_controller: true,
    granted_width: grantedWidth,
    active_after_admission: activeAfterAdmission,
    checks_required: [...V5_ADMISSION_CHECKS],
    checks_satisfied: satisfiedChecks,
    checks_not_reached: notReached,
    blocking_check: blocking,
    check_states: states,
    // An allow is a decision, not a lease. Nothing was claimed, created or held.
    lease_claimed: false,
    worktree_created: false,
    decided_by: "deterministic_controller",
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// 3. Checkpoint resume.
// ---------------------------------------------------------------------------

const RESUME_REQUEST_FIELDS = ["slice_ref", "observed_at", "checkpoint", "census", "reconstruction"];
const CHECKPOINT_FIELDS = [
  "checkpoint_ref", "recorded_at", "base_sha", "worktree_ref",
  "completed_step_refs", "next_step_ref", "record_evidence_refs",
];
const RECONSTRUCTION_FIELDS = ["source", "inherited_transcript_used"];
const RECONSTRUCTION_SOURCES = deepFreeze(["durable_records", "inherited_transcript"]);

function normalizeResumeRequest(request) {
  exact(request, RESUME_REQUEST_FIELDS, "request");
  ref(request.slice_ref, "request.slice_ref");
  const observedAtMs = instant(request.observed_at, "request.observed_at");
  const checkpoint = exact(request.checkpoint, CHECKPOINT_FIELDS, "request.checkpoint");
  ref(checkpoint.checkpoint_ref, "request.checkpoint.checkpoint_ref");
  instant(checkpoint.recorded_at, "request.checkpoint.recorded_at");
  commitSha(checkpoint.base_sha, "request.checkpoint.base_sha");
  ref(checkpoint.worktree_ref, "request.checkpoint.worktree_ref");
  sortedUnique(checkpoint.completed_step_refs, "request.checkpoint.completed_step_refs", ref);
  ref(checkpoint.next_step_ref, "request.checkpoint.next_step_ref");
  sortedUnique(checkpoint.record_evidence_refs, "request.checkpoint.record_evidence_refs", ref);
  normalizeCensus(request.census, "request.census");
  const reconstruction = exact(request.reconstruction, RECONSTRUCTION_FIELDS, "request.reconstruction");
  member(reconstruction.source, RECONSTRUCTION_SOURCES, "request.reconstruction.source");
  bool(reconstruction.inherited_transcript_used, "request.reconstruction.inherited_transcript_used");
  return observedAtMs;
}

/**
 * Decide whether work resumes FROM RECORDS.
 *
 * The catalog clause is "checkpoint resumes from records", and the word is
 * load-bearing: a session that resumes from an inherited transcript has
 * resumed from a story about the work rather than from the work. That is the
 * same boundary engineering-runtime.js draws with
 * `native_session_transfer: "semantic_state_only"`, decided here instead of
 * described.
 */
export function evaluateCheckpointResume(request) {
  const observedAtMs = normalizeResumeRequest(request);
  const { checkpoint, census, reconstruction } = request;
  // The slice's own live lease, if it still has one. Holding A lease is not
  // holding THIS checkpoint's tree -- see checkpoint_lease_held below.
  const heldLease = census.active_leases
    .find(lease => lease.slice_ref === request.slice_ref) ?? null;

  const { states, satisfiedChecks, notReached, blocking } = runChecks(V5_CHECKPOINT_CHECKS, {
    census_provenance: () => censusProvenance("census_provenance", census, observedAtMs),
    checkpoint_record_evidence: () => {
      if (reconstruction.source !== "durable_records" || reconstruction.inherited_transcript_used)
        return refused("checkpoint_record_evidence", "checkpoint_resume_requires_record_evidence",
          "resume reconstructs from durable records; an inherited transcript is not a record",
          { source: reconstruction.source, inherited_transcript_used: reconstruction.inherited_transcript_used });
      if (checkpoint.record_evidence_refs.length === 0)
        return refused("checkpoint_record_evidence", "checkpoint_resume_requires_record_evidence",
          "a checkpoint with no recorded evidence is a claim, not a checkpoint",
          { record_evidence_refs: [] });
      return satisfied("checkpoint_record_evidence",
        `${checkpoint.record_evidence_refs.length} recorded evidence reference(s)`);
    },
    checkpoint_base_current: () => checkpoint.base_sha === census.origin_main_sha
      ? satisfied("checkpoint_base_current", "the checkpoint is bound to the observed origin/main head")
      : refused("checkpoint_base_current", "checkpoint_base_moved_revalidation_required",
        "main moved under this checkpoint; rebase and revalidate before resuming",
        { checkpoint_base_sha: checkpoint.base_sha, origin_main_sha: census.origin_main_sha }),
    // TWO facts, in order, because the first version conflated them: a live
    // lease EXISTS for this slice, and it is the SAME TREE the checkpoint was
    // written in. Matching on slice_ref alone let a slice resume into whatever
    // tree it had been re-admitted to, replaying a checkpoint written somewhere
    // else -- the exact defect the independent review of PR 980 named.
    checkpoint_lease_held: () => {
      if (heldLease === null)
        return refused("checkpoint_lease_held", "checkpoint_lease_not_held",
          "the slice holds no active lease in the live census; re-admit before resuming",
          { slice_ref: request.slice_ref, worktree_ref: checkpoint.worktree_ref });
      if (heldLease.worktree_ref !== checkpoint.worktree_ref)
        return refused("checkpoint_lease_held", "checkpoint_worktree_not_held",
          "the slice holds a lease on a different tree than the one this checkpoint was written in",
          { slice_ref: request.slice_ref, checkpoint_worktree_ref: checkpoint.worktree_ref,
            held_worktree_ref: heldLease.worktree_ref });
      return satisfied("checkpoint_lease_held", "the slice still holds the tree this checkpoint was written in");
    },
    checkpoint_next_step: () => !checkpoint.completed_step_refs.includes(checkpoint.next_step_ref)
      ? satisfied("checkpoint_next_step", `resumes at ${checkpoint.next_step_ref}`)
      : refused("checkpoint_next_step", "checkpoint_next_step_already_complete",
        "resuming into a completed step is a duplicate queue, not a resume",
        { next_step_ref: checkpoint.next_step_ref }),
  });

  const resumes = blocking === null;
  return deepFreeze({
    schema_version: V5_PROGRAM_CONTROLLER_SCHEMA_VERSION,
    policy_version: V5_PROGRAM_CONTROLLER_POLICY_VERSION,
    answer: "checkpoint_resume",
    decision: resumes ? "allow" : "refuse",
    reason_id: resumes ? reason("resumes_from_recorded_checkpoint") : states[blocking].reason_id,
    slice_ref: request.slice_ref,
    checkpoint_ref: checkpoint.checkpoint_ref,
    worktree_ref: checkpoint.worktree_ref,
    resumes_from_records: resumes,
    next_step_ref: resumes ? checkpoint.next_step_ref : null,
    completed_step_refs: [...checkpoint.completed_step_refs],
    replays_completed_steps: false,
    checks_required: [...V5_CHECKPOINT_CHECKS],
    checks_satisfied: satisfiedChecks,
    checks_not_reached: notReached,
    blocking_check: blocking,
    check_states: states,
    decided_by: "deterministic_controller",
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// 4. Release eligibility.
//
// NO RELEASE FACT IS TAKEN FROM THE CALLER. The first version of this evaluator
// read the review state, the check conclusions, the merge-slot holder, the
// revalidation base and the readback digests straight off the request. The
// independent review of PR 980 named that for what it is: a matching string is
// not evidence of an independent review, a green hosted check, a serialized
// merge or a post-merge readback, and a controller that decides on facts the
// decided party wrote is a rubber stamp with a reason_id.
//
// So the request carries REFERENCES, and the facts are RESOLVED through a state
// holder port. Every reference is CONTENT-ADDRESSED: `receipt:sha256:<hex>` of
// the canonical bytes of the receipt it names. The module rehashes what comes
// back and refuses unless the bytes hash to the reference that was cited, then
// refuses again unless the receipt binds THIS slice and THIS head. A receipt
// body that has been edited keeps neither property, and a reference invented to
// look right resolves to nothing.
//
// WHAT THAT DOES AND DOES NOT BUY. It makes a release fact unforgeable relative
// to the receipt store: a caller can no longer state one, and cannot mutate one
// it was given. It does NOT authenticate the state holder itself — this module
// is handed a port and cannot prove the port is the real ledger. That is an
// integration property, and it belongs to whoever wires the port. It is stated
// here rather than implied, because THERE IS NO SUCH CALLER IN THIS REPOSITORY
// YET: see the seam note in the file header.
// ---------------------------------------------------------------------------

/** The six facts a release decision is made of. One receipt each. */
export const V5_RELEASE_RECEIPT_KINDS = deepFreeze([
  "head_observation", "merge_slot", "readback", "required_checks", "revalidation", "review",
]);

/** `receipt:` + the digest() of the receipt's own canonical bytes. */
const RELEASE_RECEIPT_REF = /^receipt:sha256:[0-9a-f]{64}$/;
const RELEASE_RECEIPT_REF_PREFIX = "receipt:";

const RELEASE_RECEIPT_FIELDS = deepFreeze({
  head_observation: ["kind", "slice_ref", "head_sha", "observed_head_sha"],
  merge_slot: ["kind", "slice_ref", "head_sha", "state", "held_by_slice_ref"],
  readback: ["kind", "slice_ref", "head_sha", "state", "main_sha",
    "delivered_source_digest", "expected_source_digest"],
  required_checks: ["kind", "slice_ref", "head_sha", "checks"],
  revalidation: ["kind", "slice_ref", "head_sha", "required",
    "revalidated_against_sha", "current_main_sha"],
  review: ["kind", "slice_ref", "head_sha", "state", "reviewer_actor_id",
    "maker_actor_id", "reviewed_head_sha"],
});

const CHECK_FIELDS = ["name", "conclusion", "head_sha"];
const RELEASE_REQUEST_FIELDS = ["slice_ref", "head_sha", "receipts", "auto_release_requested"];

function normalizeReleaseRequest(request) {
  exact(request, RELEASE_REQUEST_FIELDS, "request");
  ref(request.slice_ref, "request.slice_ref");
  commitSha(request.head_sha, "request.head_sha");
  // Each receipt reference is a STRING here, not a validated ref: a reference
  // that is not content-addressed is a policy refusal with a named reason, not
  // an unreadable request. The caller is allowed to be wrong about a receipt.
  const receipts = exact(request.receipts, V5_RELEASE_RECEIPT_KINDS, "request.receipts");
  for (const kind of V5_RELEASE_RECEIPT_KINDS) str(receipts[kind], `request.receipts.${kind}`);
  bool(request.auto_release_requested, "request.auto_release_requested");
  return request;
}

/**
 * The SHAPE of a resolved receipt is a contract violation when wrong, the same
 * as any other unreadable input: the module cannot read it, so it fails closed.
 * Its BINDINGS — kind, slice, head — are policy, and refuse by name.
 */
function validateReceiptShape(kind, body, path) {
  exact(body, RELEASE_RECEIPT_FIELDS[kind], path);
  str(body.kind, `${path}.kind`);
  ref(body.slice_ref, `${path}.slice_ref`);
  commitSha(body.head_sha, `${path}.head_sha`);
  if (kind === "head_observation") commitSha(body.observed_head_sha, `${path}.observed_head_sha`);
  if (kind === "review") {
    member(body.state, V5_REVIEW_STATES, `${path}.state`);
    str(body.maker_actor_id, `${path}.maker_actor_id`);
    if (body.state === "absent") {
      // An absent review states no reviewer and no head; forcing one to be
      // invented would make "absent" indistinguishable from "not yet read".
      for (const key of ["reviewer_actor_id", "reviewed_head_sha"])
        if (body[key] !== null)
          fail("absent_review_states_a_reviewer", `${path}.${key} must be null when the review is absent`, { path, key });
    } else {
      str(body.reviewer_actor_id, `${path}.reviewer_actor_id`);
      commitSha(body.reviewed_head_sha, `${path}.reviewed_head_sha`);
    }
  }
  if (kind === "required_checks") {
    list(body.checks, `${path}.checks`);
    body.checks.forEach((check, index) => {
      const at = `${path}.checks[${index}]`;
      exact(check, CHECK_FIELDS, at);
      str(check.name, `${at}.name`);
      member(check.conclusion, V5_CHECK_CONCLUSIONS, `${at}.conclusion`);
      commitSha(check.head_sha, `${at}.head_sha`);
    });
  }
  if (kind === "merge_slot") {
    member(body.state, V5_MERGE_SLOT_STATES, `${path}.state`);
    if (body.state === "held") ref(body.held_by_slice_ref, `${path}.held_by_slice_ref`);
    else if (body.held_by_slice_ref !== null)
      fail("free_slot_names_a_holder", `${path}.held_by_slice_ref must be null when the slot is free`, { path });
  }
  if (kind === "revalidation") {
    bool(body.required, `${path}.required`);
    commitSha(body.current_main_sha, `${path}.current_main_sha`);
    if (body.revalidated_against_sha !== null)
      commitSha(body.revalidated_against_sha, `${path}.revalidated_against_sha`);
  }
  if (kind === "readback") {
    member(body.state, V5_READBACK_STATES, `${path}.state`);
    if (body.state === "verified") {
      commitSha(body.main_sha, `${path}.main_sha`);
      str(body.delivered_source_digest, `${path}.delivered_source_digest`);
      str(body.expected_source_digest, `${path}.expected_source_digest`);
    }
  }
  return body;
}

/**
 * Resolve one receipt and verify it, in the order a forger has to survive:
 * the reference is content-addressed, the holder knows it, the bytes hash to
 * the reference, it is the kind that was asked for, and it binds this slice and
 * this head. Returns `{ ok: true, body }` or the refusal that stopped it.
 */
function resolveVerifiedReceipt(stateHolder, request, kind) {
  const receiptRef = request.receipts[kind];
  const detail = { receipt_kind: kind, receipt_ref: receiptRef };
  if (!RELEASE_RECEIPT_REF.test(receiptRef))
    return { ok: false, reasonId: "release_receipt_not_content_addressed",
      note: "a release receipt is cited by the digest of its own bytes, not by a name", detail };
  let body = null;
  try {
    body = stateHolder.resolveReceipt(receiptRef);
  } catch {
    body = null;
  }
  if (!isPlainObject(body))
    return { ok: false, reasonId: "release_receipt_unresolvable",
      note: "the authoritative state holder does not hold this receipt", detail };
  const bodyDigest = digest(body);
  if (bodyDigest !== receiptRef.slice(RELEASE_RECEIPT_REF_PREFIX.length))
    return { ok: false, reasonId: "release_receipt_digest_mismatch",
      note: "what came back is not the receipt that was cited",
      detail: { ...detail, resolved_digest: bodyDigest } };
  if (body.kind !== kind)
    return { ok: false, reasonId: "release_receipt_kind_mismatch",
      note: "a receipt of another kind does not answer this check",
      detail: { ...detail, resolved_kind: typeof body.kind === "string" ? body.kind : null } };
  validateReceiptShape(kind, body, `receipt(${kind})`);
  if (body.slice_ref !== request.slice_ref)
    return { ok: false, reasonId: "release_receipt_bound_to_other_slice",
      note: "a receipt bound to another slice proves nothing about this one",
      detail: { ...detail, receipt_slice_ref: body.slice_ref, slice_ref: request.slice_ref } };
  if (body.head_sha !== request.head_sha)
    return { ok: false, reasonId: "release_receipt_bound_to_other_head",
      note: "a receipt bound to another head proves nothing about this one",
      detail: { ...detail, receipt_head_sha: body.head_sha, head_sha: request.head_sha } };
  return { ok: true, body };
}

/**
 * Decide release in phase order.
 *
 * `merge_admitted` reports the four pre-merge gates plus descendant
 * revalidation; `decision` reports the whole clause including the readback
 * that can only exist after the merge. So a pre-merge caller is told exactly
 * the true thing: you may take the merge slot, and you may not yet call this
 * released.
 *
 * `stateHolder` is the port to the authoritative receipt store: an object with
 * `resolveReceipt(ref)`. Without it every check refuses — a controller with no
 * way to check a fact does not get to assume one.
 */
export function evaluateReleaseEligibility(request, stateHolder = null) {
  normalizeReleaseRequest(request);
  const holderBound = isPlainObject(stateHolder) && typeof stateHolder.resolveReceipt === "function";
  const resolved = new Map();
  const verifiedRefs = [];

  /** Resolve once per kind, then hand the body to the check that needs it. */
  const withReceipt = (check, kind, use) => {
    if (!holderBound)
      return refused(check, "release_state_holder_unavailable",
        "release facts are resolved from the authoritative state holder; none was bound",
        { receipt_kind: kind });
    if (!resolved.has(kind)) {
      const outcome = resolveVerifiedReceipt(stateHolder, request, kind);
      resolved.set(kind, outcome);
      if (outcome.ok) verifiedRefs.push(request.receipts[kind]);
    }
    const outcome = resolved.get(kind);
    if (!outcome.ok) return refused(check, outcome.reasonId, outcome.note, outcome.detail);
    return use(outcome.body);
  };

  const { states, satisfiedChecks, notReached, blocking } = runChecks(V5_RELEASE_CHECKS, {
    independent_review: () => withReceipt("independent_review", "review", review => {
      if (review.state !== "accepted")
        return refused("independent_review", "release_review_not_accepted",
          "release requires an accepted independent review", { review_state: review.state });
      if (review.reviewer_actor_id === review.maker_actor_id)
        return refused("independent_review", "release_reviewer_not_independent",
          "the maker may not be the reviewer", { actor_id: review.maker_actor_id });
      if (review.reviewed_head_sha !== request.head_sha)
        return refused("independent_review", "release_review_bound_to_other_head",
          "a review of another head is not a review of this one",
          { reviewed_head_sha: review.reviewed_head_sha, head_sha: request.head_sha });
      return satisfied("independent_review", "accepted by an independent reviewer on this exact head");
    }),
    green_required_checks: () => withReceipt("green_required_checks", "required_checks", receipt => {
      const checks = receipt.checks;
      const notGreen = checks.filter(check => check.conclusion !== "success");
      const otherHead = checks.filter(check => check.head_sha !== request.head_sha);
      if (checks.length === 0)
        return refused("green_required_checks", "release_required_check_not_green",
          "a release with no required check is a release with no evidence", { required_checks: [] });
      if (notGreen.length)
        return refused("green_required_checks", "release_required_check_not_green",
          "every required check must be green", { not_green: notGreen.map(check => check.name).sort() });
      if (otherHead.length)
        return refused("green_required_checks", "release_required_check_bound_to_other_head",
          "a green check on another head proves nothing about this one",
          { bound_to_other_head: otherHead.map(check => check.name).sort(), head_sha: request.head_sha });
      return satisfied("green_required_checks", `${checks.length} required check(s) green on this head`);
    }),
    exact_head: () => withReceipt("exact_head", "head_observation", observation =>
      observation.observed_head_sha === request.head_sha
        ? satisfied("exact_head", "the branch head is still the reviewed and tested head")
        : refused("exact_head", "release_head_moved",
          "the branch moved after review; the evidence describes a head that is no longer there",
          { head_sha: request.head_sha, observed_head_sha: observation.observed_head_sha })),
    serialized_merge_slot: () => withReceipt("serialized_merge_slot", "merge_slot", slot =>
      (slot.state === "free" || slot.held_by_slice_ref === request.slice_ref)
        ? satisfied("serialized_merge_slot", "one eligible slice merges at a time and this is it")
        : refused("serialized_merge_slot", "serialized_merge_slot_held",
          "merge is serialized; another slice holds the slot",
          { held_by_slice_ref: slot.held_by_slice_ref })),
    descendant_revalidation: () => withReceipt("descendant_revalidation", "revalidation", revalidation => {
      if (!revalidation.required)
        return satisfied("descendant_revalidation", "no preceding merge affects this slice");
      if (revalidation.revalidated_against_sha === revalidation.current_main_sha)
        return satisfied("descendant_revalidation", "rebased and revalidated against current main");
      return refused("descendant_revalidation", "descendant_revalidation_required",
        "after each merge every affected descendant is rebased and revalidated before its delivery",
        { revalidated_against_sha: revalidation.revalidated_against_sha, current_main_sha: revalidation.current_main_sha });
    }),
    merged_readback: () => withReceipt("merged_readback", "readback", readback => {
      if (readback.state !== "verified")
        return refused("merged_readback", "release_readback_not_verified",
          "a merge is not a delivery until the delivered source is read back from main",
          { readback_state: readback.state });
      if (readback.delivered_source_digest !== readback.expected_source_digest)
        return refused("merged_readback", "release_readback_digest_mismatch",
          "what landed on main is not what was reviewed",
          { delivered_source_digest: readback.delivered_source_digest, expected_source_digest: readback.expected_source_digest });
      return satisfied("merged_readback", `read back from main at ${readback.main_sha}`);
    }),
  });

  const released = blocking === null;
  const mergeAdmitted = V5_PRE_MERGE_RELEASE_CHECKS.every(check => states[check].state === "satisfied");
  const headObservation = resolved.get("head_observation");

  return deepFreeze({
    schema_version: V5_PROGRAM_CONTROLLER_SCHEMA_VERSION,
    policy_version: V5_PROGRAM_CONTROLLER_POLICY_VERSION,
    answer: "release_eligibility",
    decision: released ? "allow" : "refuse",
    reason_id: released
      ? reason("release_complete_after_serialized_merge_and_readback")
      : states[blocking].reason_id,
    slice_ref: request.slice_ref,
    head_sha: request.head_sha,
    // Null until a verified head-observation receipt says otherwise. The
    // controller never restates an unverified observation as a fact.
    observed_head_sha: headObservation?.ok ? headObservation.body.observed_head_sha : null,
    merge_admitted: mergeAdmitted,
    released,
    state_holder_bound: holderBound,
    receipt_refs_cited: Object.fromEntries(
      V5_RELEASE_RECEIPT_KINDS.map(kind => [kind, request.receipts[kind]])),
    receipt_refs_verified: [...verifiedRefs].sort(),
    caller_stated_release_facts: false,
    // The honest unavailable. There is no field on this request that can assert
    // either upstream receipt, so no caller can talk its way into an auto
    // release; the state is derived from steps this repository does not
    // produce, and it says which ones.
    auto_release_requested: request.auto_release_requested,
    auto_release_state: "unavailable",
    auto_release_decided: false,
    auto_release_unavailable_reason_id: reason("auto_release_upstream_receipt_absent"),
    auto_release_missing_steps: [...V5_AUTO_RELEASE_UPSTREAM_STEPS],
    checks_required: [...V5_RELEASE_CHECKS],
    pre_merge_checks: [...V5_PRE_MERGE_RELEASE_CHECKS],
    checks_satisfied: satisfiedChecks,
    checks_not_reached: notReached,
    blocking_check: blocking,
    check_states: states,
    // An allow here records that the clause is satisfied. It merges nothing and
    // deploys nothing; the Deployment Controller remains the only door.
    performs_merge: false,
    performs_deployment: false,
    decided_by: "deterministic_controller",
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest.
//
// Nothing situational is bound — no slice, lease, census, actor or head — so
// two callers describing the same policy reach the same digest. It is an
// identity for these bytes: not an acceptance, not a receipt, and not evidence
// for any consumer gate. Every closed vocabulary this module decides against is
// enumerated and EXPLICITLY sorted, so the digest is stable by construction
// rather than by the luck of a list that happens to be alphabetical today.
// ---------------------------------------------------------------------------

export function v5ProgramControllerPolicyPreimage() {
  return {
    schema_version: V5_PROGRAM_CONTROLLER_SCHEMA_VERSION,
    policy_version: V5_PROGRAM_CONTROLLER_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision_ids: [...V5_PROGRAM_CONTROLLER_DECISION_IDS].sort(),
    width_ladder: [...V5_PROGRAM_WIDTH_LADDER],
    width_evidence_classes: [...V5_WIDTH_EVIDENCE_CLASSES].sort(),
    width_checks_in_order: [...V5_WIDTH_CHECKS],
    admission_checks_in_order: [...V5_ADMISSION_CHECKS],
    checkpoint_checks_in_order: [...V5_CHECKPOINT_CHECKS],
    release_checks_in_order: [...V5_RELEASE_CHECKS],
    pre_merge_release_checks: [...V5_PRE_MERGE_RELEASE_CHECKS],
    check_states: [...V5_CHECK_STATES].sort(),
    evidence_states: [...V5_EVIDENCE_STATES].sort(),
    serialized_surfaces: [...V5_SERIALIZED_SURFACES].sort(),
    data_dispositions: [...V5_DATA_DISPOSITIONS].sort(),
    refused_data_dispositions: [...V5_REFUSED_DATA_DISPOSITIONS].sort(),
    reuse_dispositions: [...V5_REUSE_DISPOSITIONS].sort(),
    model_roles: [...V5_MODEL_ROLES].sort(),
    disjointness_sources: [...V5_DISJOINTNESS_SOURCES].sort(),
    refused_disjointness_sources: [...V5_REFUSED_DISJOINTNESS_SOURCES].sort(),
    max_census_age_seconds: V5_MAX_CENSUS_AGE_SECONDS,
    merge_slot_states: [...V5_MERGE_SLOT_STATES].sort(),
    review_states: [...V5_REVIEW_STATES].sort(),
    check_conclusions: [...V5_CHECK_CONCLUSIONS].sort(),
    readback_states: [...V5_READBACK_STATES].sort(),
    reconstruction_sources: [...RECONSTRUCTION_SOURCES].sort(),
    release_receipt_kinds: [...V5_RELEASE_RECEIPT_KINDS].sort(),
    release_receipt_ref_pattern: RELEASE_RECEIPT_REF.source,
    reason_ids: [...V5_PROGRAM_CONTROLLER_REASON_IDS].sort(),
    repository_actions: [...ENGINEERING_REPOSITORY_ACTIONS].sort(),
    auto_release_state: "unavailable",
    auto_release_missing_steps: [...V5_AUTO_RELEASE_UPSTREAM_STEPS],
    first_unsatisfied_check_decides: true,
    caller_may_select_checks: false,
    caller_may_assert_upstream_receipt: false,
    caller_may_claim_catalog_disjointness: false,
    // The three properties the independent review of PR 980 proved absent.
    caller_may_assert_granted_width: false,
    caller_may_state_release_fact: false,
    release_facts_resolved_from_state_holder: true,
    width_derived_from_evidence_at_admission: true,
    checkpoint_worktree_must_match_active_lease: true,
    path_overlap_is_segment_wise: true,
    unsorted_or_repeating_lease_is_unreadable: true,
    width_earned_one_step_at_a_time: true,
    claims_lease: false,
    creates_worktree: false,
    performs_merge: false,
    performs_deployment: false,
    decides_model_judgment: false,
  };
}

/** The deterministic `sha256:` digest of the closed program-controller policy. */
export function v5ProgramControllerPolicyDigest() {
  return digest(v5ProgramControllerPolicyPreimage());
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function v5ProgramControllerPolicyCanonicalBytes() {
  return canonicalJson(v5ProgramControllerPolicyPreimage());
}
