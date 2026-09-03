import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {compileSchema} from "../../workspace/contracts/schema-validator.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = relative => JSON.parse(fs.readFileSync(path.join(ROOT, relative), "utf8"));
const canonical = value => {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
};
const digest = value => `sha256:${crypto.createHash("sha256").update(canonical(value)).digest("hex")}`;

test("all contract files are valid versioned JSON", () => {
  const files = fs.readdirSync(path.join(ROOT, "contracts")).filter(name => name.endsWith(".json"));
  assert.ok(files.length >= 9);
  for (const file of files) {
    const value = read(path.join("contracts", file));
    if (file !== "fixture-schema.v1.json") {
      assert.equal(value.version, "1.0.0", file);
      assert.match(value.status, /^(phase[01]_.*)$/, file);
    }
  }
});

test("evidence activation manifest maps every required admission case to an executable gate", () => {
  const manifest = read("contracts/evidence-activation-acceptance-map.v1.json");
  assert.equal(manifest.schema_version, "evidence-activation-acceptance-map.v1");
  assert.equal(manifest.promotion_posture, "human_review_only");
  assert.equal(manifest.cases.length, 14);
  assert.equal(new Set(manifest.cases.map(row => row.id)).size, 14);
  for (const row of manifest.cases) {
    assert.match(row.id, /^[a-z0-9_]+$/);
    assert.ok(Array.isArray(row.evidence) && row.evidence.length > 0, `${row.id} evidence is non-empty`);
    for (const evidence of row.evidence) {
      assert.equal(typeof evidence.file, "string");
      assert.equal(typeof evidence.assertion, "string");
      const gate = path.resolve(ROOT, "..", evidence.file);
      assert.ok(fs.existsSync(gate), `${row.id} evidence file exists: ${evidence.file}`);
      assert.ok(fs.readFileSync(gate, "utf8").includes(evidence.assertion), `${row.id} assertion is executable: ${evidence.assertion}`);
    }
  }
});

test("Engineering Passport schema preserves blocked learning null and envelope authority", () => {
  const schema = read("contracts/engineering-passport.v1.schema.json");
  assert.ok(schema.required.includes("execution_envelopes"));
  const validateLearning = compileSchema(schema, schema.$defs.LearningDisposition);
  assert.equal(validateLearning({ state: "unresolved", route: null, evidence_refs: [], note: "pending" }).valid, true);
  assert.equal(validateLearning({ state: "proposed", route: null, evidence_refs: [], note: "invalid terminal omission" }).valid, false);
});

test("schema compiler enforces keyed CARR uniqueness beyond uniqueItems", () => {
  const validate = compileSchema({ type: "array", uniqueItems: true, "x-carr-unique-by": "check_ref", items: { type: "object", properties: { check_ref: { type: "string" }, failure_condition: { type: "string" } } } });
  assert.equal(validate([{ check_ref: "check:a", failure_condition: "one" }, { check_ref: "check:a", failure_condition: "different" }]).valid, false);
  assert.equal(validate([{ check_ref: "check:a" }, { check_ref: "check:b" }]).valid, true);
  assert.throws(() => compileSchema({ type: "array", "x-carr-unique-by": "" }), /x-carr-unique-by/);
  assert.throws(() => compileSchema({ type: "array", "x-carr-unique-by": "   ", items: { type: "object", properties: { check_ref: { type: "string" } } } }), /x-carr-unique-by/);
  assert.throws(() => compileSchema({ type: "array", "x-carr-unique-by": "check_ref", items: { type: "object", properties: { other: { type: "string" } } } }), /declares/);
  const scalar = compileSchema({ type: "array", "x-carr-unique-by": "check_ref", items: { type: "object", properties: { check_ref: { type: ["string", "number", "boolean"] } } } });
  assert.equal(scalar([{ check_ref: "check:a" }, { check_ref: "check:b" }]).valid, true);
  assert.equal(scalar([{ other: "check:a" }]).valid, false);
  assert.equal(scalar([{ check_ref: { nested: true } }, { check_ref: { nested: true } }]).valid, false);
  assert.equal(scalar([{ check_ref: ["a"] }]).valid, false);
  assert.equal(scalar([{ check_ref: null }]).valid, false);
  assert.equal(scalar([{ check_ref: { b: 1, a: 2 } }, { check_ref: { a: 2, b: 1 } }]).valid, false);
});

test("state machines are closed and internally consistent", () => {
  const contract = read("contracts/state-machines.v1.json");
  for (const [name, machine] of Object.entries(contract.machines)) {
    const states = [...machine.main, ...machine.side];
    assert.equal(new Set(states).size, states.length, `${name} has duplicate states`);
    assert.ok(states.includes(machine.initial), `${name} initial state is declared`);
    for (const terminal of machine.terminal) assert.ok(states.includes(terminal), `${name} terminal ${terminal} is declared`);
    assert.ok(machine.invariants.length > 0, `${name} has invariants`);
  }
  assert.ok(contract.machines.health.invariants.some(rule => rule.includes("unknown")));
});

test("nonterminal interruption states have guarded productive re-entry paths", () => {
  const {machines} = read("contracts/state-machines.v1.json");
  const edge = (machine, from, to) => machines[machine].transitions.find(item => item.from === from && item.to === to);
  for (const from of ["needs_joe", "blocked", "failed"]) {
    assert.match(edge("work_request", from, "triaged")?.guard || "", /evidence|decision/i, `work_request ${from}`);
  }
  assert.match(edge("agent_session", "blocked", "collecting")?.guard || "", /read scope revalidated.*read only/i);
  assert.match(edge("approval", "revised", "proposed")?.guard || "", /new immutable plan hash.*prior approval.*invalidated/i);
});

test("fixture schema closes every nested object shape", () => {
  const schema = read("contracts/fixture-schema.v1.json");
  const open = [];
  const inspect = (value, at = "$") => {
    if (!value || typeof value !== "object") return;
    if (value.type === "object" && value.additionalProperties !== false) open.push(at);
    for (const [key, child] of Object.entries(value)) inspect(child, `${at}/${key}`);
  };
  inspect(schema);
  assert.deepEqual(open, []);
});

test("Phase 0 API is read only and separate from MCP authority", () => {
  const api = read("contracts/ops-v1-read-contract.v1.json");
  assert.deepEqual(api.allowed_methods, ["GET", "HEAD", "OPTIONS"]);
  assert.ok(api.routes.length >= 20);
  assert.ok(api.routes.every(route => route.method === "GET"));
  assert.ok(api.prohibitions.includes("no generic MCP passthrough"));
  assert.ok(api.prohibitions.includes("no mutation route"));
  assert.equal(api.audience, "carr-control-room-web");
});

test("authorization defaults deny and never trusts profile", () => {
  const authority = read("contracts/authority-risk-matrix.v1.json");
  assert.equal(authority.default_effect, "deny");
  assert.ok(authority.enforcement.never_authoritative.includes("query parameter profile"));
  assert.ok(authority.enforcement.never_authoritative.includes("client supplied tenant"));
  assert.equal(authority.phase0.production_mobile_approval, false);
  assert.deepEqual(Object.keys(authority.risk_classes), ["R0", "R1", "R2", "R3", "R4", "R5", "R6"]);
  assert.equal(authority.roles.dell_reporter_viewer.R5, "deny");
  assert.match(authority.roles.joe_owner_production_approver.R5, /approve exact production plan hash/i);
  assert.equal(authority.authority_planes.platform_administration.customer_delegable, false);
  assert.equal(authority.authority_planes.platform_administration.launch_holder, "Joe");
  assert.deepEqual(Object.keys(authority.identity_dimensions), ["organization_tenant_id", "sponsoring_human_id", "partner_id", "agent_principal_id", "runtime_principal", "session_capability_profile", "personal_brain_scope", "personal_brain_version_and_count", "independence"]);
  assert.ok(authority.explicit_denies.some(item => item.id === "D-UNSPONSORED-HUMAN-ONLY"));
  assert.ok(authority.explicit_denies.some(item => item.id === "D-CROSS-BRAIN"));
  assert.ok(authority.explicit_denies.some(item => item.id === "D-PERSONAL-RULE-AUTHORITY"));
});

test("security contract separates audience, session, data, and freshness", () => {
  const security = read("contracts/security-redaction.v1.json");
  assert.equal(security.session.origin, "https://ops.doctorcre.com");
  assert.equal(security.session.audience, "carr-control-room-web");
  assert.equal(security.session.cookie, "__Host-ops_session");
  assert.equal(security.session.tenant_context.client_mutable, false);
  assert.match(security.classifications.O4_never_store, /DSNs/);
  assert.match(security.freshness.healthy_only_when, /Every required authoritative signal/);
  assert.ok(security.freshness.forbidden.some(rule => rule.includes("cached Healthy")));
  assert.match(security.retention.implementation_gate, /No deletion automation/);
});

test("traceability maps every required column and source section", () => {
  const trace = read("contracts/phase0-traceability.v1.json");
  assert.ok(trace.entries.length >= 18);
  for (const entry of trace.entries) {
    for (const key of ["requirement", "screen", "operational_entity", "api_or_action", "authority", "evidence", "rollback", "tests"]) {
      assert.ok(entry[key], `${entry.id} missing ${key}`);
      if (Array.isArray(entry[key])) assert.ok(entry[key].length > 0, `${entry.id} empty ${key}`);
    }
    assert.ok(entry.source_sections.length > 0, `${entry.id} missing BDUF source`);
  }
});

test("acceptance freezes six critical flows and all adversarial gates", () => {
  const acceptance = read("contracts/phase0-acceptance.v1.json");
  assert.equal(acceptance.flows.length, 6);
  assert.deepEqual(acceptance.presentation_states, ["normal", "loading", "empty", "partial", "stale", "offline", "unauthorized", "conflict", "refusal", "retry"]);
  const ids = new Set(acceptance.adversarial_tests.map(item => item.id));
  for (const required of ["cross_audience_replay", "profile_not_auth", "self_escalation", "stale_approval", "wrong_environment_or_target", "injection_is_data", "secret_canary", "offline_or_stale_collector", "reconnect_authority", "agent_new_session", "audit_chain", "server_derived_tenant_context", "cross_tenant_denial_fixture", "platform_tenant_authority_split", "workflow_lifecycle_governance", "doc_outage_operability", "maintenance_accounting_gate", "tenant_config_allowlist", "actionable_alert_and_safe_automation", "sponsor_agent_identity_separation", "joe_sponsored_personal_rules", "dell_sponsored_personal_rules", "cross_brain_personal_read_denied", "unattended_agent_humanonly_denied", "personal_scope_unknown_not_zero", "connector_render_brain_parity", "personal_rules_cannot_widen_authority", "audit_agent_and_sponsor_attribution"]) {
    assert.ok(ids.has(required), required);
  }
});

test("audit taxonomy is append only and reconstructable", () => {
  const audit = read("contracts/audit-event-taxonomy.v1.json");
  assert.equal(audit.append_only, true);
  assert.deepEqual(audit.chain, ["origin", "agent_session", "plan_revision", "approval", "execution", "verification", "release_or_incident"]);
  assert.ok(audit.never_record.includes("secret values"));
  assert.ok(audit.never_record.includes("personal rule bodies"));
  for (const field of ["organization_tenant_id", "agent_principal_id", "runtime_principal", "sponsoring_human_id", "partner_id", "personal_brain_scope", "personal_brain_version", "personal_rule_count", "session_capability_profile"]) assert.ok(audit.required_fields.includes(field), field);
  assert.ok(audit.families.governance.includes("unsupported_action.refused"));
});

test("policy learning stays offline, bounded, and non-authoritative", () => {
  const registry = read("contracts/policy-learning-formulation-registry.v1.json");
  const envelope = read("contracts/policy-learning-envelope.v1.schema.json");
  assert.equal(registry.default_effect, "deny");
  assert.equal(registry.learnability_boundary.production_learning, "forbidden");
  assert.equal(registry.learnability_boundary.automatic_promotion, "forbidden");
  assert.equal(registry.learnability_boundary.tool_environment_observations, "masked_not_actions");
  assert.ok(registry.ineligible_action_classes.includes("write"));
  assert.ok(registry.ineligible_action_classes.includes("release"));
  assert.ok(registry.domains.some(row => row.formulation === "token_or_trajectory_mdp" && row.learnability === "observational_only"));
  assert.equal(envelope.properties.data_class.enum.includes("synthetic_only"), true);
  assert.equal(envelope.additionalProperties, false);
});
test("S0 memory evaluation baseline is frozen, complete, and non-authoritative", () => {
  const schema = read("contracts/memory-evaluation-s0.v1.schema.json");
  const fixture = read("contracts/fixtures/memory-evaluation-s0.synthetic.v1.json");
  const validate = compileSchema(schema);
  const validation = validate(fixture);
  assert.equal(validation.valid, true, validation.errors?.join("\n"));

  const refuse = (mutate, label) => {
    const invalid = structuredClone(fixture);
    mutate(invalid);
    assert.equal(validate(invalid).valid, false, label);
  };
  refuse(value => { value.calls_models = true; }, "model calls are outside S0");
  refuse(value => { value.writes_records = true; }, "record writes are outside S0");
  refuse(value => { value.production_behavior_changes = true; }, "production behavior changes are outside S0");
  refuse(value => { value.source_lock.sources[0].unreviewed_claim = true; }, "source shapes are closed");
  refuse(value => { value.source_lock.sources[0].pin.value = "unpinned"; }, "source revisions must be exact");
  refuse(value => { value.memory_evaluation_contract.cases[0].contains_business_payload = true; }, "business payloads are forbidden");
  refuse(value => { value.memory_evaluation_contract.cases = value.memory_evaluation_contract.cases.slice(0, 14); }, "the frozen corpus cannot fall below its minimum");
  refuse(value => { value.threat_model.authority.memory_is_authority = true; }, "memory never becomes authority");
  refuse(value => { value.acceptance_evidence_matrix[0].check_refs = ["check:S0-unregistered"]; }, "matrix checks must use registered shapes");

  const digestBody = structuredClone(fixture);
  delete digestBody.artifact_digest;
  assert.equal(fixture.artifact_digest, digest(digestBody));
  assert.equal(fixture.schema_version, "memory-evaluation-s0.v1");
  assert.equal(fixture.status, "frozen_contracts_only");
  assert.equal(fixture.data_class, "synthetic_only");
  assert.equal(fixture.calls_models, false);
  assert.equal(fixture.writes_records, false);
  assert.equal(fixture.production_behavior_changes, false);

  assert.deepEqual(fixture.binding, {
    work_request_ref: "WR-000013",
    work_request_version: 3,
    accepted_plan_ref: "PLAN-d85ba97ccd46-v1",
    accepted_plan_digest: "sha256:d85ba97ccd4686b619fe72eaab80732057327ecbdb9b436fd1af2083960bad2e",
    registered_slice_plan_id: "cfc1d210-1069-4994-8145-8ef9b1299e9c",
    registered_slice_plan_digest: "sha256:5bb7039f727da6c99d2b7b36a563b12234360bdb23219ab7800e42dbc7ce2149",
    runbook_section_id: "0d3a6481-583f-4b3a-893f-505fd98ddcb4",
    runbook_revision_id: "0c3ecb3e-ff2f-49ac-8466-60f5f698726a",
    runbook_content_digest: "sha256:ad2316cf6c9f28c56f9c19f3033601a78021c3ade5628c2f3ea1d09d4434c718",
    slice_ref: "slice:learning-baseline-contracts-v2"
  });

  const resourceBindings = new Map(fixture.resource_bindings.map(row => [row.resource_ref, row]));
  const expectedResources = {
    "resource:source-lock": ["revision:3", "sha256:a4bd7234feadf4556abc10091a6e7464b96fcb8a293fc0dbfce3ad6171a6996d"],
    "resource:evaluation-contract": ["revision:3", "sha256:9deb92a11ce6e86c96427b26d53d1600163888d69a6ddc8e4950327e11cf471c"],
    "resource:threat-model": ["revision:3", "sha256:f802c5ef05ebb49c64adc28725f544fb2d34c60b4ee63b9cce6ba7c11aefc0ff"],
    "resource:cost-model": ["revision:3", "sha256:39c51c478ceff930a43bdb6846c17515c2a6c236603472866c8ad018c4c7604c"],
    "resource:legacy-inventory": ["revision:3", "sha256:78d122e08129a489f3f14ef850f890c5a7ba276096924f448eb48f7d8103914a"]
  };
  assert.deepEqual([...resourceBindings.keys()].sort(), Object.keys(expectedResources).sort());
  for (const [resourceRef, [revisionRef, contentDigest]] of Object.entries(expectedResources)) {
    assert.equal(resourceBindings.get(resourceRef)?.revision_ref, revisionRef);
    assert.equal(resourceBindings.get(resourceRef)?.content_digest, contentDigest);
  }

  const sourceFamilies = new Set(fixture.source_lock.sources.map(row => row.family));
  assert.deepEqual([...sourceFamilies].sort(), ["carr", "hermes", "mem0", "neon", "openviking", "postgresql", "practitioner"].sort());
  for (const source of fixture.source_lock.sources) {
    assert.match(source.exact_ref, /^(https:\/\/|repo:|doctrine:)/);
    assert.match(source.pin.value, /^(sha256:|git:|revision:)/);
    assert.ok(["current", "legacy", "mixed"].includes(source.behavior_epoch));
    assert.ok(["vendor_claim", "packet_bound_claim", "source_inspected", "locally_reproduced"].includes(source.claim_class));
    assert.ok(source.claims.length > 0);
    assert.ok(source.verification.method.length > 0);
  }
  assert.ok(fixture.source_lock.sources.some(row => row.source_id === "mem0-v3-additive" && row.behavior_epoch === "current"));
  assert.ok(fixture.source_lock.sources.some(row => row.source_id === "mem0-legacy-operations" && row.behavior_epoch === "legacy"));
  assert.ok(fixture.source_lock.sources.some(row => row.source_id === "hermes-memory-manager" && row.claims.some(claim => /built-in memory.*external provider/i.test(claim))));
  assert.ok(fixture.source_lock.sources.every(row => row.verification.observed_at.startsWith("2026-08-26T")));
  assert.deepEqual(fixture.source_lock.sources.find(row => row.source_id === "practitioner-reading")?.pin, {
    value: "sha256:45ce1d4a0c666d8e18ded71b51336b82394a3c6cd5ae9902459c99288af6614f",
    kind: "observed HTTP response-body digest"
  });

  const evaluation = fixture.memory_evaluation_contract;
  assert.ok(evaluation.cases.length >= 15);
  assert.equal(new Set(evaluation.cases.map(row => row.case_id)).size, evaluation.cases.length);
  for (const row of evaluation.cases) {
    assert.equal(row.synthetic, true);
    assert.equal(row.contains_business_payload, false);
    assert.match(row.immutable_input_digest, /^sha256:[a-f0-9]{64}$/);
    assert.match(row.expected_output_digest, /^sha256:[a-f0-9]{64}$/);
    assert.equal(row.immutable_input_digest, digest(row.input_fixture), `${row.case_id} input digest`);
    assert.equal(row.expected_output_digest, digest(row.expected_fixture), `${row.case_id} expected digest`);
    assert.ok(evaluation.labels.adjudication.includes(row.adjudication_label), `${row.case_id} adjudication label`);
    assert.ok(row.suite_ids.length > 0);
  }
  const corpusRows = evaluation.cases.map(({case_id, split, suite_ids, label, adjudication_label, critical, immutable_input_digest, expected_output_digest}) => ({case_id, split, suite_ids, label, adjudication_label, critical, immutable_input_digest, expected_output_digest}));
  assert.equal(evaluation.corpus_digest, digest(corpusRows));
  assert.deepEqual(evaluation.splits.map(row => row.name), ["development", "tuning", "held_out", "temporal_holdout", "prospective_non_derivation"]);
  assert.deepEqual(evaluation.suites.map(row => row.suite_id).sort(), ["accumulation", "adversarial", "exact_vs_ann", "held_out_real", "lexical_degraded", "outage", "public_directional", "temporal"].sort());
  for (const split of evaluation.splits) {
    assert.match(split.case_set_digest, /^sha256:[a-f0-9]{64}$/);
    const cases = evaluation.cases.filter(row => row.split === split.name)
      .map(({case_id, immutable_input_digest, expected_output_digest}) => ({case_id, immutable_input_digest, expected_output_digest}));
    assert.ok(cases.length > 0, `${split.name} cases`);
    assert.equal(split.case_set_digest, digest(cases), `${split.name} digest`);
  }
  for (const metric of evaluation.metrics) {
    assert.ok(metric.formula.length > 0);
    assert.ok(metric.threshold.length > 0);
    assert.ok(metric.bound_action.length > 0);
    assert.ok(["block_release", "pause_canary", "degrade_lexical", "require_review", "report_only"].includes(metric.failure_action));
  }
  assert.ok(evaluation.labels.adjudication.length > 0);
  assert.ok(evaluation.confidence.bootstrap_iterations >= 1000);
  assert.equal(evaluation.confidence.random_seed, 20260826);
  for (const key of ["answerer", "judge", "embedding", "reranker", "extractor"]) assert.ok(evaluation.model_versions[key]);
  assert.ok(evaluation.index_generation);
  assert.ok(evaluation.outcome_horizons.length > 0);
  assert.deepEqual(evaluation.measurement_units, {latency: "milliseconds", cost: "usd", storage: "bytes", tokens: "model_tokens"});

  assert.equal(fixture.synthetic_scope_fixtures.tenants.length, 2);
  assert.equal(fixture.synthetic_scope_fixtures.sponsors.length, 2);
  assert.equal(fixture.synthetic_scope_fixtures.agents.length, 2);
  assert.equal(fixture.synthetic_scope_fixtures.projects.length, 2);
  assert.equal(fixture.synthetic_scope_fixtures.sessions.length, 2);
  assert.ok(fixture.synthetic_scope_fixtures.cases.every(row => row.synthetic === true && row.contains_business_payload === false));
  for (const row of fixture.synthetic_scope_fixtures.cases) {
    const input = {agent_id: row.agent_id, project_id: row.project_id, session_id: row.session_id, sponsor_id: row.sponsor_id, tenant_id: row.tenant_id};
    assert.equal(row.immutable_input_digest, digest(input), `${row.case_id} scope input digest`);
    assert.equal(row.expected_output_digest, digest(row.expected_assertions), `${row.case_id} scope output digest`);
  }

  const threatIds = new Set(fixture.threat_model.threats.map(row => row.threat_id));
  for (const required of ["cross_scope_leak", "memory_poisoning", "secret_capture", "stale_publish_race", "model_self_approval", "dual_memory_authority", "shared_postfilter_ann", "deletion_restore_revival", "opaque_ranking", "cost_runaway"]) assert.ok(threatIds.has(required), required);
  for (const threat of fixture.threat_model.threats) {
    assert.ok(threat.controls.length > 0, `${threat.threat_id} controls`);
    assert.ok(threat.test_refs.length > 0, `${threat.threat_id} tests`);
    assert.ok(threat.release_action.length > 0, `${threat.threat_id} action`);
  }
  assert.equal(fixture.threat_model.authority.memory_is_authority, false);
  assert.equal(fixture.threat_model.authority.model_can_activate, false);
  assert.equal(fixture.threat_model.authority.neon_is_sole_canonical_store, true);

  const cost = fixture.cost_latency_storage_model;
  assert.deepEqual(cost.envelopes.map(row => row.envelope_id), ["carr_operating_12_month", "fleet_stress_12_month"]);
  assert.ok(cost.envelopes.every(row => row.horizon_months === 12));
  assert.ok(cost.ceilings.every(row => row.bound_action.length > 0));
  assert.ok(cost.measurements.every(row => row.measurement_status === "starting_ceiling_unmeasured" || row.evidence_refs.length > 0));
  assert.ok(cost.storage_model.components.every(row => row.formula.length > 0 && row.bound_action.length > 0));

  const gapById = new Map(fixture.current_gap_legacy_inventory.map(row => [row.inventory_id, row]));
  const sliceChecks = {
    "slice:learning-baseline-contracts-v2": ["check:S0-1", "check:S0-2", "check:S0-3"],
    "slice:learning-schema-safety-v2": ["check:S1-1", "check:S1-2", "check:S1-3"],
    "slice:learning-capture-delete-v2": ["check:S2-1", "check:S2-2", "check:S2-3"],
    "slice:learning-extraction-consolidation-v2": ["check:S3-1", "check:S3-2", "check:S3-3"],
    "slice:learning-context-bundle-v2": ["check:S4-1", "check:S4-2", "check:S4-3"],
    "slice:learning-hybrid-retrieval-v2": ["check:S5-1", "check:S5-2", "check:S5-3"],
    "slice:learning-context-packer-v2": ["check:S6-1", "check:S6-2", "check:S6-3"],
    "slice:learning-hermes-bridge-v2": ["check:S7-1", "check:S7-2", "check:S7-3"],
    "slice:learning-outcome-signals-v2": ["check:S8-1", "check:S8-2", "check:S8-3"],
    "slice:learning-temporal-entity-v2": ["check:S9-1", "check:S9-2", "check:S9-3"],
    "slice:learning-control-room-v2": ["check:S10-1", "check:S10-2", "check:S10-3"],
    "slice:learning-procedure-compiler-v2": ["check:S11-1", "check:S11-2", "check:S11-3"],
    "slice:learning-hardening-v2": ["check:S12-1", "check:S12-2", "check:S12-3"],
    "slice:learning-release-canary-v2": ["check:S13-1", "check:S13-2", "check:S13-3"]
  };
  for (const required of ["migration-0299", "context-bundle-v1", "security-redaction-v1", "policy-learning-v1", "memory-evaluation", "hermes-native-memory", "standing-context", "situation-retrieval"]) assert.ok(gapById.has(required), required);
  for (const row of gapById.values()) {
    assert.ok(["current", "legacy", "partial", "missing"].includes(row.status));
    assert.ok(row.truthful_current_behavior.length > 0);
    assert.ok(row.evidence_refs.length >= 2, `${row.inventory_id} evidence`);
    assert.ok(row.gap.length > 0);
    assert.match(row.owning_slice_ref, /^slice:/);
    assert.ok(sliceChecks[row.owning_slice_ref], `${row.inventory_id} registered owning slice`);
    assert.match(row.executable_gate_ref, /^(check:|test:|gate:)/);
    assert.equal(row.baseline_gate_ref, "gate:S0-3-evidence-ownership");
    assert.ok(["canonical", "derived", "advisory", "legacy_non_authoritative", "not_implemented"].includes(row.authority_class));
  }

  const criteria = ["RESEARCH-SOURCE-LOCK", "SINGLE-NEON-AUTHORITY", "IMMUTABLE-BITEMPORAL-LINEAGE", "ATTESTED-CONCURRENT-CAPTURE", "SCOPE-DATABASE-ISOLATION", "NONFORGEABLE-HUMAN-ACCEPTANCE", "SAFE-HERMES-CONTEXT", "HYBRID-PROGRESSIVE-RECALL", "POISONING-PRIVACY-DELETION", "EXECUTABLE-EVALUATION", "DEMONSTRATED-PROCEDURAL-LEARNING", "OPERATED-RECOVERED-FIRST-USE"];
  const matrix = fixture.acceptance_evidence_matrix;
  assert.deepEqual(matrix.map(row => row.criterion_id).sort(), [...criteria].sort());
  for (const row of matrix) {
    assert.ok(row.producing_slice_refs.length > 0, `${row.criterion_id} slices`);
    assert.ok(row.check_refs.length > 0, `${row.criterion_id} checks`);
    assert.ok(row.evidence_owner_refs.length > 0, `${row.criterion_id} owners`);
    assert.ok(row.executable_gate_refs.length > 0, `${row.criterion_id} gates`);
    assert.equal(row.baseline_gate_ref, "gate:S0-3-evidence-ownership");
    assert.ok(row.evidence_refs.length > 0, `${row.criterion_id} evidence`);
    const allowedChecks = new Set(row.producing_slice_refs.flatMap(sliceRef => {
      assert.ok(sliceChecks[sliceRef], `${row.criterion_id} registered producing slice ${sliceRef}`);
      return sliceChecks[sliceRef];
    }));
    assert.ok(row.check_refs.every(checkRef => allowedChecks.has(checkRef)), `${row.criterion_id} checks belong to producing slices`);
  }
  assert.deepEqual([...new Set(matrix.flatMap(row => row.check_refs).filter(ref => /^check:S0-/.test(ref)))].sort(), ["check:S0-1", "check:S0-2", "check:S0-3"]);

  assert.deepEqual(fixture.forbidden_changes.sort(), ["forbid:implicit-policy-override", "forbid:model-self-approval", "forbid:mutable-history", "forbid:parallel-context-authority", "forbid:raw-transcript-mirror", "forbid:shared-postfilter-ann", "forbid:simultaneous-hermes-neon-authority"].sort());
  const serialized = JSON.stringify(fixture).toLowerCase();
  for (const forbidden of ["database_url=", "private key", "client_payload", "raw_transcript", "secret_value"]) assert.equal(serialized.includes(forbidden), false, forbidden);
});

test("check:S0-1 pins every research revision and separates claims from reproduced evidence", () => {
  const schema = read("contracts/memory-evaluation-s0.v1.schema.json");
  const fixture = read("contracts/fixtures/memory-evaluation-s0.synthetic.v1.json");
  const validate = compileSchema(schema);
  assert.equal(validate(fixture).valid, true);
  assert.equal(fixture.source_lock.gate_ref, "gate:S0-1-source-lock");

  const allowedClasses = new Set(["vendor_claim", "packet_bound_claim", "source_inspected", "locally_reproduced"]);
  for (const source of fixture.source_lock.sources) {
    assert.match(source.exact_ref, /^(https:\/\/|repo:|doctrine:)/, `${source.source_id} exact ref`);
    assert.match(source.pin.value, /^(git:[a-f0-9]{7,40}|sha256:[a-f0-9]{64}|revision:[a-z0-9][a-z0-9._:-]+)$/, `${source.source_id} exact pin`);
    assert.ok(allowedClasses.has(source.claim_class), `${source.source_id} claim class`);
    assert.ok(source.claims.length > 0, `${source.source_id} claims`);
  }

  const reproduced = fixture.source_lock.sources.filter(source => source.claim_class === "locally_reproduced");
  assert.deepEqual(reproduced.map(source => source.source_id), ["carr-memory-kernel"]);
  const kernelPath = path.resolve(ROOT, "..", reproduced[0].exact_ref.slice("repo:".length));
  const kernelDigest = `sha256:${crypto.createHash("sha256").update(fs.readFileSync(kernelPath)).digest("hex")}`;
  assert.equal(reproduced[0].pin.value, kernelDigest);
  const kernelText = fs.readFileSync(kernelPath, "utf8");
  for (const invariant of ["create table memory_item", "create table memory_evidence", "memory successor lineage does not match corrected predecessor", "Deny-by-default"]) {
    assert.ok(kernelText.includes(invariant), `reproduced kernel invariant: ${invariant}`);
  }

  const runbook = fixture.source_lock.sources.find(source => source.source_id === "carr-runbook");
  assert.equal(runbook.claim_class, "packet_bound_claim");
  assert.match(runbook.verification.method, /no body reproduction/i);
  const hermes = fixture.source_lock.sources.find(source => source.source_id === "hermes-memory-manager");
  assert.equal(hermes.claims.some(claim => /CARR remains/i.test(claim)), false);

  const invalidPin = structuredClone(fixture);
  invalidPin.source_lock.sources[0].pin.value = "branch:main";
  assert.equal(validate(invalidPin).valid, false);
  const invalidClass = structuredClone(fixture);
  invalidClass.source_lock.sources[0].claim_class = "reproduced_by_assertion";
  assert.equal(validate(invalidClass).valid, false);
});

test("check:S0-2 freezes executable corpus, splits, labels, metrics, confidence, versions, cost, latency, and horizons", () => {
  const schema = read("contracts/memory-evaluation-s0.v1.schema.json");
  const fixture = read("contracts/fixtures/memory-evaluation-s0.synthetic.v1.json");
  const validate = compileSchema(schema);
  assert.equal(validate(fixture).valid, true);
  const evaluation = fixture.memory_evaluation_contract;
  assert.equal(evaluation.gate_ref, "gate:S0-2-evaluation-contract");
  assert.equal(evaluation.cases.length, 16);
  assert.equal(new Set(evaluation.cases.map(row => row.case_id)).size, 16);

  for (const row of evaluation.cases) {
    assert.equal(row.synthetic, true);
    assert.equal(row.contains_business_payload, false);
    assert.equal(row.immutable_input_digest, digest(row.input_fixture), `${row.case_id} input`);
    assert.equal(row.expected_output_digest, digest(row.expected_fixture), `${row.case_id} expected`);
    assert.ok(evaluation.labels.adjudication.includes(row.adjudication_label), `${row.case_id} label`);
  }
  const corpusRows = evaluation.cases.map(({case_id, split, suite_ids, label, adjudication_label, critical, immutable_input_digest, expected_output_digest}) => ({case_id, split, suite_ids, label, adjudication_label, critical, immutable_input_digest, expected_output_digest}));
  assert.equal(evaluation.corpus_digest, digest(corpusRows));

  const expectedSplits = ["development", "tuning", "held_out", "temporal_holdout", "prospective_non_derivation"];
  assert.deepEqual(evaluation.splits.map(row => row.name), expectedSplits);
  for (const split of evaluation.splits) {
    const cases = evaluation.cases.filter(row => row.split === split.name)
      .map(({case_id, immutable_input_digest, expected_output_digest}) => ({case_id, immutable_input_digest, expected_output_digest}));
    assert.ok(cases.length > 0, `${split.name} case coverage`);
    assert.equal(split.case_set_digest, digest(cases), `${split.name} digest`);
  }
  const expectedSuites = ["accumulation", "adversarial", "exact_vs_ann", "held_out_real", "lexical_degraded", "outage", "public_directional", "temporal"];
  assert.deepEqual(evaluation.suites.map(row => row.suite_id).sort(), expectedSuites);
  const coveredSuites = new Set(evaluation.cases.flatMap(row => row.suite_ids));
  assert.deepEqual([...coveredSuites].sort(), expectedSuites);

  for (const metric of evaluation.metrics) {
    assert.ok(metric.formula.length >= 8, `${metric.metric_id} formula`);
    assert.ok(metric.threshold.length >= 2, `${metric.metric_id} threshold`);
    assert.ok(metric.bound_action.length >= 8, `${metric.metric_id} action`);
  }
  assert.deepEqual(evaluation.confidence, {
    method: "bootstrap percentile interval",
    bootstrap_iterations: 2000,
    threshold: "95% interval must not cross the metric failure threshold",
    random_seed: 20260826
  });
  assert.deepEqual(Object.keys(evaluation.model_versions).sort(), ["answerer", "embedding", "extractor", "judge", "reranker"]);
  assert.match(evaluation.index_generation, /^index:/);
  assert.deepEqual(evaluation.outcome_horizons.map(row => row.window), ["0-24h", "30d", "90d"]);
  assert.deepEqual(evaluation.measurement_units, {latency: "milliseconds", cost: "usd", storage: "bytes", tokens: "model_tokens"});

  const cost = fixture.cost_latency_storage_model;
  assert.deepEqual(cost.envelopes.map(row => [row.envelope_id, row.horizon_months]), [["carr_operating_12_month", 12], ["fleet_stress_12_month", 12]]);
  for (const required of ["latency_prefetch", "latency_deep", "canary_spend", "live_spend", "ann_payload", "tokens_prefetch", "tokens_deep"]) {
    assert.ok(cost.ceilings.some(row => row.ceiling_id === required && row.formula.length > 0 && row.bound_action.length > 0), required);
  }
  assert.deepEqual(cost.measurements.map(row => row.measurement_status), ["starting_ceiling_unmeasured", "starting_ceiling_unmeasured", "starting_ceiling_unmeasured"]);
});

test("check:S0-3 binds every current gap, legacy authority route, and acceptance criterion to owners and executable gates", () => {
  const schema = read("contracts/memory-evaluation-s0.v1.schema.json");
  const fixture = read("contracts/fixtures/memory-evaluation-s0.synthetic.v1.json");
  const validate = compileSchema(schema);
  assert.equal(validate(fixture).valid, true);

  const gates = new Map(fixture.gate_registry.map(row => [row.gate_ref, row]));
  assert.deepEqual([...gates.keys()].sort(), ["gate:S0-1-source-lock", "gate:S0-2-evaluation-contract", "gate:S0-3-evidence-ownership"]);
  for (const gate of gates.values()) {
    assert.match(gate.owner_ref, /^owner:/);
    assert.equal(gate.evidence_requirement, "redacted_evidence_required");
    assert.match(gate.command, /^node --test --test-name-pattern='check:S0-[1-3]' control-room\/test\/contracts\.test\.mjs$/);
    assert.equal(fs.existsSync(path.resolve(ROOT, "..", "control-room/test/contracts.test.mjs")), true);
  }

  const sliceChecks = {
    "slice:learning-baseline-contracts-v2": ["check:S0-1", "check:S0-2", "check:S0-3"],
    "slice:learning-schema-safety-v2": ["check:S1-1", "check:S1-2", "check:S1-3"],
    "slice:learning-capture-delete-v2": ["check:S2-1", "check:S2-2", "check:S2-3"],
    "slice:learning-extraction-consolidation-v2": ["check:S3-1", "check:S3-2", "check:S3-3"],
    "slice:learning-context-bundle-v2": ["check:S4-1", "check:S4-2", "check:S4-3"],
    "slice:learning-hybrid-retrieval-v2": ["check:S5-1", "check:S5-2", "check:S5-3"],
    "slice:learning-context-packer-v2": ["check:S6-1", "check:S6-2", "check:S6-3"],
    "slice:learning-hermes-bridge-v2": ["check:S7-1", "check:S7-2", "check:S7-3"],
    "slice:learning-outcome-signals-v2": ["check:S8-1", "check:S8-2", "check:S8-3"],
    "slice:learning-temporal-entity-v2": ["check:S9-1", "check:S9-2", "check:S9-3"],
    "slice:learning-control-room-v2": ["check:S10-1", "check:S10-2", "check:S10-3"],
    "slice:learning-procedure-compiler-v2": ["check:S11-1", "check:S11-2", "check:S11-3"],
    "slice:learning-hardening-v2": ["check:S12-1", "check:S12-2", "check:S12-3"],
    "slice:learning-release-canary-v2": ["check:S13-1", "check:S13-2", "check:S13-3"]
  };

  for (const row of fixture.current_gap_legacy_inventory) {
    assert.ok(sliceChecks[row.owning_slice_ref]?.includes(row.executable_gate_ref), `${row.inventory_id} owning gate`);
    assert.ok(gates.has(row.baseline_gate_ref), `${row.inventory_id} baseline gate`);
    assert.ok(row.evidence_refs.length >= 2, `${row.inventory_id} evidence`);
    for (const evidenceRef of row.evidence_refs.filter(ref => ref.startsWith("repo:"))) {
      assert.equal(fs.existsSync(path.resolve(ROOT, "..", evidenceRef.slice("repo:".length))), true, evidenceRef);
    }
  }
  const hermes = fixture.current_gap_legacy_inventory.find(row => row.inventory_id === "hermes-native-memory");
  assert.equal(hermes.status, "legacy");
  assert.equal(hermes.authority_class, "legacy_non_authoritative");
  assert.match(hermes.gap, /not yet mutually exclusive/i);

  const expectedCriteria = ["ATTESTED-CONCURRENT-CAPTURE", "DEMONSTRATED-PROCEDURAL-LEARNING", "EXECUTABLE-EVALUATION", "HYBRID-PROGRESSIVE-RECALL", "IMMUTABLE-BITEMPORAL-LINEAGE", "NONFORGEABLE-HUMAN-ACCEPTANCE", "OPERATED-RECOVERED-FIRST-USE", "POISONING-PRIVACY-DELETION", "RESEARCH-SOURCE-LOCK", "SAFE-HERMES-CONTEXT", "SCOPE-DATABASE-ISOLATION", "SINGLE-NEON-AUTHORITY"];
  assert.deepEqual(fixture.acceptance_evidence_matrix.map(row => row.criterion_id).sort(), expectedCriteria);
  for (const row of fixture.acceptance_evidence_matrix) {
    assert.ok(row.evidence_owner_refs.length > 0, `${row.criterion_id} owner`);
    assert.ok(gates.has(row.baseline_gate_ref), `${row.criterion_id} baseline gate`);
    assert.ok(row.executable_gate_refs.length > 0, `${row.criterion_id} producing gate`);
    assert.ok(row.evidence_refs.length > 0, `${row.criterion_id} evidence`);
    const ownedChecks = new Set(row.producing_slice_refs.flatMap(ref => sliceChecks[ref] ?? []));
    assert.ok(row.check_refs.every(ref => ownedChecks.has(ref)), `${row.criterion_id} check ownership`);
  }
});
