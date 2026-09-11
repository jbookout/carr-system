// DoctorCRE v5 slice V5-A04: THE QUALIFICATION AND EXPECTED-TOTAL-COST REGISTRY
// — what a route is expected to cost end to end, which tier of work it is
// allowed to occupy, and the rule that cost may only ever ORDER routes that
// have already qualified (decisions Q040.D1, Q041.D1, Q115.D1).
//
// THE TWO SETTLED SENTENCES THIS FILE EXISTS TO ENFORCE:
//
//   Q040.D1 — "Qualification is the hard gate and price breaks ties using
//   expected total cost including retries, review, failure risk, effort,
//   context, tools, delegation, and escalation."
//
//   Q041.D1 — "Use inexpensive qualified builders for settled bounded
//   instructions and a reviewer strong enough to sign off confidently; premium
//   models own concentrated uncertainty and adjudication."
//
// V5-F04 ALREADY DECIDED THE HARD GATE, AND THIS FILE DOES NOT DECIDE IT AGAIN.
// `model-routing.v5.js` owns what a `route-qualification.v1` record is, and
// `model-qualification-kernel.v5.js` owns how one is derived from evidence.
// This module imports F04's own `assertQualificationRecord` and runs it over
// every candidate, so there is exactly one schema for a qualification in the
// tree and a record this module accepted is a record F04's gate would accept.
// What is NEW here is the other half of Q040 — the price that breaks the tie —
// because F04's cost vocabulary is four components (`base_cost_units`,
// `expected_retry_cost_units`, `expected_review_cost_units`,
// `expected_fallback_cost_units`) and Q040 and Q115 together name eleven.
//
// FOUR SEAMS, and they are why the file is shaped this way:
//
//   1. ELEVEN COMPONENTS, ALL REQUIRED, NONE DEFAULTED. Q040 names retries,
//      review, failure risk, effort, context, tools, delegation and escalation;
//      Q115 adds the builder base, adjudication and rework. Every one is a
//      required integer on a cost basis and a basis missing any of them is
//      refused rather than treated as zero — F04 refuses a defaulted cost for
//      exactly this reason, and an unpriced dimension makes an expensive route
//      look cheapest just as convincingly as an unpriced route does.
//
//   2. THE PROJECTION INTO F04'S FOUR BUCKETS IS EXACT, NOT APPROXIMATE.
//      `toRoutingCostComponents` partitions the eleven into F04's four: the
//      partition is total (every component lands somewhere) and disjoint (no
//      component lands twice), so the sum is preserved to the unit. That is
//      what makes F04's `expected_total_cost_units` ordering BE this slice's
//      Q040 expected total cost rather than a second, quieter number beside it.
//      Both properties are asserted at module load and tested.
//
//   3. TIER OCCUPANCY IS DERIVED FROM THE QUALIFICATION EVIDENCE, NEVER
//      ASSERTED. Q041 is implemented as a decision procedure over two declared
//      facts about the work — is the instruction settled and bounded, and is
//      this a build, a review or an adjudication — which yields exactly one
//      required tier. A route occupies that tier only when the quality floors
//      its qualification record actually MET cover the floors the tier policy
//      requires. There is no `tier: "premium"` field anywhere and a closed
//      schema refuses one, for the same reason F04 refuses `qualified: true`.
//
//   4. COST NEVER QUALIFIES, AND THERE IS NO DOWNGRADE. `admitQualifiedRoute`
//      applies the gate in this order and no other: authentic qualification,
//      then task class, then validity window, then tier, and only then cost.
//      A cheaper candidate that fails any earlier step is dropped with its own
//      named reason and can never be selected. When nothing survives, the
//      answer is an honest `unavailable` carrying every candidate's reason —
//      never the best of the unqualified. There is no option, flag or field
//      that accepts a downgrade.
//
// THE MISSING UPSTREAM GATE, ANSWERED HONESTLY. F04's `createModelRoutingGate`
// takes an `authenticateQualifications` function that the record layer is meant
// to supply, and no such verifier exists in this repository. This module does
// not imitate one: `admitQualifiedRoute` REQUIRES an `authenticate_qualification`
// function, and called without one it returns `unavailable` with
// `qualification_authenticator_unavailable` rather than trusting the records it
// was handed. Schema-valid is not authentic, and every result says which it had.
//
// A SHORTFALL IS AN ANSWER; A MALFORMED INPUT IS A THROW. "No route qualifies"
// is a fact about the candidates and is RETURNED. "This basis has an unknown
// key" is a contract violation and THROWS. The two are never mixed, matching
// the qualification kernel exactly.
//
// NO EFFECTS. No filesystem, no network, no database, no environment and no
// clock: every instant arrives on an argument.

import { digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_ROUTE_QUALIFICATION_SCHEMA_VERSION,
  V5_SELF_CERTIFICATION_FIELDS,
  assertQualificationRecord,
} from "./model-routing.v5.js";

export const V5_EXPECTED_COST_SCHEMA_VERSION = "doctorcre-v5-expected-total-cost.v1";
export const V5_COST_BASIS_SCHEMA_VERSION = "expected-total-cost-basis.v1";
export const V5_TIER_POLICY_SCHEMA_VERSION = "route-tier-policy.v1";
export const V5_ROUTE_ADMISSION_SCHEMA_VERSION = "qualified-route-admission.v1";

/**
 * THE ELEVEN COMPONENTS OF AN EXPECTED TOTAL COST, C-sorted, each traced to the
 * settled sentence that put it here. Cost is in abstract integer UNITS, not
 * currency: this slice's effect class is internal financial accounting with no
 * external payment, and a currency field would imply a payment rail that does
 * not exist. Integers because a floating-point total would make "cheapest"
 * depend on the order the components were summed in.
 */
export const V5_COST_COMPONENTS = Object.freeze([
  "adjudication_cost_units",   // Q115.D1 "adjudication"
  "builder_cost_units",        // Q115.D1 "total builder"
  "context_cost_units",        // Q040.D1 "context"
  "delegation_cost_units",     // Q040.D1 "delegation"
  "effort_cost_units",         // Q040.D1 "effort"
  "escalation_cost_units",     // Q040.D1 "escalation"
  "failure_risk_cost_units",   // Q040.D1 "failure risk"
  "retry_cost_units",          // Q040.D1 "retries", Q115.D1 "retry"
  "review_cost_units",         // Q040.D1 "review", Q115.D1 "review"
  "rework_cost_units",         // Q115.D1 "rework"
  "tool_cost_units",           // Q040.D1 "tools"
]);

/**
 * THE FOUR TIERS OF Q041.D1, C-sorted.
 *
 * The names are the settled sentence's own distinctions: an inexpensive
 * qualified builder for settled bounded instructions, a reviewer strong enough
 * to sign off confidently, and premium models owning concentrated uncertainty
 * and adjudication — which are two different jobs and so are two tiers.
 */
export const V5_ROUTE_TIERS = Object.freeze([
  "adjudicator",
  "bounded_instruction_builder",
  "concentrated_uncertainty_owner",
  "signing_reviewer",
]);

/** Whether the instruction for this work is settled and bounded, or not. */
export const V5_INSTRUCTION_STATES = Object.freeze([
  "concentrated_uncertainty",
  "settled_bounded",
]);

/** What the work item is: building, reviewing, or adjudicating a disagreement. */
export const V5_WORK_ACTIVITIES = Object.freeze(["adjudicate", "build", "review"]);

/**
 * F04's four cost buckets, and the exact partition of this slice's eleven
 * components into them. Every component appears in exactly one bucket, which is
 * checked at module load below: the mapping is what makes F04's ordering equal
 * this slice's expected total, so a component silently dropped from it would
 * make an expensive route look cheap in the only place ordering happens.
 */
export const V5_ROUTING_COST_BUCKETS = Object.freeze([
  "base_cost_units",
  "expected_fallback_cost_units",
  "expected_retry_cost_units",
  "expected_review_cost_units",
]);

const COST_BUCKET_PARTITION = Object.freeze({
  // What it takes to run the work once, at the effort and context it needs,
  // with the tools it needs, through whoever it is delegated to.
  base_cost_units: Object.freeze([
    "builder_cost_units", "context_cost_units", "delegation_cost_units",
    "effort_cost_units", "tool_cost_units",
  ]),
  // What it takes to do it again: a retry of the same attempt, or rework after
  // the attempt came back wrong.
  expected_retry_cost_units: Object.freeze(["retry_cost_units", "rework_cost_units"]),
  // What it takes to have the work checked, and to settle a disagreement about
  // the check.
  expected_review_cost_units: Object.freeze(["adjudication_cost_units", "review_cost_units"]),
  // What it takes when the attempt does not come back usable at all: the priced
  // failure risk, and the escalation that follows it.
  expected_fallback_cost_units: Object.freeze(["escalation_cost_units", "failure_risk_cost_units"]),
});

/** The route identity, matching V5-F04's `route-qualification.v1` fields exactly. */
export const V5_ROUTE_KEYS = Object.freeze([
  "backend_key", "effort", "model_key", "model_version", "task_class",
]);

const REF = /^[a-z0-9][a-z0-9_.:/-]{0,127}$/;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

export class V5ExpectedCostError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5ExpectedCostError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5ExpectedCostError(code, message, detail);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

/** A structural copy taken before hashing, so a caller cannot mutate the preimage later. */
function copy(value) {
  return JSON.parse(JSON.stringify(value));
}

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
}

function assertClosedKeys(value, allowed, path) {
  const unknown = Object.keys(value).filter(key => !allowed.includes(key)).sort();
  if (unknown.length > 0) {
    fail("unknown_field", `${path} carries fields this contract does not declare`,
      { path, unknown, allowed: [...allowed] });
  }
}

function assertRequiredKeys(value, required, path) {
  const missing = required.filter(key => value[key] === undefined).sort();
  if (missing.length > 0) {
    fail("missing_field", `${path} is missing required fields`, { path, missing });
  }
}

function assertRef(value, path) {
  if (typeof value !== "string" || !REF.test(value)) {
    fail("invalid_reference", `${path} must be a lower-case reference token`, { path, value });
  }
  return value;
}

function assertRefSet(value, path) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  value.forEach((item, index) => assertRef(item, `${path}[${index}]`));
  if (new Set(value).size !== value.length) {
    fail("duplicate_entry", `${path} contains a duplicate`, { path });
  }
  return [...value].sort();
}

function assertMember(value, allowed, path, code) {
  if (!allowed.includes(value)) {
    fail(code, `${path} must be one of ${allowed.join(", ")}`, { path, value, allowed: [...allowed] });
  }
  return value;
}

function assertCostUnits(value, path) {
  if (!Number.isSafeInteger(value) || value < 0) {
    fail("invalid_cost_units", `${path} must be a safe non-negative integer of cost units`,
      { path, value });
  }
  return value;
}

function daysInMonth(year, month) {
  if (month === 2) {
    return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  }
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/**
 * An instant is parsed, never inferred, and THE CALENDAR IS CHECKED AGAINST THE
 * LITERAL FIELDS BEFORE PARSING — `Date.parse` normalizes an impossible date
 * rather than rejecting it, and an admission decided against a `now` nobody
 * wrote would admit or expire the wrong routes. S01 and J102 each hold this
 * check privately and neither exports it; the repetition is named in the
 * slice's author report.
 */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) {
    fail("invalid_instant", `${path} must be an ISO-8601 instant`, { path, value });
  }
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  const h = Number(hour), mi = Number(minute), s = Number(second);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) || h > 23 || mi > 59 || s > 59
    || (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_instant",
      `${path} names an instant that does not exist on the calendar; it is not normalized into a different one`,
      { path, value });
  }
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) {
    fail("invalid_instant", `${path} is not a readable instant`, { path, value });
  }
  return ms;
}

/**
 * The reserved names F04 refuses on a qualification record, refused here on a
 * cost basis too. IMPORTED, not copied: a basis is not a place to smuggle back
 * the self-certification the routing kernel threw out, and a second local list
 * would be a second answer to one question.
 */
function assertSelfCertificationFree(value, path) {
  const claimed = Object.keys(value).filter(key => V5_SELF_CERTIFICATION_FIELDS.includes(key)).sort();
  if (claimed.length > 0) {
    fail("self_certified_cost_refused",
      "a cost basis states what the work is expected to cost; it never states that the route is qualified, and the routing kernel's reserved self-certification names are refused here for the same reason it refuses them on a record",
      { path, fields: claimed });
  }
}

// ---------------------------------------------------------------------------
// Module-load invariants over the partition.
//
// These run once, at import, and throw. A partition that lost a component or
// gained a duplicate would not fail any single test obviously — it would just
// quietly change every ordering — so it is checked where it cannot be skipped.
// ---------------------------------------------------------------------------

{
  const placed = V5_ROUTING_COST_BUCKETS.flatMap(bucket => COST_BUCKET_PARTITION[bucket]);
  const sorted = [...placed].sort();
  if (new Set(placed).size !== placed.length) {
    fail("invalid_partition", "a cost component is placed in more than one routing bucket",
      { placed: sorted });
  }
  if (sorted.join(" ") !== [...V5_COST_COMPONENTS].join(" ")) {
    fail("invalid_partition",
      "the routing-bucket partition must cover every declared cost component exactly once",
      { placed: sorted, components: [...V5_COST_COMPONENTS] });
  }
}

// ---------------------------------------------------------------------------
// The cost basis.
// ---------------------------------------------------------------------------

const BASIS_KEYS = Object.freeze([
  "schema_version", "basis_id", "basis_version", "route", "components",
]);

function assertRoute(value, path) {
  assertObject(value, path);
  assertClosedKeys(value, V5_ROUTE_KEYS, path);
  assertRequiredKeys(value, V5_ROUTE_KEYS, path);
  const route = {};
  for (const key of V5_ROUTE_KEYS) route[key] = assertRef(value[key], `${path}.${key}`);
  return route;
}

/**
 * The one place a route becomes a comparable key. Sorted field order, so the
 * key does not depend on how the caller's object literal was written.
 */
export function routeKey(route) {
  return V5_ROUTE_KEYS.map(key => route[key]).join("|");
}

/**
 * Sum the eleven components. Exported because the ledger and the variance
 * module both need the same total and a second summation would be a second
 * answer; `Number.isSafeInteger` is re-checked on the total because eleven safe
 * integers can add up to one that is not.
 */
export function expectedTotalCostUnits(components, path = "components") {
  assertObject(components, path);
  assertClosedKeys(components, V5_COST_COMPONENTS, path);
  assertRequiredKeys(components, V5_COST_COMPONENTS, path);
  let total = 0;
  for (const key of V5_COST_COMPONENTS) {
    total += assertCostUnits(components[key], `${path}.${key}`);
  }
  if (!Number.isSafeInteger(total)) {
    fail("invalid_cost_units", `${path} sums past the safe integer range`, { path, total });
  }
  return total;
}

/**
 * Verify a cost basis and return it sealed over its own exact bytes.
 *
 * The digest covers the source document, so a wrapper carrying a valid basis
 * and a quietly lowered component beside it is ignored rather than believed —
 * the same rule, and the same reason, as F04's `assertCompiledPolicy`.
 */
export function compileCostBasis(basis) {
  assertObject(basis, "basis");
  assertSelfCertificationFree(basis, "basis");
  assertClosedKeys(basis, BASIS_KEYS, "basis");
  assertRequiredKeys(basis, BASIS_KEYS, "basis");
  if (basis.schema_version !== V5_COST_BASIS_SCHEMA_VERSION) {
    fail("wrong_schema_version", `basis.schema_version must be "${V5_COST_BASIS_SCHEMA_VERSION}"`,
      { path: "basis.schema_version", value: basis.schema_version });
  }
  assertRef(basis.basis_id, "basis.basis_id");
  if (!Number.isInteger(basis.basis_version) || basis.basis_version < 1) {
    fail("invalid_shape", "basis.basis_version must be an integer >= 1",
      { path: "basis.basis_version", value: basis.basis_version });
  }
  const route = assertRoute(basis.route, "basis.route");
  assertSelfCertificationFree(basis.components, "basis.components");
  const total = expectedTotalCostUnits(basis.components, "basis.components");

  const components = {};
  for (const key of V5_COST_COMPONENTS) components[key] = basis.components[key];

  return deepFreeze({
    schema_version: V5_COST_BASIS_SCHEMA_VERSION,
    basis_id: basis.basis_id,
    basis_version: basis.basis_version,
    route,
    route_key: routeKey(route),
    components,
    expected_total_cost_units: total,
    basis_digest: digest(copy(basis)),
  });
}

/**
 * Project a compiled basis into V5-F04's four routing cost components.
 *
 * The partition is total and disjoint, so `sum(result) === basis.expected_total_
 * cost_units` exactly — and that equality is re-checked here rather than
 * trusted, because it is the whole reason the projection exists.
 */
export function toRoutingCostComponents(basis) {
  if (!isPlainObject(basis) || basis.schema_version !== V5_COST_BASIS_SCHEMA_VERSION) {
    fail("invalid_shape", "toRoutingCostComponents takes a compiled cost basis",
      { path: "basis" });
  }
  const projected = {};
  let sum = 0;
  for (const bucket of V5_ROUTING_COST_BUCKETS) {
    let bucketTotal = 0;
    for (const component of COST_BUCKET_PARTITION[bucket]) {
      bucketTotal += assertCostUnits(basis.components[component], `basis.components.${component}`);
    }
    projected[bucket] = bucketTotal;
    sum += bucketTotal;
  }
  if (sum !== basis.expected_total_cost_units) {
    fail("invalid_partition",
      "the routing projection must preserve the expected total cost exactly",
      { projected_total: sum, expected_total_cost_units: basis.expected_total_cost_units });
  }
  return deepFreeze(projected);
}

// ---------------------------------------------------------------------------
// Q041: which tier the work needs, and which tier a route may occupy.
//
// THE DECISION PROCEDURE, in order, and it is total over the declared
// vocabularies so there is no unhandled pair:
//
//   1. Is this an adjudication?  -> adjudicator  (premium owns adjudication)
//   2. Is this a review?         -> signing_reviewer  (strong enough to sign off)
//   3. Is the instruction settled and bounded?
//        yes -> bounded_instruction_builder  (inexpensive qualified builder)
//        no  -> concentrated_uncertainty_owner  (premium owns the uncertainty)
//
// Activity is asked before instruction state on purpose: Q041 gives
// adjudication to premium unconditionally, so a "settled bounded adjudication"
// is still an adjudication, and asking the cheaper question first would route
// it to the cheap tier.
// ---------------------------------------------------------------------------

const WORK_ITEM_KEYS = Object.freeze(["task_class", "instruction_state", "activity", "now"]);

/** The required tier for a work item, by the ordered procedure above. */
export function requiredTierForWorkItem(workItem) {
  assertObject(workItem, "work_item");
  const activity = assertMember(workItem.activity, V5_WORK_ACTIVITIES,
    "work_item.activity", "unknown_work_activity");
  const instructionState = assertMember(workItem.instruction_state, V5_INSTRUCTION_STATES,
    "work_item.instruction_state", "unknown_instruction_state");
  if (activity === "adjudicate") return "adjudicator";
  if (activity === "review") return "signing_reviewer";
  return instructionState === "settled_bounded"
    ? "bounded_instruction_builder"
    : "concentrated_uncertainty_owner";
}

const TIER_POLICY_KEYS = Object.freeze([
  "schema_version", "policy_id", "policy_version", "tiers",
]);
const TIER_ENTRY_KEYS = Object.freeze(["tier", "required_quality_floor_refs"]);

/**
 * A tier policy says, per tier, which quality floors a route's MEASURED
 * qualification must already have met to occupy it. Every one of the four tiers
 * must be declared: a policy that simply omitted `adjudicator` would make every
 * adjudication route trivially eligible, which is the quiet weakening Q041
 * exists to prevent.
 */
export function compileTierPolicy(policy) {
  assertObject(policy, "tier_policy");
  assertClosedKeys(policy, TIER_POLICY_KEYS, "tier_policy");
  assertRequiredKeys(policy, TIER_POLICY_KEYS, "tier_policy");
  if (policy.schema_version !== V5_TIER_POLICY_SCHEMA_VERSION) {
    fail("wrong_schema_version",
      `tier_policy.schema_version must be "${V5_TIER_POLICY_SCHEMA_VERSION}"`,
      { path: "tier_policy.schema_version", value: policy.schema_version });
  }
  assertRef(policy.policy_id, "tier_policy.policy_id");
  if (!Number.isInteger(policy.policy_version) || policy.policy_version < 1) {
    fail("invalid_shape", "tier_policy.policy_version must be an integer >= 1",
      { path: "tier_policy.policy_version", value: policy.policy_version });
  }
  if (!Array.isArray(policy.tiers)) {
    fail("invalid_shape", "tier_policy.tiers must be an array", { path: "tier_policy.tiers" });
  }
  const tiers = Object.create(null);
  policy.tiers.forEach((entry, index) => {
    const path = `tier_policy.tiers[${index}]`;
    assertObject(entry, path);
    assertClosedKeys(entry, TIER_ENTRY_KEYS, path);
    assertRequiredKeys(entry, TIER_ENTRY_KEYS, path);
    const tier = assertMember(entry.tier, V5_ROUTE_TIERS, `${path}.tier`, "unknown_route_tier");
    if (tiers[tier] !== undefined) {
      fail("duplicate_entry", `${path}.tier is declared twice`, { path, tier });
    }
    const floors = assertRefSet(entry.required_quality_floor_refs,
      `${path}.required_quality_floor_refs`);
    if (floors.length === 0) {
      fail("tier_floor_absent",
        `${path}.required_quality_floor_refs must name at least one floor; a tier with no floor is not a tier`,
        { path, tier });
    }
    tiers[tier] = Object.freeze(floors);
  });
  const missing = V5_ROUTE_TIERS.filter(tier => tiers[tier] === undefined);
  if (missing.length > 0) {
    fail("tier_not_declared", "tier_policy must declare every tier this slice routes to",
      { path: "tier_policy.tiers", missing });
  }
  return deepFreeze({
    schema_version: V5_TIER_POLICY_SCHEMA_VERSION,
    policy_id: policy.policy_id,
    policy_version: policy.policy_version,
    tiers: Object.fromEntries(V5_ROUTE_TIERS.map(tier => [tier, [...tiers[tier]]])),
    policy_digest: digest(copy(policy)),
  });
}

/**
 * Whether a qualification's MET floors cover the tier's required floors.
 *
 * The record's `met_quality_floor_refs` is F04's derived field — the floors the
 * measurement actually established — so tier occupancy is a fact about evidence
 * and there is no field on anything here that can shortcut it.
 */
export function evaluateTierOccupancy({ tier_policy, tier, qualification } = {}) {
  if (!isPlainObject(tier_policy) || tier_policy.schema_version !== V5_TIER_POLICY_SCHEMA_VERSION) {
    fail("invalid_shape", "evaluateTierOccupancy takes a compiled tier policy",
      { path: "tier_policy" });
  }
  assertMember(tier, V5_ROUTE_TIERS, "tier", "unknown_route_tier");
  assertQualificationRecord(qualification, "qualification");
  const required = tier_policy.tiers[tier];
  const met = new Set(qualification.met_quality_floor_refs);
  const missing = required.filter(ref => !met.has(ref));
  return deepFreeze({
    tier,
    occupies: missing.length === 0,
    required_quality_floor_refs: [...required],
    missing_quality_floor_refs: missing,
  });
}

// ---------------------------------------------------------------------------
// Admission: qualification first, then price.
// ---------------------------------------------------------------------------

const CANDIDATE_KEYS = Object.freeze(["qualification", "cost_basis"]);

/** The reasons a candidate can be dropped, C-sorted. Every one is returned, never thrown. */
export const V5_CANDIDATE_REASONS = Object.freeze([
  "qualification_expired",
  "qualification_not_authentic",
  "task_class_mismatch",
  "tier_floors_not_met",
]);

/** The reasons the whole admission can be unavailable, C-sorted. */
export const V5_ADMISSION_UNAVAILABLE_REASONS = Object.freeze([
  "no_candidate_qualified",
  "qualification_authenticator_unavailable",
]);

/**
 * Choose the cheapest route that has already qualified for this work item, or
 * say honestly that none has.
 *
 * `authenticate_qualification` is V5-F04's trusted-verifier seam, narrowed to
 * one record: it is handed the schema-checked record and must return true for
 * it to count. It is REQUIRED. There is no repository-resident verifier today
 * — the record layer's `attest-attempt-evaluation` is the authority and no pure
 * module can reach it — so a caller with nothing to pass gets `unavailable`
 * with `qualification_authenticator_unavailable` rather than a selection made
 * over records nobody vouched for.
 *
 * ORDER IS THE POINT. Authenticity, task class, validity and tier are all
 * decided before any cost is read, and `expected_total_cost_units` is consulted
 * only to order what is left. A tie is broken by `route_key`, so two routes at
 * the same price still produce one deterministic answer rather than whichever
 * the caller listed first.
 */
export function admitQualifiedRoute({
  tier_policy, work_item, candidates, authenticate_qualification,
} = {}) {
  if (!isPlainObject(tier_policy) || tier_policy.schema_version !== V5_TIER_POLICY_SCHEMA_VERSION) {
    fail("invalid_shape", "admitQualifiedRoute takes a compiled tier policy", { path: "tier_policy" });
  }
  assertObject(work_item, "work_item");
  assertClosedKeys(work_item, WORK_ITEM_KEYS, "work_item");
  assertRequiredKeys(work_item, WORK_ITEM_KEYS, "work_item");
  const taskClass = assertRef(work_item.task_class, "work_item.task_class");
  const nowMs = assertInstant(work_item.now, "work_item.now");
  const requiredTier = requiredTierForWorkItem(work_item);

  if (!Array.isArray(candidates)) {
    fail("invalid_shape", "candidates must be an array", { path: "candidates" });
  }
  if (candidates.length === 0) {
    fail("no_candidates", "admission is a comparison; there is nothing to compare",
      { path: "candidates" });
  }

  // Every candidate is SHAPE-checked before the authenticator is consulted, so
  // a malformed candidate throws whether or not a verifier was supplied — a
  // contract violation is not something an absent gate gets to hide.
  const checked = candidates.map((candidate, index) => {
    const path = `candidates[${index}]`;
    assertObject(candidate, path);
    assertClosedKeys(candidate, CANDIDATE_KEYS, path);
    assertRequiredKeys(candidate, CANDIDATE_KEYS, path);
    assertQualificationRecord(candidate.qualification, `${path}.qualification`);
    if (!isPlainObject(candidate.cost_basis)
      || candidate.cost_basis.schema_version !== V5_COST_BASIS_SCHEMA_VERSION) {
      fail("invalid_shape", `${path}.cost_basis must be a compiled cost basis`,
        { path: `${path}.cost_basis` });
    }
    const qualificationRouteKey = routeKey(candidate.qualification);
    if (qualificationRouteKey !== candidate.cost_basis.route_key) {
      fail("route_mismatch",
        `${path} prices one route and qualifies another`,
        {
          path, qualification_route_key: qualificationRouteKey,
          cost_basis_route_key: candidate.cost_basis.route_key,
        });
    }
    return { path, route_key: qualificationRouteKey, ...candidate };
  });

  const seen = new Set();
  for (const candidate of checked) {
    if (seen.has(candidate.route_key)) {
      fail("duplicate_entry", "two candidates name one route",
        { path: "candidates", route_key: candidate.route_key });
    }
    seen.add(candidate.route_key);
  }

  const base = {
    schema_version: V5_ROUTE_ADMISSION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    task_class: taskClass,
    required_tier: requiredTier,
    tier_policy_digest: tier_policy.policy_digest,
    qualification_schema_version: V5_ROUTE_QUALIFICATION_SCHEMA_VERSION,
    // Said on every answer, admitted or not: cost was never allowed to decide
    // whether a route qualifies, only which qualified route is cheapest.
    cost_decided_qualification: false,
    downgrade_permitted: false,
  };

  if (typeof authenticate_qualification !== "function") {
    return deepFreeze({
      ...base,
      admitted: false,
      reason_id: "qualification_authenticator_unavailable",
      detail: "no trusted qualification verifier was supplied; a schema-valid record is not an authenticated one and this module will not select over records nobody vouched for",
      considered: checked.map(candidate => ({
        route_key: candidate.route_key, reason_id: "qualification_not_authentic",
      })),
    });
  }

  const rejected = [];
  const survivors = [];
  for (const candidate of checked) {
    const authentic = authenticate_qualification(candidate.qualification) === true;
    if (!authentic) {
      rejected.push({ route_key: candidate.route_key, reason_id: "qualification_not_authentic" });
      continue;
    }
    if (candidate.qualification.task_class !== taskClass) {
      rejected.push({ route_key: candidate.route_key, reason_id: "task_class_mismatch" });
      continue;
    }
    if (assertInstant(candidate.qualification.expires_at,
      `${candidate.path}.qualification.expires_at`) <= nowMs) {
      rejected.push({ route_key: candidate.route_key, reason_id: "qualification_expired" });
      continue;
    }
    const occupancy = evaluateTierOccupancy({
      tier_policy, tier: requiredTier, qualification: candidate.qualification,
    });
    if (!occupancy.occupies) {
      rejected.push({
        route_key: candidate.route_key,
        reason_id: "tier_floors_not_met",
        missing_quality_floor_refs: occupancy.missing_quality_floor_refs,
      });
      continue;
    }
    survivors.push(candidate);
  }

  if (survivors.length === 0) {
    return deepFreeze({
      ...base,
      admitted: false,
      reason_id: "no_candidate_qualified",
      detail: "every candidate failed the hard gate; the cheapest of them is still not a qualified route",
      considered: [...rejected].sort((a, b) => (a.route_key < b.route_key ? -1 : 1)),
    });
  }

  const ordered = [...survivors].sort((a, b) => {
    const delta = a.cost_basis.expected_total_cost_units - b.cost_basis.expected_total_cost_units;
    if (delta !== 0) return delta;
    return a.route_key < b.route_key ? -1 : 1;
  });
  const selected = ordered[0];

  return deepFreeze({
    ...base,
    admitted: true,
    reason_id: "qualified_then_cheapest",
    selected: {
      route_key: selected.route_key,
      qualification_id: selected.qualification.qualification_id,
      basis_id: selected.cost_basis.basis_id,
      basis_digest: selected.cost_basis.basis_digest,
      expected_total_cost_units: selected.cost_basis.expected_total_cost_units,
      routing_cost_components: toRoutingCostComponents(selected.cost_basis),
    },
    ranked: ordered.map(candidate => ({
      route_key: candidate.route_key,
      expected_total_cost_units: candidate.cost_basis.expected_total_cost_units,
    })),
    considered: [...rejected].sort((a, b) => (a.route_key < b.route_key ? -1 : 1)),
  });
}

// ---------------------------------------------------------------------------
// The honest, zero-effect projection.
// ---------------------------------------------------------------------------

const PREIMAGE = deepFreeze({
  schema_version: V5_EXPECTED_COST_SCHEMA_VERSION,
  cost_components: [...V5_COST_COMPONENTS],
  routing_cost_buckets: [...V5_ROUTING_COST_BUCKETS],
  cost_bucket_partition: Object.fromEntries(
    V5_ROUTING_COST_BUCKETS.map(bucket => [bucket, [...COST_BUCKET_PARTITION[bucket]]])),
  route_tiers: [...V5_ROUTE_TIERS],
  instruction_states: [...V5_INSTRUCTION_STATES],
  work_activities: [...V5_WORK_ACTIVITIES],
  route_keys: [...V5_ROUTE_KEYS],
  candidate_reasons: [...V5_CANDIDATE_REASONS],
  admission_unavailable_reasons: [...V5_ADMISSION_UNAVAILABLE_REASONS],
});

/** The exact bytes every closed vocabulary in this module is hashed over. */
export function v5ExpectedTotalCostPreimage() {
  return PREIMAGE;
}

export const V5_EXPECTED_TOTAL_COST_DIGEST = digest(copy(PREIMAGE));

/** What this registry decides, what it refuses to invent, and what is still missing. */
export function v5ExpectedTotalCostProjection() {
  return deepFreeze({
    schema_version: V5_EXPECTED_COST_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    basis_schema_version: V5_COST_BASIS_SCHEMA_VERSION,
    tier_policy_schema_version: V5_TIER_POLICY_SCHEMA_VERSION,
    admission_schema_version: V5_ROUTE_ADMISSION_SCHEMA_VERSION,
    preimage_digest: V5_EXPECTED_TOTAL_COST_DIGEST,
    // Properties, not aspirations: each is enforced above and tested.
    cost_can_qualify_a_route: false,
    downgrade_reachable_by_any_input: false,
    cost_component_defaulted_here: false,
    tier_assertable_without_evidence: false,
    routing_projection_preserves_total: true,
    qualification_schema_owner: "model-routing.v5.js#assertQualificationRecord",
    self_certification_vocabulary_owner: "model-routing.v5.js#V5_SELF_CERTIFICATION_FIELDS",
    qualification_derivation_owner: "model-qualification-kernel.v5.js#deriveRouteQualification",
    // The whole weight of the boundary, said as a field.
    qualification_authenticity_verified_here: false,
    unimplemented_dependencies: [
      "a trusted qualification verifier: V5-F04's createModelRoutingGate takes an authenticateQualifications function that the record layer is meant to supply, and no such verifier exists in this repository; admitQualifiedRoute requires one to be injected and answers unavailable without it rather than trusting a schema-valid record",
      "a producer of real cost observations: every component on a basis here is a declared expectation, not a measured charge, because measuring one needs dispatch (V5-F06/V5-F07) and a provider bill, neither of which is a repository fact",
      "a durable store for bases, tier policies and admissions: this registry adds no table, no migration and no SQL, so nothing it compiles survives the process that compiled it",
      "a price list: this module holds no number of its own and cannot tell an honest component from a made-up one; a basis is only as true as its author",
    ],
    effects: V5_NO_EFFECTS,
  });
}
