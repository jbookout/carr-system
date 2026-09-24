// V5-F04 — the evaluation kernel: deriving a measured route qualification from
// task-class evaluation observations, proved case by case.
//
// The suite is organised the way the kernel is: the positives that must DERIVE,
// the evidence shortfalls that must be RETURNED with their counts, and the
// contract violations that must THROW. The bypass cases get their own group —
// they are the shapes someone would reach for to get an unmeasured route
// admitted, and each has to have a named refusal rather than a silent pass.
//
// EVERY MODEL, VERSION, EFFORT, BACKEND, CASE, LATENCY AND OUTCOME BELOW IS A
// SYNTHETIC FIXTURE. Nothing here reports that a real model was run against a
// real case, no observation here was attested by anybody, no test makes a
// network call, and none could.

import test from "node:test";
import assert from "node:assert/strict";

import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { nearestRankP95 } from "../src/benchmark-minimum.v5.js";
import {
  V5_SELF_CERTIFICATION_FIELDS,
  assertQualificationRecord,
  defineRole,
  compileRoutingPolicy,
  createModelRoutingGate,
} from "../src/model-routing.v5.js";
import {
  V5_QUALIFICATION_KERNEL_SCHEMA_VERSION,
  V5_EVALUATION_PLAN_SCHEMA_VERSION,
  V5_EVALUATION_OUTCOMES,
  V5_PASS_RATE_BASIS_POINTS,
  V5_EVIDENCE_AUTHENTICITY_SCOPE,
  V5QualificationKernelError,
  compileEvaluationPlan,
  deriveRouteQualification,
  v5ModelQualificationKernelProjection,
} from "../src/model-qualification-kernel.v5.js";

function refuses(fn, code) {
  try {
    fn();
  } catch (error) {
    assert.ok(error instanceof V5QualificationKernelError,
      `expected a V5QualificationKernelError, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code, `expected code "${code}", got "${error.code}" (${error.message})`);
    return error;
  }
  return assert.fail(`expected a refusal with code "${code}"`);
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

// --- synthetic fixtures ----------------------------------------------------

const CASE_SET = "case-set:f04-reviewer-evidence";
const FLOOR = "floor:reviewer-evidence-cited";
const TOOL = "tool:comp-lookup";
const VERIFIER = "verifier:fixture-evaluation-kernel";

/** A fresh plain-object plan on every call, so a mutating test cannot leak. */
function basePlan() {
  return {
    schema_version: V5_EVALUATION_PLAN_SCHEMA_VERSION,
    plan_id: "plan:f04-fixture-deal-review",
    plan_version: 1,
    verifier_id: VERIFIER,
    route: {
      task_class: "task:deal-review",
      backend_key: "backend:mac-studio-local",
      model_key: "model:fixture-strong-a",
      model_version: "2026-01",
      effort: "high",
    },
    risk_class: "risk:standard",
    required_tool_ids: [TOOL],
    required_data_classes: ["lease_economics", "market_comp"],
    minimum_observations_per_tool: 3,
    minimum_observations_per_data_class: 3,
    quality_floor_definitions: [
      {
        floor_ref: FLOOR,
        case_set_ref: CASE_SET,
        minimum_observations: 4,
        minimum_pass_rate_basis_points: 7500,
      },
    ],
    validity_duration_ms: 2592000000,
  };
}

function planWith(mutate) {
  const document = basePlan();
  if (mutate) mutate(document);
  return compileEvaluationPlan(document);
}

/**
 * One observation. Every field is explicit so a test that changes one is
 * changing exactly one thing.
 */
function observation(n, overrides = {}) {
  const base = {
    observation_id: `observation:fixture-${n}`,
    case_ref: `case:fixture-${n}`,
    case_set_refs: [CASE_SET],
    outcome: "pass",
    exercised_tool_ids: [TOOL],
    exercised_data_classes: ["lease_economics", "market_comp"],
    latency_ms: 1000 + n,
    observed_at: `2026-09-0${n}T00:00:00.000Z`,
    ...overrides,
  };
  if (base.outcome === "error") base.latency_ms = null;
  return base;
}

/** Four passing observations: the minimum this plan's floor accepts. */
function passingObservations() {
  return [1, 2, 3, 4].map(n => observation(n));
}

function derive({ plan = planWith(), observations = passingObservations() } = {}) {
  return deriveRouteQualification({ plan, observations });
}

// ---------------------------------------------------------------------------
// The plan is input, not invention.
// ---------------------------------------------------------------------------

test("a plan seals its own bytes and recompiles from them", () => {
  const plan = planWith();
  assert.equal(plan.plan_digest, digest(plan.source));
  assert.equal(plan.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(plan.declared_case_set_refs.length, 1);
  assert.equal(plan.declared_case_set_refs[0], CASE_SET);
  // The seal covers `source`, so a wrapper carrying valid bytes and a quietly
  // lowered floor beside them is ignored rather than believed.
  const tampered = {
    ...clone(plan),
    quality_floor_definitions: [{
      floor_ref: FLOOR, case_set_ref: CASE_SET,
      minimum_observations: 1, minimum_pass_rate_basis_points: 0,
    }],
  };
  const result = deriveRouteQualification({
    plan: tampered,
    observations: [observation(1, { outcome: "fail" }), observation(2, { outcome: "fail" })],
  });
  // Recompiled from the sealed source, the REAL floor still governs.
  assert.equal(result.derived, false);
  assert.equal(result.reason_id, "quality_floor_not_met");
  assert.equal(result.quality_floors[0].minimum_pass_rate_basis_points, 7500);
});

test("an edited plan no longer hashes to its own seal", () => {
  const plan = clone(planWith());
  plan.source.quality_floor_definitions[0].minimum_pass_rate_basis_points = 0;
  refuses(() => deriveRouteQualification({ plan, observations: passingObservations() }),
    "plan_digest_mismatch");
  refuses(() => deriveRouteQualification({
    plan: { schema_version: V5_EVALUATION_PLAN_SCHEMA_VERSION },
    observations: passingObservations(),
  }), "plan_not_compiled");
});

test("a plan that omits a threshold is refused rather than having one defaulted", () => {
  for (const key of ["minimum_observations_per_tool", "minimum_observations_per_data_class",
    "validity_duration_ms", "quality_floor_definitions", "verifier_id", "risk_class"]) {
    const document = basePlan();
    delete document[key];
    const error = refuses(() => compileEvaluationPlan(document), "missing_field");
    assert.deepEqual(error.detail.missing, [key]);
  }
  // And a floor definition that omits its own numbers.
  for (const key of ["minimum_observations", "minimum_pass_rate_basis_points", "case_set_ref"]) {
    const document = basePlan();
    delete document.quality_floor_definitions[0][key];
    refuses(() => compileEvaluationPlan(document), "missing_field");
  }
});

test("a plan carrying an unknown field or an unregistered data class refuses", () => {
  refuses(() => compileEvaluationPlan({ ...basePlan(), default_pass_rate: 5000 }), "unknown_field");
  refuses(() => compileEvaluationPlan({
    ...basePlan(), required_data_classes: ["not_a_registered_class"],
  }), "unknown_data_class");
  refuses(() => compileEvaluationPlan({ ...basePlan(), schema_version: "route-evaluation-plan.v2" }),
    "wrong_schema_version");
  refuses(() => compileEvaluationPlan({ ...basePlan(), validity_duration_ms: 0 }), "invalid_shape");
  const overRate = basePlan();
  overRate.quality_floor_definitions[0].minimum_pass_rate_basis_points = V5_PASS_RATE_BASIS_POINTS + 1;
  refuses(() => compileEvaluationPlan(overRate), "invalid_shape");
  const twice = basePlan();
  twice.quality_floor_definitions.push({ ...twice.quality_floor_definitions[0] });
  refuses(() => compileEvaluationPlan(twice), "duplicate_entry");
});

// ---------------------------------------------------------------------------
// The derivation.
// ---------------------------------------------------------------------------

test("evidence that meets the plan derives a record the routing kernel accepts", () => {
  const result = derive();
  assert.equal(result.derived, true);
  assert.equal(result.reason_id, "qualification_derived_from_evidence");
  const record = result.qualification;
  assert.equal(record.task_class, "task:deal-review");
  assert.equal(record.backend_key, "backend:mac-studio-local");
  assert.equal(record.model_key, "model:fixture-strong-a");
  assert.equal(record.model_version, "2026-01");
  assert.equal(record.effort, "high");
  assert.deepEqual([...record.qualified_tool_ids], [TOOL]);
  assert.deepEqual([...record.permitted_data_classes], ["lease_economics", "market_comp"]);
  assert.equal(record.qualified_max_risk_class, "risk:standard");
  assert.deepEqual([...record.met_quality_floor_refs], [FLOOR]);
  assert.equal(record.verifier_id, VERIFIER);
  // The record the OTHER module validates, validated by that module's own
  // validator rather than by a copy of its rules living here.
  const validated = assertQualificationRecord(clone(record));
  assert.equal(validated.occupant_key, "model:fixture-strong-a@2026-01/high");
});

test("the p95 is the tree's nearest-rank rule over the responses that arrived", () => {
  // Latencies 1001, 1002, 1003, 1004 plus one errored case that produced none.
  const result = derive({
    observations: [...passingObservations(), observation(5, { outcome: "error" })],
  });
  assert.equal(result.latency_sample_size, 4);
  assert.equal(result.observation_count, 5);
  assert.equal(result.error_count, 1);
  // Computed by the module that OWNS the p95 rule, over the sample this test
  // states independently — not by re-running the function under test.
  assert.equal(result.qualification.measured_latency_ms_p95, nearestRankP95([1001, 1002, 1003, 1004]));
  assert.equal(result.qualification.measured_latency_ms_p95, 1004);
});

test("the measurement window is the last observation plus the plan's declared duration", () => {
  const result = derive();
  // The fourth fixture observation is the latest, and its own instant is
  // carried through rather than reformatted.
  assert.equal(result.qualification.measured_at, "2026-09-04T00:00:00.000Z");
  // 2026-09-04 + 30 days. Stated as a literal, not recomputed from the module.
  assert.equal(result.qualification.expires_at, "2026-10-04T00:00:00.000Z");

  // Array order does not decide which observation is last.
  const shuffled = derive({ observations: [...passingObservations()].reverse() });
  assert.equal(shuffled.qualification.measured_at, "2026-09-04T00:00:00.000Z");
});

test("two identical derivations produce byte-identical results", () => {
  const first = derive();
  const second = derive({ observations: [...passingObservations()].reverse() });
  assert.equal(second.measurement_digest, first.measurement_digest);
  assert.equal(second.qualification.qualification_id, first.qualification.qualification_id);
  assert.deepEqual(clone(second.qualification), clone(first.qualification));
});

test("the measurement digest covers the evidence, so changing one observation moves it", () => {
  const baseline = derive();
  const changed = derive({
    observations: [observation(1, { latency_ms: 9999 }), observation(2), observation(3), observation(4)],
  });
  assert.notEqual(changed.measurement_digest, baseline.measurement_digest);
  assert.notEqual(changed.qualification.qualification_id, baseline.qualification.qualification_id);
  // And the id is derived FROM the digest, not minted beside it.
  assert.equal(baseline.qualification.qualification_id,
    `qualification:${baseline.measurement_digest.slice("sha256:".length, "sha256:".length + 32)}`);
});

test("every result is deeply frozen and reports no effects", () => {
  const result = derive();
  assert.throws(() => { result.derived = false; }, TypeError);
  assert.throws(() => { result.qualification.verifier_id = "verifier:other"; }, TypeError);
  assert.throws(() => { result.quality_floors[0].met = false; }, TypeError);
  assert.equal(result.effects.creates_effect, false);
  assert.equal(result.effects.network_calls, 0);
});

// ---------------------------------------------------------------------------
// Evidence shortfalls: RETURNED with their counts, never thrown.
// ---------------------------------------------------------------------------

test("a floor the evidence does not reach is a returned shortfall, not a thrown error", () => {
  // Three of four passed: 7500 basis points exactly, which MEETS the floor.
  const atFloor = derive({
    observations: [observation(1), observation(2), observation(3), observation(4, { outcome: "fail" })],
  });
  assert.equal(atFloor.derived, true);
  assert.equal(atFloor.quality_floors[0].pass_rate_basis_points, 7500);

  // Two of four passed: 5000, below it.
  const belowFloor = derive({
    observations: [observation(1), observation(2),
      observation(3, { outcome: "fail" }), observation(4, { outcome: "fail" })],
  });
  assert.equal(belowFloor.derived, false);
  assert.equal(belowFloor.qualification, null);
  assert.equal(belowFloor.reason_id, "quality_floor_not_met");
  assert.deepEqual([...belowFloor.unmet_quality_floor_refs], [FLOOR]);
  assert.equal(belowFloor.quality_floors[0].pass_rate_basis_points, 5000);
  assert.equal(belowFloor.quality_floors[0].shortfall, "pass_rate");
  assert.equal(belowFloor.quality_floors[0].met, false);
});

test("an errored case counts against the floor and never toward it", () => {
  // Two passes, two errors: 5000, below the 7500 floor. An `error` folded into
  // the passing side, or dropped from the denominator, would flip this.
  const result = derive({
    observations: [observation(1), observation(2),
      observation(3, { outcome: "error" }), observation(4, { outcome: "error" })],
  });
  assert.equal(result.derived, false);
  assert.equal(result.reason_id, "quality_floor_not_met");
  assert.equal(result.quality_floors[0].observations, 4);
  assert.equal(result.quality_floors[0].passed, 2);
  assert.equal(result.quality_floors[0].pass_rate_basis_points, 5000);
});

test("too few observations is a different shortfall from too low a pass rate", () => {
  // Three passing observations under a floor that requires four. The pass rate
  // is a perfect 10000 and it still does not qualify.
  const result = derive({ observations: [observation(1), observation(2), observation(3)] });
  assert.equal(result.derived, false);
  assert.equal(result.reason_id, "quality_floor_not_met");
  assert.equal(result.quality_floors[0].pass_rate_basis_points, V5_PASS_RATE_BASIS_POINTS);
  assert.equal(result.quality_floors[0].observations, 3);
  assert.equal(result.quality_floors[0].shortfall, "observations");
});

test("the pass rate is floored, so rounding never lifts a measurement over its floor", () => {
  // Two of three passed: 6666.66... basis points. ONE body of evidence, two
  // plans differing by a single basis point. Rounding up would make both
  // derive; flooring makes exactly one.
  const observations = [observation(1), observation(2), observation(3, { outcome: "fail" })];
  const under = (basisPoints) => deriveRouteQualification({
    plan: planWith(p => {
      p.quality_floor_definitions[0].minimum_observations = 3;
      p.quality_floor_definitions[0].minimum_pass_rate_basis_points = basisPoints;
      p.minimum_observations_per_tool = 2;
      p.minimum_observations_per_data_class = 2;
    }),
    observations,
  });
  const strict = under(6667);
  assert.equal(strict.quality_floors[0].pass_rate_basis_points, 6666);
  assert.equal(strict.derived, false);
  assert.equal(strict.reason_id, "quality_floor_not_met");

  const relaxed = under(6666);
  assert.equal(relaxed.quality_floors[0].pass_rate_basis_points, 6666);
  assert.equal(relaxed.derived, true);
});

test("a required tool the evidence does not carry is a shortfall, not a narrower record", () => {
  // Four passing cases, so the floor is comfortably met, but the tool was
  // exercised by only two of them under a plan that requires three.
  const result = derive({
    observations: [
      observation(1), observation(2),
      observation(3, { exercised_tool_ids: [] }), observation(4, { exercised_tool_ids: [] }),
    ],
  });
  assert.equal(result.quality_floors[0].met, true);
  assert.equal(result.derived, false);
  assert.equal(result.reason_id, "required_tool_not_evidenced");
  assert.deepEqual([...result.missing_tool_ids], [TOOL]);
  const coverage = result.tool_coverage.find(entry => entry.key === TOOL);
  assert.equal(coverage.passing_observations, 2);
  assert.equal(coverage.minimum_observations, 3);
  assert.equal(coverage.evidenced, false);
});

test("a failing case neither vetoes a capability nor counts toward it", () => {
  // Three passing exercises plus one failing one, under a plan requiring three.
  // The failure is reported and decides nothing: quality is the floors' job.
  const result = derive({
    observations: [observation(1), observation(2), observation(3),
      observation(4, { outcome: "fail" })],
  });
  const coverage = result.tool_coverage.find(entry => entry.key === TOOL);
  assert.equal(coverage.observations, 4);
  assert.equal(coverage.passing_observations, 3);
  assert.equal(coverage.failing_observations, 1);
  assert.equal(coverage.evidenced, true);
  assert.equal(result.derived, true);

  // Two passing exercises under the same plan is one short, and that is what
  // decides it — not the presence of the failure.
  const short = derive({
    observations: [observation(1), observation(2),
      observation(3, { outcome: "fail" }), observation(4, { outcome: "fail" }),
      observation(5), observation(6), observation(7), observation(8)],
  });
  const shortCoverage = short.tool_coverage.find(entry => entry.key === TOOL);
  assert.equal(shortCoverage.passing_observations, 6);
  assert.equal(shortCoverage.evidenced, true);
});

test("a required data class the evidence does not carry is its own named shortfall", () => {
  const result = derive({
    observations: [1, 2, 3, 4].map(n =>
      observation(n, { exercised_data_classes: ["lease_economics"] })),
  });
  assert.equal(result.derived, false);
  assert.equal(result.reason_id, "required_data_class_not_evidenced");
  assert.deepEqual([...result.missing_data_classes], ["market_comp"]);
});

test("a capability the plan did not require is still derived when the evidence carries it", () => {
  const result = derive({
    observations: [1, 2, 3, 4].map(n =>
      observation(n, { exercised_tool_ids: [TOOL, "tool:incidental-lookup"] })),
  });
  assert.equal(result.derived, true);
  assert.deepEqual([...result.qualification.qualified_tool_ids],
    ["tool:comp-lookup", "tool:incidental-lookup"]);
});

test("evidence with no response at all is an honest shortfall, not a zero latency", () => {
  const result = deriveRouteQualification({
    plan: planWith(p => {
      p.quality_floor_definitions[0].minimum_pass_rate_basis_points = 0;
      p.required_tool_ids = [];
      p.required_data_classes = [];
    }),
    observations: [1, 2, 3, 4].map(n => observation(n, { outcome: "error" })),
  });
  assert.equal(result.quality_floors[0].met, true);
  assert.equal(result.derived, false);
  assert.equal(result.reason_id, "no_latency_sample");
  assert.equal(result.latency_sample_size, 0);
  assert.equal(result.qualification, null);
});

test("the shortfall order is quality, then scope, then timing", () => {
  // Evidence that fails the floor, the tool rule and the data-class rule at
  // once. The FLOOR answers, because a measurement that did not succeed is not
  // usefully described by which tool it did not cover.
  const everything = derive({
    observations: [1, 2, 3, 4].map(n => observation(n, {
      outcome: "fail", exercised_tool_ids: [], exercised_data_classes: [],
    })),
  });
  assert.equal(everything.reason_id, "quality_floor_not_met");

  // Same shape with the floor met: scope answers next, tools before data
  // classes, and both are genuinely missing.
  const scopeOnly = derive({
    observations: [1, 2, 3, 4].map(n =>
      observation(n, { exercised_tool_ids: [], exercised_data_classes: [] })),
  });
  assert.equal(scopeOnly.quality_floors[0].met, true);
  assert.equal(scopeOnly.reason_id, "required_tool_not_evidenced");

  const dataOnly = derive({
    observations: [1, 2, 3, 4].map(n => observation(n, { exercised_data_classes: [] })),
  });
  assert.equal(dataOnly.reason_id, "required_data_class_not_evidenced");
});

// ---------------------------------------------------------------------------
// Bypasses: the shapes someone would reach for, each refused by name.
// ---------------------------------------------------------------------------

test("an observation that certifies itself is refused by name, on the routing kernel's own list", () => {
  assert.ok(V5_SELF_CERTIFICATION_FIELDS.length > 0);
  for (const field of V5_SELF_CERTIFICATION_FIELDS) {
    const error = refuses(() => derive({
      observations: [observation(1, { [field]: true }), observation(2), observation(3), observation(4)],
    }), "self_certified_observation_refused");
    assert.deepEqual(error.detail.fields, [field]);
  }
});

test("an observation carrying an unknown field is a contract violation, not a shortfall", () => {
  refuses(() => derive({
    observations: [observation(1, { weight: 10 }), observation(2), observation(3), observation(4)],
  }), "unknown_field");
});

test("an unregistered outcome cannot be counted as anything", () => {
  refuses(() => derive({
    observations: [observation(1, { outcome: "probably_fine" }), observation(2),
      observation(3), observation(4)],
  }), "unknown_outcome");
  assert.deepEqual([...V5_EVALUATION_OUTCOMES], ["pass", "fail", "error"]);
});

test("the latency contract holds in both directions", () => {
  // A latency on a case that never produced a response.
  refuses(() => deriveRouteQualification({
    plan: planWith(),
    observations: [{ ...observation(1), outcome: "error", latency_ms: 40 },
      observation(2), observation(3), observation(4)],
  }), "latency_contract_violation");
  // A response with no latency at all, which would silently shrink the sample.
  refuses(() => deriveRouteQualification({
    plan: planWith(),
    observations: [{ ...observation(1), latency_ms: null }, observation(2),
      observation(3), observation(4)],
  }), "latency_contract_violation");
});

test("an observation tagged with a case set no floor declares refuses", () => {
  refuses(() => derive({
    observations: [observation(1, { case_set_refs: ["case-set:undeclared"] }),
      observation(2), observation(3), observation(4)],
  }), "undeclared_case_set");
});

test("an observation naming a data class the global boundary does not register refuses", () => {
  refuses(() => derive({
    observations: [observation(1, { exercised_data_classes: ["invented_class"] }),
      observation(2), observation(3), observation(4)],
  }), "unknown_data_class");
});

test("an empty or duplicated observation set cannot become a measurement", () => {
  refuses(() => derive({ observations: [] }), "no_observations");
  refuses(() => derive({ observations: "four passes" }), "invalid_shape");
  refuses(() => derive({
    observations: [observation(1), observation(1), observation(3), observation(4)],
  }), "duplicate_entry");
});

test("the kernel mints no verifier identity of its own", () => {
  const result = deriveRouteQualification({
    plan: planWith(p => { p.verifier_id = "verifier:some-other-authority"; }),
    observations: passingObservations(),
  });
  assert.equal(result.qualification.verifier_id, "verifier:some-other-authority");
  assert.equal(result.verifier_id, "verifier:some-other-authority");
});

test("no result ever claims the evidence was authenticated", () => {
  const derived = derive();
  const shortfall = derive({ observations: [observation(1), observation(2), observation(3)] });
  for (const result of [derived, shortfall]) {
    assert.equal(result.evidence_authenticity_verified, false);
    assert.equal(result.evidence_authenticity_scope, V5_EVIDENCE_AUTHENTICITY_SCOPE);
    assert.equal(result.evidence_authenticity_scope, "declared_by_caller_not_verified_here");
  }
});

// ---------------------------------------------------------------------------
// The seam: a derived record, through the REAL routing gate.
// ---------------------------------------------------------------------------

/**
 * A minimal routing policy whose local route is exactly the one the fixture plan
 * measures, so the record this kernel derives can be handed to the real gate
 * without a single field being adjusted between the two modules.
 */
function routingPolicy() {
  return compileRoutingPolicy({
    schema_version: "model-routing-policy.v1",
    policy_id: "policy:f04-kernel-seam",
    policy_version: 1,
    ranking: {
      key_order: ["quality_rank", "residual_risk_rank", "privacy_rank",
        "latency_ms_p95", "expected_total_cost_units", "local_preference_rank"],
    },
    risk_ranks: { "risk:low": 0, "risk:standard": 1 },
    privacy_ranks: { "egress:none": 0 },
    quality_scale: {
      scale_id: "scale:f04-kernel-seam",
      scale_version: 1,
      grades: ["grade:fixture-review", "grade:fixture-architecture"],
    },
    occupant_grades: { "model:fixture-strong-a@2026-01/high": "grade:fixture-architecture" },
    strength_refs: { "strength:review-grade": "grade:fixture-review" },
    maximum_qualification_age_ms: 2592000000,
    backends: [{
      backend_key: "backend:mac-studio-local", kind: "local", local_node: "mac-studio",
      egress_class: "egress:none",
      permitted_data_classes: ["lease_economics", "market_comp"],
      user_facing: false, grants_authority: false,
    }],
    task_classes: [{
      task_class: "task:deal-review",
      permitted_backend_keys: ["backend:mac-studio-local"],
      permitted_data_classes: ["lease_economics", "market_comp"],
      required_quality_floor_refs: [FLOOR],
      local_preference_enabled: true,
      local_capability: "local_model_inference",
      privacy_restriction: "none",
    }],
    routes: [{
      route_key: "route:local-strong", task_class: "task:deal-review",
      backend_key: "backend:mac-studio-local", model_key: "model:fixture-strong-a",
      model_version: "2026-01", effort: "high", residual_risk_class: "risk:standard",
      cost_components: {
        base_cost_units: 4, expected_retry_cost_units: 1,
        expected_review_cost_units: 1, expected_fallback_cost_units: 0,
      },
    }],
  });
}

function seamRole() {
  return defineRole({
    role_key: "reviewer",
    title: "Reviewer",
    mission: "Independently review delivered work against its accepted contract and its evidence.",
    skills: ["reads a delivered change against the accepted slice contract"],
    rules: ["never reviews work it authored"],
    authority: {
      authority_class: "developer",
      capability_refs: ["capability:deal.read"],
    },
    evidence_requirements: ["an exact source digest for every finding"],
    quality_floor_refs: [FLOOR],
    minimum_strength_ref: "strength:review-grade",
    task_classes: ["task:deal-review"],
  });
}

const SEAM_JOB = Object.freeze({
  job_id: "job:f04-kernel-seam-1",
  task_class: "task:deal-review",
  data_classes: ["lease_economics", "market_comp"],
  required_tool_ids: [TOOL],
  risk_class: "risk:standard",
  context_digest: digest({ fixture: "f04-kernel-seam-context" }),
  capability_refs: ["capability:deal.read"],
  receipt_binding_ref: "receipt:f04-kernel-seam-1",
});

/**
 * A gate whose synthetic verifier hands over exactly what this kernel derived.
 * It stands in for the trusted projection this repository does not yet have and
 * authenticates nothing real; what it proves is that the two modules agree on
 * the record, not that any record here is true.
 */
function gateOver(records) {
  const probe = createModelRoutingGate({
    authenticateQualifications: () => ({}), trusted_verifier_ids: [VERIFIER],
  });
  return createModelRoutingGate({
    trusted_verifier_ids: [VERIFIER],
    authenticateQualifications: request => ({
      request_binding_digest: probe.requestBindingDigest(request),
      qualifications: clone(records),
    }),
  });
}

test("a derived qualification routes through the real gate, with no field adjusted between them", () => {
  const result = derive();
  assert.equal(result.derived, true);
  const decision = gateOver([result.qualification]).evaluate({
    policy: routingPolicy(), role: seamRole(), job: { ...SEAM_JOB },
    now: "2026-09-05T00:00:00.000Z",
    local_node_states: { "mac-studio": "available" },
  });
  assert.equal(decision.decision, "route");
  assert.equal(decision.selected_route.route_key, "route:local-strong");
  // The gate selected on THIS derivation's evidence, by digest and by id.
  assert.equal(decision.selected_qualification_id, result.qualification.qualification_id);
  assert.equal(decision.selected_measurement_digest, result.measurement_digest);
  assert.equal(decision.selected_verifier_id, VERIFIER);
  assert.equal(decision.qualification_authenticated, true);
});

test("evidence measured for a shorter window expires in the gate rather than in the kernel", () => {
  // The kernel's job is the window, the gate's job is whether `now` is inside
  // it. A one-day plan derives a perfectly valid record that the gate then
  // refuses as expired eight days later — each module answering its own half.
  const result = deriveRouteQualification({
    plan: planWith(p => { p.validity_duration_ms = 86400000; }),
    observations: passingObservations(),
  });
  assert.equal(result.derived, true);
  assert.equal(result.qualification.expires_at, "2026-09-05T00:00:00.000Z");
  const decision = gateOver([result.qualification]).evaluate({
    policy: routingPolicy(), role: seamRole(), job: { ...SEAM_JOB },
    now: "2026-09-12T00:00:00.000Z",
    local_node_states: { "mac-studio": "available" },
  });
  assert.equal(decision.decision, "unavailable");
  const considered = decision.considered.find(entry => entry.route_key === "route:local-strong");
  assert.equal(considered.reason_id, "qualification_expired");
});

test("a floor the derivation refused never reaches the gate as a qualified route", () => {
  const shortfall = derive({
    observations: [observation(1), observation(2),
      observation(3, { outcome: "fail" }), observation(4, { outcome: "fail" })],
  });
  assert.equal(shortfall.derived, false);
  assert.equal(shortfall.qualification, null);
  // There is nothing to hand the gate, and the gate's own answer for a task
  // class with no current record is an honest unavailable rather than a route.
  const decision = gateOver([]).evaluate({
    policy: routingPolicy(), role: seamRole(), job: { ...SEAM_JOB },
    now: "2026-09-05T00:00:00.000Z",
    local_node_states: { "mac-studio": "available" },
  });
  assert.equal(decision.decision, "unavailable");
  assert.equal(decision.reason_id, "no_qualified_route_available");
});

// ---------------------------------------------------------------------------
// The projection.
// ---------------------------------------------------------------------------

test("the projection names what is derived and what is still missing", () => {
  const projection = v5ModelQualificationKernelProjection();
  assert.equal(projection.schema_version, V5_QUALIFICATION_KERNEL_SCHEMA_VERSION);
  assert.equal(projection.produces_schema_version, "route-qualification.v1");
  assert.equal(projection.thresholds_invented_here, false);
  assert.equal(projection.observation_outcome_judged_here, false);
  assert.equal(projection.verifier_id_minted_here, false);
  assert.equal(projection.coverage_can_be_asserted_without_evidence, false);
  assert.equal(projection.evidence_authenticity_verified, false);
  // The reuse is stated where a reader can check it, not only in a comment.
  assert.equal(projection.p95_rule_owner, "benchmark-minimum.v5.js#nearestRankP95");
  assert.equal(projection.qualification_schema_owner,
    "model-routing.v5.js#assertQualificationRecord");
  // The gaps are stated, not simulated.
  assert.ok(projection.unimplemented_dependencies.some(entry => entry.includes("V5-F06/V5-F07")));
  assert.ok(projection.unimplemented_dependencies.some(entry =>
    entry.includes("attest-attempt-evaluation")));
  assert.ok(projection.unimplemented_dependencies.some(entry => entry.includes("migration")));
  assert.ok(projection.unimplemented_dependencies.some(entry =>
    entry.includes("authenticateQualifications")));
  assert.equal(projection.effects.creates_effect, false);
  assert.throws(() => { projection.thresholds_invented_here = true; }, TypeError);
});
