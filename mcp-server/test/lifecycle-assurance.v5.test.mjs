// V5-A02 half two — workflow lifecycle and rule delivery assurance, proved in
// two halves that must not be confused with each other.
//
// HALF A, THE PUBLIC SURFACE. Everything `lifecycle-assurance.v5.js` exports,
// enumerated from the module namespace, swept with every caller-controlled
// input shape — clean fixtures, every rule state, every enforcement mechanism,
// every fallback kind, and the shapes that simply assert the answer — and
// asserted never to produce `operational`, `active`, `allow` or
// `coverage_complete` under any name. The surface is also asserted INDIFFERENT
// to its input: every shape produces byte-identical output. Plus a
// parser-backed scan (V8's own ESM parser via vm.SourceTextModule in a child
// process, not a regex) proving no production module imports the classifier
// entry.
//
// HALF B, THE CLASSIFIERS, through `lifecycle-classifiers.v5.testonly.js`, the
// dedicated test-only entry. Their answers are conditional by name
// (`would_derive_state_if_authoritative`, `would_permit_if_authoritative`,
// `would_be_covered_if_authoritative`) because no authoritative workflow, rule,
// control, test or receipt reader exists to make them anything else. The
// positive case comes first on purpose: a clause that only ever refuses cannot
// be told apart from a broken one, so every negative is a single NAMED mutation
// of one clean request that satisfies.
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
import { spawnSync } from "node:child_process";

import { digest } from "../src/artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import {
  V5_F05_ENFORCEMENT_MECHANISMS,
  V5_F05_RULE_CLASSES,
  V5_F05_RETIREMENT_BEHAVIORS,
} from "../src/rule-applicability.v5.js";

import * as surface from "../src/lifecycle-assurance.v5.js";
import {
  V5_A02_LIFECYCLE_SCHEMA_VERSION,
  V5_A02_LIFECYCLE_POLICY_VERSION,
  V5_A02_LIFECYCLE_REASON_IDS,
  V5_A02_LIFECYCLE_OWED_SEAMS,
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
  V5_A02_WORKFLOW_STATE_READER_SEAM,
  V5_A02_RULE_REGISTRY_READER_SEAM,
  V5_A02_CONTROL_IMPLEMENTATION_READER_SEAM,
  V5_A02_TEST_RESULT_READER_SEAM,
  V5_A02_ACCEPTANCE_RECEIPT_READER_SEAM,
  V5_A02_DECISION_IDS,
  readWorkflowLifecycle,
  readRuleLifecycleTransition,
  readRuleEnforcementCoverage,
  emitRuleActivation,
  v5A02LifecyclePolicyPreimage,
  v5A02LifecyclePolicyDigest,
  v5A02LifecyclePolicyCanonicalBytes,
} from "../src/lifecycle-assurance.v5.js";

import {
  V5_A02_CLASSIFIER_EVIDENCE_SOURCE,
  classifyWorkflowLifecycleState,
  classifyWorkflowLifecycle,
  classifyRuleTransition,
  classifyRuleEnforcementCoverage,
} from "../src/lifecycle-classifiers.v5.testonly.js";

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

// ===========================================================================
// HALF A — THE PUBLIC SURFACE.
// ===========================================================================

/**
 * Exactly what this module may export. A new name here is a deliberate decision
 * a reader has to make, which is the point: the lifecycle, transition and
 * coverage evaluators used to be on this list, and that was the defect.
 */
const EXPECTED_PUBLIC_EXPORTS = [
  "V5_A02_ACCEPTANCE_RECEIPT_READER_SEAM",
  "V5_A02_CLASS_ENFORCEMENT_MECHANISM",
  "V5_A02_CONTROL_IMPLEMENTATION_READER_SEAM",
  "V5_A02_DECISION_IDS",
  "V5_A02_FALLBACK_KINDS",
  "V5_A02_LIFECYCLE_OWED_SEAMS",
  "V5_A02_LIFECYCLE_POLICY_VERSION",
  "V5_A02_LIFECYCLE_REASON_IDS",
  "V5_A02_LIFECYCLE_SCHEMA_VERSION",
  "V5_A02_MACHINE_ENFORCEMENT_MECHANISMS",
  "V5_A02_MANDATORY_PROOF_DIMENSIONS",
  "V5_A02_RULE_ACTIVATION_SEAM",
  "V5_A02_RULE_REGISTRY_READER_SEAM",
  "V5_A02_RULE_STATES",
  "V5_A02_RULE_TRANSITIONS",
  "V5_A02_SHADOW_MISS_DISPOSITIONS",
  "V5_A02_SHORT_WORKFLOW_DROPPABLE",
  "V5_A02_TEST_RESULT_READER_SEAM",
  "V5_A02_WORKFLOW_DIMENSIONS",
  "V5_A02_WORKFLOW_DISPOSITIONS",
  "V5_A02_WORKFLOW_KINDS",
  "V5_A02_WORKFLOW_LIFECYCLE_STATES",
  "V5_A02_WORKFLOW_STATE_READER_SEAM",
  "V5_NO_EFFECTS",
  "emitRuleActivation",
  "readRuleEnforcementCoverage",
  "readRuleLifecycleTransition",
  "readWorkflowLifecycle",
  "v5A02LifecyclePolicyCanonicalBytes",
  "v5A02LifecyclePolicyDigest",
  "v5A02LifecyclePolicyPreimage",
];

/** The words a consumer would act on. None may come back from this surface. */
const PRIVILEGED_TRUE_KEYS = new Set([
  "ok", "green", "joins_exactly", "passable", "activated", "allow", "allowed",
  "satisfied", "coverage_complete", "claim_matches_derivation", "operational",
  "caller_evidence_admitted", "controller_bound", "request_read", "performs_transition",
]);
const PRIVILEGED_VALUES = new Set([
  "allow", "allowed", "green", "pass", "passed", "passable", "operational", "active",
  "activated", "covered", "coverage_complete",
]);

/** Every string, key and boolean in a returned value, walked to the leaves. */
function privilegedFindings(value, path = "$", found = []) {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => privilegedFindings(entry, `${path}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) {
      const at = `${path}.${key}`;
      if (entry === true && PRIVILEGED_TRUE_KEYS.has(key)) found.push(`${at} === true`);
      if (key.startsWith("would_")) found.push(`${at} is a classifier field on the public surface`);
      privilegedFindings(entry, at, found);
    }
    return found;
  }
  if (typeof value === "string" && PRIVILEGED_VALUES.has(value)) found.push(`${path} === ${value}`);
  return found;
}

/** Every caller-controlled shape this surface could ever be handed. */
function callerControlledShapes() {
  const shapes = [cleanComplexWorkflow(), cleanShortWorkflow(), cleanTransition(), cleanCoverage()];
  for (const from of V5_A02_RULE_STATES)
    for (const to of V5_A02_RULE_STATES)
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
  for (const claimed of V5_A02_WORKFLOW_LIFECYCLE_STATES)
    shapes.push({ ...cleanComplexWorkflow(), claimed_state: claimed });
  // The shapes that try to say the answer outright.
  shapes.push({ ...cleanComplexWorkflow(), derived_state: "operational", decision: "allow" });
  shapes.push({ coverage_complete: true, activated: true, state: "active" });
  shapes.push({}, null, undefined, "operational", 1, true, []);
  return shapes;
}

const PUBLIC_FUNCTIONS_OVER_CALLER_INPUT = [
  ["readWorkflowLifecycle", readWorkflowLifecycle],
  ["readRuleLifecycleTransition", readRuleLifecycleTransition],
  ["readRuleEnforcementCoverage", readRuleEnforcementCoverage],
  ["emitRuleActivation", emitRuleActivation],
];

test("SURFACE: the public export list is exactly the unavailable surface", () => {
  assert.deepEqual(Object.keys(surface).sort(), EXPECTED_PUBLIC_EXPORTS);
  for (const name of Object.keys(surface)) {
    assert.ok(!/^(classify|evaluate|derive)/.test(name),
      `${name} is a classifier name on the public surface`);
    assert.ok(!name.includes("would_"), `${name} is a classifier field on the public surface`);
  }
});

test("SURFACE: no caller-controlled shape produces a privileged outcome", () => {
  const shapes = callerControlledShapes();
  assert.ok(shapes.length >= 50, "the sweep must cover the caller-controlled domain");
  for (const [name, fn] of PUBLIC_FUNCTIONS_OVER_CALLER_INPUT) {
    for (const shape of shapes) {
      const result = fn(shape);
      assert.equal(result.status, "unavailable", name);
      assert.equal(result.decision, "refuse", name);
      assert.equal(result.request_read, false, name);
      assert.equal(result.caller_evidence_admitted, false, name);
      assert.ok(V5_A02_LIFECYCLE_REASON_IDS.includes(result.reason_id), name);
      assert.deepEqual(privilegedFindings(result), [],
        `${name} leaked a privileged outcome for ${String(JSON.stringify(shape)).slice(0, 80)}`);
      assert.ok(Object.isFrozen(result), name);
      assert.deepEqual(result.effects, V5_NO_EFFECTS, name);
    }
  }
});

test("SURFACE: the answer is byte-identical across every caller shape", () => {
  for (const [name, fn] of PUBLIC_FUNCTIONS_OVER_CALLER_INPUT) {
    const first = digest(fn(cleanTransition()));
    for (const shape of callerControlledShapes())
      assert.equal(digest(fn(shape)), first, `${name} answered differently for a caller shape`);
    assert.equal(digest(fn()), first, `${name} answered differently for no argument at all`);
  }
});

test("SURFACE: no rule is activated, and no transition verdict rides inside the refusal", () => {
  for (const shape of [cleanTransition(), undefined, { rule_id: "x" }]) {
    const result = emitRuleActivation(shape);
    assert.equal(result.activated, false);
    assert.equal(result.reason_id, "rule_activation_seam_unavailable");
    assert.equal(result.controller_bound, false);
    assert.equal(result.controller_is_caller_supplied, false);
    assert.equal(result.activation_seam, V5_A02_RULE_ACTIVATION_SEAM);
    assert.equal(result.performs_transition, false);
    // The defect the reviewer named: an allow inside a refusal.
    assert.equal(result.transition, null, "no transition verdict may ride inside the refusal");
    assert.deepEqual(result.owed_seams, [...V5_A02_LIFECYCLE_OWED_SEAMS]);
  }
});

test("SURFACE: every unavailable answer names the seams it is owed and binds none", () => {
  const workflow = readWorkflowLifecycle();
  assert.equal(workflow.reason_id, "workflow_state_reader_unavailable");
  assert.deepEqual(workflow.owed_seams,
    [V5_A02_WORKFLOW_STATE_READER_SEAM, V5_A02_TEST_RESULT_READER_SEAM].sort());
  assert.equal(workflow.workflow_state_reader_bound, false);

  const transition = readRuleLifecycleTransition();
  assert.equal(transition.reason_id, "rule_registry_reader_unavailable");
  assert.deepEqual(transition.owed_seams, [V5_A02_RULE_REGISTRY_READER_SEAM,
    V5_A02_TEST_RESULT_READER_SEAM, V5_A02_ACCEPTANCE_RECEIPT_READER_SEAM].sort());
  assert.equal(transition.rule_registry_reader_bound, false);

  const coverage = readRuleEnforcementCoverage();
  assert.equal(coverage.reason_id, "control_implementation_reader_unavailable");
  assert.deepEqual(coverage.owed_seams, [V5_A02_RULE_REGISTRY_READER_SEAM,
    V5_A02_CONTROL_IMPLEMENTATION_READER_SEAM, V5_A02_TEST_RESULT_READER_SEAM].sort());
  assert.equal(coverage.control_implementation_reader_bound, false);

  for (const result of [workflow, transition, coverage, emitRuleActivation(cleanTransition())])
    for (const entry of result.seams_bound)
      assert.equal(entry.bound, false, `${entry.seam} must be unbound`);
});

test("SURFACE: a controller cannot be handed in as a second argument", () => {
  assert.throws(
    () => emitRuleActivation(cleanTransition(), { activate: () => ({ activated: true }) }),
    error => error instanceof V5BoundaryError &&
      error.code === "rule_activation_controller_is_not_an_argument");
});

// ---------------------------------------------------------------------------
// The classifier entry is unreachable from production. Parsed, not grepped.
// ---------------------------------------------------------------------------

/**
 * V8's own ESM parser, through vm.SourceTextModule in a child process — a real
 * parser, and the one Node itself uses, rather than a regex over source text.
 */
function moduleImports(directory) {
  const script = `
    const { readdirSync, readFileSync } = require("node:fs");
    const { join } = require("node:path");
    const vm = require("node:vm");
    const dir = process.argv[1];
    const out = {};
    for (const name of readdirSync(dir).sort()) {
      if (!name.endsWith(".js")) continue;
      const source = readFileSync(join(dir, name), "utf8");
      out[name] = new vm.SourceTextModule(source, { identifier: name }).dependencySpecifiers;
    }
    process.stdout.write(JSON.stringify(out));
  `;
  const run = spawnSync(process.execPath, ["--experimental-vm-modules", "-e", script, directory],
    { encoding: "utf8" });
  assert.equal(run.status, 0, `the module parser failed: ${run.stderr}`);
  return JSON.parse(run.stdout);
}

test("ISOLATION: no production module imports the classifier test-only entry", () => {
  const directory = fileURLToPath(new URL("../src", import.meta.url));
  const imports = moduleImports(directory);
  assert.ok(Object.hasOwn(imports, "lifecycle-assurance.v5.js"));
  assert.ok(Object.hasOwn(imports, "lifecycle-classifiers.v5.testonly.js"));
  assert.ok(Object.keys(imports).length > 100, "every module in src must have been parsed");

  const offenders = Object.entries(imports)
    .filter(([, specifiers]) => specifiers.some(one => one.includes(".testonly.")))
    .map(([name]) => name);
  assert.deepEqual(offenders, [], "a production module reached the classifier entry");

  assert.deepEqual(imports["lifecycle-assurance.v5.js"],
    ["./artifact-trust.js", "./global-boundaries.v5.js", "./identity.js",
      "./gate-zero-assurance.v5.js"]);
});

test("ISOLATION: the classifier entry answers only in the conditional", () => {
  const source = readFileSync(
    fileURLToPath(new URL("../src/lifecycle-classifiers.v5.testonly.js", import.meta.url)), "utf8");
  for (const forbidden of ["decision:", "derived_state:", "coverage_complete:", "activated:"])
    assert.equal(source.includes(`\n    ${forbidden}`), false,
      `${forbidden} is a privileged result field`);
  for (const result of [
    classifyWorkflowLifecycle(cleanComplexWorkflow()),
    classifyRuleTransition(cleanTransition()),
    classifyRuleEnforcementCoverage(cleanCoverage()),
    classifyWorkflowLifecycleState(cleanEvidence(), "none", true),
  ]) {
    assert.equal(result.is_not_authority, true);
    assert.equal(result.evidence_source, V5_A02_CLASSIFIER_EVIDENCE_SOURCE);
    assert.equal(Object.hasOwn(result, "decision"), false);
  }
});

// ===========================================================================
// HALF B — THE CLASSIFIERS, CLAUSE BY CLAUSE.
// ===========================================================================

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

test("WORKFLOW: a complete complex workflow would derive the finished state", () => {
  const result = classifyWorkflowLifecycle(cleanComplexWorkflow());
  assert.equal(result.would_derive_state_if_authoritative, "operational");
  assert.equal(result.would_permit_if_authoritative, true);
  assert.equal(result.reason_id, null);
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
});

test("WORKFLOW: a complete short workflow finishes without a build artifact", () => {
  const request = cleanShortWorkflow();
  request.evidence.has_artifact = false;
  const result = classifyWorkflowLifecycle(request);
  assert.equal(result.would_derive_state_if_authoritative, "operational");
  assert.equal(result.would_permit_if_authoritative, true);
});

test("WORKFLOW: canonical is structurally undroppable under F09's own derivation", () => {
  // An artifact with no canonical is `built_unmerged`, and that clause sits
  // above `operational` in the precedence — so no evidence at all finishes such
  // a workflow. This is why `canonical` is not the droppable dimension.
  for (const activation of [false, true])
    for (const readback of [false, true])
      for (const telemetry of [false, true]) {
        const state = classifyWorkflowLifecycleState(
          cleanEvidence({ has_canonical: false, has_activation: activation,
            has_readback: readback, has_telemetry: telemetry }), "none", true);
        assert.equal(state.would_derive_state_if_authoritative, "built_unmerged",
          `activation=${activation} readback=${readback} telemetry=${telemetry}`);
      }
});

test("WORKFLOW: a short workflow may not drop a proof dimension to finish", () => {
  const request = cleanShortWorkflow();
  request.required_dimensions = request.required_dimensions.filter(dim => dim !== "telemetry");
  request.evidence.has_telemetry = false;
  const result = classifyWorkflowLifecycle(request);
  assert.equal(result.would_permit_if_authoritative, false);
  assert.equal(result.reason_id, "short_workflow_drops_proof_dimension");
  assert.deepEqual(result.proof_dimensions_dropped, ["telemetry"]);
  assert.equal(result.would_derive_state_if_authoritative, "active_unproven",
    "F09's own projection answers active_unproven, never operational");
});

test("WORKFLOW: a complex workflow may not shorten its own requirement list", () => {
  const request = cleanComplexWorkflow();
  request.required_dimensions = request.required_dimensions.filter(dim => dim !== "artifact");
  const result = classifyWorkflowLifecycle(request);
  assert.equal(result.reason_id, "workflow_required_dimension_missing");
  assert.deepEqual(result.required_dimensions_missing_from_declaration, ["artifact"]);
});

test("WORKFLOW: a short workflow may not declare the artifact stage back in", () => {
  const request = cleanShortWorkflow();
  request.required_dimensions = [...V5_A02_WORKFLOW_DIMENSIONS];
  const result = classifyWorkflowLifecycle(request);
  assert.equal(result.reason_id, "workflow_required_dimension_missing");
  assert.deepEqual(result.required_dimensions_over_declared, ["artifact"]);
});

test("WORKFLOW: the claimed state never wins over the derived one", () => {
  const request = cleanComplexWorkflow();
  request.evidence.has_telemetry = false;
  const result = classifyWorkflowLifecycle(request);
  assert.equal(result.claimed_state_cited, "operational");
  assert.equal(result.would_derive_state_if_authoritative, "active_unproven");
  assert.equal(result.claim_matches_derivation, false);
  assert.equal(result.would_permit_if_authoritative, false);
  assert.equal(result.reason_id, "workflow_state_claim_not_derived");
  assert.equal(result.caller_stated_state_honoured, false);
});

test("WORKFLOW: no claimed state reaches a finished derivation without the proof", () => {
  // Every caller-controlled claim, against evidence that is missing readback.
  const request = cleanComplexWorkflow();
  request.evidence.has_readback = false;
  for (const claimed of V5_A02_WORKFLOW_LIFECYCLE_STATES) {
    const result = classifyWorkflowLifecycle({ ...request, claimed_state: claimed });
    assert.equal(result.would_derive_state_if_authoritative, "active_unproven", `claimed ${claimed}`);
  }
});

test("WORKFLOW: the precedence order decides, and conflicting beats everything", () => {
  const derived = (evidence, disposition, present) =>
    classifyWorkflowLifecycleState(evidence, disposition, present).would_derive_state_if_authoritative;
  const evidence = cleanEvidence({ has_conflict: true, has_blocker: true, has_stale: true });
  assert.equal(derived(evidence, "canceled", true), "conflicting");
  assert.equal(derived(cleanEvidence({ has_stale: true, has_blocker: true }), "none", true),
    "unknown_stale");
  assert.equal(derived(cleanEvidence({ has_blocker: true }), "none", true), "blocked");
});

test("WORKFLOW: each intermediate state is reachable from its own evidence", () => {
  const derived = (evidence, disposition, present) =>
    classifyWorkflowLifecycleState(evidence, disposition, present).would_derive_state_if_authoritative;
  const cases = [
    ["planned", cleanEvidence({ has_artifact: false, has_canonical: false, has_activation: false })],
    ["built_unmerged", cleanEvidence({ has_canonical: false, has_activation: false })],
    ["merged_unactivated", cleanEvidence({ has_activation: false })],
    ["active_unproven", cleanEvidence({ has_readback: false })],
  ];
  for (const [expected, evidence] of cases)
    assert.equal(derived(evidence, "none", true), expected);
  assert.equal(derived(cleanEvidence(), "none", false), "partially_built");
  assert.equal(derived(cleanEvidence(), "canceled", true), "canceled");
  assert.equal(derived(cleanEvidence(), "superseded", true), "superseded");
});

test("WORKFLOW: an unknown field is unreadable", () => {
  const request = cleanComplexWorkflow();
  request.approved_by_me = true;
  assert.throws(() => classifyWorkflowLifecycle(request),
    error => error instanceof V5BoundaryError && error.code === "unknown_field");
});

test("WORKFLOW: an unsorted requirement list is unreadable", () => {
  const request = cleanComplexWorkflow();
  request.required_dimensions = ["intent", "artifact", "canonical", "activation", "readback", "telemetry"];
  assert.throws(() => classifyWorkflowLifecycle(request),
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

test("RULE: shadow -> active with full evidence would be permitted", () => {
  const result = classifyRuleTransition(cleanTransition());
  assert.equal(result.would_permit_if_authoritative, true);
  assert.equal(result.reason_id, null);
  assert.equal(result.performs_transition, false);
});

test("RULE: an edge outside the ladder is refused and names what is permitted", () => {
  const result = classifyRuleTransition(cleanTransition({ from_state: "proposed", to_state: "active" }));
  assert.equal(result.reason_id, "rule_state_transition_not_permitted");
  assert.deepEqual(result.detail.permitted, ["retired", "reviewed"]);
});

test("RULE: a retired rule cannot walk back to active", () => {
  const result = classifyRuleTransition(cleanTransition({ from_state: "retired", to_state: "active" }));
  assert.equal(result.reason_id, "rule_state_transition_not_permitted");
});

test("RULE: activation is reversible — active -> shadow is an edge and is flagged", () => {
  const result = classifyRuleTransition(cleanTransition({ from_state: "active", to_state: "shadow" }));
  assert.equal(result.would_permit_if_authoritative, true);
  assert.equal(result.reversible_activation_edge, true);
  assert.equal(classifyRuleTransition(cleanTransition()).reversible_activation_edge, false);
});

test("RULE: the proposer may not be the reviewer", () => {
  const result = classifyRuleTransition(cleanTransition({
    from_state: "proposed", to_state: "reviewed",
    review: { proposer_actor_id: "claude", reviewer_actor_id: "claude" },
  }));
  assert.equal(result.reason_id, "rule_reviewer_not_independent");
});

test("RULE: proposed -> reviewed with an independent reviewer would be permitted", () => {
  const result = classifyRuleTransition(cleanTransition({ from_state: "proposed", to_state: "reviewed" }));
  assert.equal(result.would_permit_if_authoritative, true);
});

test("RULE: a rule cannot be tested by no test", () => {
  const result = classifyRuleTransition(cleanTransition({
    from_state: "reviewed", to_state: "tested", tests: [],
  }));
  assert.equal(result.reason_id, "rule_tests_absent");
});

test("RULE: a failing or skipped test does not make a rule tested", () => {
  for (const result of ["fail", "skipped"]) {
    const answer = classifyRuleTransition(cleanTransition({
      from_state: "reviewed", to_state: "tested",
      tests: [{ test_ref: "test:one", result: "pass" }, { test_ref: "test:two", result }],
    }));
    assert.equal(answer.reason_id, "rule_tests_not_passing", result);
    assert.deepEqual(answer.detail.not_passing, ["test:two"]);
  }
});

test("RULE: shadow observation needs a shadow window", () => {
  const result = classifyRuleTransition(cleanTransition({
    from_state: "tested", to_state: "shadow", shadow_window: null,
  }));
  assert.equal(result.reason_id, "shadow_window_absent");
});

test("RULE: a shadow miss without a disposition blocks activation", () => {
  const request = cleanTransition();
  request.shadow_window.misses.push({ miss_ref: "miss:0002", disposition: null });
  const result = classifyRuleTransition(request);
  assert.equal(result.reason_id, "shadow_miss_without_disposition");
  assert.deepEqual(result.detail.undisposed, ["miss:0002"]);
});

test("RULE: an active rule must map to a control", () => {
  assert.equal(classifyRuleTransition(cleanTransition({ control: null })).reason_id,
    "active_rule_control_unmapped");
});

test("RULE: an active rule must say what happens when its control is unavailable", () => {
  assert.equal(classifyRuleTransition(cleanTransition({ fallback: null })).reason_id,
    "active_rule_fallback_absent");
});

test("RULE: a mandatory rule enforced only by judgment denies nothing", () => {
  const result = classifyRuleTransition(cleanTransition({
    rule_class: "scoped_judgment", mandatory: true,
    control: cleanControl("model_judgment"),
  }));
  assert.equal(result.reason_id, "mandatory_rule_without_machine_control");
});

test("RULE: a non-mandatory judgment rule may satisfy the clause on a judgment control", () => {
  const result = classifyRuleTransition(cleanTransition({
    rule_class: "scoped_judgment", mandatory: false,
    control: cleanControl("model_judgment"),
  }));
  assert.equal(result.would_permit_if_authoritative, true);
});

test("RULE: retirement states a behaviour, and superseded_only names its successor", () => {
  const withoutBehaviour = classifyRuleTransition(cleanTransition({
    from_state: "active", to_state: "retired", retirement: null,
  }));
  assert.equal(withoutBehaviour.reason_id, "rule_retirement_successor_absent");
  const withoutSuccessor = classifyRuleTransition(cleanTransition({
    from_state: "active", to_state: "retired",
    retirement: { behavior: "superseded_only", successor_rule_id: null },
  }));
  assert.equal(withoutSuccessor.reason_id, "rule_retirement_successor_absent");
  const complete = classifyRuleTransition(cleanTransition({
    from_state: "active", to_state: "retired",
    retirement: { behavior: "superseded_only", successor_rule_id: "a02-successor-rule" },
  }));
  assert.equal(complete.would_permit_if_authoritative, true);
  for (const behavior of V5_F05_RETIREMENT_BEHAVIORS) {
    const answer = classifyRuleTransition(cleanTransition({
      from_state: "active", to_state: "retired",
      retirement: { behavior, successor_rule_id: behavior === "superseded_only" ? "a02-successor-rule" : null },
    }));
    assert.equal(answer.would_permit_if_authoritative, true, behavior);
  }
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

test("COVERAGE: a fully mapped active set would be covered", () => {
  const result = classifyRuleEnforcementCoverage(cleanCoverage());
  assert.equal(result.would_be_covered_if_authoritative, true);
  assert.equal(result.active_rule_count, 3);
  assert.equal(result.mapped_count, 3);
  assert.deepEqual(result.unmapped_rules, []);
  assert.equal(result.out_of_scope_rules.length, 1);
  assert.equal(result.out_of_scope_rules[0].rule_id, "retired-rule");
});

test("COVERAGE: an active rule with no control is listed unmapped, not hidden", () => {
  const request = cleanCoverage();
  request.rules[0].control = null;
  const result = classifyRuleEnforcementCoverage(request);
  assert.equal(result.would_be_covered_if_authoritative, false);
  assert.equal(result.unmapped_count, 1);
  assert.equal(result.unmapped_rules[0].rule_id, "code-rule");
  assert.equal(result.unmapped_rules[0].reason_id, "active_rule_control_unmapped");
});

test("COVERAGE: rule presence is not enforcement proof", () => {
  const request = cleanCoverage();
  request.rules[1].control = null;             // workflow-rule, binding text present
  const result = classifyRuleEnforcementCoverage(request);
  assert.equal(result.would_be_covered_if_authoritative, false);
  assert.equal(result.unmapped_rules[0].reason_id, "rule_presence_is_not_enforcement");
});

test("COVERAGE: an active rule with no fallback is unmapped", () => {
  const request = cleanCoverage();
  request.rules[0].fallback = null;
  assert.equal(classifyRuleEnforcementCoverage(request).unmapped_rules[0].reason_id,
    "active_rule_fallback_absent");
});

test("COVERAGE: a control from another class family does not enforce this rule", () => {
  const request = cleanCoverage();
  request.rules[0].control = cleanControl("partner_preference");
  assert.equal(classifyRuleEnforcementCoverage(request).unmapped_rules[0].reason_id,
    "rule_control_mechanism_not_for_class");
});

test("COVERAGE: a mandatory rule on a non-machine control is unmapped", () => {
  const request = cleanCoverage();
  request.rules[2].mandatory = true;           // judgment-rule, model_judgment control
  const result = classifyRuleEnforcementCoverage(request);
  assert.equal(result.unmapped_rules[0].rule_id, "judgment-rule");
  assert.equal(result.unmapped_rules[0].reason_id, "mandatory_rule_without_machine_control");
});

test("COVERAGE: every unmapped shape refuses, across the whole caller domain", () => {
  // For each rule class, a control from EVERY mechanism. Coverage holds only
  // where the mechanism is the one the class declares.
  for (const ruleClass of V5_F05_RULE_CLASSES) {
    for (const mechanism of V5_F05_ENFORCEMENT_MECHANISMS) {
      const result = classifyRuleEnforcementCoverage({
        as_of: AS_OF,
        rules: [{
          rule_id: "sweep-rule", version: 1, rule_class: ruleClass, state: "active",
          mandatory: false, binding_text_present: true,
          control: cleanControl(mechanism), fallback: cleanFallback(),
        }],
      });
      const matches = V5_A02_CLASS_ENFORCEMENT_MECHANISM[ruleClass] === mechanism;
      assert.equal(result.would_be_covered_if_authoritative, matches, `${ruleClass} / ${mechanism}`);
    }
  }
});

test("COVERAGE: a non-active rule owes no control and is reported out of scope", () => {
  for (const state of V5_A02_RULE_STATES.filter(one => one !== "active")) {
    const result = classifyRuleEnforcementCoverage({
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
  assert.throws(() => classifyRuleEnforcementCoverage(request),
    error => error instanceof V5BoundaryError && error.code === "duplicate_rule");
});

test("COVERAGE: a self-asserted coverage field is unreadable", () => {
  const request = cleanCoverage();
  request.rules[0].enforced = true;
  assert.throws(() => classifyRuleEnforcementCoverage(request),
    error => error instanceof V5BoundaryError && error.code === "unknown_field");
  assert.equal(classifyRuleEnforcementCoverage(cleanCoverage()).caller_stated_coverage, false);
});

// ---------------------------------------------------------------------------
// POLICY IDENTITY.
// ---------------------------------------------------------------------------

test("POLICY: the preimage carries the slice's decision ids and the seam state", () => {
  const preimage = v5A02LifecyclePolicyPreimage();
  assert.deepEqual(preimage.decision_ids, [...V5_A02_DECISION_IDS]);
  assert.equal(preimage.schema_version, V5_A02_LIFECYCLE_SCHEMA_VERSION);
  assert.equal(preimage.policy_version, V5_A02_LIFECYCLE_POLICY_VERSION);
  assert.equal(preimage.rule_activation_controller_bound, false);
  assert.equal(preimage.authoritative_readers_bound, false);
  assert.equal(preimage.public_surface_answers, "unavailable");
  assert.deepEqual(preimage.owed_seams, [...V5_A02_LIFECYCLE_OWED_SEAMS]);
  assert.deepEqual(preimage.workflow_lifecycle_states_in_precedence_order,
    [...V5_A02_WORKFLOW_LIFECYCLE_STATES]);
  assert.deepEqual(preimage.workflow_kinds, [...V5_A02_WORKFLOW_KINDS].sort());
  assert.deepEqual(preimage.workflow_proof_dimensions, [...V5_A02_MANDATORY_PROOF_DIMENSIONS].sort());
  assert.equal(digest(v5A02LifecyclePolicyPreimage({ coverage_complete: true })),
    digest(v5A02LifecyclePolicyPreimage()));
});

test("POLICY: the digest is deterministic and matches its canonical bytes", () => {
  assert.equal(v5A02LifecyclePolicyDigest(), v5A02LifecyclePolicyDigest());
  assert.equal(v5A02LifecyclePolicyDigest(), digest(v5A02LifecyclePolicyPreimage()));
  assert.equal(v5A02LifecyclePolicyCanonicalBytes(),
    JSON.stringify(JSON.parse(v5A02LifecyclePolicyCanonicalBytes())));
});

test("POLICY: every reason either half can answer with is registered", () => {
  // Three spellings: the clauses answer through `wouldNotPermit("id", ...)` and
  // `reason("id")`, and the public surface passes the id as the second argument
  // of its one `unavailable(...)` shape.
  const citations = path => {
    const source = readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");
    return [
      ...[...source.matchAll(/reason\("([a-z_]+)"\)/g)].map(match => match[1]),
      ...[...source.matchAll(/wouldNotPermit\("([a-z_]+)"/g)].map(match => match[1]),
      ...[...source.matchAll(/unavailable\(\s*"[a-z_]+",\s*"([a-z_]+)"/g)].map(match => match[1]),
    ];
  };
  const publicCitations = citations("../src/lifecycle-assurance.v5.js");
  assert.ok(publicCitations.length >= 4, "the public surface must cite its own refusals");
  const classifierCitations = citations("../src/lifecycle-classifiers.v5.testonly.js");
  assert.ok(classifierCitations.length >= 10, "the clauses must cite their own refusals");
  for (const id of [...publicCitations, ...classifierCitations])
    assert.ok(V5_A02_LIFECYCLE_REASON_IDS.includes(id), `${id} is not registered`);
  for (const result of [readWorkflowLifecycle(), readRuleLifecycleTransition(),
    readRuleEnforcementCoverage(), emitRuleActivation({})])
    assert.ok(V5_A02_LIFECYCLE_REASON_IDS.includes(result.reason_id), result.reason_id);
});

test("POLICY: every result on both halves is frozen and carries the no-effects marker", () => {
  for (const result of [
    readWorkflowLifecycle(), readRuleLifecycleTransition(), readRuleEnforcementCoverage(),
    emitRuleActivation(cleanTransition()),
    classifyWorkflowLifecycle(cleanComplexWorkflow()),
    classifyRuleTransition(cleanTransition()),
    classifyRuleEnforcementCoverage(cleanCoverage()),
  ]) {
    assert.deepEqual(result.effects, V5_NO_EFFECTS);
    assert.ok(Object.isFrozen(result));
  }
});
