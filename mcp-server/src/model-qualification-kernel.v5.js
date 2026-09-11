// DoctorCRE v5 slice V5-F04: THE EVALUATION KERNEL — the deterministic
// derivation of a `route-qualification.v1` record from task-class evaluation
// observations (decisions Q031.D1, Q047.D1).
//
// WHAT THIS IS, AND THE ONE THING IT IS NOT. `model-routing.v5.js` reads
// qualification records and never makes one; its own projection has said so
// since the slice merged, and its fixtures name a `verifier:...evaluation-kernel`
// that did not exist. This file is that kernel's DERIVATION half: given a sealed
// evaluation plan and the observations taken under it, it produces the exact
// record the routing gate consumes, or says in one named reason why the evidence
// does not support one.
//
// It is NOT the producer of observations. Running a model against a case needs
// dispatch, which is V5-F06/V5-F07 work and does not exist here; attesting that
// an observation is real is the record layer's `attest-attempt-evaluation`,
// which is human-and-authority-only and is not reachable from a pure module.
// So: every observation is an INPUT, this kernel cannot tell an attested one
// from a typed-in one, and it says exactly that on every result it returns
// (`evidence_authenticity_verified: false`) rather than implying otherwise.
// Closing that half is named in `v5ModelQualificationKernelProjection`.
//
// THE TRUST SEAMS, which are why the file is shaped this way:
//
//   1. THE PLAN IS INPUT, NOT INVENTION. Every threshold — how many observations
//      a floor needs, what pass rate it needs, how long a measurement stays
//      valid, how often a tool must be exercised — arrives in an explicit,
//      versioned, closed `route-evaluation-plan.v1` document that is hashed on
//      the way in. This kernel holds no number of its own. It does not know what
//      a good pass rate is and refuses a plan that omits one rather than
//      defaulting it, for exactly the reason the routing policy refuses a
//      defaulted zero cost: a defaulted floor makes an unmeasured route look
//      qualified.
//
//   2. AN OBSERVATION IS NEVER JUDGED HERE. Whether one case passed is carried
//      on the observation as `outcome`. This kernel counts, it does not grade,
//      and an observation carrying `qualified`, `self_reported_quality` or any
//      other member of the routing kernel's reserved self-certification list is
//      refused BY NAME — the same list, imported, not a second copy of it.
//
//   3. COVERAGE IS DERIVED FROM EVIDENCE, NEVER ASSERTED. A tool or a data class
//      is qualified only when at least the plan's declared minimum number of
//      PASSING observations exercised it. There is no field on a plan or an
//      observation that can put a tool into the derived record without evidence
//      for it, and a tool the plan REQUIRES but the evidence does not carry is
//      an honest `required_tool_not_evidenced`, never a quietly narrower record.
//
//   4. A SHORTFALL IS AN ANSWER; A MALFORMED INPUT IS A THROW. "The evidence
//      does not reach this floor" is a fact about the measurement and is
//      RETURNED, with the counts behind it, exactly as the routing kernel
//      returns `unavailable`. "This plan has an unknown key" is a contract
//      violation and THROWS. The two are never mixed, so a caller can tell a
//      measured shortfall from a broken caller without reading a message.
//
// REUSED, NOT REDECIDED. The p95 rule is `benchmark-minimum.v5.js`'s exported
// `nearestRankP95` — the tree already decided that nearest-rank is the p95 in
// this system, and a second aggregation here would be a second answer to one
// question. The qualification schema is `model-routing.v5.js`'s
// `assertQualificationRecord`, run over this kernel's own output before it is
// returned, so this file cannot emit a record the routing gate would reject.
// The data-class vocabulary is `global-boundaries.v5.js`'s, the tenant is
// `identity.js`'s, canonical hashing is `artifact-trust.js`'s `digest`.
//
// NO PROVENANCE BRAND, ON PURPOSE. The routing gate authenticates a
// qualification by its `verifier_id` against the identities it was configured to
// accept, not by object identity. A WeakSet brand here would be a second
// authenticity claim that the gate never consults — impressive-looking and worth
// nothing — so there is none, and `verifier_id` is carried through from the
// plan and never minted.
//
// NO EFFECTS. No filesystem, no network, no database, no environment and no
// clock: every instant arrives on an observation or is derived from one.

import { digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS, V5_DATA_CLASSES } from "./global-boundaries.v5.js";
import { nearestRankP95 } from "./benchmark-minimum.v5.js";
import {
  V5_ROUTE_QUALIFICATION_SCHEMA_VERSION,
  V5_SELF_CERTIFICATION_FIELDS,
  assertQualificationRecord,
} from "./model-routing.v5.js";

export const V5_QUALIFICATION_KERNEL_SCHEMA_VERSION =
  "doctorcre-v5-model-qualification-kernel.v1";
export const V5_EVALUATION_PLAN_SCHEMA_VERSION = "route-evaluation-plan.v1";
export const V5_EVALUATION_OBSERVATION_SCHEMA_VERSION =
  "task-class-evaluation-observation.v1";
export const V5_QUALIFICATION_DERIVATION_SCHEMA_VERSION =
  "route-qualification-derivation.v1";

/**
 * The three outcomes an observation may carry.
 *
 * `error` is deliberately distinct from `fail`: a case that never produced a
 * response is evidence about reliability but carries no latency, and folding it
 * into `fail` would silently drop it out of the pass-rate denominator or into
 * the latency sample. It is counted as not-passing everywhere and excluded from
 * latency everywhere, and both halves of that are tested.
 */
export const V5_EVALUATION_OUTCOMES = Object.freeze(["pass", "fail", "error"]);

/**
 * Pass rates are integer basis points, matching the basis
 * `benchmark-minimum.v5.js` already uses for workload weights. Integers because
 * a floating-point pass rate would make "met the floor" depend on the order the
 * observations were summed in.
 */
export const V5_PASS_RATE_BASIS_POINTS = 10000;

/** The scope of what this kernel can say about the evidence it was handed. */
export const V5_EVIDENCE_AUTHENTICITY_SCOPE = "declared_by_caller_not_verified_here";

const REF = /^[a-z0-9][a-z0-9_.:/-]{0,127}$/;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

export class V5QualificationKernelError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5QualificationKernelError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5QualificationKernelError(code, message, detail);
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

function assertInteger(value, path, { minimum = null } = {}) {
  if (!Number.isInteger(value)) fail("invalid_shape", `${path} must be an integer`, { path, value });
  if (minimum !== null && value < minimum) {
    fail("invalid_shape", `${path} must be >= ${minimum}`, { path, value });
  }
  return value;
}

function assertNonNegativeNumber(value, path) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    fail("invalid_shape", `${path} must be a finite number >= 0`, { path, value });
  }
  return value;
}

/**
 * An instant is a readable ISO-8601 point in time, returned as milliseconds.
 * The regular expression comes first so that `Date.parse`'s tolerance for
 * half-formed strings is never the thing that decides a measurement window.
 */
function assertInstant(value, path) {
  if (typeof value !== "string" || !ISO_INSTANT.test(value)) {
    fail("invalid_instant", `${path} must be an ISO-8601 instant`, { path, value });
  }
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) fail("invalid_instant", `${path} is not a readable instant`, { path, value });
  return ms;
}

function assertSelfCertificationFree(value, path) {
  const claimed = Object.keys(value).filter(key => V5_SELF_CERTIFICATION_FIELDS.includes(key)).sort();
  if (claimed.length > 0) {
    fail("self_certified_observation_refused",
      "an observation reports what happened; it never reports that the route is qualified, and the routing kernel's reserved self-certification names are refused here for the same reason it refuses them on a record",
      { path, fields: claimed });
  }
}

// ---------------------------------------------------------------------------
// The evaluation plan.
//
// One plan measures exactly ONE route at exactly ONE risk class. That is not a
// convenience: a plan that could span routes would make "which route did this
// evidence qualify" depend on how the observations were tagged, and the routing
// kernel already refuses two records for one route as an ambiguous projection.
// Keeping the route on the PLAN rather than on the observation makes a
// mixed-route observation set structurally impossible rather than merely
// detected.
// ---------------------------------------------------------------------------

const PLAN_KEYS = Object.freeze([
  "schema_version", "plan_id", "plan_version", "verifier_id", "route", "risk_class",
  "required_tool_ids", "required_data_classes", "minimum_observations_per_tool",
  "minimum_observations_per_data_class", "quality_floor_definitions", "validity_duration_ms",
]);

const ROUTE_KEYS = Object.freeze([
  "task_class", "backend_key", "model_key", "model_version", "effort",
]);

const FLOOR_KEYS = Object.freeze([
  "floor_ref", "case_set_ref", "minimum_observations", "minimum_pass_rate_basis_points",
]);

function assertPlanRoute(value, path) {
  assertObject(value, path);
  assertClosedKeys(value, ROUTE_KEYS, path);
  assertRequiredKeys(value, ROUTE_KEYS, path);
  const route = {};
  for (const key of ROUTE_KEYS) route[key] = assertRef(value[key], `${path}.${key}`);
  return route;
}

function assertFloorDefinition(value, path) {
  assertObject(value, path);
  assertClosedKeys(value, FLOOR_KEYS, path);
  assertRequiredKeys(value, FLOOR_KEYS, path);
  const floor = {
    floor_ref: assertRef(value.floor_ref, `${path}.floor_ref`),
    case_set_ref: assertRef(value.case_set_ref, `${path}.case_set_ref`),
    minimum_observations: assertInteger(value.minimum_observations, `${path}.minimum_observations`, { minimum: 1 }),
    minimum_pass_rate_basis_points: assertInteger(
      value.minimum_pass_rate_basis_points, `${path}.minimum_pass_rate_basis_points`, { minimum: 0 }),
  };
  if (floor.minimum_pass_rate_basis_points > V5_PASS_RATE_BASIS_POINTS) {
    fail("invalid_shape",
      `${path}.minimum_pass_rate_basis_points must be <= ${V5_PASS_RATE_BASIS_POINTS}`,
      { path: `${path}.minimum_pass_rate_basis_points`, value: floor.minimum_pass_rate_basis_points });
  }
  return floor;
}

/**
 * Verify a plan and return it sealed over its own exact bytes.
 *
 * The digest covers the source document, so a wrapper carrying a valid source
 * and a quietly lowered floor beside it is ignored rather than believed — the
 * same rule, and the same reason, as `assertCompiledPolicy` in the routing
 * kernel.
 */
export function compileEvaluationPlan(plan) {
  assertObject(plan, "plan");
  assertClosedKeys(plan, PLAN_KEYS, "plan");
  assertRequiredKeys(plan, PLAN_KEYS, "plan");
  if (plan.schema_version !== V5_EVALUATION_PLAN_SCHEMA_VERSION) {
    fail("wrong_schema_version",
      `plan.schema_version must be "${V5_EVALUATION_PLAN_SCHEMA_VERSION}"`,
      { path: "plan.schema_version", value: plan.schema_version });
  }
  assertRef(plan.plan_id, "plan.plan_id");
  assertInteger(plan.plan_version, "plan.plan_version", { minimum: 1 });
  // Carried through, never minted: the routing gate decides which verifier
  // identities it trusts, and this kernel has no opinion and no wildcard.
  assertRef(plan.verifier_id, "plan.verifier_id");
  const route = assertPlanRoute(plan.route, "plan.route");
  assertRef(plan.risk_class, "plan.risk_class");
  const requiredTools = assertRefSet(plan.required_tool_ids, "plan.required_tool_ids");
  const requiredDataClasses = assertRefSet(plan.required_data_classes, "plan.required_data_classes");
  // S01 owns the data-class vocabulary; this kernel shares it rather than
  // carrying a second one that could drift out of step with the privacy
  // boundary the routing kernel consults.
  for (const dataClass of requiredDataClasses) {
    if (!V5_DATA_CLASSES.includes(dataClass)) {
      fail("unknown_data_class", `"${dataClass}" is not a data class the global boundary registers`,
        { path: "plan.required_data_classes", value: dataClass, registered: [...V5_DATA_CLASSES] });
    }
  }
  assertInteger(plan.minimum_observations_per_tool, "plan.minimum_observations_per_tool", { minimum: 1 });
  assertInteger(plan.minimum_observations_per_data_class,
    "plan.minimum_observations_per_data_class", { minimum: 1 });
  assertInteger(plan.validity_duration_ms, "plan.validity_duration_ms", { minimum: 1 });
  if (!Array.isArray(plan.quality_floor_definitions)) {
    fail("invalid_shape", "plan.quality_floor_definitions must be an array",
      { path: "plan.quality_floor_definitions" });
  }
  const floors = plan.quality_floor_definitions.map((entry, index) =>
    assertFloorDefinition(entry, `plan.quality_floor_definitions[${index}]`));
  const floorRefs = floors.map(entry => entry.floor_ref);
  if (new Set(floorRefs).size !== floorRefs.length) {
    fail("duplicate_entry", "two floor definitions declare the same floor_ref",
      { path: "plan.quality_floor_definitions", floor_refs: [...floorRefs].sort() });
  }
  const source = copy(plan);
  return deepFreeze({
    schema_version: V5_EVALUATION_PLAN_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    plan_id: plan.plan_id,
    plan_version: plan.plan_version,
    plan_digest: digest(source),
    verifier_id: plan.verifier_id,
    route,
    risk_class: plan.risk_class,
    required_tool_ids: requiredTools,
    required_data_classes: requiredDataClasses,
    minimum_observations_per_tool: plan.minimum_observations_per_tool,
    minimum_observations_per_data_class: plan.minimum_observations_per_data_class,
    quality_floor_definitions: floors.sort((a, b) => (a.floor_ref < b.floor_ref ? -1 : 1)),
    validity_duration_ms: plan.validity_duration_ms,
    declared_case_set_refs: [...new Set(floors.map(entry => entry.case_set_ref))].sort(),
    source,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Verify a sealed plan and return it RECOMPILED from its own verified bytes,
 * for the reason the routing kernel recompiles a policy: the seal covers
 * `source`, so everything indexed beside it is only trustworthy when rebuilt
 * from those exact bytes.
 */
function assertSealedPlan(plan) {
  assertObject(plan, "plan");
  if (plan.schema_version !== V5_EVALUATION_PLAN_SCHEMA_VERSION ||
      !isPlainObject(plan.source) || typeof plan.plan_digest !== "string") {
    fail("plan_not_compiled",
      "pass a plan compiled by compileEvaluationPlan, so its digest binds the exact bytes supplied");
  }
  if (digest(plan.source) !== plan.plan_digest) {
    fail("plan_digest_mismatch", "the compiled plan no longer hashes to its own plan_digest",
      { expected: digest(plan.source), actual: plan.plan_digest });
  }
  return compileEvaluationPlan(plan.source);
}

// ---------------------------------------------------------------------------
// Observations.
// ---------------------------------------------------------------------------

const OBSERVATION_KEYS = Object.freeze([
  "observation_id", "case_ref", "case_set_refs", "outcome",
  "exercised_tool_ids", "exercised_data_classes", "latency_ms", "observed_at",
]);

function assertObservation(observation, plan, path) {
  assertObject(observation, path);
  // By name before the closed-key sweep, so the bypass somebody would actually
  // reach for has an error a reviewer can grep rather than a generic one.
  assertSelfCertificationFree(observation, path);
  assertClosedKeys(observation, OBSERVATION_KEYS, path);
  assertRequiredKeys(observation, OBSERVATION_KEYS.filter(key => key !== "latency_ms"), path);
  assertRef(observation.observation_id, `${path}.observation_id`);
  assertRef(observation.case_ref, `${path}.case_ref`);
  const caseSets = assertRefSet(observation.case_set_refs, `${path}.case_set_refs`);
  for (const ref of caseSets) {
    if (!plan.declared_case_set_refs.includes(ref)) {
      fail("undeclared_case_set",
        `${path}.case_set_refs names a case set no floor in this plan declares`,
        { path, case_set_ref: ref, declared: [...plan.declared_case_set_refs] });
    }
  }
  if (!V5_EVALUATION_OUTCOMES.includes(observation.outcome)) {
    fail("unknown_outcome", `${path}.outcome is not a registered evaluation outcome`,
      { path: `${path}.outcome`, value: observation.outcome, registered: [...V5_EVALUATION_OUTCOMES] });
  }
  const tools = assertRefSet(observation.exercised_tool_ids, `${path}.exercised_tool_ids`);
  const dataClasses = assertRefSet(observation.exercised_data_classes, `${path}.exercised_data_classes`);
  for (const dataClass of dataClasses) {
    if (!V5_DATA_CLASSES.includes(dataClass)) {
      fail("unknown_data_class", `"${dataClass}" is not a data class the global boundary registers`,
        { path: `${path}.exercised_data_classes`, value: dataClass, registered: [...V5_DATA_CLASSES] });
    }
  }
  // The latency contract, both directions. An `error` case produced no response,
  // so a latency on it would be a number measuring nothing; a `pass` or `fail`
  // that omits one would silently shrink the latency sample.
  if (observation.outcome === "error") {
    if (observation.latency_ms !== null && observation.latency_ms !== undefined) {
      fail("latency_contract_violation",
        `${path}.latency_ms must be null on an errored observation: no response arrived to time`,
        { path: `${path}.latency_ms`, outcome: observation.outcome });
    }
  } else if (observation.latency_ms === null || observation.latency_ms === undefined) {
    fail("latency_contract_violation",
      `${path}.latency_ms is required on a "${observation.outcome}" observation`,
      { path: `${path}.latency_ms`, outcome: observation.outcome });
  } else {
    assertNonNegativeNumber(observation.latency_ms, `${path}.latency_ms`);
  }
  const observedAtMs = assertInstant(observation.observed_at, `${path}.observed_at`);
  return Object.freeze({
    observation_id: observation.observation_id,
    case_ref: observation.case_ref,
    case_set_refs: Object.freeze(caseSets),
    outcome: observation.outcome,
    exercised_tool_ids: Object.freeze(tools),
    exercised_data_classes: Object.freeze(dataClasses),
    latency_ms: observation.outcome === "error" ? null : observation.latency_ms,
    observed_at: observation.observed_at,
    observed_at_ms: observedAtMs,
  });
}

// ---------------------------------------------------------------------------
// The derivation.
// ---------------------------------------------------------------------------

/**
 * The ONE coverage rule, used for both tools and data classes.
 *
 * A key is evidenced when it was exercised by at least the plan's declared
 * minimum number of PASSING observations. Written once and called twice on
 * purpose: "how much evidence makes a capability qualified" is a single
 * question, and two implementations of it would eventually answer it two ways.
 *
 * WHY PASSING OBSERVATIONS AND NOT "NO FAILURES". The first shape of this rule
 * vetoed a key the moment any failing case had exercised it. That is a SECOND,
 * far stricter quality standard sitting next to the floors — a plan could
 * declare a 75% floor and still lose a tool to one failure in a hundred — and
 * two standards over one body of evidence is exactly the kind of thing that
 * makes a refusal impossible to explain. Quality is the floors' question and is
 * answered by pass RATE; coverage is this rule's question and is answered by
 * how many successful demonstrations exist. A failure neither vetoes a key nor
 * counts toward it; it is reported below because a reviewer wants to see it,
 * and it decides nothing.
 */
function deriveEvidencedCoverage({ keys, minimum, observations, exercisedField }) {
  return keys.map(key => {
    const exercising = observations.filter(entry => entry[exercisedField].includes(key));
    const passing = exercising.filter(entry => entry.outcome === "pass");
    return {
      key,
      observations: exercising.length,
      passing_observations: passing.length,
      // Reported, not load-bearing. See the note above.
      failing_observations: exercising.length - passing.length,
      minimum_observations: minimum,
      evidenced: passing.length >= minimum,
    };
  }).sort((a, b) => (a.key < b.key ? -1 : 1));
}

/**
 * Evaluate one declared floor against the observations tagged with its case set.
 *
 * The pass rate is floored integer division of basis points. Flooring is the
 * conservative direction: a rate that lands between two basis points counts as
 * the lower one, so rounding can never be the thing that lifts a measurement
 * over its floor.
 */
function evaluateFloor(floor, observations) {
  const inSet = observations.filter(entry => entry.case_set_refs.includes(floor.case_set_ref));
  const passed = inSet.filter(entry => entry.outcome === "pass").length;
  const rate = inSet.length === 0
    ? 0
    : Math.floor((passed * V5_PASS_RATE_BASIS_POINTS) / inSet.length);
  const enough = inSet.length >= floor.minimum_observations;
  return {
    floor_ref: floor.floor_ref,
    case_set_ref: floor.case_set_ref,
    observations: inSet.length,
    passed,
    pass_rate_basis_points: rate,
    minimum_observations: floor.minimum_observations,
    minimum_pass_rate_basis_points: floor.minimum_pass_rate_basis_points,
    met: enough && rate >= floor.minimum_pass_rate_basis_points,
    shortfall: enough
      ? (rate >= floor.minimum_pass_rate_basis_points ? null : "pass_rate")
      : "observations",
  };
}

/**
 * Derive a `route-qualification.v1` record, or say why the evidence does not
 * support one.
 *
 * Contract violations THROW; an evidence shortfall is RETURNED with the counts
 * behind it. The shortfall checks run in a FIXED order and the first one wins,
 * so the reported reason is a property of the evidence rather than of iteration
 * order — the same discipline `admitRoute` follows in the routing kernel.
 */
export function deriveRouteQualification({ plan, observations } = {}) {
  const sealed = assertSealedPlan(plan);
  if (!Array.isArray(observations)) {
    fail("invalid_shape", "observations must be an array", { path: "observations" });
  }
  if (observations.length === 0) {
    fail("no_observations",
      "a qualification is a measurement; there is nothing to derive one from",
      { path: "observations" });
  }
  const checked = observations.map((entry, index) =>
    assertObservation(entry, sealed, `observations[${index}]`));
  const ids = checked.map(entry => entry.observation_id);
  if (new Set(ids).size !== ids.length) {
    fail("duplicate_entry", "two observations share one observation_id",
      { path: "observations", observation_ids: [...ids].sort() });
  }

  const ordered = [...checked].sort((a, b) =>
    (a.observation_id < b.observation_id ? -1 : 1));
  const passCount = ordered.filter(entry => entry.outcome === "pass").length;
  const failCount = ordered.filter(entry => entry.outcome === "fail").length;
  const errorCount = ordered.filter(entry => entry.outcome === "error").length;

  // Every tool and data class the evidence touches OR the plan requires. The
  // union rather than the plan's list alone: an incidental capability the
  // observations genuinely cover is real evidence, and dropping it would throw
  // away a measurement that was actually taken.
  const toolKeys = [...new Set([
    ...sealed.required_tool_ids,
    ...ordered.flatMap(entry => [...entry.exercised_tool_ids]),
  ])].sort();
  const dataClassKeys = [...new Set([
    ...sealed.required_data_classes,
    ...ordered.flatMap(entry => [...entry.exercised_data_classes]),
  ])].sort();
  const tools = deriveEvidencedCoverage({
    keys: toolKeys, minimum: sealed.minimum_observations_per_tool,
    observations: ordered, exercisedField: "exercised_tool_ids",
  });
  const dataClasses = deriveEvidencedCoverage({
    keys: dataClassKeys, minimum: sealed.minimum_observations_per_data_class,
    observations: ordered, exercisedField: "exercised_data_classes",
  });
  const floors = sealed.quality_floor_definitions.map(floor => evaluateFloor(floor, ordered));

  const latencySamples = ordered
    .filter(entry => entry.latency_ms !== null)
    .map(entry => entry.latency_ms);

  // The measurement preimage: the exact plan seal and the exact observation
  // bytes, so `measurement_digest` is something a reviewer can rehash by hand
  // rather than a value this kernel asserts.
  const measurementPreimage = {
    schema_version: V5_QUALIFICATION_KERNEL_SCHEMA_VERSION,
    part: "measurement",
    plan_digest: sealed.plan_digest,
    route: { ...sealed.route },
    risk_class: sealed.risk_class,
    observations: ordered.map(entry => ({
      observation_id: entry.observation_id,
      case_ref: entry.case_ref,
      case_set_refs: [...entry.case_set_refs],
      outcome: entry.outcome,
      exercised_tool_ids: [...entry.exercised_tool_ids],
      exercised_data_classes: [...entry.exercised_data_classes],
      latency_ms: entry.latency_ms,
      observed_at: entry.observed_at,
    })),
  };
  const measurementDigest = digest(measurementPreimage);

  const derivation = {
    schema_version: V5_QUALIFICATION_DERIVATION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    plan_id: sealed.plan_id,
    plan_version: sealed.plan_version,
    plan_digest: sealed.plan_digest,
    route: { ...sealed.route },
    risk_class: sealed.risk_class,
    verifier_id: sealed.verifier_id,
    observation_count: ordered.length,
    pass_count: passCount,
    fail_count: failCount,
    error_count: errorCount,
    latency_sample_size: latencySamples.length,
    quality_floors: floors,
    tool_coverage: tools,
    data_class_coverage: dataClasses,
    measurement_digest: measurementDigest,
    // Said on every result, met or not: this kernel counts what it was handed
    // and cannot tell an attested observation from a typed-in one.
    evidence_authenticity_verified: false,
    evidence_authenticity_scope: V5_EVIDENCE_AUTHENTICITY_SCOPE,
    canonical_qualification_schema: V5_ROUTE_QUALIFICATION_SCHEMA_VERSION,
    effects: V5_NO_EFFECTS,
  };
  const shortfall = fields => deepFreeze({
    ...derivation, derived: false, qualification: null, ...fields,
  });

  // FIXED ORDER, first failure wins, so the reported reason is a property of the
  // evidence rather than of iteration order.
  //
  // THE ORDER IS QUALITY, THEN SCOPE, THEN TIMING, and it is that way round on
  // purpose. A floor is the question "did this measurement succeed at all"; a
  // coverage rule is the narrower question "what does the successful
  // measurement cover". Leading with coverage — the first shape of this
  // function did — answers a measurement that failed outright with a note about
  // a tool, which is true and useless.
  //
  // EVERY DECLARED FLOOR, OR NONE OF THEM. A plan declares the floors its
  // measurement exists to establish, so a floor it declared and missed is a
  // failed measurement rather than a smaller successful one. The alternative —
  // emitting a record carrying only the floors that happened to be met — is
  // safe downstream (the routing kernel refuses a route whose record lacks a
  // required floor) but reads as a deliberate scope rather than as a gap, and a
  // plan that wants a floor to be optional can simply not declare it.
  const unmetFloors = floors.filter(entry => !entry.met);
  if (unmetFloors.length > 0) {
    return shortfall({
      reason_id: "quality_floor_not_met",
      unmet_quality_floor_refs: unmetFloors.map(entry => entry.floor_ref).sort(),
    });
  }
  const missingTools = tools.filter(entry =>
    sealed.required_tool_ids.includes(entry.key) && !entry.evidenced);
  if (missingTools.length > 0) {
    return shortfall({
      reason_id: "required_tool_not_evidenced",
      missing_tool_ids: missingTools.map(entry => entry.key),
    });
  }
  const missingDataClasses = dataClasses.filter(entry =>
    sealed.required_data_classes.includes(entry.key) && !entry.evidenced);
  if (missingDataClasses.length > 0) {
    return shortfall({
      reason_id: "required_data_class_not_evidenced",
      missing_data_classes: missingDataClasses.map(entry => entry.key),
    });
  }
  if (latencySamples.length === 0) {
    return shortfall({
      reason_id: "no_latency_sample",
      error_count: errorCount,
    });
  }

  // The window. `measured_at` is the LAST observation's own instant — a
  // measurement is not complete until its final case is in — carried through as
  // the string the observation supplied rather than reformatted, and
  // `expires_at` is that instant plus the plan's declared duration. Neither
  // number is this kernel's.
  const lastObserved = ordered.reduce((latest, entry) =>
    (entry.observed_at_ms > latest.observed_at_ms ? entry : latest), ordered[0]);
  const measuredAt = lastObserved.observed_at;
  const expiresAt = new Date(lastObserved.observed_at_ms + sealed.validity_duration_ms).toISOString();

  const qualification = {
    qualification_id: `qualification:${measurementDigest.slice("sha256:".length, "sha256:".length + 32)}`,
    task_class: sealed.route.task_class,
    backend_key: sealed.route.backend_key,
    model_key: sealed.route.model_key,
    model_version: sealed.route.model_version,
    effort: sealed.route.effort,
    qualified_tool_ids: tools.filter(entry => entry.evidenced).map(entry => entry.key),
    permitted_data_classes: dataClasses.filter(entry => entry.evidenced).map(entry => entry.key),
    qualified_max_risk_class: sealed.risk_class,
    met_quality_floor_refs: floors.map(entry => entry.floor_ref).sort(),
    measured_latency_ms_p95: nearestRankP95(latencySamples),
    measured_at: measuredAt,
    expires_at: expiresAt,
    measurement_digest: measurementDigest,
    verifier_id: sealed.verifier_id,
  };
  // The seam, closed here rather than discovered downstream: the routing
  // kernel's own validator runs over this kernel's output before it is
  // returned, so this file cannot emit a record `createModelRoutingGate` would
  // reject. Its return value is deliberately dropped — it carries derived
  // fields that would break the closed-key check if they travelled on.
  assertQualificationRecord(qualification, "derived_qualification");

  return deepFreeze({
    ...derivation,
    derived: true,
    reason_id: "qualification_derived_from_evidence",
    qualification,
  });
}

// ---------------------------------------------------------------------------
// The honest, zero-effect projection.
// ---------------------------------------------------------------------------

/** What this kernel derives, what it refuses to invent, and what is still missing. */
export function v5ModelQualificationKernelProjection() {
  return deepFreeze({
    schema_version: V5_QUALIFICATION_KERNEL_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    plan_schema_version: V5_EVALUATION_PLAN_SCHEMA_VERSION,
    observation_schema_version: V5_EVALUATION_OBSERVATION_SCHEMA_VERSION,
    derivation_schema_version: V5_QUALIFICATION_DERIVATION_SCHEMA_VERSION,
    produces_schema_version: V5_ROUTE_QUALIFICATION_SCHEMA_VERSION,
    outcomes: [...V5_EVALUATION_OUTCOMES],
    pass_rate_basis_points: V5_PASS_RATE_BASIS_POINTS,
    // Properties, not aspirations: each is enforced above and tested.
    thresholds_invented_here: false,
    observation_outcome_judged_here: false,
    verifier_id_minted_here: false,
    coverage_can_be_asserted_without_evidence: false,
    p95_rule_owner: "benchmark-minimum.v5.js#nearestRankP95",
    qualification_schema_owner: "model-routing.v5.js#assertQualificationRecord",
    data_class_vocabulary_owner: "global-boundaries.v5.js#V5_DATA_CLASSES",
    // The whole weight of the boundary, said as a field because a downstream
    // reader needs the difference between these two lines.
    evidence_authenticity_verified: false,
    evidence_authenticity_scope: V5_EVIDENCE_AUTHENTICITY_SCOPE,
    // Named gaps. None is simulated, stubbed into a fake success, or implied by
    // any result this module returns.
    unimplemented_dependencies: [
      "a producer of observations: running a model against an evaluation case needs dispatch, which is V5-F06/V5-F07 work and does not exist in this repository, so every observation reaching this kernel today is a fixture and the route-qualification.v1 records it derives from them are fixtures too",
      "authenticity for an observation: the record layer's attest-attempt-evaluation is the human-and-authority-only verb that makes an evaluation result authoritative, and this pure module cannot call it, cannot read its rows and cannot tell an attested observation from a typed-in one; every result says so on evidence_authenticity_verified",
      "a durable store for plans, observations, measurements and derived qualifications: this kernel adds no table, no migration and no SQL integration, so nothing it derives survives the process that derived it",
      "the trusted projection verifier itself: createModelRoutingGate takes an authenticateQualifications function and this kernel is not one — it derives a record, it does not fetch, authenticate or bind one to a request, and wiring a verifier around it needs the durable store above",
      "a case-set catalogue binding: a plan's case_set_ref values are reference tokens this kernel checks for internal consistency only; that they name the record layer's accepted evaluation-case golden sets is the plan author's obligation and nothing here verifies it",
    ],
    effects: V5_NO_EFFECTS,
  });
}
