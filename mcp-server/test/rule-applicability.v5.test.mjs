// V5-F05, half one — rule taxonomy, typed applicability, coverage and the
// relation graph, proved case by case.
//
// The suite is organised by the decision it proves, and every decision gets
// both halves. The positive case is asserted FIRST in each section, because a
// rule that only ever refuses cannot be told apart from a rule that is broken:
// the exception has to fire before its five failure shapes mean anything.

import test from "node:test";
import assert from "node:assert/strict";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5F05Error,
  V5_F05_UNIVERSE_SCHEMA_VERSION,
  V5_F05_COVERAGE_SCHEMA_VERSION,
  V5_F05_SETTLED_DECISIONS,
  V5_F05_SETTLED_DECISION_IDS,
  V5_F05_FACT_DIMENSIONS,
  V5_F05_RULE_CLASSES,
  V5_F05_RELATIONS,
  V5_F05_UNKNOWN_FACT,
  V5_F05_MAX_ENFORCEMENT_EVIDENCE_AGE_SECONDS,
  V5_NO_EFFECTS,
  assertF05DecisionBinding,
  compileRuleUniverse,
  requireCompiledUniverse,
  deriveRuleApplicability,
  projectRuleForModel,
  verifyCoverageReceipt,
  ruleUniversePreimage,
  estimateTokens,
  ruleKernelIntegrationGaps,
  assertRuleKernelIntegrationComplete,
  v5F05DecisionSubsetDigest,
  v5F05RuleKernelPreimage,
  v5F05RuleKernelDigest,
  v5F05RuleKernelCanonicalBytes,
} from "../src/rule-applicability.v5.js";

// The seven source-evidence digests exactly as the reviewed F05 source binding
// carries them. Copied here so drift between the module and the binding is a
// test failure rather than a later discovery.
const REVIEWED_DECISION_BINDING = Object.freeze({
  "Q050.D1": "aceecd2a205cb8e54ba3a25f4d76794755f282347aedff647aa64c6d7849870e",
  "Q051.D1": "a025a6f44b4ce876eedb74f4ddef3cf55a106e65fd8503ac5b9f3f6b10a47e4e",
  "Q064.D1": "37d4a659edb38342b0dbfda64ef0ac6cb86c69bd47cd59bf4edd09f724a971eb",
  "Q065.D1": "97a70bb7fb499e647ace6061a9abb430746615e940beda49b7fbc224d69740b0",
  "Q066.D1": "0841228973232c76214c3bfb286870ca26e6c582b57e7bcc7eeecce0e86e5438",
  "Q068.D1": "b4ed3495d133e30e425dbe17ce207f1aad00de3497ef61a1fd3734a00ce90ec8",
  "Q087.D1": "b5fe67a82599096c8e1d1e2b6c8f83bdd8d82dabd8c1f8e3775c0a74c7b14b1e",
});

// --------------------------------------------------------------- fixtures

const NOW = "2026-09-09T12:00:00Z";
const FRESH_EVIDENCE = "2026-09-09T11:00:00Z";
const EXACTLY_AT_BOUND = "2026-09-08T12:00:00Z";
const ONE_SECOND_PAST_BOUND = "2026-09-08T11:59:59Z";

const code = fn => {
  try { fn(); } catch (error) { return error instanceof V5F05Error ? error.code : `not-a-V5F05Error:${error}`; }
  return "no-throw";
};

const worktreeRule = () => ({
  rule_id: "worktree-first",
  version: 3,
  rule_class: "workflow",
  scope: "shared",
  owner: "joe",
  mandatory: true,
  trigger: { action: ["repo.commit"] },
  control_effect: { control_key: "isolated_worktree", effect: "require" },
  binding_text: "Before the first write to tracked files, take a session-owned worktree.\n"
    + "The canonical checkout is the integration lane only.",
  summary: "worktree per session",
  tests: ["check:worktree-isolated"],
  retirement: { behavior: "permanent_until_superseded" },
  relations: [{ relation: "supersedes", target_rule_id: "legacy-branching", target_version: 1 }],
});

const legacyRule = () => ({
  rule_id: "legacy-branching",
  version: 1,
  rule_class: "workflow",
  scope: "shared",
  owner: "joe",
  mandatory: true,
  trigger: { action: ["repo.commit"] },
  control_effect: { control_key: "shared_checkout", effect: "require" },
  binding_text: "Branch inside the shared checkout and coordinate by hand.",
  tests: ["check:legacy-branching"],
  retirement: { behavior: "superseded_only" },
});

const noPhiRule = (evidence = { verified_at: FRESH_EVIDENCE, control_version: "7" }) => ({
  rule_id: "no-phi",
  version: 2,
  rule_class: "code_enforced",
  scope: "global",
  owner: "joe",
  mandatory: true,
  trigger: {},
  control_effect: { control_key: "phi_payload", effect: "forbid" },
  code_enforcement: {
    implementation_ref: "mcp-server/src/global-boundaries.v5.js:evaluatePrivacyBoundary",
    control_id: "global.no_phi",
    control_version: "7",
    resulting_constraint: "No PHI or raw patient-level location may enter any payload.",
    evidence: evidence === null ? null : {
      verifier_id: "ops.ci",
      verified_at: evidence.verified_at,
      control_version: evidence.control_version,
      implementation_digest: `sha256:${"1".repeat(64)}`,
      evidence_digest: `sha256:${"2".repeat(64)}`,
    },
  },
  tests: ["check:no-phi"],
  retirement: { behavior: "permanent_until_superseded" },
});

const sendGateRule = () => ({
  rule_id: "send-gate",
  version: 1,
  rule_class: "workflow",
  scope: "shared",
  owner: "joe",
  mandatory: true,
  trigger: { action: ["document.send"], audience: ["client"] },
  control_effect: { control_key: "client_send", effect: "require" },
  binding_text: "A client-facing document is reviewed by a second seat before it is sent.",
  tests: ["check:client-send-review"],
  retirement: { behavior: "permanent_until_superseded" },
});

const sendGateExceptionRule = () => ({
  rule_id: "send-gate-exception",
  version: 1,
  rule_class: "workflow",
  scope: "shared",
  owner: "joe",
  mandatory: false,
  trigger: { action: ["document.send"] },
  binding_text: "In development, a send rehearsal does not need the second-seat review.",
  tests: ["check:send-gate-exception"],
  retirement: { behavior: "permanent_until_superseded" },
  relations: [{ relation: "exception_to", target_rule_id: "send-gate", target_version: 1 }],
  scoped_validity: { environment: ["development"] },
});

const toneRule = () => ({
  rule_id: "client-review",
  version: 1,
  rule_class: "scoped_judgment",
  scope: "shared",
  owner: "joe",
  mandatory: false,
  trigger: { audience: ["client"] },
  binding_text: "Write to a client the way Joe would: plain, specific, no hedging.",
  no_machine_control_reason: "Only a reader applying context can tell plain from curt.",
  retirement: { behavior: "permanent_until_superseded" },
});

const awayModeRule = () => ({
  rule_id: "away-mode",
  version: 1,
  rule_class: "runtime_state",
  scope: "shared",
  owner: "joe",
  mandatory: true,
  trigger: { actor_class: ["unattended_run"] },
  control_effect: { control_key: "unattended_dispatch", effect: "forbid" },
  binding_text: "Away mode: unattended dispatch is off until Joe returns.",
  retirement: { behavior: "expires_at", expires_at: "2026-09-08T00:00:00Z" },
});

const basePolicy = () => ({
  schema_version: V5_F05_UNIVERSE_SCHEMA_VERSION,
  universe_version: 4,
  tenant: ORGANIZATION_TENANT_ID,
  completeness: "complete_authoritative_universe",
  declared_actions: ["deal.update", "document.send", "repo.commit"],
  declared_resource_classes: ["deal", "document", "repository"],
  rules: [
    worktreeRule(), legacyRule(), noPhiRule(), sendGateRule(),
    sendGateExceptionRule(), toneRule(), awayModeRule(),
  ],
});

const commitFacts = () => ({
  action: "repo.commit",
  actor_class: "sponsored_agent",
  audience: "internal",
  environment: "isolated_worktree",
  lifecycle_transition: "create",
  resource_class: "repository",
  risk_tier: "consequential",
});

const sendFacts = (overrides = {}) => ({
  action: "document.send",
  actor_class: "verified_partner",
  audience: "client",
  environment: "production",
  lifecycle_transition: "send",
  resource_class: "document",
  risk_tier: "consequential",
  ...overrides,
});

const derive = (policy, facts, extra = {}) => deriveRuleApplicability({
  tenant: ORGANIZATION_TENANT_ID,
  universe: compileRuleUniverse(policy),
  facts,
  now: NOW,
  ...extra,
});

// ------------------------------------------- the reviewed decision binding

test("the seven reviewed decisions are bound verbatim and their digest is derived", () => {
  assert.deepEqual([...V5_F05_SETTLED_DECISION_IDS], Object.keys(REVIEWED_DECISION_BINDING).sort());
  for (const [id, expected] of Object.entries(REVIEWED_DECISION_BINDING)) {
    assert.equal(V5_F05_SETTLED_DECISIONS[id].source_evidence_digest, expected);
  }
  assert.equal(assertF05DecisionBinding({
    decision_subset_digest: v5F05DecisionSubsetDigest(),
    decisions: Object.fromEntries(Object.entries(REVIEWED_DECISION_BINDING)
      .map(([id, source_evidence_digest]) => [id, { source_evidence_digest }])),
  }), true);
});

test("a drifted, short or over-long decision subset refuses in both directions", () => {
  const drifted = Object.fromEntries(Object.entries(REVIEWED_DECISION_BINDING)
    .map(([id, source_evidence_digest]) => [id, { source_evidence_digest }]));
  drifted["Q050.D1"] = { source_evidence_digest: "0".repeat(64) };
  assert.equal(code(() => assertF05DecisionBinding({ decisions: drifted })), "decision_binding_drift");

  const short = { ...drifted };
  delete short["Q087.D1"];
  short["Q050.D1"] = { source_evidence_digest: REVIEWED_DECISION_BINDING["Q050.D1"] };
  assert.equal(code(() => assertF05DecisionBinding({ decisions: short })), "decision_binding_drift");

  assert.equal(code(() => assertF05DecisionBinding({
    decision_subset_digest: `sha256:${"9".repeat(64)}`,
    decisions: Object.fromEntries(Object.entries(REVIEWED_DECISION_BINDING)
      .map(([id, source_evidence_digest]) => [id, { source_evidence_digest }])),
  })), "decision_binding_drift");
});

// ----------------------------------------- Q051, the six rule classes

test("Q051 the class table is closed and each class fixes its own enforcement", () => {
  const kernel = v5F05RuleKernelPreimage();
  assert.deepEqual(kernel.rule_classes.map(entry => entry.rule_class), [...V5_F05_RULE_CLASSES]);
  const byClass = Object.fromEntries(kernel.rule_classes.map(entry => [entry.rule_class, entry]));
  assert.equal(byClass.code_enforced.enforcement, "code_control");
  assert.equal(byClass.code_enforced.binding_text_required, false);
  assert.equal(byClass.scoped_judgment.may_be_mandatory, false);
  assert.equal(byClass.preference.may_be_mandatory, false);
  assert.equal(byClass.runtime_state.must_expire, true);
});

test("Q051 a judgment or preference rule may not claim to be mandatory", () => {
  const policy = basePolicy();
  policy.rules.push({
    ...toneRule(), rule_id: "tone-as-control", mandatory: true,
    control_effect: { control_key: "tone", effect: "require" },
  });
  assert.equal(code(() => compileRuleUniverse(policy)), "judgment_rule_cannot_be_mandatory");
});

test("Q051 every required piece of rule metadata is required, one refusal each", () => {
  const cases = [
    ["missing_binding_text", rule => { delete rule.binding_text; }, "worktree-first"],
    ["missing_test_ref", rule => { rule.tests = []; }, "worktree-first"],
    ["missing_control_effect", rule => { delete rule.control_effect; }, "worktree-first"],
    ["unused_control_effect", rule => { rule.mandatory = false; }, "worktree-first"],
    ["unused_code_enforcement", rule => {
      rule.code_enforcement = noPhiRule().code_enforcement;
    }, "worktree-first"],
    ["missing_no_machine_control_reason", rule => { delete rule.no_machine_control_reason; },
      "client-review"],
    ["missing_code_enforcement", rule => { delete rule.code_enforcement; }, "no-phi"],
  ];
  for (const [expected, mutate, rule_id] of cases) {
    const policy = basePolicy();
    mutate(policy.rules.find(rule => rule.rule_id === rule_id));
    assert.equal(code(() => compileRuleUniverse(policy)), expected, expected);
  }
});

test("Q051 a runtime_state rule must expire, and an expired one is retired at derive", () => {
  const policy = basePolicy();
  const away = policy.rules.find(rule => rule.rule_id === "away-mode");
  away.retirement = { behavior: "permanent_until_superseded" };
  assert.equal(code(() => compileRuleUniverse(policy)), "runtime_state_must_expire");

  const receipt = derive(basePolicy(), { ...commitFacts(), actor_class: "unattended_run" });
  assert.deepEqual(receipt.retired.map(entry => entry.rule_id), ["away-mode"]);
  assert.equal(receipt.retired[0].reason_id, "retired_by_expiry");
  assert.ok(!receipt.effective.some(entry => entry.rule_id === "away-mode"));
});

// ------------------------------- Q064, typed applicability and coverage

test("Q064 typed facts decide applicability, and the receipt enumerates the whole universe", () => {
  const receipt = derive(basePolicy(), commitFacts());

  assert.equal(receipt.schema_version, V5_F05_COVERAGE_SCHEMA_VERSION);
  assert.equal(receipt.decision, "allow");
  assert.equal(receipt.reason_id, "coverage_complete");
  assert.equal(receipt.coverage_complete, true);
  assert.equal(receipt.consequential_action_permitted, true);
  assert.deepEqual(receipt.blocking_reasons, []);
  assert.deepEqual(receipt.effects, V5_NO_EFFECTS);

  assert.deepEqual(receipt.effective.map(e => e.rule_id), ["no-phi", "worktree-first"]);
  assert.deepEqual(receipt.superseded.map(e => e.rule_id), ["legacy-branching"]);
  assert.deepEqual(receipt.not_applicable.map(e => e.rule_id).sort(),
    ["client-review", "send-gate", "send-gate-exception"]);
  assert.deepEqual(receipt.retired.map(e => e.rule_id), ["away-mode"]);

  // The partition, asserted rather than trusted: an unlisted rule is
  // indistinguishable from an absent one.
  const buckets = [
    ...receipt.effective, ...receipt.possibly_binding, ...receipt.not_applicable,
    ...receipt.retired, ...receipt.superseded, ...receipt.overridden,
    ...receipt.suppressed_by_exception,
  ].map(entry => entry.rule_id).sort();
  assert.deepEqual(buckets, [...receipt.universe_rule_ids].sort());
  assert.equal(new Set(buckets).size, buckets.length);

  // The reason each applicable rule applied, dimension by dimension.
  const worktree = receipt.effective.find(e => e.rule_id === "worktree-first");
  assert.deepEqual(worktree.matched, [{ dimension: "action", fact_value: "repo.commit",
    matched: ["repo.commit"] }]);
  assert.equal(receipt.effective.find(e => e.rule_id === "no-phi").reason_id, "universal_trigger");
  assert.equal(receipt.not_applicable.find(e => e.rule_id === "client-review").mismatch.dimension,
    "audience");
});

test("Q064/Q065 an unknown fact makes a rule POSSIBLY binding, never absent", () => {
  const facts = commitFacts();
  delete facts.action;
  const receipt = derive(basePolicy(), facts);

  assert.deepEqual(receipt.possibly_binding.map(e => e.rule_id).sort(),
    ["legacy-branching", "send-gate-exception", "worktree-first"]);
  assert.equal(receipt.possibly_binding[0].reason_id, "required_fact_unknown");
  assert.deepEqual(receipt.unknown_facts, [{ dimension: "action", reason_id: "fact_absent" }]);
  assert.equal(receipt.coverage_complete, false);
  assert.equal(receipt.consequential_action_permitted, false);
  assert.equal(receipt.read_only_exploration_permitted, true);
  assert.ok(receipt.blocking_reasons.includes("possible_binding_rule_undecided"));
  assert.ok(receipt.blocking_reasons.includes("typed_facts_unknown"));

  // A possible binding rule is DELIVERED, with its text, exactly like an
  // effective one — that is the half Q065 is about.
  const delivered = receipt.delivery.find(entry => entry.rule_id === "worktree-first");
  assert.equal(delivered.bucket, "possibly_binding");
  assert.equal(delivered.mode, "full_binding_text");
  assert.equal(delivered.omissible, false);
});

test("Q064 the explicit unknown sentinel reads the same as an absent fact", () => {
  const receipt = derive(basePolicy(), { ...commitFacts(), action: V5_F05_UNKNOWN_FACT });
  assert.deepEqual(receipt.unknown_facts,
    [{ dimension: "action", reason_id: "fact_declared_unknown" }]);
  assert.ok(receipt.possibly_binding.some(e => e.rule_id === "worktree-first"));
});

test("Q064 a definite mismatch settles a rule even while another dimension is unknown", () => {
  const facts = sendFacts({ audience: "internal" });
  delete facts.environment;
  const receipt = derive(basePolicy(), facts);
  // send-gate needs audience=client, which is KNOWN and wrong; the unknown
  // environment cannot rescue it into "possibly binding".
  assert.ok(receipt.not_applicable.some(e => e.rule_id === "send-gate"));
  assert.ok(!receipt.possibly_binding.some(e => e.rule_id === "send-gate"));
});

test("Q064 an action outside the declared vocabulary is unknown, not inapplicable", () => {
  const facts = { ...commitFacts(), action: "deal.close" };
  const receipt = derive(basePolicy(), facts);
  assert.deepEqual(receipt.unknown_facts,
    [{ dimension: "action", reason_id: "fact_outside_declared_vocabulary", value: "deal.close" }]);
  assert.ok(receipt.possibly_binding.some(e => e.rule_id === "worktree-first"));
  assert.equal(receipt.consequential_action_permitted, false);
});

test("Q064 an unreadable typed fact is a contract violation, not a policy answer", () => {
  assert.equal(code(() => derive(basePolicy(), { ...commitFacts(), risk_tier: "spicy" })),
    "unknown_risk_tier");
  assert.equal(code(() => derive(basePolicy(), { ...commitFacts(), audience: 7 })), "invalid_shape");
  assert.equal(code(() => derive(basePolicy(), { ...commitFacts(), weather: "fine" })), "unknown_field");
});

test("Q064 a partial universe cannot support a consequential action", () => {
  const policy = basePolicy();
  policy.completeness = "partial_unknown_coverage";
  const receipt = derive(policy, commitFacts());
  assert.equal(receipt.coverage_complete, false);
  assert.equal(receipt.consequential_action_permitted, false);
  assert.deepEqual(receipt.blocking_reasons, ["universe_coverage_unknown"]);
  assert.equal(receipt.read_only_exploration_permitted, true);
});

// ------------------------- Q064, semantic retrieval adds and never removes

test("Q064 semantic retrieval adds guidance and cannot remove a control", () => {
  const withoutRetrieval = derive(basePolicy(), sendFacts());
  const withRetrieval = derive(basePolicy(), sendFacts(), {
    semantic_candidates: [
      { rule_id: "worktree-first", reason: "the task mentions a commit", similarity: 0.71 },
      { rule_id: "client-review", reason: "already bound, restated by retrieval" },
    ],
  });

  // The effective set is byte-identical: retrieval is read after it is final.
  assert.deepEqual(withRetrieval.effective.map(e => e.rule_id),
    withoutRetrieval.effective.map(e => e.rule_id));
  assert.equal(withRetrieval.semantic_may_remove_controls, false);

  const addition = withRetrieval.semantic_additions.find(e => e.rule_id === "worktree-first");
  assert.equal(addition.elevates_to_control, false);
  assert.equal(addition.delivered_as, "guidance_only");
  assert.equal(addition.omissible, true);
  assert.deepEqual(withRetrieval.semantic_reinforcements.map(e => e.rule_id), ["client-review"]);
});

test("Q064 retrieval that omits a bound control changes nothing about it", () => {
  const receipt = derive(basePolicy(), sendFacts(), {
    semantic_candidates: [{ rule_id: "client-review", reason: "tone" }],
  });
  // send-gate was never retrieved and is still effective and still delivered.
  assert.ok(receipt.effective.some(e => e.rule_id === "send-gate"));
  assert.ok(receipt.delivery.some(e => e.rule_id === "send-gate" && e.mode === "full_binding_text"));
});

test("Q064 retrieval cannot suppress, and cannot invent a rule", () => {
  assert.equal(code(() => derive(basePolicy(), sendFacts(), {
    semantic_candidates: [{ rule_id: "send-gate", reason: "not needed here", suppress: true }],
  })), "caller_assertion_field_refused");
  assert.equal(code(() => derive(basePolicy(), sendFacts(), {
    semantic_candidates: [{ rule_id: "invented-rule", reason: "felt relevant" }],
  })), "semantic_candidate_unknown_rule");
});

// ------------------------------------- Q087, the relation graph

test("Q087 an exception fires inside its scope, and only inside it", () => {
  const inside = derive(basePolicy(), sendFacts({ environment: "development" }));
  assert.deepEqual(inside.suppressed_by_exception.map(e => e.rule_id), ["send-gate"]);
  assert.equal(inside.suppressed_by_exception[0].by_rule_id, "send-gate-exception");
  assert.equal(inside.suppressed_by_exception[0].reason_id, "removed_by_exception_to");
  assert.ok(!inside.effective.some(e => e.rule_id === "send-gate"));
  assert.equal(inside.consequential_action_permitted, true);

  const outside = derive(basePolicy(), sendFacts({ environment: "production" }));
  assert.deepEqual(outside.suppressed_by_exception, []);
  assert.ok(outside.effective.some(e => e.rule_id === "send-gate"));
});

test("Q087 an exception whose own scope is unknown removes nothing and blocks", () => {
  const facts = sendFacts();
  delete facts.environment;
  const receipt = derive(basePolicy(), facts);
  assert.ok(receipt.effective.some(e => e.rule_id === "send-gate"));
  assert.deepEqual(receipt.pending_relations, [{
    rule_id: "send-gate-exception", relation: "exception_to", target_rule_id: "send-gate",
    reason_id: "exception_scope_unknown", undecided_dimensions: ["environment"],
  }]);
  assert.equal(receipt.consequential_action_permitted, false);
  assert.ok(receipt.blocking_reasons.includes("relation_resolution_pending"));
});

test("Q087 a superseded rule leaves the effective set and says who removed it", () => {
  const receipt = derive(basePolicy(), commitFacts());
  assert.deepEqual(receipt.superseded, [{
    rule_id: "legacy-branching", version: 1, rule_class: "workflow", scope: "shared", owner: "joe",
    mandatory: true, reason_id: "removed_by_supersedes", by_rule_id: "worktree-first",
    matched: [{ dimension: "action", fact_value: "repo.commit", matched: ["repo.commit"] }],
  }]);
});

test("Q087 a dangling, drifted, self or cyclic relation refuses at compile", () => {
  const dangling = basePolicy();
  dangling.rules.find(r => r.rule_id === "worktree-first").relations =
    [{ relation: "supersedes", target_rule_id: "never-existed", target_version: 1 }];
  assert.equal(code(() => compileRuleUniverse(dangling)), "dangling_relation");

  const drifted = basePolicy();
  drifted.rules.find(r => r.rule_id === "worktree-first").relations =
    [{ relation: "supersedes", target_rule_id: "legacy-branching", target_version: 9 }];
  assert.equal(code(() => compileRuleUniverse(drifted)), "relation_version_mismatch");

  const self = basePolicy();
  self.rules.find(r => r.rule_id === "worktree-first").relations =
    [{ relation: "supersedes", target_rule_id: "worktree-first", target_version: 3 }];
  assert.equal(code(() => compileRuleUniverse(self)), "self_relation");

  const cyclic = basePolicy();
  cyclic.rules.find(r => r.rule_id === "legacy-branching").relations =
    [{ relation: "overrides", target_rule_id: "worktree-first", target_version: 3 }];
  assert.equal(code(() => compileRuleUniverse(cyclic)), "relation_cycle");
});

test("Q087 identical triggers that require and forbid one control refuse at compile", () => {
  const policy = basePolicy();
  policy.rules.push({
    rule_id: "no-worktree",
    version: 1, rule_class: "workflow", scope: "shared", owner: "dell", mandatory: true,
    trigger: { action: ["repo.commit"] },
    control_effect: { control_key: "isolated_worktree", effect: "forbid" },
    binding_text: "Commit in the shared checkout.",
    tests: ["check:no-worktree"],
    retirement: { behavior: "permanent_until_superseded" },
  });
  assert.equal(code(() => compileRuleUniverse(policy)), "unresolved_binding_conflict");
});

test("Q087 a conflict that only appears at fact time refuses the receipt, and no model picks", () => {
  const policy = basePolicy();
  policy.rules.push({
    rule_id: "send-freeze",
    version: 1, rule_class: "workflow", scope: "shared", owner: "joe", mandatory: true,
    // A DIFFERENT trigger, so the compiler cannot see the overlap; the facts can.
    trigger: { risk_tier: ["consequential"] },
    control_effect: { control_key: "client_send", effect: "forbid" },
    binding_text: "Client sends are frozen during the migration window.",
    tests: ["check:send-freeze"],
    retirement: { behavior: "permanent_until_superseded" },
  });
  const receipt = derive(policy, sendFacts());
  assert.equal(receipt.decision, "refuse");
  assert.equal(receipt.reason_id, "unresolved_binding_conflict");
  assert.deepEqual(receipt.binding_conflicts, [{
    control_key: "client_send", reason_id: "unresolved_binding_conflict",
    require_rule_ids: ["send-gate"], forbid_rule_ids: ["send-freeze"], resolved_by_model: false,
  }]);
  assert.equal(receipt.model_resolves_conflicts, false);
  assert.equal(receipt.consequential_action_permitted, false);
  assert.equal(receipt.read_only_exploration_permitted, false);
});

test("Q087 an exception with no scoped validity is a silent repeal and refuses", () => {
  const policy = basePolicy();
  delete policy.rules.find(r => r.rule_id === "send-gate-exception").scoped_validity;
  assert.equal(code(() => compileRuleUniverse(policy)), "exception_without_scoped_validity");
});

// ---------------------------------- Q066, what the model actually receives

test("Q066 an interpreted rule arrives with its full binding text, not its summary", () => {
  const universe = compileRuleUniverse(basePolicy());
  const projected = projectRuleForModel({ universe, rule_id: "worktree-first", now: NOW });
  assert.equal(projected.mode, "full_binding_text");
  assert.equal(projected.delivered, true);
  assert.equal(projected.binding_text, worktreeRule().binding_text);
  assert.equal(projected.summary_is_navigation_only, true);
  assert.equal(projected.resulting_constraint, null);
  assert.equal(projected.estimated_tokens, estimateTokens(worktreeRule().binding_text));
});

test("Q066 a code-enforced control is delivered as its constraint, bound to the exact control", () => {
  const universe = compileRuleUniverse(basePolicy());
  const projected = projectRuleForModel({ universe, rule_id: "no-phi", now: NOW });
  assert.equal(projected.mode, "code_enforced_constraint");
  assert.equal(projected.binding_text, null);
  assert.equal(projected.resulting_constraint,
    "No PHI or raw patient-level location may enter any payload.");
  assert.equal(projected.enforced_control.control_id, "global.no_phi");
  assert.equal(projected.enforced_control.control_version, "7");
  assert.equal(projected.enforced_control.implementation_ref,
    "mcp-server/src/global-boundaries.v5.js:evaluatePrivacyBoundary");
  assert.equal(projected.enforced_control.evidence_age_seconds, 3600);
});

test("Q066 missing, stale or mismatched enforcement evidence fails closed", () => {
  const missing = basePolicy();
  missing.rules[missing.rules.findIndex(r => r.rule_id === "no-phi")] = noPhiRule(null);
  const missingReceipt = derive(missing, commitFacts());
  assert.equal(missingReceipt.decision, "refuse");
  assert.deepEqual(missingReceipt.delivery_refusals,
    [{ rule_id: "no-phi", reason_id: "code_enforcement_evidence_missing" }]);
  assert.equal(missingReceipt.consequential_action_permitted, false);

  const stale = basePolicy();
  stale.rules[stale.rules.findIndex(r => r.rule_id === "no-phi")] =
    noPhiRule({ verified_at: ONE_SECOND_PAST_BOUND, control_version: "7" });
  const staleDelivery = projectRuleForModel({
    universe: compileRuleUniverse(stale), rule_id: "no-phi", now: NOW });
  assert.equal(staleDelivery.mode, "refused");
  assert.equal(staleDelivery.reason_id, "code_enforcement_evidence_stale");
  assert.equal(staleDelivery.evidence_age_seconds, V5_F05_MAX_ENFORCEMENT_EVIDENCE_AGE_SECONDS + 1);

  const atBound = basePolicy();
  atBound.rules[atBound.rules.findIndex(r => r.rule_id === "no-phi")] =
    noPhiRule({ verified_at: EXACTLY_AT_BOUND, control_version: "7" });
  assert.equal(projectRuleForModel({
    universe: compileRuleUniverse(atBound), rule_id: "no-phi", now: NOW }).mode,
    "code_enforced_constraint");

  const drifted = basePolicy();
  drifted.rules[drifted.rules.findIndex(r => r.rule_id === "no-phi")] =
    noPhiRule({ verified_at: FRESH_EVIDENCE, control_version: "6" });
  const driftedDelivery = projectRuleForModel({
    universe: compileRuleUniverse(drifted), rule_id: "no-phi", now: NOW });
  assert.equal(driftedDelivery.reason_id, "code_enforcement_evidence_version_mismatch");
  assert.equal(driftedDelivery.declared_control_version, "7");
  assert.equal(driftedDelivery.verified_control_version, "6");
});

test("Q066 a caller cannot assert enforcement instead of evidencing it", () => {
  const policy = basePolicy();
  policy.rules.find(r => r.rule_id === "no-phi").enforced = true;
  assert.equal(code(() => compileRuleUniverse(policy)), "caller_assertion_field_refused");

  const verified = basePolicy();
  verified.rules.find(r => r.rule_id === "worktree-first").verified = true;
  assert.equal(code(() => compileRuleUniverse(verified)), "caller_assertion_field_refused");

  const granted = basePolicy();
  granted.rules.find(r => r.rule_id === "worktree-first").authority_override = "joe";
  assert.equal(code(() => compileRuleUniverse(granted)), "caller_authority_field_refused");
});

// -------------------------------- reproducibility and forgery resistance

test("the same universe and facts reproduce the same digests, byte for byte", () => {
  const first = compileRuleUniverse(basePolicy());
  const second = compileRuleUniverse(basePolicy());
  assert.equal(first.universe_digest, second.universe_digest);
  assert.equal(first.universe_digest, digest(ruleUniversePreimage(first)));

  const a = derive(basePolicy(), commitFacts());
  const b = derive(basePolicy(), commitFacts());
  assert.equal(a.receipt_digest, b.receipt_digest);
  assert.equal(verifyCoverageReceipt(a), true);

  // Rule ORDER in the policy is not part of the universe's identity.
  const reordered = basePolicy();
  reordered.rules.reverse();
  assert.equal(compileRuleUniverse(reordered).universe_digest, first.universe_digest);
});

test("a hand-forged or edited compiled universe is refused, digest recomputed not trusted", () => {
  const compiled = compileRuleUniverse(basePolicy());
  const clone = structuredClone(compiled);
  assert.equal(requireCompiledUniverse(clone).universe_digest, compiled.universe_digest);

  clone.rules[0].binding_text = "anything I like";
  assert.equal(code(() => requireCompiledUniverse(clone)), "universe_digest_mismatch");

  const reordered = structuredClone(compiled);
  reordered.removal_order = [...reordered.removal_order].reverse();
  assert.equal(code(() => requireCompiledUniverse(reordered)), "universe_digest_mismatch");

  assert.equal(code(() => requireCompiledUniverse({
    compiled: true, schema_version: V5_F05_UNIVERSE_SCHEMA_VERSION, universe_version: 1,
    tenant: ORGANIZATION_TENANT_ID, completeness: "complete_authoritative_universe",
    declared_actions: ["x"], declared_resource_classes: ["y"], rules: [], removal_order: [],
    universe_digest: compiled.universe_digest,
  })), "universe_digest_mismatch");

  // A prototype-only "clone" inherits every value and owns none of them.
  assert.equal(code(() => requireCompiledUniverse(Object.create(compiled))), "invalid_shape");
});

test("an accessor, a prototype key or a symbol key is refused rather than read", () => {
  const accessor = basePolicy();
  Object.defineProperty(accessor.rules[0], "scope", {
    get: () => "shared", enumerable: true, configurable: true,
  });
  assert.equal(code(() => compileRuleUniverse(accessor)), "accessor_property_refused");

  const polluted = JSON.parse(
    JSON.stringify(basePolicy()).replace('"rules":[{', '"rules":[{"__proto__":{"x":1},'));
  assert.equal(code(() => compileRuleUniverse(polluted)), "prototype_key_refused");

  const symbolic = basePolicy();
  symbolic.rules[0][Symbol("hidden")] = true;
  assert.equal(code(() => compileRuleUniverse(symbolic)), "symbol_key_refused");
});

test("a coverage receipt that was edited no longer hashes to its own digest", () => {
  const receipt = derive(basePolicy(), commitFacts());
  const forged = { ...receipt, consequential_action_permitted: true, effective: [] };
  assert.equal(code(() => verifyCoverageReceipt(forged)), "coverage_receipt_digest_mismatch");
});

test("identity strings are checked, not merely typed", () => {
  const invisible = basePolicy();
  // Written as an escape, never as a literal: a zero-width space in the source
  // would make the test that refuses invisible characters itself unreadable.
  invisible.rules[0].rule_id = `worktree${String.fromCodePoint(0x200B)}first`;
  assert.equal(code(() => compileRuleUniverse(invisible)), "unsafe_unicode");

  const untrimmed = basePolicy();
  untrimmed.rules[0].scope = " shared";
  assert.equal(code(() => compileRuleUniverse(untrimmed)), "untrimmed_text");

  const carriage = basePolicy();
  carriage.rules[0].binding_text = "line one\r\nline two";
  assert.equal(code(() => compileRuleUniverse(carriage)), "unsafe_unicode");

  const duplicated = basePolicy();
  duplicated.rules.push(worktreeRule());
  assert.equal(code(() => compileRuleUniverse(duplicated)), "duplicate_rule");
});

// ----------------------------------------- the closed kernel projection

test("the kernel projection is closed, hashed and states what it does not do", () => {
  const preimage = v5F05RuleKernelPreimage();
  assert.equal(v5F05RuleKernelDigest(), digest(preimage));
  assert.equal(v5F05RuleKernelCanonicalBytes(), canonicalJson(preimage));
  assert.deepEqual(JSON.parse(v5F05RuleKernelCanonicalBytes()).decisions.map(d => d.decision_id),
    [...V5_F05_SETTLED_DECISION_IDS]);
  assert.equal(preimage.semantic_retrieval_may_remove_controls, false);
  assert.equal(preimage.model_resolves_conflicts, false);
  assert.deepEqual(preimage.fact_dimensions.map(d => d.dimension), [...V5_F05_FACT_DIMENSIONS]);
  assert.deepEqual(preimage.relations, [...V5_F05_RELATIONS]);
});

test("the unbuilt runtime seams are named and fail closed", () => {
  const gaps = ruleKernelIntegrationGaps();
  assert.ok(gaps.length >= 4);
  assert.ok(gaps.every(gap => gap.landed !== true));
  assert.equal(code(() => assertRuleKernelIntegrationComplete()), "kernel_integration_incomplete");
});
