import test from "node:test";
import assert from "node:assert/strict";
import { doctrineTools } from "../src/doctrine.js";
import { ToolError } from "../src/tools.js";

const writes = [];
const tools = doctrineTools({
  withEnvelope: async (_c, _a, _v, _args, fn) => fn(),
  writeEvent: async (...args) => writes.push(args),
  ToolError,
});
const actor = { id: "actor-id", slug: "codex" };

test("rule-context returns active scoped rules without partner selection", async () => {
  const client = { query: async (sql, params) => {
    assert.match(sql, /personal_to is null/);
    assert.deepEqual(params, ["intro_politics"]);
    return { rows: [{ id: "r1", statement: "Do not pair competitors" }] };
  }};
  const out = await tools["rule-context"].handler(client, actor, { scope_kind: "intro_politics" });
  assert.equal(out.count, 1);
});

test("record-system-evidence writes typed append-only evidence", async () => {
  writes.length = 0;
  const client = { query: async () => ({ rows: [{ id: "evidence-uuid" }] }) };
  const out = await tools["record-system-evidence"].handler(client, actor, {
    idempotency_key: "idem", evidence_type: "cutoff_cold_start",
    observations: { fresh_session: true }, provenance: "task 123 live readback",
  });
  assert.equal(out.evidence_id, "evidence-uuid");
  assert.equal(writes.length, 1);
  assert.equal(writes[0][2], "record-system-evidence");
  assert.equal(writes[0][3], "system_evidence");
});

test("cutoff-evidence requires a current doctrine revision", async () => {
  const client = { query: async (sql) => {
    if (sql.includes("from event")) return { rows: [] };
    assert.match(sql, /current_revision_id=r.id/);
    return { rows: [{ id: "rev", plain_text: "standing-context", commit_message: "updated",
                      actor: "codex", created_at: "now", section_key: "s00",
                      document: "carr-workspace-bduf" }] };
  }};
  const out = await tools["cutoff-evidence"].handler(client, actor, { record_id: "rev" });
  assert.equal(out.evidence.current, true);
  assert.equal(out.evidence.document, "carr-workspace-bduf");
});

const ids = {
  monday: "11111111-1111-4111-8111-111111111111",
  workspace: "22222222-2222-4222-8222-222222222221",
  control: "22222222-2222-4222-8222-222222222222",
  mature: "22222222-2222-4222-8222-222222222223",
  approval: "33333333-3333-4333-8333-333333333333",
  stageEvidence: "44444444-4444-4444-8444-444444444441",
  cold: "44444444-4444-4444-8444-444444444442",
  rollback: "44444444-4444-4444-8444-444444444443",
  finalApproval: "55555555-5555-4555-8555-555555555551",
  rollbackApproval: "55555555-5555-4555-8555-555555555552",
};

function transitionHarness({ state = "active", sponsor = "joe", tenant = "carr-internal" } = {}) {
  const mutations = [], audits = [];
  const docs = new Map([
    [ids.workspace, "carr-workspace-bduf"], [ids.control, "carr-control-room-bduf"],
    [ids.mature, "carr-mature-software-end-state-bduf"],
  ]);
  const client = { query: async (sql, params = []) => {
    if (sql.includes("select key, value") && sql.includes("system_config")) {
      const rows = [];
      if (state === "retiring") rows.push({ key: "doctrine.md_renders_retiring", value: "true" });
      if (state === "retired") rows.push({ key: "doctrine.md_renders_retired", value: "true" });
      return { rows };
    }
    if (sql.includes("from event e")) {
      if (params[0] === ids.monday) return { rows: [{ verb: "record-system-evidence",
        subject_type: "system_evidence", actor: "codex", sponsoring_human_slug: sponsor,
        organization_tenant_id: tenant, new_value: { evidence_type: "monday_store_cycle",
          observations: { cycle: "monday", store_first: true, heartbeat_complete: true },
          provenance: "live Monday task readback" } }] };
      if (params[0] === ids.approval) return { rows: [{ verb: "log-decision",
        subject_type: "decision", actor: "codex", sponsoring_human_slug: sponsor,
        organization_tenant_id: tenant, human_quote: "I approve cutoff stage",
        new_value: { provenance: "Joe live approval" } }] };
      const systemTypes = new Map([[ids.stageEvidence, ["cutoff_stage_smoke",
          { manifest_sha256: "b".repeat(64) }]], [ids.cold, ["cutoff_cold_start",
          { fresh_session: true, standing_context: true, file_bootstrap_used: false,
            shared_count: 144, personal_count: 30 }]], [ids.rollback, ["cutoff_rollback",
          { manifest_sha256: "b".repeat(64), collision_preflight_passed: true,
            restored_hashes_verified: true }]]]);
      if (systemTypes.has(params[0])) {
        const [evidence_type, observations] = systemTypes.get(params[0]);
        return { rows: [{ verb: "record-system-evidence", subject_type: "system_evidence",
          actor: "codex", sponsoring_human_slug: sponsor, organization_tenant_id: tenant,
          new_value: { evidence_type, observations, provenance: "verified live readback" } }] };
      }
      const approvals = new Map([[ids.finalApproval, "final"], [ids.rollbackApproval, "rollback"]]);
      if (approvals.has(params[0])) return { rows: [{ verb: "log-decision",
        subject_type: "decision", actor: "codex", sponsoring_human_slug: sponsor,
        organization_tenant_id: tenant,
        human_quote: `I approve cutoff ${approvals.get(params[0])}`,
        new_value: { provenance: "Joe live approval" } }] };
      return { rows: [] };
    }
    if (sql.includes("from doctrine_revision")) {
      const document = docs.get(params[0]);
      return { rows: document ? [{ id: params[0], document, actor: "codex",
        plain_text: "Fresh sessions call standing-context through the record store",
        commit_message: "Joe-approved bootstrap revision" }] : [] };
    }
    if (sql.includes("insert into system_config")) {
      mutations.push(params);
      state = params[1] === "true" ? "retired" : (params[0] === "true" ? "retiring" : "active");
      return { rows: [] };
    }
    if (sql.includes("select gen_random_uuid")) return { rows: [{ id: "transition-id" }] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const cache = new Map();
  const localTools = doctrineTools({
    withEnvelope: async (_c, _a, _v, args, fn) => {
      if (cache.has(args.idempotency_key)) return { replayed: true, ...cache.get(args.idempotency_key) };
      const result = await fn(); cache.set(args.idempotency_key, result); return result;
    },
    writeEvent: async (...args) => audits.push(args), ToolError,
  });
  return { client, tool: localTools["transition-doctrine-cutoff"], mutations, audits };
}

function stageArgs(extra = {}) {
  return { idempotency_key: "transition-idempotency", expected_state: "active",
    target_state: "retiring", approved_commit: "a".repeat(40), manifest_sha256: "b".repeat(64),
    monday_evidence_id: ids.monday,
    bootstrap_revision_ids: [ids.workspace, ids.control, ids.mature],
    approval_decision_id: ids.approval, reason: "verified reversible stage", ...extra };
}

test("typed cutoff transition is Joe-only, evidence-bound, audited, and idempotent", async () => {
  const h = transitionHarness();
  const joe = { id: "joe-id", slug: "joe", human: true, authorization_class: "human" };
  const first = await h.tool.handler(h.client, joe, stageArgs({
    organization_tenant_id: "spoof", authorization_class: "root" }));
  const replay = await h.tool.handler(h.client, joe, stageArgs({
    organization_tenant_id: "spoof", authorization_class: "root" }));
  assert.equal(first.state, "retiring");
  assert.equal(replay.replayed, true);
  assert.equal(h.mutations.length, 1);
  assert.equal(h.audits.length, 1);
  assert.equal(h.audits[0][5].new.organization_tenant_id, "carr-internal");
  assert.equal(h.audits[0][5].new.authorization_class, undefined);
});

test("typed cutoff transition refuses illegal state, nonhuman, and spoofed evidence", async () => {
  const joe = { id: "joe-id", slug: "joe", human: true };
  const illegal = transitionHarness({ state: "retiring" });
  await assert.rejects(() => illegal.tool.handler(illegal.client, joe, stageArgs()),
                       /cutoff_state_conflict/);
  const machine = transitionHarness();
  await assert.rejects(() => machine.tool.handler(machine.client,
    { id: "codex-id", slug: "codex", human: false }, stageArgs()), /joe_human_only/);
  const crossTenant = transitionHarness({ tenant: "other-tenant" });
  await assert.rejects(() => crossTenant.tool.handler(crossTenant.client, joe, stageArgs()),
                       /invalid_system_evidence/);
  const crossBrain = transitionHarness({ sponsor: "dell" });
  await assert.rejects(() => crossBrain.tool.handler(crossBrain.client, joe, stageArgs()),
                       /invalid_system_evidence/);
});

test("typed cutoff transition validates finalization and rollback evidence", async () => {
  const joe = { id: "joe-id", slug: "joe", human: true };
  const final = transitionHarness({ state: "retiring" });
  const finalized = await final.tool.handler(final.client, joe, {
    idempotency_key: "finalize-idem", expected_state: "retiring", target_state: "retired",
    approved_commit: "a".repeat(40), manifest_sha256: "b".repeat(64),
    stage_evidence_id: ids.stageEvidence, cold_start_evidence_id: ids.cold,
    approval_decision_id: ids.finalApproval, reason: "verified finalization" });
  assert.equal(finalized.state, "retired");

  const rollback = transitionHarness({ state: "retired" });
  const active = await rollback.tool.handler(rollback.client, joe, {
    idempotency_key: "rollback-idem", expected_state: "retired", target_state: "active",
    approved_commit: "a".repeat(40), manifest_sha256: "b".repeat(64),
    rollback_evidence_id: ids.rollback, approval_decision_id: ids.rollbackApproval,
    reason: "verified rollback" });
  assert.equal(active.state, "active");
});
