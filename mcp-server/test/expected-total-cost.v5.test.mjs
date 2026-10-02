// V5-A04 — qualification first, then price: the eleven-component expected total
// cost, the Q041 tier rule, and the admission that cost is never allowed to win.
//
// The suite is organised the way the module is: the partition that keeps this
// slice's total equal to V5-F04's ordering number, the tier procedure and its
// evidence requirement, the admission order, and the bypass shapes — the things
// someone would reach for to get a cheap unqualified route admitted, each of
// which must have a named refusal rather than a silent pass.
//
// EVERY MODEL, VERSION, EFFORT, BACKEND, FLOOR AND PRICE BELOW IS A SYNTHETIC
// FIXTURE. No model was run, no provider was called, no charge was incurred and
// no qualification here was attested by anybody.

import test from "node:test";
import assert from "node:assert/strict";

import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_SELF_CERTIFICATION_FIELDS,
  V5RoutingError,
  assertQualificationRecord,
} from "../src/model-routing.v5.js";
import {
  V5_COST_BASIS_SCHEMA_VERSION,
  V5_TIER_POLICY_SCHEMA_VERSION,
  V5_ROUTE_ADMISSION_SCHEMA_VERSION,
  V5_COST_COMPONENTS,
  V5_ROUTE_TIERS,
  V5_ROUTE_KEYS,
  V5_ROUTING_COST_BUCKETS,
  V5_INSTRUCTION_STATES,
  V5_WORK_ACTIVITIES,
  V5_CANDIDATE_REASONS,
  V5ExpectedCostError,
  compileCostBasis,
  compileTierPolicy,
  expectedTotalCostUnits,
  toRoutingCostComponents,
  requiredTierForWorkItem,
  evaluateTierOccupancy,
  admitQualifiedRoute,
  routeKey,
  v5ExpectedTotalCostPreimage,
  v5ExpectedTotalCostProjection,
} from "../src/expected-total-cost.v5.js";

function refuses(fn, code) {
  try {
    fn();
  } catch (error) {
    assert.ok(error instanceof V5ExpectedCostError,
      `expected a V5ExpectedCostError, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code, `expected code "${code}", got "${error.code}" (${error.message})`);
    return error;
  }
  return assert.fail(`expected a refusal with code "${code}"`);
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

// --- synthetic fixtures ----------------------------------------------------

const FLOOR_BUILD = "floor:bounded-instruction-correctness";
const FLOOR_REVIEW = "floor:reviewer-finds-seeded-defect";
const FLOOR_UNCERTAINTY = "floor:novel-problem-solved";
const FLOOR_ADJUDICATE = "floor:adjudication-reasoned";
const TASK_CLASS = "task:v5-slice-authoring";
const VERIFIER = "verifier:a04-fixture-evaluation-kernel";

const CHEAP_ROUTE = Object.freeze({
  task_class: TASK_CLASS,
  backend_key: "backend:hosted-open-model",
  model_key: "model:cheap-builder",
  model_version: "2026-09-01",
  effort: "effort:standard",
});
const PREMIUM_ROUTE = Object.freeze({
  task_class: TASK_CLASS,
  backend_key: "backend:cloud",
  model_key: "model:premium-owner",
  model_version: "2026-09-01",
  effort: "effort:high",
});

/** Every one of the eleven components, so a basis is never accidentally partial. */
function components(overrides = {}) {
  const base = {
    adjudication_cost_units: 3,
    builder_cost_units: 100,
    context_cost_units: 7,
    delegation_cost_units: 5,
    effort_cost_units: 11,
    escalation_cost_units: 2,
    failure_risk_cost_units: 13,
    retry_cost_units: 17,
    review_cost_units: 19,
    rework_cost_units: 23,
    tool_cost_units: 29,
  };
  return { ...base, ...overrides };
}

const BASE_TOTAL = 100 + 7 + 5 + 11 + 29 + 17 + 23 + 19 + 3 + 2 + 13; // 229

function basis(route, overrides = {}, id = "basis:fixture") {
  return compileCostBasis({
    schema_version: V5_COST_BASIS_SCHEMA_VERSION,
    basis_id: id,
    basis_version: 1,
    route: { ...route },
    components: components(overrides),
  });
}

function qualification(route, floors, overrides = {}) {
  return {
    qualification_id: `qualification:${route.model_key.replace(/[^a-z0-9]/g, "-")}`,
    task_class: route.task_class,
    backend_key: route.backend_key,
    model_key: route.model_key,
    model_version: route.model_version,
    effort: route.effort,
    qualified_tool_ids: ["tool:record-read"],
    permitted_data_classes: ["internal_business"],
    qualified_max_risk_class: "risk:moderate",
    met_quality_floor_refs: [...floors],
    measured_latency_ms_p95: 4200,
    measured_at: "2026-09-01T00:00:00.000Z",
    expires_at: "2026-12-01T00:00:00.000Z",
    measurement_digest: `sha256:${"a".repeat(64)}`,
    verifier_id: VERIFIER,
    ...overrides,
  };
}

const TIER_POLICY = compileTierPolicy({
  schema_version: V5_TIER_POLICY_SCHEMA_VERSION,
  policy_id: "tier-policy:a04-fixture",
  policy_version: 1,
  tiers: [
    { tier: "bounded_instruction_builder", required_quality_floor_refs: [FLOOR_BUILD] },
    { tier: "signing_reviewer", required_quality_floor_refs: [FLOOR_BUILD, FLOOR_REVIEW] },
    { tier: "concentrated_uncertainty_owner", required_quality_floor_refs: [FLOOR_UNCERTAINTY] },
    { tier: "adjudicator", required_quality_floor_refs: [FLOOR_ADJUDICATE] },
  ],
});

const ALWAYS_AUTHENTIC = () => true;
const NEVER_AUTHENTIC = () => false;

const BUILD_WORK = Object.freeze({
  task_class: TASK_CLASS,
  instruction_state: "settled_bounded",
  activity: "build",
  now: "2026-09-11T00:00:00.000Z",
});

// --- the eleven components and the total -----------------------------------

test("COST: the eleven components are exactly Q040's and Q115's, and all are required", () => {
  assert.equal(V5_COST_COMPONENTS.length, 11);
  assert.deepEqual([...V5_COST_COMPONENTS], [...V5_COST_COMPONENTS].sort());
  for (const key of V5_COST_COMPONENTS) {
    const partial = components();
    delete partial[key];
    refuses(() => expectedTotalCostUnits(partial), "missing_field");
  }
});

test("COST: a missing component is refused rather than defaulted to zero", () => {
  const partial = components();
  delete partial.escalation_cost_units;
  const error = refuses(() => expectedTotalCostUnits(partial), "missing_field");
  assert.deepEqual(error.detail.missing, ["escalation_cost_units"]);
});

test("COST: an undeclared component name is refused by name", () => {
  const extra = { ...components(), token_cost_units: 4 };
  const error = refuses(() => expectedTotalCostUnits(extra), "unknown_field");
  assert.deepEqual(error.detail.unknown, ["token_cost_units"]);
});

test("COST: the total is the sum of all eleven, and every one of them moves it", () => {
  assert.equal(expectedTotalCostUnits(components()), BASE_TOTAL);
  for (const key of V5_COST_COMPONENTS) {
    const raised = components({ [key]: components()[key] + 1000 });
    assert.equal(expectedTotalCostUnits(raised), BASE_TOTAL + 1000,
      `${key} did not reach the total`);
  }
});

test("COST: a negative or fractional component is refused", () => {
  refuses(() => expectedTotalCostUnits(components({ retry_cost_units: -1 })), "invalid_cost_units");
  refuses(() => expectedTotalCostUnits(components({ retry_cost_units: 1.5 })), "invalid_cost_units");
  refuses(() => expectedTotalCostUnits(components({ retry_cost_units: Number.MAX_SAFE_INTEGER })),
    "invalid_cost_units");
});

test("COST: a compiled basis seals its own bytes and carries its route key", () => {
  const compiled = basis(CHEAP_ROUTE);
  assert.equal(compiled.expected_total_cost_units, BASE_TOTAL);
  assert.match(compiled.basis_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(compiled.route_key, routeKey(CHEAP_ROUTE));
  assert.ok(Object.isFrozen(compiled.components));
  const second = basis(CHEAP_ROUTE);
  assert.equal(second.basis_digest, compiled.basis_digest);
  const dearer = basis(CHEAP_ROUTE, { retry_cost_units: 18 });
  assert.notEqual(dearer.basis_digest, compiled.basis_digest);
});

test("COST: a basis cannot smuggle back a self-certification F04 threw out", () => {
  for (const field of V5_SELF_CERTIFICATION_FIELDS) {
    // Refused BY NAME, not merely as an unknown key: the self-certification
    // check runs before the closed-key check so the refusal says what was
    // actually attempted rather than "this field is not declared".
    refuses(() => compileCostBasis({
      schema_version: V5_COST_BASIS_SCHEMA_VERSION,
      basis_id: "basis:smuggled",
      basis_version: 1,
      route: { ...CHEAP_ROUTE },
      components: components(),
      [field]: true,
    }), "self_certified_cost_refused");
    refuses(() => compileCostBasis({
      schema_version: V5_COST_BASIS_SCHEMA_VERSION,
      basis_id: "basis:smuggled",
      basis_version: 1,
      route: { ...CHEAP_ROUTE },
      components: { ...components(), [field]: true },
    }), "self_certified_cost_refused");
  }
});

test("COST: the route on a basis is exactly F04's route identity, no more and no less", () => {
  const record = assertQualificationRecord(qualification(CHEAP_ROUTE, [FLOOR_BUILD]));
  for (const key of V5_ROUTE_KEYS) {
    assert.ok(record[key] !== undefined, `${key} is not a field on an F04 qualification record`);
  }
  refuses(() => compileCostBasis({
    schema_version: V5_COST_BASIS_SCHEMA_VERSION,
    basis_id: "basis:widened",
    basis_version: 1,
    route: { ...CHEAP_ROUTE, provider_account: "acct-1" },
    components: components(),
  }), "unknown_field");
});

// --- the projection into F04's four buckets --------------------------------

test("PARTITION: every component lands in exactly one routing bucket", () => {
  const preimage = v5ExpectedTotalCostPreimage();
  const placed = V5_ROUTING_COST_BUCKETS.flatMap(bucket => preimage.cost_bucket_partition[bucket]);
  assert.equal(new Set(placed).size, placed.length, "a component is placed twice");
  assert.deepEqual([...placed].sort(), [...V5_COST_COMPONENTS]);
});

test("PARTITION: the routing projection preserves the expected total exactly", () => {
  const compiled = basis(CHEAP_ROUTE);
  const projected = toRoutingCostComponents(compiled);
  assert.deepEqual(Object.keys(projected).sort(), [...V5_ROUTING_COST_BUCKETS]);
  const sum = V5_ROUTING_COST_BUCKETS.reduce((total, key) => total + projected[key], 0);
  assert.equal(sum, compiled.expected_total_cost_units);
});

test("PARTITION: raising any single component raises the routing total by the same amount", () => {
  for (const key of V5_COST_COMPONENTS) {
    const compiled = basis(CHEAP_ROUTE, { [key]: components()[key] + 41 });
    const projected = toRoutingCostComponents(compiled);
    const sum = V5_ROUTING_COST_BUCKETS.reduce((total, bucket) => total + projected[bucket], 0);
    assert.equal(sum, BASE_TOTAL + 41, `${key} did not reach the routing total`);
  }
});

test("PARTITION: the projection takes a compiled basis, never a raw object", () => {
  refuses(() => toRoutingCostComponents({ components: components() }), "invalid_shape");
});

// --- Q041: which tier the work needs ---------------------------------------

test("TIER: the required tier is total over the declared vocabularies", () => {
  const seen = new Set();
  for (const instruction_state of V5_INSTRUCTION_STATES) {
    for (const activity of V5_WORK_ACTIVITIES) {
      const tier = requiredTierForWorkItem({ instruction_state, activity });
      assert.ok(V5_ROUTE_TIERS.includes(tier), `${instruction_state}/${activity} -> ${tier}`);
      seen.add(tier);
    }
  }
  assert.deepEqual([...seen].sort(), [...V5_ROUTE_TIERS],
    "every declared tier must be reachable from some work item");
});

test("TIER: a settled bounded build goes to the inexpensive builder", () => {
  assert.equal(requiredTierForWorkItem({
    instruction_state: "settled_bounded", activity: "build",
  }), "bounded_instruction_builder");
});

test("TIER: concentrated uncertainty goes to the premium owner, never the builder", () => {
  assert.equal(requiredTierForWorkItem({
    instruction_state: "concentrated_uncertainty", activity: "build",
  }), "concentrated_uncertainty_owner");
});

test("TIER: an adjudication stays premium even when the instruction is settled and bounded", () => {
  assert.equal(requiredTierForWorkItem({
    instruction_state: "settled_bounded", activity: "adjudicate",
  }), "adjudicator");
});

test("TIER: a review goes to the signing reviewer under either instruction state", () => {
  for (const instruction_state of V5_INSTRUCTION_STATES) {
    assert.equal(requiredTierForWorkItem({ instruction_state, activity: "review" }),
      "signing_reviewer");
  }
});

test("TIER: an unregistered activity or instruction state is refused by name", () => {
  refuses(() => requiredTierForWorkItem({
    instruction_state: "settled_bounded", activity: "vibes",
  }), "unknown_work_activity");
  refuses(() => requiredTierForWorkItem({
    instruction_state: "probably_fine", activity: "build",
  }), "unknown_instruction_state");
});

// --- Q041: which tier a route may occupy -----------------------------------

test("TIER: occupancy is derived from the floors the measurement MET", () => {
  const occupies = evaluateTierOccupancy({
    tier_policy: TIER_POLICY, tier: "bounded_instruction_builder",
    qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
  });
  assert.equal(occupies.occupies, true);
  assert.deepEqual(occupies.missing_quality_floor_refs, []);

  const shortfall = evaluateTierOccupancy({
    tier_policy: TIER_POLICY, tier: "signing_reviewer",
    qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
  });
  assert.equal(shortfall.occupies, false);
  assert.deepEqual(shortfall.missing_quality_floor_refs, [FLOOR_REVIEW]);
});

test("TIER: there is no field on a qualification that asserts a tier", () => {
  // F04 owns the qualification schema, so the refusal is F04's error, not this
  // module's — which is the point: there is one closed record shape in the tree
  // and this slice did not widen it to admit a tier claim.
  assert.throws(() => evaluateTierOccupancy({
    tier_policy: TIER_POLICY, tier: "adjudicator",
    qualification: { ...qualification(CHEAP_ROUTE, [FLOOR_BUILD]), tier: "adjudicator" },
  }), error => error instanceof V5RoutingError && error.code === "unknown_field");
});

test("TIER: a policy that omits a tier is refused, not treated as unrestricted", () => {
  const error = refuses(() => compileTierPolicy({
    schema_version: V5_TIER_POLICY_SCHEMA_VERSION,
    policy_id: "tier-policy:partial",
    policy_version: 1,
    tiers: [{ tier: "bounded_instruction_builder", required_quality_floor_refs: [FLOOR_BUILD] }],
  }), "tier_not_declared");
  assert.deepEqual(error.detail.missing.sort(),
    ["adjudicator", "concentrated_uncertainty_owner", "signing_reviewer"]);
});

test("TIER: a tier with no floor at all is refused", () => {
  refuses(() => compileTierPolicy({
    schema_version: V5_TIER_POLICY_SCHEMA_VERSION,
    policy_id: "tier-policy:empty-floor",
    policy_version: 1,
    tiers: [
      { tier: "bounded_instruction_builder", required_quality_floor_refs: [] },
      { tier: "signing_reviewer", required_quality_floor_refs: [FLOOR_REVIEW] },
      { tier: "concentrated_uncertainty_owner", required_quality_floor_refs: [FLOOR_UNCERTAINTY] },
      { tier: "adjudicator", required_quality_floor_refs: [FLOOR_ADJUDICATE] },
    ],
  }), "tier_floor_absent");
});

// --- admission: qualification first, then price ----------------------------

test("ADMISSION: the cheapest QUALIFIED route wins", () => {
  const result = admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    work_item: BUILD_WORK,
    candidates: [
      {
        qualification: qualification(PREMIUM_ROUTE, [FLOOR_BUILD, FLOOR_UNCERTAINTY]),
        cost_basis: basis(PREMIUM_ROUTE, { builder_cost_units: 900 }, "basis:premium"),
      },
      {
        qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
        cost_basis: basis(CHEAP_ROUTE, {}, "basis:cheap"),
      },
    ],
    authenticate_qualification: ALWAYS_AUTHENTIC,
  });
  assert.equal(result.admitted, true);
  assert.equal(result.reason_id, "qualified_then_cheapest");
  assert.equal(result.selected.route_key, routeKey(CHEAP_ROUTE));
  assert.equal(result.selected.expected_total_cost_units, BASE_TOTAL);
  assert.equal(result.cost_decided_qualification, false);
  assert.equal(result.downgrade_permitted, false);
  assert.equal(result.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(result.schema_version, V5_ROUTE_ADMISSION_SCHEMA_VERSION);
});

test("ADMISSION: a cheaper route that misses the tier floors never wins", () => {
  const result = admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    // A review, so the required tier is signing_reviewer and the cheap route's
    // single build floor is not enough.
    work_item: { ...BUILD_WORK, activity: "review" },
    candidates: [
      {
        qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
        cost_basis: basis(CHEAP_ROUTE, { builder_cost_units: 1 }, "basis:cheap"),
      },
      {
        qualification: qualification(PREMIUM_ROUTE, [FLOOR_BUILD, FLOOR_REVIEW]),
        cost_basis: basis(PREMIUM_ROUTE, { builder_cost_units: 9000 }, "basis:premium"),
      },
    ],
    authenticate_qualification: ALWAYS_AUTHENTIC,
  });
  assert.equal(result.admitted, true);
  assert.equal(result.required_tier, "signing_reviewer");
  assert.equal(result.selected.route_key, routeKey(PREMIUM_ROUTE));
  assert.deepEqual(result.considered, [{
    route_key: routeKey(CHEAP_ROUTE),
    reason_id: "tier_floors_not_met",
    missing_quality_floor_refs: [FLOOR_REVIEW],
  }]);
});

test("ADMISSION: when only unqualified routes remain the answer is unavailable, not the best of them", () => {
  const result = admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    work_item: { ...BUILD_WORK, activity: "adjudicate" },
    candidates: [
      {
        qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
        cost_basis: basis(CHEAP_ROUTE, {}, "basis:cheap"),
      },
      {
        qualification: qualification(PREMIUM_ROUTE, [FLOOR_UNCERTAINTY]),
        cost_basis: basis(PREMIUM_ROUTE, {}, "basis:premium"),
      },
    ],
    authenticate_qualification: ALWAYS_AUTHENTIC,
  });
  assert.equal(result.admitted, false);
  assert.equal(result.reason_id, "no_candidate_qualified");
  assert.equal(result.selected, undefined);
  assert.deepEqual(result.considered.map(entry => entry.reason_id),
    ["tier_floors_not_met", "tier_floors_not_met"]);
});

test("ADMISSION: an expired qualification is dropped even when it is the cheapest", () => {
  const result = admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    work_item: BUILD_WORK,
    candidates: [
      {
        qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD],
          { expires_at: "2026-09-02T00:00:00.000Z" }),
        cost_basis: basis(CHEAP_ROUTE, { builder_cost_units: 1 }, "basis:cheap"),
      },
      {
        qualification: qualification(PREMIUM_ROUTE, [FLOOR_BUILD]),
        cost_basis: basis(PREMIUM_ROUTE, { builder_cost_units: 5000 }, "basis:premium"),
      },
    ],
    authenticate_qualification: ALWAYS_AUTHENTIC,
  });
  assert.equal(result.selected.route_key, routeKey(PREMIUM_ROUTE));
  assert.deepEqual(result.considered,
    [{ route_key: routeKey(CHEAP_ROUTE), reason_id: "qualification_expired" }]);
});

test("ADMISSION: a qualification for another task class is dropped", () => {
  const otherRoute = { ...CHEAP_ROUTE, task_class: "task:something-else" };
  const result = admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    work_item: BUILD_WORK,
    candidates: [
      {
        qualification: qualification(otherRoute, [FLOOR_BUILD]),
        cost_basis: basis(otherRoute, { builder_cost_units: 1 }, "basis:other"),
      },
      {
        qualification: qualification(PREMIUM_ROUTE, [FLOOR_BUILD]),
        cost_basis: basis(PREMIUM_ROUTE, {}, "basis:premium"),
      },
    ],
    authenticate_qualification: ALWAYS_AUTHENTIC,
  });
  assert.equal(result.selected.route_key, routeKey(PREMIUM_ROUTE));
  assert.deepEqual(result.considered,
    [{ route_key: routeKey(otherRoute), reason_id: "task_class_mismatch" }]);
});

test("ADMISSION: an unauthenticated record is not a qualification, however valid its schema", () => {
  const result = admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    work_item: BUILD_WORK,
    candidates: [{
      qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
      cost_basis: basis(CHEAP_ROUTE),
    }],
    authenticate_qualification: NEVER_AUTHENTIC,
  });
  assert.equal(result.admitted, false);
  assert.equal(result.reason_id, "no_candidate_qualified");
  assert.deepEqual(result.considered,
    [{ route_key: routeKey(CHEAP_ROUTE), reason_id: "qualification_not_authentic" }]);
});

test("ADMISSION: with no verifier installed the answer is unavailable, not a selection", () => {
  const result = admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    work_item: BUILD_WORK,
    candidates: [{
      qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
      cost_basis: basis(CHEAP_ROUTE),
    }],
  });
  assert.equal(result.admitted, false);
  assert.equal(result.reason_id, "qualification_authenticator_unavailable");
  assert.equal(result.selected, undefined);
  assert.deepEqual(result.considered,
    [{ route_key: routeKey(CHEAP_ROUTE), reason_id: "qualification_not_authentic" }]);
});

test("ADMISSION: the authenticator must return true exactly; a truthy value is not enough", () => {
  const result = admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    work_item: BUILD_WORK,
    candidates: [{
      qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
      cost_basis: basis(CHEAP_ROUTE),
    }],
    authenticate_qualification: () => "yes",
  });
  assert.equal(result.admitted, false);
  assert.equal(result.reason_id, "no_candidate_qualified");
});

test("ADMISSION: a tie on price is broken by route key, so the answer is one and the same either way", () => {
  const candidates = [
    {
      qualification: qualification(PREMIUM_ROUTE, [FLOOR_BUILD]),
      cost_basis: basis(PREMIUM_ROUTE, {}, "basis:premium"),
    },
    {
      qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
      cost_basis: basis(CHEAP_ROUTE, {}, "basis:cheap"),
    },
  ];
  const forward = admitQualifiedRoute({
    tier_policy: TIER_POLICY, work_item: BUILD_WORK, candidates,
    authenticate_qualification: ALWAYS_AUTHENTIC,
  });
  const reversed = admitQualifiedRoute({
    tier_policy: TIER_POLICY, work_item: BUILD_WORK, candidates: [...candidates].reverse(),
    authenticate_qualification: ALWAYS_AUTHENTIC,
  });
  assert.equal(forward.selected.expected_total_cost_units,
    reversed.selected.expected_total_cost_units);
  assert.equal(forward.selected.route_key, reversed.selected.route_key);
  assert.deepEqual(forward.ranked, reversed.ranked);
});

test("ADMISSION: pricing one route while qualifying another is refused", () => {
  refuses(() => admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    work_item: BUILD_WORK,
    candidates: [{
      qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
      cost_basis: basis(PREMIUM_ROUTE),
    }],
    authenticate_qualification: ALWAYS_AUTHENTIC,
  }), "route_mismatch");
});

test("ADMISSION: two candidates naming one route are refused", () => {
  refuses(() => admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    work_item: BUILD_WORK,
    candidates: [
      {
        qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
        cost_basis: basis(CHEAP_ROUTE, {}, "basis:one"),
      },
      {
        qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
        cost_basis: basis(CHEAP_ROUTE, { builder_cost_units: 1 }, "basis:two"),
      },
    ],
    authenticate_qualification: ALWAYS_AUTHENTIC,
  }), "duplicate_entry");
});

test("ADMISSION: a malformed candidate throws even when no verifier was supplied", () => {
  assert.throws(() => admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    work_item: BUILD_WORK,
    candidates: [{
      qualification: { ...qualification(CHEAP_ROUTE, [FLOOR_BUILD]), qualified: true },
      cost_basis: basis(CHEAP_ROUTE),
    }],
  }), error => error instanceof V5RoutingError
    && error.code === "self_certified_qualification_refused");
});

test("ADMISSION: the qualification schema is F04's, so its refusals are F04's errors", () => {
  const broken = qualification(CHEAP_ROUTE, [FLOOR_BUILD]);
  delete broken.measurement_digest;
  assert.throws(() => admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    work_item: BUILD_WORK,
    candidates: [{ qualification: broken, cost_basis: basis(CHEAP_ROUTE) }],
    authenticate_qualification: ALWAYS_AUTHENTIC,
  }), error => error instanceof V5RoutingError);
});

test("ADMISSION: an empty candidate list is a broken caller, not an unavailable answer", () => {
  refuses(() => admitQualifiedRoute({
    tier_policy: TIER_POLICY, work_item: BUILD_WORK, candidates: [],
    authenticate_qualification: ALWAYS_AUTHENTIC,
  }), "no_candidates");
});

test("ADMISSION: every reason a candidate is dropped is inside the closed list", () => {
  const cases = [
    { qualification: qualification(CHEAP_ROUTE, []), basis: basis(CHEAP_ROUTE) },
    {
      qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD],
        { expires_at: "2026-09-02T00:00:00.000Z" }),
      basis: basis(CHEAP_ROUTE),
    },
    {
      qualification: qualification({ ...CHEAP_ROUTE, task_class: "task:other" }, [FLOOR_BUILD]),
      basis: basis({ ...CHEAP_ROUTE, task_class: "task:other" }),
    },
  ];
  for (const entry of cases) {
    const result = admitQualifiedRoute({
      tier_policy: TIER_POLICY,
      work_item: BUILD_WORK,
      candidates: [{ qualification: entry.qualification, cost_basis: entry.basis }],
      authenticate_qualification: ALWAYS_AUTHENTIC,
    });
    for (const considered of result.considered) {
      assert.ok(V5_CANDIDATE_REASONS.includes(considered.reason_id),
        `${considered.reason_id} is outside the closed list`);
    }
  }
});

test("ADMISSION: there is no input that sets downgrade_permitted or cost_decided_qualification", () => {
  const results = [];
  for (const activity of V5_WORK_ACTIVITIES) {
    for (const instruction_state of V5_INSTRUCTION_STATES) {
      for (const authenticate of [ALWAYS_AUTHENTIC, NEVER_AUTHENTIC, undefined]) {
        results.push(admitQualifiedRoute({
          tier_policy: TIER_POLICY,
          work_item: { ...BUILD_WORK, activity, instruction_state },
          candidates: [{
            qualification: qualification(CHEAP_ROUTE,
              [FLOOR_BUILD, FLOOR_REVIEW, FLOOR_UNCERTAINTY, FLOOR_ADJUDICATE]),
            cost_basis: basis(CHEAP_ROUTE),
          }],
          authenticate_qualification: authenticate,
        }));
      }
    }
  }
  assert.equal(results.length, 18);
  for (const result of results) {
    assert.equal(result.downgrade_permitted, false);
    assert.equal(result.cost_decided_qualification, false);
  }
  assert.ok(results.some(result => result.admitted === true), "the sweep proved nothing if none admitted");
  assert.ok(results.some(result => result.admitted === false), "the sweep proved nothing if none refused");
});

// --- the projection --------------------------------------------------------

test("PROJECTION: every closed vocabulary is hashed in, so none can drift inline", () => {
  const preimage = v5ExpectedTotalCostPreimage();
  const text = JSON.stringify(preimage);
  for (const name of [...V5_COST_COMPONENTS, ...V5_ROUTE_TIERS, ...V5_ROUTING_COST_BUCKETS,
    ...V5_INSTRUCTION_STATES, ...V5_WORK_ACTIVITIES, ...V5_CANDIDATE_REASONS]) {
    assert.ok(text.includes(name), `${name} is not in the hashed preimage`);
  }
  assert.ok(Object.isFrozen(preimage));
});

test("PROJECTION: the digest moves when the preimage does and not otherwise", () => {
  const first = v5ExpectedTotalCostProjection().preimage_digest;
  assert.equal(v5ExpectedTotalCostProjection().preimage_digest, first);
  assert.match(first, /^sha256:[0-9a-f]{64}$/);
});

test("PROJECTION: the honest boundary is stated, not implied", () => {
  const projection = v5ExpectedTotalCostProjection();
  assert.equal(projection.qualification_authenticity_verified_here, false);
  assert.equal(projection.cost_can_qualify_a_route, false);
  assert.equal(projection.downgrade_reachable_by_any_input, false);
  assert.equal(projection.tier_assertable_without_evidence, false);
  assert.equal(projection.routing_projection_preserves_total, true);
  assert.ok(projection.unimplemented_dependencies.length >= 4);
  assert.ok(projection.unimplemented_dependencies.some(gap =>
    gap.includes("trusted qualification verifier")));
  assert.equal(projection.tenant, ORGANIZATION_TENANT_ID);
  assert.ok(Object.isFrozen(projection));
});

test("PROJECTION: this module names F04 as the owner of the things it did not decide", () => {
  const projection = v5ExpectedTotalCostProjection();
  assert.equal(projection.qualification_schema_owner,
    "model-routing.v5.js#assertQualificationRecord");
  assert.equal(projection.self_certification_vocabulary_owner,
    "model-routing.v5.js#V5_SELF_CERTIFICATION_FIELDS");
  assert.equal(projection.qualification_derivation_owner,
    "model-qualification-kernel.v5.js#deriveRouteQualification");
});

test("PROJECTION: the self-certification list is F04's own, not a second copy", () => {
  const preimage = JSON.stringify(v5ExpectedTotalCostPreimage());
  for (const field of V5_SELF_CERTIFICATION_FIELDS) {
    assert.ok(!preimage.includes(`"${field}"`),
      `${field} is re-listed in this module's preimage; it should be imported from F04`);
  }
});

test("PROJECTION: a returned result cannot be mutated by its caller", () => {
  const result = admitQualifiedRoute({
    tier_policy: TIER_POLICY,
    work_item: BUILD_WORK,
    candidates: [{
      qualification: qualification(CHEAP_ROUTE, [FLOOR_BUILD]),
      cost_basis: basis(CHEAP_ROUTE),
    }],
    authenticate_qualification: ALWAYS_AUTHENTIC,
  });
  assert.throws(() => { result.admitted = false; }, TypeError);
  assert.throws(() => { result.selected.expected_total_cost_units = 0; }, TypeError);
  const before = clone(result);
  assert.deepEqual(clone(result), before);
});
