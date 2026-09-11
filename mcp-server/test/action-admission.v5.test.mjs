// V5-F05 — ACTION ADMISSION, the join between a manifest, its coverage receipt
// and the action about to run.
//
// The suite is built around one fact that is stated rather than worked around:
// `admitConsequentialAction` cannot return `allow` in this repository, because
// its last check needs a verifier attestation nothing issues. So the FIRST test
// walks the whole ladder on a clean pair and asserts that every check up to the
// last one PASSED and that the last one is the only unmet term. Every refusal
// below is a mutation of that same clean pair, and each asserts WHICH check
// stopped the walk — not merely that something refused, which would be true of
// every input including the clean one.

import test from "node:test";
import assert from "node:assert/strict";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5F05Error,
  V5_F05_UNIVERSE_SCHEMA_VERSION,
  compileRuleUniverse,
  deriveRuleApplicability,
} from "../src/rule-applicability.v5.js";
import {
  V5_F05_MANIFEST_SCHEMA_VERSION,
  V5_F05_ATTESTATION_SCHEMA_VERSION,
  V5_F05_UNTRUSTED_FORBIDDEN_USES,
  assembleContextManifest,
  authenticateRuntimeProjection,
  compileTaintLineage,
  freezeAssemblyInput,
  verifierAttestationDigest,
} from "../src/context-assembly.v5.js";
import {
  V5_NO_EFFECTS,
  V5_F05_ADMISSION_SCHEMA_VERSION,
  V5_F05_ADMISSION_ARGUMENT_USE,
  V5_F05_ADMISSION_AUTHENTICATED_PROJECTION_EMITTED,
  V5_F05_ADMISSION_CHECKS,
  V5_F05_ADMISSION_REASON_IDS,
  V5_F05_ADMISSION_WRITE_GATE_FIELD,
  actionAdmissionGaps,
  admitConsequentialAction,
  assertActionAdmissionComplete,
  verifyAdmissionDecision,
  v5F05AdmissionContractPreimage,
  v5F05AdmissionContractDigest,
  v5F05AdmissionContractCanonicalBytes,
} from "../src/action-admission.v5.js";

// --------------------------------------------------------------- fixtures
//
// Deliberately the same shapes the assembler suite proves, so a case here is
// about admission and not about whether a manifest assembles.

const NOW = "2026-09-09T12:00:00Z";              // assembly instant
const ADMIT_AT = "2026-09-09T12:05:00Z";         // 300s later
const OBSERVED = "2026-09-09T11:45:00Z";
const FRESH_EVIDENCE = "2026-09-09T11:00:00Z";

const sha = ch => `sha256:${ch.repeat(64)}`;

const code = fn => {
  try { fn(); } catch (error) {
    return error instanceof V5F05Error ? error.code : `not-a-V5F05Error:${error}`;
  }
  return "no-throw";
};

const JOE = {
  slug: "joe", display: "Joe", human: true, via: "oauth-google",
  client_id: null, sponsoring_human_slug: null, human_slug: null, sponsor_required: false,
};

const ruleProv = (source_record_id, ch) => ({
  source_record_id, source_version: 1, source_content_digest: sha(ch),
  retrieved_at: OBSERVED,
});
const ruleSourceRecord = (record_id, ch) => ({
  record_id, record_kind: "rule", version: 1, content_digest: sha(ch),
  origin: "record_layer", derived_kind: "primary", derived_from: [], query_id: "q-deal",
  observed_at: OBSERVED, estimated_tokens: 5, omissible: false, backs_control: true,
  provenance: { source_id: "src-neon", retrieval_class: "typed_read" },
});

const universePolicy = (overrides = {}) => ({
  schema_version: V5_F05_UNIVERSE_SCHEMA_VERSION,
  universe_version: 2,
  tenant: ORGANIZATION_TENANT_ID,
  completeness: "complete_authoritative_universe",
  declared_actions: ["deal.update", "document.send"],
  declared_resource_classes: ["deal", "document", "tour"],
  rules: [
    {
      rule_id: "no-phi",
      version: 2, rule_class: "code_enforced", scope: "global", owner: "joe", mandatory: true,
      trigger: {},
      control_effect: { control_key: "phi_payload", effect: "forbid" },
      binding_text: "No PHI and no raw patient-level location may enter any payload.",
      code_enforcement: {
        implementation_ref: "mcp-server/src/global-boundaries.v5.js:evaluatePrivacyBoundary",
        control_id: "global.no_phi",
        control_version: "7",
        resulting_constraint: "No PHI or raw patient-level location may enter any payload.",
        evidence: {
          verifier_id: "ops.ci", verified_at: FRESH_EVIDENCE, control_version: "7",
          implementation_digest: sha("1"), evidence_digest: sha("2"),
        },
      },
      tests: ["check:no-phi"],
      retirement: { behavior: "permanent_until_superseded" },
      provenance: ruleProv("r-rule-no-phi", "c"),
    },
    {
      rule_id: "client-send-gate",
      version: 1, rule_class: "workflow", scope: "shared", owner: "joe", mandatory: true,
      trigger: { action: ["document.send"], audience: ["client"] },
      control_effect: { control_key: "client_send", effect: "require" },
      binding_text: "A client-facing document is reviewed by a second seat before it is sent.",
      summary: "second-seat review before a client send",
      tests: ["check:client-send-review"],
      retirement: { behavior: "permanent_until_superseded" },
      provenance: ruleProv("r-rule-send-gate", "d"),
    },
  ],
  ...overrides,
});

const facts = (overrides = {}) => ({
  action: "document.send",
  actor_class: "verified_partner",
  audience: "client",
  environment: "production",
  lifecycle_transition: "send",
  resource_class: "document",
  risk_tier: "consequential",
  ...overrides,
});

const controls = () => ({
  deal_owner_slug: "joe",
  account_slug: "joe",
  policy_scope: ["business.send_client_document"],
  capabilities: ["document.send"],
});

const queries = () => ([
  { query_id: "q-deal", query_kind: "deal_by_id", parameters_digest: sha("7"),
    retrieved_at: OBSERVED },
  { query_id: "q-mail", query_kind: "thread_by_deal", parameters_digest: sha("8"),
    retrieved_at: OBSERVED },
]);

const sources = () => ([
  { source_id: "src-neon", state: "available", required_for_task: true },
  { source_id: "src-outlook", state: "available", required_for_task: false },
]);

/**
 * `r-deal` is first-party and `r-email` came off a connector, so the two
 * argument cases below differ in exactly one property: taint.
 */
const records = () => ([
  {
    record_id: "r-deal", record_kind: "operating_fact", version: 4, content_digest: sha("3"),
    origin: "record_layer", derived_kind: "primary", derived_from: [], query_id: "q-deal",
    observed_at: OBSERVED, max_age_seconds: 3600, estimated_tokens: 120,
    omissible: false, backs_control: true,
    provenance: { source_id: "src-neon", retrieval_class: "typed_read", evidence_ref: "deal/8812" },
  },
  {
    record_id: "r-email", record_kind: "message", version: 1, content_digest: sha("4"),
    origin: "email", derived_kind: "primary", derived_from: [], query_id: "q-mail",
    observed_at: OBSERVED, estimated_tokens: 300, omissible: true,
    provenance: { source_id: "src-outlook", retrieval_class: "connector_fetch" },
  },
  ruleSourceRecord("r-rule-no-phi", "c"),
  ruleSourceRecord("r-rule-send-gate", "d"),
]);

const assemblyRequest = (overrides = {}) => ({
  schema_version: V5_F05_MANIFEST_SCHEMA_VERSION,
  tenant: ORGANIZATION_TENANT_ID,
  now: NOW,
  mode: "consequential_action_proposal",
  actor: JOE,
  task: {
    task_id: "t-9001",
    title: "Send the executed LOI to the client",
    boundary_action: "business.send_client_document",
    facts: facts(),
  },
  controls: controls(),
  universe: compileRuleUniverse(universePolicy()),
  records: records(),
  sources: sources(),
  queries: queries(),
  ...overrides,
});

const assemble = (overrides = {}) =>
  assembleContextManifest(freezeAssemblyInput(assemblyRequest(overrides)));

/** The receipt the assembler derived for that manifest, derived the same way. */
const receiptFor = (overrides = {}) => {
  const request = assemblyRequest(overrides);
  return deriveRuleApplicability({
    tenant: ORGANIZATION_TENANT_ID,
    universe: request.universe,
    facts: request.task.facts,
    now: request.now,
  });
};

const lineageFor = (overrides = {}) => compileTaintLineage(assemblyRequest(overrides).records);

const admissionRequest = (overrides = {}) => ({
  schema_version: V5_F05_ADMISSION_SCHEMA_VERSION,
  tenant: ORGANIZATION_TENANT_ID,
  now: ADMIT_AT,
  manifest: assemble(),
  receipt: receiptFor(),
  proposed_action: {
    action: "business.send_client_document",
    risk_tier: "consequential",
  },
  ...overrides,
});

const admit = (overrides = {}) => admitConsequentialAction(admissionRequest(overrides));

/** The state of one named check on a decision. */
const stateOf = (decision, check) =>
  decision.checks.find(entry => entry.check === check).state;

// ------------------------------------------------ the ladder that must walk

test("the clean pair walks every check and stops only at the missing verifier", () => {
  const decision = admit();

  // The headline, stated rather than worked around.
  assert.equal(decision.decision, "refuse");
  assert.equal(decision.admitted, false);
  assert.equal(decision.reason_id, "no_runtime_projection_supplied");

  // And the part that makes this suite worth anything: EVERY check before the
  // last one really ran and really passed. A regression that starts refusing
  // earlier fails here rather than hiding behind the same final reason_id.
  const before = V5_F05_ADMISSION_CHECKS.slice(0, -1);
  assert.deepEqual(before.map(check => stateOf(decision, check)),
    before.map(() => "pass"));
  assert.equal(stateOf(decision, "authenticated_runtime_projection"), "refuse");
  assert.deepEqual(decision.unmet_admission_terms, ["authenticated_runtime_projection"]);
  assert.equal(decision.execution_gap_id, "no_registered_verifier");

  // The bindings this module exists to make, carried in the record.
  assert.equal(decision.action, "business.send_client_document");
  assert.equal(decision.assembled_action, "business.send_client_document");
  assert.equal(decision.coverage_receipt_digest, decision.manifest_coverage_receipt_digest);
  assert.equal(decision.manifest_mode, "consequential_action_proposal");
  assert.equal(decision.gate_applies, true);
  assert.equal(decision.manifest_age_seconds, 300);
  assert.equal(decision.manifest_age_policy_supplied, false);
  assert.equal(decision.manifest_max_age_seconds, null);

  // Nothing here is a second opinion, and the record says whose opinion it is.
  assert.equal(decision.write_gate_field_read, "consequential_action_permitted");
  assert.equal(decision.taint_decided_by, "context-assembly.v5.evaluateUntrustedUse");
  assert.equal(decision.coverage_decided_by, "rule-applicability.v5.deriveRuleApplicability");
  assert.equal(decision.authority_decided_by, "global-boundaries.v5.evaluateActorAuthority");
  assert.equal(decision.authenticated_runtime_projection_available, false);
  assert.equal(decision.authenticated_by_caller_boolean, false);
  assert.equal(decision.enforced_at_a_call_site, false);
  assert.deepEqual(decision.effects, V5_NO_EFFECTS);
  assert.equal(verifyAdmissionDecision(decision), true);
});

test("the fixtures really are the manifest's own receipt and lineage", () => {
  // Guards this whole suite: if these two ever stopped matching, every binding
  // test below would refuse for the wrong reason and still look green.
  const manifest = assemble();
  assert.equal(receiptFor().receipt_digest, manifest.coverage_receipt_digest);
  assert.equal(canonicalJson(lineageFor().entries), canonicalJson(manifest.taint_lineage));
});

test("the same pair admits the same way, byte for byte", () => {
  const first = admit();
  const second = admit();
  assert.equal(first.admission_digest, second.admission_digest);
  assert.equal(canonicalJson(first), canonicalJson(second));
});

test("an edited decision no longer hashes to its own digest", () => {
  const decision = admit();
  assert.equal(code(() => verifyAdmissionDecision({ ...decision, admitted: true })),
    "admission_digest_mismatch");
  assert.equal(code(() => verifyAdmissionDecision({ ...decision, reason_id: "action_admitted" })),
    "admission_digest_mismatch");
});

// ------------------------------------------------- the bindings, one by one

test("a receipt that is not this manifest's refuses before any permission is read", () => {
  // A receipt derived over a DIFFERENT fact set: individually honest, and not
  // the receipt this manifest was assembled with.
  const other = deriveRuleApplicability({
    tenant: ORGANIZATION_TENANT_ID,
    universe: compileRuleUniverse(universePolicy()),
    facts: facts({ audience: "internal", risk_tier: "consequential" }),
    now: NOW,
  });
  const manifest = assemble();
  assert.notEqual(other.receipt_digest, manifest.coverage_receipt_digest);
  // Same rule universe, so it clears the coarse check and is answered by the
  // exact one — which is the check this case exists to prove.
  assert.equal(other.universe_digest, manifest.universe_digest);
  // It permits the action on its own terms, which is what makes the swap worth
  // attempting and the refusal worth having.
  assert.equal(other.consequential_action_permitted, true);

  const decision = admit({ receipt: other });
  assert.equal(decision.reason_id, "coverage_receipt_not_the_manifests");
  assert.equal(stateOf(decision, "universe_binds_manifest"), "pass");
  assert.equal(stateOf(decision, "receipt_binds_manifest"), "refuse");
  assert.equal(stateOf(decision, "coverage_permits_the_action"), "not_reached");
  assert.equal(decision.detail.presented_receipt_digest, other.receipt_digest);
});

test("a receipt compiled over a different rule universe refuses at the universe check", () => {
  // Same facts, universe_version moved on: the receipt is honest about a rule
  // set the manifest never saw.
  const manifest = assemble();
  const otherUniverse = compileRuleUniverse(universePolicy({ universe_version: 3 }));
  const other = deriveRuleApplicability({
    tenant: ORGANIZATION_TENANT_ID, universe: otherUniverse, facts: facts(), now: NOW,
  });
  assert.notEqual(other.universe_digest, manifest.universe_digest);
  assert.equal(other.consequential_action_permitted, true);

  const decision = admit({ receipt: other });
  assert.equal(decision.reason_id, "rule_universe_not_the_manifests");
  assert.equal(stateOf(decision, "universe_binds_manifest"), "refuse");
  assert.equal(stateOf(decision, "receipt_binds_manifest"), "not_reached");
  assert.equal(decision.detail.receipt_universe_digest, other.universe_digest);
});

test("a different action cannot ride a manifest assembled for this one", () => {
  const decision = admit({
    proposed_action: { action: "business.update_deal", risk_tier: "consequential" },
  });
  assert.equal(decision.reason_id, "action_not_the_assembled_action");
  assert.equal(stateOf(decision, "action_is_the_assembled_action"), "refuse");
  assert.equal(decision.detail.assembled_action, "business.send_client_document");
  assert.equal(decision.detail.proposed_action, "business.update_deal");
  // Everything after it is unestablished, and the record says so rather than
  // implying the rest was checked and passed.
  assert.equal(stateOf(decision, "coverage_permits_the_action"), "not_reached");
  assert.ok(decision.unmet_admission_terms.includes("manifest_permits_the_action"));
});

test("an action executed at a higher risk tier than it was assembled at refuses", () => {
  const decision = admit({
    proposed_action: {
      action: "business.send_client_document", risk_tier: "irreversible",
    },
  });
  assert.equal(decision.reason_id, "risk_tier_not_the_assembled_risk");
  assert.equal(stateOf(decision, "risk_tier_is_the_assembled_risk"), "refuse");
  assert.equal(decision.detail.assembled_risk_tier, "consequential");
  assert.equal(decision.detail.proposed_risk_tier, "irreversible");
});

test("a routine action is outside this gate, and the answer says so", () => {
  // Assembled AND proposed at "routine", so the tiers agree and the next check
  // is the one that fires. A refusal here is not a prohibition.
  const decision = admit({
    manifest: assemble({ task: {
      task_id: "t-9001", title: "Send the executed LOI to the client",
      boundary_action: "business.send_client_document", facts: facts({ risk_tier: "routine" }),
    } }),
    receipt: receiptFor({ task: {
      task_id: "t-9001", title: "Send the executed LOI to the client",
      boundary_action: "business.send_client_document", facts: facts({ risk_tier: "routine" }),
    } }),
    proposed_action: { action: "business.send_client_document", risk_tier: "routine" },
  });
  assert.equal(decision.reason_id, "risk_tier_not_consequential");
  assert.equal(stateOf(decision, "risk_tier_is_the_assembled_risk"), "pass");
  assert.equal(stateOf(decision, "risk_tier_is_consequential"), "refuse");
  assert.equal(decision.gate_applies, false);
});

test("an exploration manifest cannot be upgraded into a write permission", () => {
  const decision = admit({
    manifest: assemble({ mode: "read_only_exploration" }),
  });
  assert.equal(decision.reason_id, "manifest_mode_is_read_only_exploration");
  assert.equal(stateOf(decision, "mode_admits_a_write"), "refuse");
  assert.equal(stateOf(decision, "action_is_the_assembled_action"), "not_reached");
});

test("an artifact naming a write gate this module cannot read refuses first of all", () => {
  // Version drift on the manifest half. Reading `decision` instead is the exact
  // mistake both halves warn about, so an unknown gate name refuses.
  const manifest = assemble();
  const drifted = { ...manifest, write_gate_field: "decision" };
  const rehashed = { ...drifted };
  // The digest is recomputed so this is a GATE-NAME refusal and not a forgery
  // refusal — a negative that fires for the wrong reason proves nothing.
  const { manifest_digest: _d, effects: _e, ...body } = drifted;
  rehashed.manifest_digest = digest(body);

  const decision = admitConsequentialAction(admissionRequest({ manifest: rehashed }));
  assert.equal(decision.reason_id, "unknown_write_gate_field");
  assert.equal(stateOf(decision, "write_gate_field_known"), "refuse");
  assert.equal(stateOf(decision, "receipt_binds_manifest"), "not_reached");
  assert.equal(decision.detail.manifest_write_gate_field, "decision");
});

// ------------------------------------------------------------- freshness

test("a manifest assembled after the admission instant refuses", () => {
  const decision = admit({ now: "2026-09-09T11:59:00Z" });
  assert.equal(decision.reason_id, "manifest_assembled_after_admission");
  assert.equal(stateOf(decision, "manifest_freshness"), "refuse");
  assert.equal(decision.manifest_age_seconds, -60);
});

test("manifest age is the caller's policy or it is no policy", () => {
  // No policy: the age is reported and no verdict is drawn from it.
  const silent = admit();
  assert.equal(silent.manifest_age_seconds, 300);
  assert.equal(silent.manifest_age_policy_supplied, false);
  assert.equal(stateOf(silent, "manifest_freshness"), "pass");

  // A policy the manifest meets changes nothing.
  const within = admit({ manifest_max_age_seconds: 600 });
  assert.equal(stateOf(within, "manifest_freshness"), "pass");
  assert.equal(within.manifest_age_policy_supplied, true);

  // A policy it exceeds refuses, and the refusal names both numbers.
  const stale = admit({ manifest_max_age_seconds: 120 });
  assert.equal(stale.reason_id, "manifest_stale");
  assert.equal(stateOf(stale, "manifest_freshness"), "refuse");
  assert.equal(stale.detail.manifest_age_seconds, 300);
  assert.equal(stale.detail.max_age_seconds, 120);

  // And no window is invented anywhere: the contract says so in the record.
  assert.equal(v5F05AdmissionContractPreimage().manifest_freshness_default_window_seconds, null);
});

// ------------------------------------------------------ argument records

test("a first-party record this manifest carries may be an action argument", () => {
  const decision = admit({
    lineage: lineageFor(),
    proposed_action: {
      action: "business.send_client_document", risk_tier: "consequential",
      argument_records: ["r-deal"],
    },
  });
  assert.equal(stateOf(decision, "argument_records_admissible"), "pass");
  assert.deepEqual(decision.argument_records, ["r-deal"]);
  // Still stopped by the last check and nothing else.
  assert.equal(decision.reason_id, "no_runtime_projection_supplied");
});

test("Q068 an email cannot become a tool argument, whatever else is clean", () => {
  const decision = admit({
    lineage: lineageFor(),
    proposed_action: {
      action: "business.send_client_document", risk_tier: "consequential",
      argument_records: ["r-deal", "r-email"],
    },
  });
  assert.equal(decision.reason_id, "argument_record_use_refused");
  assert.equal(stateOf(decision, "argument_records_admissible"), "refuse");
  assert.equal(decision.detail.record_id, "r-email");
  assert.equal(decision.detail.taint_class, "untrusted_external");
  assert.equal(decision.detail.intended_use, "tool_invocation");
  assert.equal(decision.detail.use_reason_id, "untrusted_content_cannot_confer_authority");
  // The use this module evaluates under is one Q068 forbids, which is the
  // whole reason the refusal exists rather than being invented here.
  assert.ok(V5_F05_UNTRUSTED_FORBIDDEN_USES.includes(V5_F05_ADMISSION_ARGUMENT_USE));
});

test("a friendlier lineage cannot be swapped in for the manifest's own", () => {
  // The attacker's lineage relabels the email as first-party. It compiles, it
  // hashes to itself, and evaluateUntrustedUse would allow the argument on it.
  const clean = records().map(record => record.record_id === "r-email"
    ? { ...record, origin: "record_layer",
      provenance: { source_id: "src-neon", retrieval_class: "typed_read" } }
    : record);
  const forged = compileTaintLineage(clean);
  assert.equal(forged.entries.find(e => e.record_id === "r-email").tainted, false);

  const decision = admit({
    lineage: forged,
    proposed_action: {
      action: "business.send_client_document", risk_tier: "consequential",
      argument_records: ["r-email"],
    },
  });
  assert.equal(decision.reason_id, "lineage_not_the_manifests");
  assert.equal(stateOf(decision, "argument_records_admissible"), "refuse");
});

test("naming an argument record with no lineage fails closed rather than skipping the check", () => {
  const decision = admit({
    proposed_action: {
      action: "business.send_client_document", risk_tier: "consequential",
      argument_records: ["r-deal"],
    },
  });
  assert.equal(decision.reason_id, "lineage_not_supplied");
  assert.equal(stateOf(decision, "argument_records_admissible"), "refuse");
});

test("a record this manifest does not carry cannot be an argument", () => {
  const decision = admit({
    lineage: lineageFor(),
    proposed_action: {
      action: "business.send_client_document", risk_tier: "consequential",
      argument_records: ["r-not-here"],
    },
  });
  assert.equal(decision.reason_id, "argument_record_not_in_manifest");
  assert.equal(decision.detail.record_id, "r-not-here");
});

test("a record the budget dropped cannot be an argument to the action", () => {
  // A budget tight enough to drop the omissible email, which the manifest then
  // reports as omitted. The record is still IN the manifest and in the lineage;
  // what changed is that this manifest no longer carries its content.
  const overrides = { budget: { token_budget: 200 } };
  const manifest = assemble(overrides);
  const email = manifest.records.find(r => r.record_id === "r-email");
  assert.equal(email.included, false);
  assert.equal(email.omission_reason_id, "token_budget_omissible_record_dropped");

  const decision = admit({
    manifest,
    receipt: receiptFor(overrides),
    lineage: lineageFor(overrides),
    proposed_action: {
      action: "business.send_client_document", risk_tier: "consequential",
      argument_records: ["r-email"],
    },
  });
  assert.equal(decision.reason_id, "argument_record_omitted_from_manifest");
  assert.equal(decision.detail.omission_reason_id, "token_budget_omissible_record_dropped");
});

// -------------------------------------------- the two permission questions

test("a manifest that blocks its own write is not admitted by a permissive receipt", () => {
  // An unavailable required source blocks the manifest and touches no rule, so
  // the receipt still permits the action on its own terms.
  const overrides = {
    sources: [
      { source_id: "src-neon", state: "unavailable", required_for_task: true },
      { source_id: "src-outlook", state: "available", required_for_task: false },
    ],
  };
  const manifest = assemble(overrides);
  const receipt = receiptFor(overrides);
  assert.equal(receipt.consequential_action_permitted, true);
  assert.equal(manifest.consequential_action_permitted, false);

  const decision = admit({ manifest, receipt });
  assert.equal(decision.reason_id, "manifest_does_not_permit_consequential_action");
  assert.equal(stateOf(decision, "coverage_permits_the_action"), "pass");
  assert.equal(stateOf(decision, "manifest_permits_the_action"), "refuse");
  assert.ok(decision.detail.manifest_blocking_reasons.includes("required_source_unavailable"));
});

test("an undecided possibly-binding rule stops the write at the coverage check", () => {
  // The checkable_done clause walked end to end: a fact nobody supplied leaves a
  // rule POSSIBLY binding, and the action does not run.
  const unknownFacts = {
    task_id: "t-9001", title: "Send the executed LOI to the client",
    boundary_action: "business.send_client_document",
    facts: facts({ audience: "unknown" }),
  };
  const receipt = receiptFor({ task: unknownFacts });
  assert.ok(receipt.possibly_binding.length > 0);
  assert.equal(receipt.consequential_action_permitted, false);
  assert.ok(receipt.blocking_reasons.includes("possible_binding_rule_undecided"));

  // Assembled as a WRITE, so the mode check passes and the ladder reaches the
  // coverage question rather than stopping short of it.
  const manifest = assemble({ task: unknownFacts });
  assert.equal(manifest.mode, "consequential_action_proposal");

  const decision = admit({ manifest, receipt });
  assert.equal(decision.reason_id, "coverage_does_not_permit_consequential_action");
  assert.equal(stateOf(decision, "mode_admits_a_write"), "pass");
  assert.equal(stateOf(decision, "manifest_freshness"), "pass");
  assert.equal(stateOf(decision, "coverage_permits_the_action"), "refuse");
  assert.equal(stateOf(decision, "manifest_permits_the_action"), "not_reached");
  assert.ok(decision.detail.receipt_blocking_reasons.includes("possible_binding_rule_undecided"));
});

test("admission reads the gate field, not `decision`, on both artifacts", () => {
  // The manifest above says decision "refuse" AND the receipt says "allow";
  // neither field is the write gate, and reading either one would give the
  // wrong answer on one of the two artifacts.
  const unknownFacts = {
    task_id: "t-9001", title: "Send the executed LOI to the client",
    boundary_action: "business.send_client_document",
    facts: facts({ audience: "unknown" }),
  };
  const manifest = assemble({ task: unknownFacts });
  const receipt = receiptFor({ task: unknownFacts });
  assert.equal(manifest.decision, "refuse");
  assert.equal(receipt.decision, "allow");
  assert.equal(receipt[receipt.write_gate_field], false);

  const decision = admit({ manifest, receipt });
  assert.equal(decision.write_gate_field_read, "consequential_action_permitted");
  assert.equal(decision.reason_id, "coverage_does_not_permit_consequential_action");

  // And the clean pair goes the other way: the receipt's `decision` is "allow"
  // there too, so a module reading `decision` would look identical on both.
  assert.equal(receiptFor().decision, "allow");
});

// ---------------------------------------------------- the projection check

test("a reproducible projection over these exact bytes is still not an authentication", () => {
  const request = assemblyRequest();
  const frozen = freezeAssemblyInput(request);
  const manifest = assembleContextManifest(frozen);
  const attestation = {
    verifier_id: "verifier.hosted-ci",
    input_digest: manifest.input_digest,
    manifest_digest: manifest.manifest_digest,
    attested_at: NOW,
    attestation_digest: verifierAttestationDigest({
      verifier_id: "verifier.hosted-ci", input_digest: manifest.input_digest,
      manifest_digest: manifest.manifest_digest, attested_at: NOW,
    }),
  };
  const projection = authenticateRuntimeProjection({
    manifest, input_bytes: frozen.input_bytes, attestation, now: ADMIT_AT,
  });
  // The sibling allows it: the manifest really does re-derive from these bytes.
  assert.equal(projection.decision, "allow");
  assert.equal(projection.reason_id, "attestation_internally_consistent");
  assert.equal(projection.authenticated, false);

  const decision = admitConsequentialAction(admissionRequest({ manifest, projection }));
  assert.equal(decision.reason_id, "runtime_projection_not_authenticated");
  assert.equal(stateOf(decision, "authenticated_runtime_projection"), "refuse");
  assert.equal(decision.detail.execution_gap_id, "no_registered_verifier");
  assert.equal(decision.detail.projection_kind, "reproducible_proposal");
});

test("a projection about another manifest refuses before its verdict is read", () => {
  const request = assemblyRequest();
  const frozen = freezeAssemblyInput(request);
  const manifest = assembleContextManifest(frozen);
  const attestation = {
    verifier_id: "verifier.hosted-ci",
    input_digest: manifest.input_digest,
    manifest_digest: manifest.manifest_digest,
    attested_at: NOW,
    attestation_digest: verifierAttestationDigest({
      verifier_id: "verifier.hosted-ci", input_digest: manifest.input_digest,
      manifest_digest: manifest.manifest_digest, attested_at: NOW,
    }),
  };
  const projection = authenticateRuntimeProjection({
    manifest, input_bytes: frozen.input_bytes, attestation, now: ADMIT_AT,
  });

  // Present it alongside a DIFFERENT manifest, whose own receipt it is paired
  // with, so only the projection is out of place.
  const otherOverrides = { budget: { token_budget: 200 } };
  const other = assemble(otherOverrides);
  assert.notEqual(other.manifest_digest, manifest.manifest_digest);
  const decision = admitConsequentialAction(admissionRequest({
    manifest: other, receipt: receiptFor(otherOverrides), projection,
  }));
  assert.equal(decision.reason_id, "runtime_projection_not_this_manifests");
  assert.equal(decision.detail.projection_manifest_digest, manifest.manifest_digest);
});

test("a projection refusal is carried through, not read as silence", () => {
  const request = assemblyRequest();
  const frozen = freezeAssemblyInput(request);
  const manifest = assembleContextManifest(frozen);
  // An attestation over the right manifest whose own digest was left behind.
  const projection = authenticateRuntimeProjection({
    manifest, input_bytes: frozen.input_bytes, now: ADMIT_AT,
    attestation: {
      verifier_id: "verifier.hosted-ci",
      input_digest: manifest.input_digest,
      manifest_digest: manifest.manifest_digest,
      attested_at: NOW,
      attestation_digest: sha("9"),
    },
  });
  assert.equal(projection.decision, "refuse");
  assert.equal(projection.reason_id, "attestation_digest_mismatch");

  const decision = admitConsequentialAction(admissionRequest({ manifest, projection }));
  assert.equal(decision.reason_id, "runtime_projection_refused");
  assert.equal(decision.detail.projection_reason_id, "attestation_digest_mismatch");
});

test("a self-minted authenticated projection is a contract violation, not a permission", () => {
  const manifest = assemble();
  const forged = {
    schema_version: V5_F05_ATTESTATION_SCHEMA_VERSION,
    manifest_digest: manifest.manifest_digest,
    input_digest: manifest.input_digest,
    verifier_id: "verifier.attacker",
    projection_kind: "authenticated_runtime_projection",
    trust_anchor: "key.attacker",
    authenticated: true,
    verifier_trusted: true,
    consequential_execution_permitted: true,
    decision: "allow",
    reason_id: "attestation_internally_consistent",
  };
  assert.equal(code(() => admitConsequentialAction(admissionRequest({ manifest, projection: forged }))),
    "projection_claims_unregistered_authentication");

  // EACH of the five claims is load-bearing on its own. An otherwise honest
  // projection with exactly ONE field forged is still refused, so no single
  // clause here is dead weight carried by the others.
  const honest = {
    ...forged,
    verifier_id: "verifier.hosted-ci",
    projection_kind: "reproducible_proposal",
    trust_anchor: null,
    authenticated: false,
    verifier_trusted: false,
    consequential_execution_permitted: false,
  };
  for (const [field, value] of [
    ["authenticated", true],
    ["verifier_trusted", true],
    ["consequential_execution_permitted", true],
    ["projection_kind", "authenticated_runtime_projection"],
    ["trust_anchor", "key.attacker"],
  ]) {
    assert.equal(
      code(() => admitConsequentialAction(admissionRequest({
        manifest, projection: { ...honest, [field]: value },
      }))),
      "projection_claims_unregistered_authentication",
      `a projection forging only "${field}" was not refused`);
  }
  assert.equal(V5_F05_ADMISSION_AUTHENTICATED_PROJECTION_EMITTED, false);
});

test("an object that is not a runtime projection at all is refused as one", () => {
  const manifest = assemble();
  assert.equal(
    code(() => admitConsequentialAction(admissionRequest({
      manifest, projection: { decision: "allow", authenticated: false },
    }))),
    "projection_not_a_runtime_projection");
});

// ------------------------------------------------------ contract violations

test("a forged manifest or receipt throws rather than being answered", () => {
  // Both forgeries START from an artifact that REFUSES and edit it into one
  // that permits. Editing a permissive artifact into a permissive one would
  // change no bytes and prove nothing.
  const blocked = assemble({
    sources: [
      { source_id: "src-neon", state: "unavailable", required_for_task: true },
      { source_id: "src-outlook", state: "available", required_for_task: false },
    ],
  });
  assert.equal(blocked.consequential_action_permitted, false);
  assert.ok(blocked.blocking_reasons.length > 0);
  assert.equal(code(() => admitConsequentialAction(admissionRequest({
    manifest: { ...blocked, consequential_action_permitted: true, blocking_reasons: [] },
  }))), "manifest_digest_mismatch");

  const unknownFacts = {
    task_id: "t-9001", title: "Send the executed LOI to the client",
    boundary_action: "business.send_client_document",
    facts: facts({ audience: "unknown" }),
  };
  const blockedReceipt = receiptFor({ task: unknownFacts });
  assert.equal(blockedReceipt.consequential_action_permitted, false);
  assert.equal(code(() => admitConsequentialAction(admissionRequest({
    receipt: { ...blockedReceipt, consequential_action_permitted: true, blocking_reasons: [] },
  }))), "coverage_receipt_digest_mismatch");
});

test("unknown fields, unknown actions and unknown tiers are unreadable, not refusals", () => {
  assert.equal(code(() => admitConsequentialAction(admissionRequest({ unexpected: true }))),
    "unknown_field");
  // `trusted` and `enforced` are not merely unknown here: the shared guard
  // refuses those NAMES outright, so a request cannot even carry the claim.
  assert.equal(code(() => admitConsequentialAction(admissionRequest({ trusted: true }))),
    "caller_assertion_field_refused");
  assert.equal(code(() => admitConsequentialAction(admissionRequest({
    proposed_action: {
      action: "business.send_client_document", risk_tier: "consequential", enforced: true,
    },
  }))), "caller_assertion_field_refused");
  assert.equal(code(() => admitConsequentialAction(admissionRequest({
    proposed_action: { action: "not.an.action", risk_tier: "consequential" },
  }))), "unknown_action");
  assert.equal(code(() => admitConsequentialAction(admissionRequest({
    proposed_action: { action: "business.send_client_document", risk_tier: "trivial" },
  }))), "unknown_risk_tier");
  assert.equal(code(() => admitConsequentialAction(admissionRequest({
    schema_version: "doctorcre-v5-f05-action-admission.v99",
  }))), "unknown_schema_version");
});

test("an accessor or a non-enumerable own key is refused rather than read", () => {
  const withAccessor = admissionRequest();
  Object.defineProperty(withAccessor, "now", {
    enumerable: true, configurable: true, get: () => ADMIT_AT,
  });
  assert.equal(code(() => admitConsequentialAction(withAccessor)), "accessor_property_refused");

  // A non-enumerable own key does not ride along unread: the shared guard walks
  // own property NAMES, so the field is refused rather than dropped.
  const action = { action: "business.send_client_document", risk_tier: "consequential" };
  Object.defineProperty(action, "hidden", { enumerable: false, value: 1 });
  assert.equal(code(() => admitConsequentialAction(admissionRequest({ proposed_action: action }))),
    "unknown_field");

  const proto = JSON.parse('{"action":"business.send_client_document",'
    + '"risk_tier":"consequential","__proto__":{"admitted":true}}');
  assert.equal(code(() => admitConsequentialAction(admissionRequest({ proposed_action: proto }))),
    "prototype_key_refused");
});

test("one record cannot be named twice among an action's arguments", () => {
  assert.equal(code(() => admitConsequentialAction(admissionRequest({
    lineage: lineageFor(),
    proposed_action: {
      action: "business.send_client_document", risk_tier: "consequential",
      argument_records: ["r-deal", "r-deal"],
    },
  }))), "duplicate_argument_record");
});

test("a lineage that is not a compiled one is unreadable", () => {
  assert.equal(code(() => admitConsequentialAction(admissionRequest({
    lineage: { entries: [], lineage_digest: sha("9") },
    proposed_action: {
      action: "business.send_client_document", risk_tier: "consequential",
      argument_records: ["r-deal"],
    },
  }))), "lineage_not_compiled");
});

// ---------------------------------------------- the contract and the seams

test("the admission contract is closed, hashed and states what it does not do", () => {
  const preimage = v5F05AdmissionContractPreimage();
  assert.equal(preimage.schema_version, V5_F05_ADMISSION_SCHEMA_VERSION);
  assert.equal(preimage.consequential_action_admissible_today, false);
  assert.equal(preimage.authenticated_projection_emitted, false);
  assert.equal(preimage.enforced_at_a_call_site, false);
  assert.equal(preimage.taint_recomputed_here, false);
  assert.equal(preimage.authority_recomputed_here, false);
  assert.equal(preimage.write_gate_field_read, V5_F05_ADMISSION_WRITE_GATE_FIELD);
  assert.deepEqual(preimage.checks, [...V5_F05_ADMISSION_CHECKS]);
  // The ordering property the module's load-time guard exists to keep: the one
  // check that cannot pass today runs LAST, so every check above it is really
  // exercised rather than short-circuited by the missing verifier.
  assert.equal(V5_F05_ADMISSION_CHECKS[V5_F05_ADMISSION_CHECKS.length - 1],
    "authenticated_runtime_projection");
  assert.deepEqual([...V5_F05_ADMISSION_CHECKS], [...new Set(V5_F05_ADMISSION_CHECKS)]);
  assert.deepEqual(preimage.effects, V5_NO_EFFECTS);

  assert.equal(v5F05AdmissionContractDigest(), digest(preimage));
  assert.equal(v5F05AdmissionContractCanonicalBytes(), canonicalJson(preimage));
  // Bound to both halves it joins: a change to either moves this digest.
  assert.equal(typeof preimage.rule_kernel_digest, "string");
  assert.equal(typeof preimage.context_contract_digest, "string");
});

test("every reason this module returns is in its published list", () => {
  // Walked over the refusals this suite actually produces, so a reason added
  // later without being published fails here.
  const observed = new Set([
    admit().reason_id,
    admit({ proposed_action: { action: "business.update_deal", risk_tier: "consequential" } })
      .reason_id,
    admit({ manifest: assemble({ mode: "read_only_exploration" }) }).reason_id,
    admit({ manifest_max_age_seconds: 120 }).reason_id,
    admit({ now: "2026-09-09T11:59:00Z" }).reason_id,
    admit({ proposed_action: {
      action: "business.send_client_document", risk_tier: "consequential",
      argument_records: ["r-deal"] } }).reason_id,
  ]);
  for (const reason_id of observed) {
    assert.ok(V5_F05_ADMISSION_REASON_IDS.includes(reason_id),
      `"${reason_id}" is returned and not published`);
  }
  // The list is sorted and has no duplicates, so a consumer can bisect it.
  assert.deepEqual([...V5_F05_ADMISSION_REASON_IDS],
    [...new Set(V5_F05_ADMISSION_REASON_IDS)].sort());
});

test("the unbuilt seams are named and fail closed", () => {
  const gaps = actionAdmissionGaps();
  assert.ok(gaps.length >= 5);
  assert.ok(gaps.every(gap => gap.landed === false));
  const ids = gaps.map(gap => gap.gap);
  assert.ok(ids.includes("no_action_admission_call_site"));
  assert.ok(ids.includes("no_registered_verifier"));
  assert.ok(ids.includes("no_argument_value_binding"));
  assert.equal(code(() => assertActionAdmissionComplete()),
    "action_admission_integration_incomplete");
});
