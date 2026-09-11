// V5-A02 half two — workflow lifecycle and rule delivery assurance, proved
// clause by clause.
//
// The positive case comes first on purpose: a checker that only ever refuses
// cannot be told apart from a broken one, so every negative below is a single
// NAMED mutation of one clean request that allows, and each `clean*()` returns a
// fresh deep copy so a mutation in one test cannot leak into another.
//
// Two suites prove that a vocabulary was READ rather than invented: one reads
// db/schema.sql and asserts the eleven workflow lifecycle states match
// V5-F09's `ops.completion_projection` in ITS precedence order, and one asserts
// every rule class and every enforcement mechanism V5-F05 exports is accounted
// for here exactly once.
//
//   node --test mcp-server/test/lifecycle-assurance.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { digest } from "../src/artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import {
  V5_F05_ENFORCEMENT_MECHANISMS,
  V5_F05_RULE_CLASSES,
  V5_F05_RETIREMENT_BEHAVIORS,
} from "../src/rule-applicability.v5.js";
import {
  V5_A02_LIFECYCLE_SCHEMA_VERSION,
  V5_A02_LIFECYCLE_POLICY_VERSION,
  V5_A02_LIFECYCLE_REASON_IDS,
  V5_A02_WORKFLOW_LIFECYCLE_STATES,
  V5_A02_WORKFLOW_DIMENSIONS,
  V5_A02_MANDATORY_PROOF_DIMENSIONS,
  V5_A02_WORKFLOW_KINDS,
  V5_A02_RULE_STATES,
  V5_A02_RULE_TRANSITIONS,
  V5_A02_SHADOW_MISS_DISPOSITIONS,
  V5_A02_FALLBACK_KINDS,
  V5_A02_MACHINE_ENFORCEMENT_MECHANISMS,
  V5_A02_CLASS_ENFORCEMENT_MECHANISM,
  V5_A02_RULE_ACTIVATION_SEAM,
  V5_A02_DECISION_IDS,
  deriveWorkflowLifecycleState,
  evaluateWorkflowLifecycle,
  evaluateRuleTransition,
  emitRuleActivation,
  evaluateRuleEnforcementCoverage,
  v5A02LifecyclePolicyPreimage,
  v5A02LifecyclePolicyDigest,
  v5A02LifecyclePolicyCanonicalBytes,
} from "../src/lifecycle-assurance.v5.js";

const AS_OF = "2026-09-11T18:00:00Z";
const IMPLEMENTATION_DIGEST = `sha256:${"d".repeat(64)}`;

function cleanEvidence(overrides = {}) {
  return {
    has_activation: true, has_artifact: true, has_blocker: false, has_canonical: true,
    has_conflict: false, has_intent: true, has_readback: true, has_stale: false,
    has_telemetry: true, ...overrides,
  };
}

function cleanComplexWorkflow() {
  return {
    workflow_ref: "workflow:engineering-slice-delivery",
    workflow_kind: "complex",
    required_dimensions: [...V5_A02_WORKFLOW_DIMENSIONS],
    disposition: "none",
    evidence: cleanEvidence(),
    claimed_state: "operational",
  };
}

function cleanShortWorkflow() {
  return {
    workflow_ref: "workflow:capture-note",
    workflow_kind: "short",
    required_dimensions: V5_A02_WORKFLOW_DIMENSIONS.filter(dim => dim !== "artifact"),
    disposition: "none",
    evidence: cleanEvidence(),
    claimed_state: "operational",
  };
}

function cleanControl(mechanism = "code_control") {
  return {
    control_id: "gate-paths",
    control_ref: "control:hooks/gate_paths.py",
    enforcement_mechanism: mechanism,
    implementation_digest: IMPLEMENTATION_DIGEST,
    verifier_id: "ci",
    verified_at: "2026-09-11T12:00:00Z",
  };
}

function cleanFallback() {
  return { kind: "refuse_closed", ref: "fallback:refuse-and-name-the-seam" };
}

function cleanTransition(overrides = {}) {
  return {
    rule_id: "a02-example-rule",
    rule_class: "code_enforced",
    mandatory: true,
    from_state: "shadow",
    to_state: "active",
    review: { proposer_actor_id: "claude", reviewer_actor_id: "joe" },
    tests: [{ test_ref: "test:gate-zero-assurance", result: "pass" }],
    shadow_window: {
      window_ref: "window:2026-09-shadow",
      opened_at: "2026-09-04T00:00:00Z",
      closed_at: "2026-09-11T00:00:00Z",
      misses: [{ miss_ref: "miss:0001", disposition: "confirmed_gap" }],
    },
    control: cleanControl(),
    fallback: cleanFallback(),
    retirement: null,
    ...overrides,
  };
}

function cleanCoverage() {
  return {
    as_of: AS_OF,
    rules: [
      {
        rule_id: "code-rule", version: 3, rule_class: "code_enforced", state: "active",
        mandatory: true, binding_text_present: false,
        control: cleanControl(), fallback: cleanFallback(),
      },
      {
        rule_id: "workflow-rule", version: 1, rule_class: "workflow", state: "active",
        mandatory: true, binding_text_present: true,
        control: { ...cleanControl("workflow_definition"), control_id: "light-path" },
        fallback: { kind: "documented_manual_procedure", ref: "fallback:manual-serialized-merge" },
      },
      {
        rule_id: "judgment-rule", version: 2, rule_class: "scoped_judgment", state: "active",
        mandatory: false, binding_text_present: true,
        control: { ...cleanControl("model_judgment"), control_id: "voice-review" },
        fallback: { kind: "escalate_to_verified_partner", ref: "fallback:ask-joe" },
      },
      {
        rule_id: "retired-rule", version: 9, rule_class: "preference", state: "retired",
        mandatory: false, binding_text_present: true, control: null, fallback: null,
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// WORKFLOW LIFECYCLE — the vocabulary is V5-F09's, read not invented.
// ---------------------------------------------------------------------------

test("WORKFLOW: the eleven states match ops.completion_projection in its own order", () => {
  const schema = readFileSync(
    fileURLToPath(new URL("../../db/schema.sql", import.meta.url)), "utf8");
  const marker = "possibility(lifecycle_state, applies)";
  const at = schema.indexOf(marker);
  assert.ok(at > 0, "the F09 completion projection must still be in db/schema.sql");
  const values = schema.slice(schema.lastIndexOf("VALUES", at), at);
  const stated = [...values.matchAll(/\('([a-z_]+)'::text,/g)].map(match => match[1]);
  assert.equal(stated.length, 11);
  assert.deepEqual([...V5_A02_WORKFLOW_LIFECYCLE_STATES], stated);
});

test("WORKFLOW: a complete complex workflow derives operational", () => {
  const result = evaluateWorkflowLifecycle(cleanComplexWorkflow());
  assert.equal(result.derived_state, "operational");
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, null);
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
});

test("WORKFLOW: a complete short workflow derives operational without a build artifact", () => {
  const request = cleanShortWorkflow();
  request.evidence.has_artifact = false;
  const result = evaluateWorkflowLifecycle(request);
  assert.equal(result.derived_state, "operational");
  assert.equal(result.decision, "allow");
});

test("WORKFLOW: canonical is structurally undroppable under F09's own derivation", () => {
  // An artifact with no canonical is `built_unmerged`, and that clause sits
  // above `operational` in the precedence — so no evidence at all finishes such
  // a workflow. This is why `canonical` is not the droppable dimension.
  for (const activation of [false, true])
    for (const readback of [false, true])
      for (const telemetry of [false, true]) {
        const state = deriveWorkflowLifecycleState(
          cleanEvidence({ has_canonical: false, has_activation: activation,
            has_readback: readback, has_telemetry: telemetry }), "none", true);
        assert.equal(state, "built_unmerged",
          `activation=${activation} readback=${readback} telemetry=${telemetry}`);
      }
});

test("WORKFLOW: a short workflow may not drop a proof dimension to reach operational", () => {
  const request = cleanShortWorkflow();
  request.required_dimensions = request.required_dimensions.filter(dim => dim !== "telemetry");
  request.evidence.has_telemetry = false;
  const result = evaluateWorkflowLifecycle(request);
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "short_workflow_drops_proof_dimension");
  assert.deepEqual(result.proof_dimensions_dropped, ["telemetry"]);
  assert.equal(result.derived_state, "active_unproven",
    "F09's own projection answers active_unproven, never operational");
});

test("WORKFLOW: a complex workflow may not shorten its own requirement list", () => {
  const request = cleanComplexWorkflow();
  request.required_dimensions = request.required_dimensions.filter(dim => dim !== "artifact");
  const result = evaluateWorkflowLifecycle(request);
  assert.equal(result.reason_id, "workflow_required_dimension_missing");
  assert.deepEqual(result.required_dimensions_missing_from_declaration, ["artifact"]);
});

test("WORKFLOW: a short workflow may not declare the artifact stage back in", () => {
  const request = cleanShortWorkflow();
  request.required_dimensions = [...V5_A02_WORKFLOW_DIMENSIONS];
  const result = evaluateWorkflowLifecycle(request);
  assert.equal(result.reason_id, "workflow_required_dimension_missing");
  assert.deepEqual(result.required_dimensions_over_declared, ["artifact"]);
});

test("WORKFLOW: the claimed state never wins over the derived one", () => {
  const request = cleanComplexWorkflow();
  request.evidence.has_telemetry = false;
  const result = evaluateWorkflowLifecycle(request);
  assert.equal(result.claimed_state, "operational");
  assert.equal(result.derived_state, "active_unproven");
  assert.equal(result.claim_matches_derivation, false);
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "workflow_state_claim_not_derived");
  assert.equal(result.caller_stated_state_honoured, false);
});

test("WORKFLOW: no claimed state reaches an operational derivation without the proof", () => {
  // Every caller-controlled claim, against evidence that is missing readback.
  const request = cleanComplexWorkflow();
  request.evidence.has_readback = false;
  for (const claimed of V5_A02_WORKFLOW_LIFECYCLE_STATES) {
    const result = evaluateWorkflowLifecycle({ ...request, claimed_state: claimed });
    assert.equal(result.derived_state, "active_unproven", `claimed ${claimed}`);
    assert.notEqual(result.derived_state, "operational");
  }
});

test("WORKFLOW: the precedence order decides, and conflicting beats everything", () => {
  const evidence = cleanEvidence({ has_conflict: true, has_blocker: true, has_stale: true });
  assert.equal(deriveWorkflowLifecycleState(evidence, "canceled", true), "conflicting");
  assert.equal(deriveWorkflowLifecycleState(cleanEvidence({ has_stale: true, has_blocker: true }), "none", true),
    "unknown_stale");
  assert.equal(deriveWorkflowLifecycleState(cleanEvidence({ has_blocker: true }), "none", true), "blocked");
});

test("WORKFLOW: each intermediate state is reachable from its own evidence", () => {
  const cases = [
    ["planned", cleanEvidence({ has_artifact: false, has_canonical: false, has_activation: false })],
    ["built_unmerged", cleanEvidence({ has_canonical: false, has_activation: false })],
    ["merged_unactivated", cleanEvidence({ has_activation: false })],
    ["active_unproven", cleanEvidence({ has_readback: false })],
  ];
  for (const [expected, evidence] of cases)
    assert.equal(deriveWorkflowLifecycleState(evidence, "none", true), expected);
  assert.equal(deriveWorkflowLifecycleState(cleanEvidence(), "none", false), "partially_built");
  assert.equal(deriveWorkflowLifecycleState(cleanEvidence(), "canceled", true), "canceled");
  assert.equal(deriveWorkflowLifecycleState(cleanEvidence(), "superseded", true), "superseded");
});

test("WORKFLOW: an unknown field is unreadable", () => {
  const request = cleanComplexWorkflow();
  request.approved_by_me = true;
  assert.throws(() => evaluateWorkflowLifecycle(request),
    error => error instanceof V5BoundaryError && error.code === "unknown_field");
});

test("WORKFLOW: an unsorted requirement list is unreadable", () => {
  const request = cleanComplexWorkflow();
  request.required_dimensions = ["intent", "artifact", "canonical", "activation", "readback", "telemetry"];
  assert.throws(() => evaluateWorkflowLifecycle(request),
    error => error instanceof V5BoundaryError && error.code === "unsorted_list");
});

// ---------------------------------------------------------------------------
// RULE LIFECYCLE.
// ---------------------------------------------------------------------------

test("RULE: the state ladder is the catalog's, and retired is terminal", () => {
  assert.deepEqual([...V5_A02_RULE_STATES],
    ["proposed", "reviewed", "tested", "shadow", "active", "retired"]);
  assert.deepEqual(V5_A02_RULE_TRANSITIONS.retired, []);
  for (const state of V5_A02_RULE_STATES)
    for (const target of V5_A02_RULE_TRANSITIONS[state])
      assert.ok(V5_A02_RULE_STATES.includes(target), `${state} -> ${target}`);
});

test("RULE: shadow -> active with full evidence is permitted", () => {
  const result = evaluateRuleTransition(cleanTransition());
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, null);
  assert.equal(result.performs_transition, false);
});

test("RULE: an edge outside the ladder is refused and names what is permitted", () => {
  const result = evaluateRuleTransition(cleanTransition({ from_state: "proposed", to_state: "active" }));
  assert.equal(result.reason_id, "rule_state_transition_not_permitted");
  assert.deepEqual(result.detail.permitted, ["retired", "reviewed"]);
});

test("RULE: a retired rule cannot walk back to active", () => {
  const result = evaluateRuleTransition(cleanTransition({ from_state: "retired", to_state: "active" }));
  assert.equal(result.reason_id, "rule_state_transition_not_permitted");
});

test("RULE: activation is reversible — active -> shadow is an edge and is flagged", () => {
  const result = evaluateRuleTransition(cleanTransition({ from_state: "active", to_state: "shadow" }));
  assert.equal(result.decision, "allow");
  assert.equal(result.reversible_activation, true);
  assert.equal(evaluateRuleTransition(cleanTransition()).reversible_activation, false);
});

test("RULE: the proposer may not be the reviewer", () => {
  const result = evaluateRuleTransition(cleanTransition({
    from_state: "proposed", to_state: "reviewed",
    review: { proposer_actor_id: "claude", reviewer_actor_id: "claude" },
  }));
  assert.equal(result.reason_id, "rule_reviewer_not_independent");
});

test("RULE: proposed -> reviewed with an independent reviewer is permitted", () => {
  const result = evaluateRuleTransition(cleanTransition({ from_state: "proposed", to_state: "reviewed" }));
  assert.equal(result.decision, "allow");
});

test("RULE: a rule cannot be tested by no test", () => {
  const result = evaluateRuleTransition(cleanTransition({
    from_state: "reviewed", to_state: "tested", tests: [],
  }));
  assert.equal(result.reason_id, "rule_tests_absent");
});

test("RULE: a failing or skipped test does not make a rule tested", () => {
  for (const result of ["fail", "skipped"]) {
    const answer = evaluateRuleTransition(cleanTransition({
      from_state: "reviewed", to_state: "tested",
      tests: [{ test_ref: "test:one", result: "pass" }, { test_ref: "test:two", result }],
    }));
    assert.equal(answer.reason_id, "rule_tests_not_passing", result);
    assert.deepEqual(answer.detail.not_passing, ["test:two"]);
  }
});

test("RULE: shadow observation needs a shadow window", () => {
  const result = evaluateRuleTransition(cleanTransition({
    from_state: "tested", to_state: "shadow", shadow_window: null,
  }));
  assert.equal(result.reason_id, "shadow_window_absent");
});

test("RULE: a shadow miss without a disposition blocks activation", () => {
  const request = cleanTransition();
  request.shadow_window.misses.push({ miss_ref: "miss:0002", disposition: null });
  const result = evaluateRuleTransition(request);
  assert.equal(result.reason_id, "shadow_miss_without_disposition");
  assert.deepEqual(result.detail.undisposed, ["miss:0002"]);
});

test("RULE: an active rule must map to a control", () => {
  const result = evaluateRuleTransition(cleanTransition({ control: null }));
  assert.equal(result.reason_id, "active_rule_control_unmapped");
});

test("RULE: an active rule must say what happens when its control is unavailable", () => {
  const result = evaluateRuleTransition(cleanTransition({ fallback: null }));
  assert.equal(result.reason_id, "active_rule_fallback_absent");
});

test("RULE: a mandatory rule enforced only by judgment denies nothing", () => {
  const result = evaluateRuleTransition(cleanTransition({
    rule_class: "scoped_judgment", mandatory: true,
    control: cleanControl("model_judgment"),
  }));
  assert.equal(result.reason_id, "mandatory_rule_without_machine_control");
});

test("RULE: a non-mandatory judgment rule may activate on a judgment control", () => {
  const result = evaluateRuleTransition(cleanTransition({
    rule_class: "scoped_judgment", mandatory: false,
    control: cleanControl("model_judgment"),
  }));
  assert.equal(result.decision, "allow");
});

test("RULE: retirement states a behaviour, and superseded_only names its successor", () => {
  const withoutBehaviour = evaluateRuleTransition(cleanTransition({
    from_state: "active", to_state: "retired", retirement: null,
  }));
  assert.equal(withoutBehaviour.reason_id, "rule_retirement_successor_absent");
  const withoutSuccessor = evaluateRuleTransition(cleanTransition({
    from_state: "active", to_state: "retired",
    retirement: { behavior: "superseded_only", successor_rule_id: null },
  }));
  assert.equal(withoutSuccessor.reason_id, "rule_retirement_successor_absent");
  const complete = evaluateRuleTransition(cleanTransition({
    from_state: "active", to_state: "retired",
    retirement: { behavior: "superseded_only", successor_rule_id: "a02-successor-rule" },
  }));
  assert.equal(complete.decision, "allow");
  for (const behavior of V5_F05_RETIREMENT_BEHAVIORS) {
    const answer = evaluateRuleTransition(cleanTransition({
      from_state: "active", to_state: "retired",
      retirement: { behavior, successor_rule_id: behavior === "superseded_only" ? "a02-successor-rule" : null },
    }));
    assert.equal(answer.decision, "allow", behavior);
  }
});

// ---------------------------------------------------------------------------
// ACTIVATION IS UNREACHABLE.
// ---------------------------------------------------------------------------

test("ACTIVATION: no caller-controlled input shape can activate a rule", () => {
  const shapes = [cleanTransition()];
  for (const from of V5_A02_RULE_STATES)
    for (const to of V5_A02_RULE_STATES)
      if (V5_A02_RULE_TRANSITIONS[from].includes(to))
        shapes.push(cleanTransition({
          from_state: from, to_state: to,
          retirement: to === "retired"
            ? { behavior: "permanent_until_superseded", successor_rule_id: null } : null,
        }));
  for (const mechanism of V5_F05_ENFORCEMENT_MECHANISMS)
    shapes.push(cleanTransition({ mandatory: false, control: cleanControl(mechanism) }));
  for (const kind of V5_A02_FALLBACK_KINDS)
    shapes.push(cleanTransition({ fallback: { kind, ref: "fallback:any" } }));
  for (const disposition of V5_A02_SHADOW_MISS_DISPOSITIONS) {
    const request = cleanTransition();
    request.shadow_window.misses = [{ miss_ref: "miss:0001", disposition }];
    shapes.push(request);
  }
  assert.ok(shapes.length >= 25, "the sweep must cover the caller-controlled domain");
  for (const request of shapes) {
    const result = emitRuleActivation(request);
    assert.equal(result.activated, false);
    assert.equal(result.decision, "refuse");
    assert.equal(result.reason_id, "rule_activation_seam_unavailable");
    assert.equal(result.controller_bound, false);
    assert.equal(result.controller_is_caller_supplied, false);
  }
});

test("ACTIVATION: a controller cannot be handed in as a second argument", () => {
  assert.throws(
    () => emitRuleActivation(cleanTransition(), { activate: () => ({ activated: true }) }),
    error => error instanceof V5BoundaryError &&
      error.code === "rule_activation_controller_is_not_an_argument");
});

test("ACTIVATION: the refusal still reports the transition it evaluated", () => {
  const result = emitRuleActivation(cleanTransition());
  assert.equal(result.activation_seam, V5_A02_RULE_ACTIVATION_SEAM);
  assert.equal(result.transition.decision, "allow");
  assert.equal(result.transition.performs_transition, false);
});

// ---------------------------------------------------------------------------
// ENFORCEMENT COVERAGE — "every active rule maps to enforceable control and
// fallback".
// ---------------------------------------------------------------------------

test("COVERAGE: the F05 class and mechanism vocabularies are accounted for exactly once", () => {
  const classes = Object.keys(V5_A02_CLASS_ENFORCEMENT_MECHANISM).sort();
  assert.deepEqual(classes, [...V5_F05_RULE_CLASSES].sort());
  const mechanisms = Object.values(V5_A02_CLASS_ENFORCEMENT_MECHANISM).sort();
  assert.deepEqual(mechanisms, [...V5_F05_ENFORCEMENT_MECHANISMS].sort());
  assert.equal(new Set(mechanisms).size, mechanisms.length, "one mechanism per class");
  for (const mechanism of V5_A02_MACHINE_ENFORCEMENT_MECHANISMS)
    assert.ok(V5_F05_ENFORCEMENT_MECHANISMS.includes(mechanism));
});

test("COVERAGE: a fully mapped active set is complete", () => {
  const result = evaluateRuleEnforcementCoverage(cleanCoverage());
  assert.equal(result.coverage_complete, true);
  assert.equal(result.decision, "allow");
  assert.equal(result.active_rule_count, 3);
  assert.equal(result.mapped_count, 3);
  assert.deepEqual(result.unmapped_rules, []);
  assert.equal(result.out_of_scope_rules.length, 1);
  assert.equal(result.out_of_scope_rules[0].rule_id, "retired-rule");
});

test("COVERAGE: an active rule with no control is listed unmapped, not hidden", () => {
  const request = cleanCoverage();
  request.rules[0].control = null;
  const result = evaluateRuleEnforcementCoverage(request);
  assert.equal(result.coverage_complete, false);
  assert.equal(result.decision, "refuse");
  assert.equal(result.unmapped_count, 1);
  assert.equal(result.unmapped_rules[0].rule_id, "code-rule");
  assert.equal(result.unmapped_rules[0].reason_id, "active_rule_control_unmapped");
});

test("COVERAGE: rule presence is not enforcement proof", () => {
  const request = cleanCoverage();
  request.rules[1].control = null;             // workflow-rule, binding text present
  const result = evaluateRuleEnforcementCoverage(request);
  assert.equal(result.coverage_complete, false);
  assert.equal(result.unmapped_rules[0].reason_id, "rule_presence_is_not_enforcement");
});

test("COVERAGE: an active rule with no fallback is unmapped", () => {
  const request = cleanCoverage();
  request.rules[0].fallback = null;
  const result = evaluateRuleEnforcementCoverage(request);
  assert.equal(result.unmapped_rules[0].reason_id, "active_rule_fallback_absent");
});

test("COVERAGE: a control from another class family does not enforce this rule", () => {
  const request = cleanCoverage();
  request.rules[0].control = cleanControl("partner_preference");
  const result = evaluateRuleEnforcementCoverage(request);
  assert.equal(result.unmapped_rules[0].reason_id, "rule_control_mechanism_not_for_class");
});

test("COVERAGE: a mandatory rule on a non-machine control is unmapped", () => {
  const request = cleanCoverage();
  request.rules[2].mandatory = true;           // judgment-rule, model_judgment control
  const result = evaluateRuleEnforcementCoverage(request);
  assert.equal(result.unmapped_rules[0].rule_id, "judgment-rule");
  assert.equal(result.unmapped_rules[0].reason_id, "mandatory_rule_without_machine_control");
});

test("COVERAGE: every unmapped shape refuses, across the whole caller domain", () => {
  // For each rule class, a control from EVERY mechanism. Coverage is complete
  // only where the mechanism is the one the class declares.
  for (const ruleClass of V5_F05_RULE_CLASSES) {
    for (const mechanism of V5_F05_ENFORCEMENT_MECHANISMS) {
      const result = evaluateRuleEnforcementCoverage({
        as_of: AS_OF,
        rules: [{
          rule_id: "sweep-rule", version: 1, rule_class: ruleClass, state: "active",
          mandatory: false, binding_text_present: true,
          control: cleanControl(mechanism), fallback: cleanFallback(),
        }],
      });
      const matches = V5_A02_CLASS_ENFORCEMENT_MECHANISM[ruleClass] === mechanism;
      assert.equal(result.coverage_complete, matches, `${ruleClass} / ${mechanism}`);
    }
  }
});

test("COVERAGE: a non-active rule owes no control and is reported out of scope", () => {
  for (const state of V5_A02_RULE_STATES.filter(one => one !== "active")) {
    const result = evaluateRuleEnforcementCoverage({
      as_of: AS_OF,
      rules: [{
        rule_id: "sweep-rule", version: 1, rule_class: "code_enforced", state,
        mandatory: true, binding_text_present: true, control: null, fallback: null,
      }],
    });
    assert.equal(result.active_rule_count, 0, state);
    assert.equal(result.out_of_scope_rules.length, 1, state);
  }
});

test("COVERAGE: the same rule at the same version twice is unreadable", () => {
  const request = cleanCoverage();
  request.rules.push({ ...request.rules[0] });
  assert.throws(() => evaluateRuleEnforcementCoverage(request),
    error => error instanceof V5BoundaryError && error.code === "duplicate_rule");
});

test("COVERAGE: a self-asserted coverage field is unreadable", () => {
  const request = cleanCoverage();
  request.rules[0].enforced = true;
  assert.throws(() => evaluateRuleEnforcementCoverage(request),
    error => error instanceof V5BoundaryError && error.code === "unknown_field");
  assert.equal(evaluateRuleEnforcementCoverage(cleanCoverage()).caller_stated_coverage, false);
});

// ---------------------------------------------------------------------------
// POLICY IDENTITY.
// ---------------------------------------------------------------------------

test("POLICY: the preimage carries the slice's four decision ids and the seam state", () => {
  const preimage = v5A02LifecyclePolicyPreimage();
  assert.deepEqual(preimage.decision_ids, [...V5_A02_DECISION_IDS]);
  assert.equal(preimage.schema_version, V5_A02_LIFECYCLE_SCHEMA_VERSION);
  assert.equal(preimage.policy_version, V5_A02_LIFECYCLE_POLICY_VERSION);
  assert.equal(preimage.rule_activation_controller_bound, false);
  assert.deepEqual(preimage.workflow_lifecycle_states_in_precedence_order,
    [...V5_A02_WORKFLOW_LIFECYCLE_STATES]);
  assert.deepEqual(preimage.workflow_kinds, [...V5_A02_WORKFLOW_KINDS].sort());
  assert.deepEqual(preimage.workflow_proof_dimensions, [...V5_A02_MANDATORY_PROOF_DIMENSIONS].sort());
});

test("POLICY: the digest is deterministic and matches its canonical bytes", () => {
  assert.equal(v5A02LifecyclePolicyDigest(), v5A02LifecyclePolicyDigest());
  assert.equal(v5A02LifecyclePolicyDigest(), digest(v5A02LifecyclePolicyPreimage()));
  assert.equal(v5A02LifecyclePolicyCanonicalBytes(),
    JSON.stringify(JSON.parse(v5A02LifecyclePolicyCanonicalBytes())));
});

test("POLICY: every reason this module can answer with is registered", () => {
  const source = readFileSync(
    fileURLToPath(new URL("../src/lifecycle-assurance.v5.js", import.meta.url)), "utf8");
  const used = [...source.matchAll(/reason\("([a-z_]+)"\)/g)].map(match => match[1]);
  assert.ok(used.length > 0);
  for (const id of used)
    assert.ok(V5_A02_LIFECYCLE_REASON_IDS.includes(id), `${id} is not registered`);
});

test("POLICY: every result is frozen and carries the no-effects marker", () => {
  for (const result of [
    evaluateWorkflowLifecycle(cleanComplexWorkflow()),
    evaluateRuleTransition(cleanTransition()),
    emitRuleActivation(cleanTransition()),
    evaluateRuleEnforcementCoverage(cleanCoverage()),
  ]) {
    assert.deepEqual(result.effects, V5_NO_EFFECTS);
    assert.ok(Object.isFrozen(result));
  }
});
