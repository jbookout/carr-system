import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { allowedIn, callTool } from "../src/mcp.js";

const JOE = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", human: true, via: "test" };
const BOT = { ...JOE, human: false, slug: "codex" };
const PROPOSE = { idempotency_key: "10000000-0000-0000-0000-000000000099", human_ref: "WR-000001", base_version: 2,
  scope_summary: "Bounded scope", runbook_ref: "doctrine:runbook#safe-plan", dependency_refs: [],
  recovery_ref: "safe:recovery:rollback", observability_ref: "safe:observe:logs", caps: { max_steps: 2, max_duration_minutes: 30 } };
const EVIDENCE = (suffix, source_class) => ({ source_ref: `safe:research:${suffix}`, source_class,
  locator: `https://example.com/${suffix}`, observed_at: "2026-08-25T12:00:00.000Z",
  content_digest: `sha256:${"e".repeat(64)}`, finding: `Verified ${source_class} evidence for the heavy-build decision.` });
const HEAVY_BUILD = {
  builder_session_ref: "session:builder:tour-packet-golden",
  research_manifest: {
    primary_sources: [EVIDENCE("primary", "primary_source")],
    maintained_repositories: [EVIDENCE("repo-one", "maintained_repository"), EVIDENCE("repo-two", "maintained_repository")],
    practitioner_evidence: [EVIDENCE("practitioner", "practitioner_evidence")],
    current_baseline: [EVIDENCE("baseline", "current_baseline")],
    failure_modes: [EVIDENCE("failure", "failure_mode")],
    unresolved_contradictions: [],
    conclusion: "The evidence supports the chosen design and names the remaining falsifier explicitly.",
  },
  master_plan: {
    product_goal: "Ship the complete governed capability, not only its prerequisite framework.",
    non_goals: ["Do not substitute prerequisite repairs for the requested product."],
    architecture: ["Admission layer", "Execution layer", "Verification layer"],
    authority_boundaries: ["Models propose evidence; the database derives admission and humans retain acceptance."],
    dependency_dag: [{ step_ref: "step:admission", depends_on: [] }, { step_ref: "step:execution", depends_on: ["step:admission"] }],
    planned_checks: [{ artifact: "heavy-build admission receipt", comparator: "typed contract", failure_condition: "any evidence class or review receipt is absent" }],
    baseline_comparison: "Replay the tour-packet task as the positive fixture and the Hermes-memory task as the refusal fixture.",
    release_strategy: "Land behind the existing human ready-plan acceptance boundary.",
    rollback_strategy: "Revoke the new admission functions and leave existing accepted plans untouched.",
    observability_strategy: "Return classifier reasons, admission hashes, review hashes, and actionable refusal codes.",
    fully_shipped_definition: "A heavy plan cannot become ready without research, master-plan, shape, and independent-review receipts.",
    prerequisite_policy: "A discovered prerequisite becomes a dependency and never replaces the parent product plan.",
  },
};
const ACCEPT = { idempotency_key: "20000000-0000-0000-0000-000000000099", human_ref: "WR-000001", base_version: 2,
  plan_hash: `sha256:${"a".repeat(64)}` };

async function refused(fn) { try { await fn(); assert.fail("expected refusal"); } catch (error) { assert.ok(error instanceof ToolError); return error.payload; } }

class PlanFake {
  constructor({ tier = "standard", shapeReady = false, registryError = false } = {}) { this.calls = []; this.toolCalls = new Map(); this.tier = tier; this.shapeReady = shapeReady; this.registryError = registryError; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim(); this.calls.push({ sql, params });
    if (/^(savepoint|release savepoint|rollback to savepoint)/i.test(sql)) return { rows: [] };
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [] };
    if (sql.startsWith("select request_hash, response")) { const row = this.toolCalls.get(params[0]); return { rows: row ? [row] : [] }; }
    if (sql.includes("classify_sourced_work_request_build")) return { rows: [{ work_request_id: "40000000-0000-0000-0000-000000000001",
      ref: "WR-000001", tier: this.tier, reasons: this.tier === "heavy" ? ["signal:new_capability", "scale:max_steps"] : [],
      shape_disposition: this.shapeReady ? "required" : null, shape_ready: this.shapeReady }] };
    if (sql.includes("propose_sourced_work_request_plan")) return { rows: [{ plan_id: "30000000-0000-0000-0000-000000000001", plan_ref: "PLAN-000001", plan_hash: ACCEPT.plan_hash,
      work_request_id: "40000000-0000-0000-0000-000000000001", ref: "WR-000001", state: "triaged", version: 2,
      scope_summary: PROPOSE.scope_summary, runbook_ref: PROPOSE.runbook_ref,
      runbook_revision_id: "50000000-0000-0000-0000-000000000001", runbook_content_hash: `sha256:${"b".repeat(64)}` }] };
    if (sql.includes("rule-delivery-source-snapshot")) {
      if (this.registryError) throw new Error("synthetic registry unavailable");
      return { rows: [{ map_versions: 1,
      map_digest: "f".repeat(64), tagged_rules: 2, work_request_title: "Bounded maintenance",
      desired_outcome: "Keep the existing bounded behavior truthful.", acceptance_criteria: [],
      pack_index: [
        { pack: "engineering-git", title: "Engineering", triggers: ["migration", "schema", "deploy", "test"], rule_count: 2 },
        { pack: "source-study", title: "Study", triggers: ["new capability"], rule_count: 1 },
      ] }] };
    }
    if (sql.includes("record_sourced_heavy_build_admission")) return { rows: [{ admission_ref: "HBA-000001",
      admission_hash: `sha256:${"c".repeat(64)}`, tier: "heavy", classifier_reasons: ["signal:new_capability", "scale:max_steps"],
      builder_session_ref: HEAVY_BUILD.builder_session_ref, replayed: false }] };
    if (sql.includes("sourced_heavy_build_review_target")) return { rows: [{ work_request_id: "40000000-0000-0000-0000-000000000001",
      ref: "WR-000001", builder_session_ref: HEAVY_BUILD.builder_session_ref }] };
    if (sql.includes("review_sourced_heavy_build_plan")) return { rows: [{ review_ref: "HBR-000001",
      review_hash: `sha256:${"d".repeat(64)}`, admission_ref: "HBA-000001", admission_hash: `sha256:${"c".repeat(64)}`,
      verdict: params[4], reviewer_session_ref: params[5], replayed: false }] };
    if (sql.includes("accept_sourced_work_request_plan")) return { rows: [{ work_request_id: "40000000-0000-0000-0000-000000000001", ref: "WR-000001", state: "ready", version: 3,
      plan_id: "30000000-0000-0000-0000-000000000001", plan_ref: "PLAN-000001", plan_hash: ACCEPT.plan_hash,
      accepted_by_actor_slug: "joe", accepted_at: "2026-08-16T00:00:00Z", shape_disposition: "not_required", shape_fixed_surface_ref: `sourced-plan:PLAN-000001#${ACCEPT.plan_hash}` }] };
    if (sql.startsWith("insert into event")) return { rows: [] };
    if (sql.startsWith("insert into tool_call")) { this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null }); return { rows: [] }; }
    throw new Error(`unexpected query: ${sql}`);
  }
}

test("ready-plan schemas are closed and acceptance is human authority only", () => {
  assert.equal(TOOLS["propose-ready-plan"].inputSchema.additionalProperties, false);
  assert.equal(TOOLS["accept-ready-plan"].inputSchema.additionalProperties, false);
  assert.equal(TOOLS["accept-ready-plan"].humanOnly, true);
  assert.equal(TOOLS["accept-ready-plan"].authorityOnly, true);
  assert.equal(TOOLS["review-heavy-build-plan"].write, true);
  for (const profile of ["capture", "hermes", "probe", "reviewer"]) assert.equal(allowedIn(profile, "propose-ready-plan", TOOLS["propose-ready-plan"]), false);
});

test("proposal returns explicit plan readback and audits the Work Request entity", async () => {
  const db = new PlanFake(); const out = await executeRegisteredTool(db, JOE, "propose-ready-plan", structuredClone(PROPOSE));
  assert.equal(out.ok, true); assert.equal(out.human_ref, "WR-000001"); assert.equal(out.state, "triaged");
  assert.equal(out.version, 2); assert.equal(out.plan_ref, "PLAN-000001"); assert.equal(out.plan_hash, ACCEPT.plan_hash);
  assert.equal(out.scope_summary, PROPOSE.scope_summary); assert.equal(out.runbook_ref, PROPOSE.runbook_ref);
  assert.deepEqual(out.build_admission, { tier: "standard", reasons: [], required: false });
  assert.equal(out.rule_delivery_source.schema_version, "rule-delivery-source.v1");
  assert.equal(out.rule_delivery_source.trigger_map.map_digest, `sha256:${"f".repeat(64)}`);
  assert.match(out.rule_delivery_source.contract_digest, /^sha256:[0-9a-f]{64}$/);
  const event = db.calls.find(call => call.sql.startsWith("insert into event"));
  assert.equal(event.params[4], "40000000-0000-0000-0000-000000000001");
  assert.equal(JSON.parse(event.params[7]).plan_ref, "PLAN-000001");
});

test("heavy classification refuses before plan creation when shape or the typed contract is missing", async () => {
  const unshaped = new PlanFake({ tier: "heavy", shapeReady: false });
  const shapeError = await refused(() => executeRegisteredTool(unshaped, JOE, "propose-ready-plan", structuredClone(PROPOSE)));
  assert.equal(shapeError.error, "heavy_build_shape_required");
  assert.equal(unshaped.calls.some(call => call.sql.includes("propose_sourced_work_request_plan")), false);

  const shaped = new PlanFake({ tier: "heavy", shapeReady: true });
  const contractError = await refused(() => executeRegisteredTool(shaped, JOE, "propose-ready-plan", structuredClone(PROPOSE)));
  assert.equal(contractError.error, "heavy_build_admission_required");
  assert.deepEqual(contractError.missing, ["research_manifest", "master_plan", "builder_session_ref"]);
  assert.equal(shaped.calls.some(call => call.sql.includes("propose_sourced_work_request_plan")), false);
});

test("heavy proposal records the contract against the exact immutable plan", async () => {
  const db = new PlanFake({ tier: "heavy", shapeReady: true });
  const out = await executeRegisteredTool(db, JOE, "propose-ready-plan", { ...structuredClone(PROPOSE), heavy_build: structuredClone(HEAVY_BUILD) });
  assert.equal(out.build_admission.tier, "heavy");
  assert.equal(out.build_admission.required, true);
  assert.equal(out.build_admission.admission_ref, "HBA-000001");
  assert.equal(out.build_admission.admission_hash, `sha256:${"c".repeat(64)}`);
  const admission = db.calls.find(call => call.sql.includes("record_sourced_heavy_build_admission"));
  assert.equal(admission.params[0], "30000000-0000-0000-0000-000000000001");
  assert.deepEqual(JSON.parse(admission.params[4]), HEAVY_BUILD);
});

test("heavy plan review is a separate receipt and cannot reuse the builder context", async () => {
  const db = new PlanFake();
  const args = { idempotency_key: "30000000-0000-0000-0000-000000000099", human_ref: "WR-000001",
    plan_hash: ACCEPT.plan_hash, admission_hash: `sha256:${"c".repeat(64)}`, verdict: "pass",
    reviewer_session_ref: "session:reviewer:fresh-sol", review_summary: "Fresh review checked the actual manifest and plan against every acceptance criterion.",
    evidence_refs: ["safe:review:heavy-plan-fixture"], gaps: [] };
  const out = await executeRegisteredTool(db, JOE, "review-heavy-build-plan", args);
  assert.equal(out.verdict, "pass");
  assert.equal(out.status, "ready_for_human_plan_acceptance");
  const sameContext = await refused(() => executeRegisteredTool(new PlanFake(), JOE, "review-heavy-build-plan",
    { ...args, idempotency_key: "30000000-0000-0000-0000-000000000098", reviewer_session_ref: HEAVY_BUILD.builder_session_ref }));
  assert.equal(sameContext.error, "heavy_build_review_context_not_fresh");
});

test("acceptance returns exact durable readback and audits Work Request state", async () => {
  const db = new PlanFake(); const out = await executeRegisteredTool(db, JOE, "accept-ready-plan", structuredClone(ACCEPT));
  assert.equal(out.state, "ready"); assert.equal(out.plan_ref, "PLAN-000001"); assert.equal(out.accepted_by_actor_slug, "joe");
  const event = db.calls.find(call => call.sql.startsWith("insert into event"));
  assert.equal(event.params[4], "40000000-0000-0000-0000-000000000001");
  assert.equal(JSON.parse(event.params[7]).state, "ready");
});

test("actor-bound replay does not repeat proposal SQL or event and changed payload refuses", async () => {
  const db = new PlanFake(); await executeRegisteredTool(db, JOE, "propose-ready-plan", structuredClone(PROPOSE));
  const replay = await executeRegisteredTool(db, JOE, "propose-ready-plan", structuredClone(PROPOSE));
  assert.equal(replay.replayed, true); assert.equal(db.calls.filter(c => c.sql.includes("propose_sourced_work_request_plan")).length, 1);
  assert.equal(db.calls.filter(c => c.sql.startsWith("insert into event")).length, 1);
  const changed = await refused(() => executeRegisteredTool(db, JOE, "propose-ready-plan", { ...PROPOSE, scope_summary: "changed" }));
  assert.equal(changed.error, "key_reuse");
  const dell = { ...JOE, id: "10000000-0000-0000-0000-000000000003", slug: "dell" };
  const foreign = await refused(() => executeRegisteredTool(db, dell, "propose-ready-plan", structuredClone(PROPOSE)));
  assert.equal(foreign.error, "key_reuse");
});

test("acceptance replay is actor-bound and never repeats acceptance SQL or event", async () => {
  const db = new PlanFake();
  const first = await executeRegisteredTool(db, JOE, "accept-ready-plan", structuredClone(ACCEPT));
  assert.equal(first.shape_fixed_surface_ref, `sourced-plan:PLAN-000001#${ACCEPT.plan_hash}`);
  const replay = await executeRegisteredTool(db, JOE, "accept-ready-plan", structuredClone(ACCEPT));
  assert.equal(replay.replayed, true);
  assert.equal(db.calls.filter(c => c.sql.includes("accept_sourced_work_request_plan")).length, 1);
  assert.equal(db.calls.filter(c => c.sql.startsWith("insert into event")).length, 1);
  const changed = await refused(() => executeRegisteredTool(db, JOE, "accept-ready-plan", { ...ACCEPT, plan_hash: `sha256:${"b".repeat(64)}` }));
  assert.equal(changed.error, "key_reuse");
  const dell = { ...JOE, id: "10000000-0000-0000-0000-000000000003", slug: "dell" };
  const foreign = await refused(() => executeRegisteredTool(db, dell, "accept-ready-plan", structuredClone(ACCEPT)));
  assert.equal(foreign.error, "key_reuse");
});

test("validation and human authority boundaries refuse before DB I/O", async () => {
  const db = { query: async () => { throw new Error("database must not be called"); } };
  for (const args of [{ ...PROPOSE, runbook_ref: "doctrine:other#x" }, { ...PROPOSE, recovery_ref: `safe:${"x".repeat(301)}` }, { ...PROPOSE, dependency_refs: ["safe:a", "safe:a"] }, { ...PROPOSE, caps: { max_steps: 21, max_duration_minutes: 30 } }]) {
    const out = await refused(() => executeRegisteredTool(db, JOE, "propose-ready-plan", args)); assert.equal(out.error, "invalid_ready_plan");
  }
  const invalidHeavy = await refused(() => executeRegisteredTool(db, JOE, "propose-ready-plan", { ...PROPOSE, heavy_build: { builder_session_ref: "x" } }));
  assert.equal(invalidHeavy.error, "invalid_heavy_build_contract");
  // INVERTED, not deleted (Joe's ruling 2026-08-26, decision dc57f62d). This
  // test's whole subject is what refuses BEFORE database I/O, and authority is
  // no longer one of those things. So the proof runs the other way: `db` throws
  // the moment it is touched, and a machine actor now getting that throw —
  // rather than a human_only payload — is exactly the evidence that the call
  // passed the authority boundary and went on to do real work.
  // Caught raw rather than through `refused`, which requires a ToolError: what
  // proves the point here is the fake database's PLAIN Error arriving at all.
  let machineError;
  try { await executeRegisteredTool(db, BOT, "accept-ready-plan", structuredClone(ACCEPT)); }
  catch (error) { machineError = error; }
  assert.ok(machineError, "expected the machine actor to reach the database fake");
  assert.notEqual(machineError?.payload?.error, "human_only");
  const noAuthority = await refused(() => callTool({}, JOE, "accept-ready-plan", structuredClone(ACCEPT), "full")); assert.equal(noAuthority.error, "authority_connection_unavailable");
});

test("caller delivery labels are closed out and the registry snapshot uses the server tenant", async () => {
  for (const field of ["packs", "workflow", "tier", "reasons", "map_digest", "actor", "tenant_id"]) {
    const db = { query: async () => { throw new Error("database must not be called"); } };
    const error = await refused(() => executeRegisteredTool(db, JOE, "propose-ready-plan",
      { ...structuredClone(PROPOSE), [field]: field === "packs" ? ["engineering-git"] : "forged" }));
    assert.ok(["unregistered_operation_fields", "caller_authority_field_forbidden"].includes(error.error), field);
  }
  const db = new PlanFake();
  await executeRegisteredTool(db, JOE, "propose-ready-plan", structuredClone(PROPOSE));
  const snapshot = db.calls.find(call => call.sql.includes("rule-delivery-source-snapshot"));
  assert.equal(snapshot.params[3], "carr-internal");
});

test("catalog failure rolls back its savepoint and leaves proposal audit writes committable", async () => {
  const db = new PlanFake({ registryError: true });
  const out = await executeRegisteredTool(db, JOE, "propose-ready-plan", structuredClone(PROPOSE));
  assert.equal(out.rule_delivery_source.status, "unavailable");
  assert.equal(out.rule_delivery_source.reason, "rule_delivery_registry_unavailable");
  assert.ok(db.calls.some(call => /^rollback to savepoint siep02_rule_delivery_source/i.test(call.sql)));
  assert.ok(db.calls.some(call => call.sql.startsWith("insert into event")));
  assert.ok(db.calls.some(call => call.sql.startsWith("insert into tool_call")));
});
