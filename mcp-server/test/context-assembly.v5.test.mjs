// V5-F05, half two — the Context Assembler, read-only exploration, taint
// lineage and the typed correction, proved case by case.
//
// The positive manifest is asserted first and in full, because every refusal
// below is a mutation of it: if the clean path did not assemble, a suite of
// refusals would prove only that the module refuses everything.

import test from "node:test";
import assert from "node:assert/strict";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { V5_F01_TAINT_CLASSES } from "../src/record-source-authority.v5.js";
import {
  V5F05Error,
  V5_F05_UNIVERSE_SCHEMA_VERSION,
  compileRuleUniverse,
} from "../src/rule-applicability.v5.js";
import {
  V5_F05_MANIFEST_SCHEMA_VERSION,
  V5_F05_FROZEN_INPUT_SCHEMA_VERSION,
  V5_F05_LINEAGE_SCHEMA_VERSION,
  V5_F05_TAINT_CLASSES,
  V5_F05_EXTERNAL_ORIGINS,
  V5_F05_MAX_CORRECTION_NOTE_CHARS,
  V5_F05_MAX_ATTESTATION_AGE_SECONDS,
  V5_NO_EFFECTS,
  freezeAssemblyInput,
  assembleContextManifest,
  verifyContextManifest,
  authenticateRuntimeProjection,
  verifierAttestationDigest,
  compileTaintLineage,
  evaluateUntrustedUse,
  proposeCorrection,
  contextAssemblyIntegrationGaps,
  assertContextAssemblyIntegrationComplete,
  v5F05ContextContractPreimage,
  v5F05ContextContractDigest,
  v5F05ContextContractCanonicalBytes,
} from "../src/context-assembly.v5.js";

// --------------------------------------------------------------- fixtures

const NOW = "2026-09-09T12:00:00Z";
const OBSERVED = "2026-09-09T11:45:00Z";        // 900s before NOW
const FRESH_EVIDENCE = "2026-09-09T11:00:00Z";  // 3600s before NOW

const sha = ch => `sha256:${ch.repeat(64)}`;

const code = fn => {
  try { fn(); } catch (error) {
    return error instanceof V5F05Error ? error.code : `not-a-V5F05Error:${error}`;
  }
  return "no-throw";
};

const partner = slug => ({
  slug, display: slug === "joe" ? "Joe" : "Dell", human: true, via: "oauth-google",
  client_id: null, sponsoring_human_slug: null, human_slug: null, sponsor_required: false,
});
const JOE = partner("joe");
const DELL = partner("dell");
const SPONSORED_AGENT = {
  slug: "claude", display: "Claude", human: false, via: "oauth-google",
  client_id: "c1", sponsoring_human_slug: "joe", human_slug: "joe", sponsor_required: true,
};

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
    },
    {
      rule_id: "tone-guidance",
      version: 1, rule_class: "scoped_judgment", scope: "shared", owner: "joe", mandatory: false,
      trigger: { audience: ["client"] },
      binding_text: "Write to a client the way Joe would: plain, specific, no hedging.",
      no_machine_control_reason: "Only a reader applying context can tell plain from curt.",
      retirement: { behavior: "permanent_until_superseded" },
    },
    {
      rule_id: "tour-doctrine",
      version: 1, rule_class: "scoped_judgment", scope: "shared", owner: "joe", mandatory: false,
      trigger: { resource_class: ["tour"] },
      binding_text: "A tour is planned around the client's day, not around the properties.",
      no_machine_control_reason: "Whether a day reads as considerate is not machine-checkable.",
      retirement: { behavior: "permanent_until_superseded" },
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

const task = (taskFacts = facts()) => ({
  task_id: "t-9001",
  title: "Send the executed LOI to the client",
  boundary_action: "business.send_client_document",
  facts: taskFacts,
});

const controls = (overrides = {}) => ({
  deal_owner_slug: "joe",
  account_slug: "joe",
  policy_scope: ["business.send_client_document"],
  capabilities: ["document.send"],
  ...overrides,
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
  {
    record_id: "r-email-summary", record_kind: "summary", version: 1, content_digest: sha("5"),
    origin: "record_layer", derived_kind: "summary", derived_from: ["r-email"], query_id: "q-mail",
    observed_at: OBSERVED, estimated_tokens: 40, omissible: true,
    provenance: { source_id: "src-neon", retrieval_class: "derived_index" },
  },
  {
    record_id: "r-email-vector", record_kind: "embedding", version: 1, content_digest: sha("6"),
    origin: "record_layer", derived_kind: "embedding", derived_from: ["r-email-summary"],
    query_id: "q-mail", observed_at: OBSERVED, estimated_tokens: 10, omissible: true,
    provenance: { source_id: "src-neon", retrieval_class: "derived_index" },
  },
]);

const request = (overrides = {}) => ({
  schema_version: V5_F05_MANIFEST_SCHEMA_VERSION,
  tenant: ORGANIZATION_TENANT_ID,
  now: NOW,
  mode: "consequential_action_proposal",
  actor: JOE,
  task: task(),
  controls: controls(),
  universe: compileRuleUniverse(universePolicy()),
  records: records(),
  sources: sources(),
  queries: queries(),
  ...overrides,
});

const assemble = (overrides = {}) => assembleContextManifest(freezeAssemblyInput(request(overrides)));

// -------------------------------------- Q050, the manifest that must work

test("Q050 a clean task assembles one reproducible manifest carrying every named part", () => {
  const manifest = assemble();

  assert.equal(manifest.schema_version, V5_F05_MANIFEST_SCHEMA_VERSION);
  assert.equal(manifest.decision, "allow");
  assert.equal(manifest.reason_id, "context_assembled");
  assert.equal(manifest.consequential_action_permitted, true);
  assert.deepEqual(manifest.blocking_reasons, []);
  assert.deepEqual(manifest.effects, V5_NO_EFFECTS);

  // A proposal, and it says so. Nothing here is authenticated.
  assert.equal(manifest.projection_kind, "reproducible_proposal");

  // Actor, task and authority envelope.
  assert.equal(manifest.actor.slug, "joe");
  assert.equal(manifest.task.task_id, "t-9001");
  assert.equal(manifest.task.boundary_action, "business.send_client_document");
  assert.deepEqual(manifest.task.facts, facts());
  assert.deepEqual(manifest.task.unknown_facts, []);
  assert.equal(manifest.authority_envelope.decision, "allow");
  assert.equal(manifest.authority_envelope.reason_id, "ordinary_business_within_controls");
  assert.equal(manifest.authority_envelope.asserted_by_caller, false);
  assert.equal(manifest.authority_envelope.computed_by,
    "global-boundaries.v5.evaluateActorAuthority");
  assert.equal(manifest.authority_envelope.permanent_privilege_granted, false);

  // Records with ids, versions, digests, provenance and freshness.
  assert.deepEqual(manifest.records.map(r => r.record_id),
    ["r-deal", "r-email", "r-email-summary", "r-email-vector"]);
  const deal = manifest.records.find(r => r.record_id === "r-deal");
  assert.equal(deal.version, 4);
  assert.equal(deal.content_digest, sha("3"));
  assert.equal(deal.freshness, "fresh");
  assert.equal(deal.age_seconds, 900);
  assert.equal(deal.provenance.source_id, "src-neon");
  assert.equal(deal.query_id, "q-deal");

  // Applicable rules, why they apply, and their full binding text.
  assert.deepEqual(manifest.rule_coverage.effective.map(e => e.rule_id),
    ["client-send-gate", "no-phi", "tone-guidance"]);
  assert.deepEqual(manifest.rule_coverage.not_applicable.map(e => e.rule_id), ["tour-doctrine"]);
  assert.equal(manifest.rule_coverage.coverage_complete, true);
  const gate = manifest.delivered_rules.find(r => r.rule_id === "client-send-gate");
  assert.equal(gate.mode, "full_binding_text");
  assert.equal(gate.binding_text,
    "A client-facing document is reviewed by a second seat before it is sent.");
  assert.equal(gate.summary_is_navigation_only, true);
  const phi = manifest.delivered_rules.find(r => r.rule_id === "no-phi");
  assert.equal(phi.mode, "code_enforced_constraint");
  assert.equal(phi.enforced_control.control_id, "global.no_phi");

  // Sources, omissions, budget and reproducible query identifiers.
  assert.deepEqual(manifest.unavailable_sources, []);
  assert.deepEqual(manifest.conflicting_sources, []);
  assert.deepEqual(manifest.omissions, []);
  assert.equal(manifest.budget.token_budget, null);
  assert.equal(manifest.budget.binding_constraint_omitted, false);
  assert.deepEqual(manifest.queries.map(q => q.query_id), ["q-deal", "q-mail"]);
  assert.equal(manifest.queries[0].parameters_digest, sha("7"));

  // And the whole thing hashes to its own digest.
  assert.equal(verifyContextManifest(manifest), true);
  assert.equal(manifest.record_attribution_written, false);
});

test("Q050 the same input reproduces the same manifest digest, byte for byte", () => {
  const first = assemble();
  const second = assemble();
  assert.equal(first.input_digest, second.input_digest);
  assert.equal(first.manifest_digest, second.manifest_digest);

  // A different task is a different manifest; nothing carries over.
  const other = assemble({ task: task(facts({ audience: "internal" })) });
  assert.notEqual(other.manifest_digest, first.manifest_digest);
});

test("Q050 the manifest binds the frozen bytes, so a later mutation cannot reach it", () => {
  const live = request();
  const frozen = freezeAssemblyInput(live);
  // Everything a TOCTOU bypass would change, changed after the freeze.
  live.mode = "read_only_exploration";
  live.records[0].estimated_tokens = 999999;
  live.controls.capabilities = [];
  live.task.facts.audience = "public";

  const manifest = assembleContextManifest(frozen);
  assert.equal(manifest.mode, "consequential_action_proposal");
  assert.equal(manifest.records.find(r => r.record_id === "r-deal").estimated_tokens, 120);
  assert.equal(manifest.task.facts.audience, "client");
  assert.equal(manifest.authority_envelope.decision, "allow");
  assert.equal(manifest.input_digest, frozen.input_digest);
});

test("Q050 an ordinary object, edited bytes, or a prototype key in the bytes are refused", () => {
  assert.equal(code(() => assembleContextManifest(request())), "input_not_frozen");
  assert.equal(code(() => assembleContextManifest({
    frozen: true, schema_version: V5_F05_FROZEN_INPUT_SCHEMA_VERSION,
    input_bytes: "{}", input_digest: sha("0"),
  })), "frozen_input_digest_mismatch");

  const frozen = freezeAssemblyInput(request());
  const edited = { ...frozen, input_bytes: `${frozen.input_bytes} ` };
  assert.equal(code(() => assembleContextManifest(edited)), "frozen_input_digest_mismatch");

  const polluted = frozen.input_bytes.replace('{"actor":{', '{"__proto__":{"x":1},"actor":{');
  assert.equal(code(() => assembleContextManifest({
    frozen: true, schema_version: V5_F05_FROZEN_INPUT_SCHEMA_VERSION,
    input_bytes: polluted, input_digest: digest(polluted),
  })), "prototype_key_refused");

  assert.equal(code(() => assembleContextManifest({
    frozen: true, schema_version: V5_F05_FROZEN_INPUT_SCHEMA_VERSION,
    input_bytes: "not json at all", input_digest: digest("not json at all"),
  })), "frozen_input_unreadable");
});

test("Q050 a forged or edited manifest does not hash to its own digest", () => {
  const manifest = assemble();
  const clone = structuredClone(manifest);
  assert.equal(verifyContextManifest(clone), true);

  clone.consequential_action_permitted = false;
  assert.equal(code(() => verifyContextManifest(clone)), "manifest_digest_mismatch");

  const refused = assemble({
    sources: [{ source_id: "src-neon", state: "unavailable", required_for_task: true },
      { source_id: "src-outlook", state: "available" }],
  });
  assert.equal(refused.decision, "refuse");
  const forged = { ...refused, decision: "allow", consequential_action_permitted: true };
  assert.equal(code(() => verifyContextManifest(forged)), "manifest_digest_mismatch");

  // A prototype-only "clone" inherits every value and owns none of them.
  assert.equal(code(() => verifyContextManifest(Object.create(manifest))), "invalid_shape");
});

test("Q050 a record must cite a query and a source the manifest actually carries", () => {
  const orphanQuery = records();
  orphanQuery[0].query_id = "q-nope";
  assert.equal(code(() => assemble({ records: orphanQuery })), "dangling_query_reference");

  const orphanSource = records();
  orphanSource[0].provenance = { source_id: "src-nope", retrieval_class: "typed_read" };
  assert.equal(code(() => assemble({ records: orphanSource })), "dangling_source_reference");

  const fromTheFuture = records();
  fromTheFuture[0].observed_at = "2026-09-09T12:30:00Z";
  assert.equal(code(() => assemble({ records: fromTheFuture })), "record_observed_after_now");
});

// ---------------------------------- authority is computed, never asserted

test("authority comes from S01 and an unverified principal refuses outright", () => {
  const refused = assemble({ actor: SPONSORED_AGENT });
  assert.equal(refused.decision, "refuse");
  assert.equal(refused.reason_id, "actor_not_verified_partner");
  assert.equal(refused.read_only_exploration_permitted, false);
  assert.equal(refused.consequential_action_permitted, false);
});

test("a verified partner missing a capability blocks the write and still may explore", () => {
  const overrides = {
    actor: DELL,
    controls: controls({ deal_owner_slug: "dell", account_slug: "dell", capabilities: [] }),
  };
  const write = assemble(overrides);
  assert.equal(write.authority_envelope.decision, "refuse");
  assert.equal(write.authority_envelope.reason_id, "capability_not_granted");
  assert.equal(write.decision, "refuse");
  assert.equal(write.reason_id, "authority_not_established");
  assert.equal(write.consequential_action_permitted, false);

  const explore = assemble({ ...overrides, mode: "read_only_exploration" });
  assert.equal(explore.decision, "allow");
  assert.equal(explore.reason_id, "read_only_exploration_under_uncertainty");
  assert.equal(explore.read_only_exploration_permitted, true);
  assert.equal(explore.consequential_action_permitted, false);
  assert.ok(explore.blocking_reasons.includes("authority_not_established"));
});

// ------------------------------- Q065, uncertainty, budget and honesty

test("Q065 read-only exploration is permitted under uncertainty and is marked as such", () => {
  const uncertain = { task: task(facts({ audience: undefined })), mode: "read_only_exploration" };
  delete uncertain.task.facts.audience;
  const manifest = assemble(uncertain);

  assert.equal(manifest.decision, "allow");
  assert.equal(manifest.reason_id, "read_only_exploration_under_uncertainty");
  assert.equal(manifest.uncertainty.marker, true);
  assert.equal(manifest.uncertainty.unknown_fact_count, 1);
  assert.equal(manifest.uncertainty.possibly_binding_count, 2);
  assert.equal(manifest.consequential_action_permitted, false);
  assert.equal(manifest.read_only_exploration_permitted, true);

  // The possibly binding rules are DELIVERED, with their text, and cannot be
  // dropped: that is the difference between uncertainty and silent permission.
  const possiblyBinding = manifest.delivered_rules.filter(r => r.bucket === "possibly_binding");
  assert.deepEqual(possiblyBinding.map(r => r.rule_id), ["client-send-gate", "tone-guidance"]);
  assert.ok(possiblyBinding.every(r => r.omissible === false));
  assert.equal(possiblyBinding.find(r => r.rule_id === "client-send-gate").mode,
    "full_binding_text");
});

test("Q065 the same uncertainty refuses a consequential proposal instead of assuming", () => {
  const uncertain = { task: task(facts()) };
  delete uncertain.task.facts.audience;
  const manifest = assemble(uncertain);
  assert.equal(manifest.decision, "refuse");
  assert.equal(manifest.reason_id, "typed_facts_unknown");
  assert.ok(manifest.blocking_reasons.includes("possible_binding_rule_undecided"));
  assert.equal(manifest.consequential_action_permitted, false);
});

test("Q065 an unknown rule universe blocks the write rather than reading as empty", () => {
  const partial = compileRuleUniverse(universePolicy({ completeness: "partial_unknown_coverage" }));
  const write = assemble({ universe: partial });
  assert.equal(write.decision, "refuse");
  assert.equal(write.reason_id, "universe_coverage_unknown");
  assert.equal(write.uncertainty.universe_coverage_known, false);

  const explore = assemble({ universe: partial, mode: "read_only_exploration" });
  assert.equal(explore.decision, "allow");
  assert.equal(explore.read_only_exploration_permitted, true);
});

test("Q065 the token budget drops guidance, never a binding constraint", () => {
  const retrieval = {
    semantic_candidates: [{ rule_id: "tour-doctrine", reason: "the thread mentions a site tour" }],
  };
  const unbudgeted = assemble(retrieval);
  const total = unbudgeted.budget.estimated_tokens_total;
  const guidanceTokens = unbudgeted.guidance.reduce((sum, g) => sum + g.estimated_tokens, 0);
  assert.ok(guidanceTokens > 0);
  assert.equal(unbudgeted.guidance[0].rule_id, "tour-doctrine");

  const tight = assemble({ ...retrieval, budget: { token_budget: total - guidanceTokens } });
  assert.equal(tight.decision, "allow");
  assert.equal(tight.budget.within_budget, true);
  assert.deepEqual(tight.omissions.map(o => o.ref), ["tour-doctrine"]);
  assert.equal(tight.omissions[0].kind, "semantic_guidance");
  assert.equal(tight.guidance[0].included, false);
  assert.ok(tight.delivered_rules.every(rule => rule.included));
});

test("Q065 a budget that cannot fit the binding rules refuses instead of omitting one", () => {
  const starved = assemble({
    semantic_candidates: [{ rule_id: "tour-doctrine", reason: "site tour" }],
    budget: { token_budget: 0 },
  });
  assert.equal(starved.decision, "refuse");
  assert.equal(starved.reason_id, "budget_cannot_omit_binding_constraint");
  assert.equal(starved.budget.within_budget, false);
  assert.equal(starved.budget.binding_constraint_omitted, false);
  // Every rule is still delivered, and the record that backs a control survives.
  assert.ok(starved.delivered_rules.every(rule => rule.included));
  assert.ok(!starved.omissions.some(o => o.kind === "rule"));
  assert.equal(starved.records.find(r => r.record_id === "r-deal").included, true);
  assert.equal(starved.records.find(r => r.record_id === "r-email").included, false);
});

test("Q065 an unavailable, conflicting or stale-control source is exposed, not hidden", () => {
  const unavailable = assemble({
    sources: [{ source_id: "src-neon", state: "unavailable", required_for_task: true },
      { source_id: "src-outlook", state: "available" }],
  });
  assert.deepEqual(unavailable.unavailable_sources, ["src-neon"]);
  assert.equal(unavailable.reason_id, "required_source_unavailable");

  const conflicting = assemble({
    sources: [{ source_id: "src-neon", state: "available", required_for_task: true },
      { source_id: "src-outlook", state: "conflicting", note: "two answers for the same thread" }],
  });
  assert.deepEqual(conflicting.conflicting_sources, ["src-outlook"]);
  assert.ok(conflicting.blocking_reasons.includes("source_conflict_unresolved"));

  const staleRecords = records();
  staleRecords[0].max_age_seconds = 60;
  const stale = assemble({ records: staleRecords });
  assert.deepEqual(stale.stale_records, ["r-deal"]);
  assert.equal(stale.stale_control_records[0].reason_id, "control_backing_record_stale");
  assert.equal(stale.decision, "refuse");
  assert.equal(stale.reason_id, "control_backing_record_stale");
});

// --------------------------------------------- Q068, taint and lineage

test("Q068 taint is carried by lineage, through summaries and embeddings", () => {
  // The vocabulary is F01's, reused rather than reinvented.
  assert.equal(V5_F05_TAINT_CLASSES, V5_F01_TAINT_CLASSES);

  const lineage = compileTaintLineage(records());
  assert.equal(lineage.schema_version, V5_F05_LINEAGE_SCHEMA_VERSION);
  const byId = Object.fromEntries(lineage.entries.map(e => [e.record_id, e]));

  assert.equal(byId["r-deal"].taint_class, "first_party_record_layer");
  assert.equal(byId["r-deal"].tainted, false);
  assert.equal(byId["r-email"].taint_class, "untrusted_external");
  assert.equal(byId["r-email"].reason_id, "external_origin");
  // A summary of an email is not a cleaner email...
  assert.equal(byId["r-email-summary"].taint_class, "untrusted_parsed");
  assert.deepEqual(byId["r-email-summary"].tainted_ancestors, ["r-email"]);
  // ...and neither is an embedding of that summary, two generations out.
  assert.equal(byId["r-email-vector"].taint_class, "untrusted_parsed");
  assert.deepEqual(byId["r-email-vector"].tainted_ancestors, ["r-email-summary"]);

  assert.deepEqual(lineage.tainted_record_ids,
    ["r-email", "r-email-summary", "r-email-vector"]);
  assert.equal(lineage.declassification_supported, false);
  assert.ok(lineage.entries.every(e => e.may_instruct === false && e.treated_as === "data"));
});

test("Q068 every origin Q068 names is external, and each derived kind stays tainted", () => {
  assert.deepEqual([...V5_F05_EXTERNAL_ORIGINS],
    ["document", "email", "mls", "salesforce", "upload", "web"]);
  for (const origin of V5_F05_EXTERNAL_ORIGINS) {
    const lineage = compileTaintLineage([{
      record_id: "r-x", record_kind: "note", version: 1, content_digest: sha("a"),
      origin, derived_kind: "primary", derived_from: [], query_id: "q-x",
      observed_at: OBSERVED, estimated_tokens: 1,
      provenance: { source_id: "s-x", retrieval_class: "connector_fetch" },
    }]);
    assert.equal(lineage.entries[0].taint_class, "untrusted_external", origin);
  }
});

test("Q068 tainted content cannot become a rule, an authority, a tool call or a secret", () => {
  const lineage = compileTaintLineage(records());
  for (const use of ["rule_authorship", "authority_grant", "policy_change", "recipient_change",
    "secret_request", "tool_invocation"]) {
    const answer = evaluateUntrustedUse({ lineage, record_id: "r-email", intended_use: use });
    assert.equal(answer.decision, "refuse", use);
    assert.equal(answer.reason_id, "untrusted_content_cannot_confer_authority", use);
    assert.equal(answer.content_that_looks_like_an_instruction_is_still_data, true);
  }
  // The taint reaches two generations of derivation, not just the email itself.
  assert.equal(evaluateUntrustedUse({
    lineage, record_id: "r-email-vector", intended_use: "tool_invocation" }).decision, "refuse");

  // It may still inform the work, which is the whole point of keeping it.
  const asData = evaluateUntrustedUse({ lineage, record_id: "r-email",
    intended_use: "analysis_input" });
  assert.equal(asData.decision, "allow");
  assert.equal(asData.reason_id, "untrusted_content_as_data");

  // First-party content is not blanket-restricted...
  assert.equal(evaluateUntrustedUse({ lineage, record_id: "r-deal",
    intended_use: "tool_invocation" }).decision, "allow");
  // ...and nothing at all can be declassified here.
  assert.equal(evaluateUntrustedUse({ lineage, record_id: "r-deal",
    intended_use: "declassification" }).reason_id, "declassification_not_supported");
});

test("Q068 tainted content wearing an authority-bearing record kind refuses the manifest", () => {
  const smuggled = records();
  smuggled[1].record_kind = "rule";
  const manifest = assemble({ records: smuggled });
  assert.equal(manifest.decision, "refuse");
  assert.equal(manifest.reason_id, "untrusted_content_cannot_be_authority");
  assert.deepEqual(manifest.taint_violations, [{
    record_id: "r-email", record_kind: "rule", taint_class: "untrusted_external",
    reason_id: "untrusted_content_cannot_be_authority",
  }]);
  assert.equal(manifest.read_only_exploration_permitted, false);
  assert.equal(manifest.declassification_supported, false);
});

test("Q068 a broken or forged lineage refuses rather than losing a taint", () => {
  const dangling = records();
  dangling[2].derived_from = ["r-ghost"];
  assert.equal(code(() => compileTaintLineage(dangling)), "taint_lineage_dangling_parent");

  const cyclic = records();
  cyclic[1].derived_from = ["r-email-vector"];
  assert.equal(code(() => compileTaintLineage(cyclic)), "taint_lineage_cycle");

  const selfCycle = records();
  selfCycle[1].derived_from = ["r-email"];
  assert.equal(code(() => compileTaintLineage(selfCycle)), "taint_lineage_self_reference");

  const forged = structuredClone(compileTaintLineage(records()));
  forged.entries.find(e => e.record_id === "r-email").tainted = false;
  forged.entries.find(e => e.record_id === "r-email").taint_class = "first_party_record_layer";
  assert.equal(code(() => evaluateUntrustedUse({
    lineage: forged, record_id: "r-email", intended_use: "rule_authorship" })),
    "lineage_digest_mismatch");
});

// ------------------------- proposal versus authenticated runtime projection

test("a verifier attestation over the exact input bytes authenticates the projection", () => {
  const frozen = freezeAssemblyInput(request());
  const manifest = assembleContextManifest(frozen);
  const attestation = {
    verifier_id: "verifier.hosted-ci",
    input_digest: manifest.input_digest,
    manifest_digest: manifest.manifest_digest,
    attested_at: "2026-09-09T11:58:00Z",
  };
  attestation.attestation_digest = verifierAttestationDigest(attestation);

  const projection = authenticateRuntimeProjection({
    manifest, input_bytes: frozen.input_bytes, attestation, now: NOW });
  assert.equal(projection.decision, "allow");
  assert.equal(projection.reason_id, "attestation_binds_exact_input_bytes");
  assert.equal(projection.projection_kind, "authenticated_runtime_projection");
  assert.equal(projection.verifier_id, "verifier.hosted-ci");
  assert.equal(projection.authenticated_by_caller_boolean, false);
  // The manifest itself never claimed to be authenticated.
  assert.equal(manifest.projection_kind, "reproducible_proposal");
});

test("the verifier interface refuses a boolean, a stray hash and the wrong bytes", () => {
  const frozen = freezeAssemblyInput(request());
  const manifest = assembleContextManifest(frozen);
  const good = {
    verifier_id: "verifier.hosted-ci",
    input_digest: manifest.input_digest,
    manifest_digest: manifest.manifest_digest,
    attested_at: "2026-09-09T11:58:00Z",
  };
  good.attestation_digest = verifierAttestationDigest(good);
  const project = (attestation, extra = {}) => authenticateRuntimeProjection({
    manifest, input_bytes: frozen.input_bytes, attestation, now: NOW, ...extra });

  // A caller boolean is not an authentication.
  assert.equal(code(() => project({ ...good, authenticated: true })),
    "caller_assertion_field_refused");

  // Bytes from a different request cannot authenticate this manifest.
  const otherBytes = freezeAssemblyInput(request({ mode: "read_only_exploration" })).input_bytes;
  assert.equal(authenticateRuntimeProjection({
    manifest, input_bytes: otherBytes, attestation: good, now: NOW }).reason_id,
    "input_bytes_do_not_match_manifest");

  // A digest that does not recompute is a forgery, not a signature.
  assert.equal(project({ ...good, attestation_digest: sha("0") }).reason_id,
    "attestation_digest_mismatch");

  // An attestation about some other input, correctly self-hashed, still refuses.
  const elsewhere = { ...good, input_digest: sha("b") };
  elsewhere.attestation_digest = verifierAttestationDigest(elsewhere);
  assert.equal(project(elsewhere).reason_id, "attestation_input_mismatch");

  // Time bounds, both directions.
  const stale = { ...good, attested_at: "2026-09-09T11:40:00Z" };
  stale.attestation_digest = verifierAttestationDigest(stale);
  const staleAnswer = project(stale);
  assert.equal(staleAnswer.reason_id, "attestation_stale");
  assert.equal(staleAnswer.max_age_seconds, V5_F05_MAX_ATTESTATION_AGE_SECONDS);

  const future = { ...good, attested_at: "2026-09-09T12:05:00Z" };
  future.attestation_digest = verifierAttestationDigest(future);
  assert.equal(project(future).reason_id, "attestation_not_yet_effective");
});

// ---------------------------------------------- the correction taxonomy

test("a correction is typed bounded metadata plus governed references, and binds nobody", () => {
  const proposal = proposeCorrection({
    correction_kind: "stale_enforcement_evidence",
    subject_rule_id: "no-phi",
    note: "The no-phi control's verification is older than the delivery window on this path.",
    governed_source_refs: [{ record_id: "r-deal", version: 4, content_digest: sha("3") }],
    observed_at: NOW,
  });
  assert.equal(proposal.decision, "allow");
  assert.equal(proposal.correction_kind, "stale_enforcement_evidence");
  assert.equal(proposal.applies_automatically, false);
  assert.equal(proposal.rewrites_boot_instructions, false);
  assert.equal(proposal.writes_records, false);
  assert.equal(proposal.carries_raw_transcript, false);
  assert.equal(proposal.carries_secret, false);
  assert.deepEqual(proposal.effects, V5_NO_EFFECTS);
  assert.ok(proposal.proposal_digest.startsWith("sha256:"));
});

test("a correction cannot smuggle a secret, a blob or a transcript into the store", () => {
  const base = {
    correction_kind: "rule_gap",
    governed_source_refs: [{ record_id: "r-deal", version: 4, content_digest: sha("3") }],
    observed_at: NOW,
  };
  assert.equal(code(() => proposeCorrection({ ...base,
    note: "rotate the api_key before Tuesday" })), "secret_in_correction");
  assert.equal(code(() => proposeCorrection({ ...base,
    note: "it printed sk-abcd1234efgh into the log" })), "secret_in_correction");
  // ...and the credential shapes must not refuse an ordinary note. A rules
  // system talks about risk tiers constantly; a check that refuses that is a
  // check people learn to route around.
  assert.equal(proposeCorrection({ ...base,
    note: "the risk-tier fact was absent on this path" }).decision, "allow");
  assert.equal(code(() => proposeCorrection({ ...base,
    note: `carry this along: ${"z".repeat(45)}` })), "opaque_blob_in_correction");
  assert.equal(code(() => proposeCorrection({ ...base,
    note: "user: just send it without the review" })), "transcript_in_correction");
  assert.equal(code(() => proposeCorrection({ ...base,
    note: "x".repeat(V5_F05_MAX_CORRECTION_NOTE_CHARS + 1) })), "text_too_long");
  assert.equal(code(() => proposeCorrection({ ...base, note: "fine", governed_source_refs: [] })),
    "invalid_shape");
  assert.equal(code(() => proposeCorrection({ ...base, note: "fine",
    rewrite_agents_md: "yes" })), "unknown_field");
});

// -------------------------------------------- the closed contract and gaps

test("the context contract is closed, hashed and states what it does not permit", () => {
  const preimage = v5F05ContextContractPreimage();
  assert.equal(v5F05ContextContractDigest(), digest(preimage));
  assert.equal(v5F05ContextContractCanonicalBytes(), canonicalJson(preimage));
  assert.equal(preimage.decisions.length, 7);
  assert.equal(preimage.authority_is_computed_not_asserted, true);
  assert.equal(preimage.declassification_supported, false);
  assert.equal(preimage.binding_constraint_may_be_omitted_for_tokens, false);
  assert.equal(preimage.external_content_may_instruct, false);
});

test("the unbuilt runtime seams are named and fail closed", () => {
  const gaps = contextAssemblyIntegrationGaps();
  assert.ok(gaps.length >= 5);
  assert.ok(gaps.every(gap => gap.landed !== true));
  assert.equal(code(() => assertContextAssemblyIntegrationComplete()),
    "context_assembly_integration_incomplete");
});
