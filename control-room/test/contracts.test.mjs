import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = relative => JSON.parse(fs.readFileSync(path.join(ROOT, relative), "utf8"));

test("all contract files are valid versioned JSON", () => {
  const files = fs.readdirSync(path.join(ROOT, "contracts")).filter(name => name.endsWith(".json"));
  assert.ok(files.length >= 9);
  for (const file of files) {
    const value = read(path.join("contracts", file));
    if (file !== "fixture-schema.v1.json") {
      assert.equal(value.version, "1.0.0", file);
      assert.match(value.status, /^phase0_/, file);
    }
  }
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
  for (const required of ["cross_audience_replay", "profile_not_auth", "self_escalation", "stale_approval", "wrong_environment_or_target", "injection_is_data", "secret_canary", "offline_or_stale_collector", "reconnect_authority", "agent_new_session", "audit_chain", "server_derived_tenant_context", "cross_tenant_denial_fixture", "platform_tenant_authority_split", "workflow_lifecycle_governance", "doc_outage_operability", "maintenance_accounting_gate", "tenant_config_allowlist", "actionable_alert_and_safe_automation"]) {
    assert.ok(ids.has(required), required);
  }
});

test("audit taxonomy is append only and reconstructable", () => {
  const audit = read("contracts/audit-event-taxonomy.v1.json");
  assert.equal(audit.append_only, true);
  assert.deepEqual(audit.chain, ["origin", "agent_session", "plan_revision", "approval", "execution", "verification", "release_or_incident"]);
  assert.ok(audit.never_record.includes("secret values"));
  assert.ok(audit.families.governance.includes("unsupported_action.refused"));
});
