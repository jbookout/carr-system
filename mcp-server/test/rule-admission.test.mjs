// Phase 1 admission gate: capture is free, authority is explicit, and the
// database plus server independently refuse activation without a complete
// admitted contract.
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";

const ACTOR = { id: "11111111-1111-4111-8111-111111111111", slug: "joe", human: true };
const RULE = "22222222-2222-4222-8222-222222222222";

function fakeClient(route) {
  const calls = [];
  return {
    calls,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      if (/select request_hash, response/i.test(sql)) return { rows: [] };
      if (/insert into tool_call/i.test(sql) || /insert into event/i.test(sql)) return { rows: [] };
      return route(sql, params, calls);
    },
  };
}

test("admit-rule is an explicit human-only authority verb with all four D-04 dimensions", () => {
  const tool = TOOLS["admit-rule"];
  assert.equal(tool.humanOnly, true);
  const required = new Set(tool.inputSchema.required);
  for (const field of ["rule_id", "enforcement_class", "binding_moment",
                       "applicability", "projection", "reachability", "input_contract",
                       "fixture_refs", "enforcement_points", "reason"])
    assert.equal(required.has(field), true, `${field} must be required`);
});

test("teach captures guidance intake in the same envelope but leaves it non-authoritative", async () => {
  const c = fakeClient((sql) => {
    if (/insert into rule/i.test(sql)) return { rows: [{ id: RULE, personal_to: null }] };
    return { rows: [] };
  });
  await executeRegisteredTool(c, ACTOR, "teach", {
    idempotency_key: "capture-1", statement: "fixture rule", human_quote: "make this a rule",
  });
  const intake = c.calls.find(x => /insert into ops\.guidance_intake/i.test(x.sql));
  assert.ok(intake, "teach must create an intake row");
  assert.match(intake.sql, /'captured'/i);
  assert.equal(intake.params.includes(`rule:${RULE}`), true,
               "intake must point back to the proposed rule");
});

test("activate-rule is retired so approval can never be separated from enforcement", async () => {
  const c = fakeClient(() => ({ rows: [] }));
  await assert.rejects(
    () => executeRegisteredTool(c, ACTOR, "activate-rule", {
      idempotency_key: "activate-1", rule_id: RULE,
    }),
    e => e instanceof ToolError && e.payload.error === "direct_rule_activation_retired",
  );
  assert.equal(c.calls.some(x => /update rule set status='active'/i.test(x.sql)), false);
  assert.equal(c.calls.some(x => /insert into ops\.authority_receipt/i.test(x.sql)), false);
});

test("approve-rule is one human authority act, not separate admission and activation", () => {
  const tool = TOOLS["approve-rule"];
  assert.equal(tool.humanOnly, true);
  assert.equal(tool.authorityOnly, true);
  assert.deepEqual(new Set(tool.inputSchema.required), new Set([
    "idempotency_key", "rule_id", "policy_kind", "control_keys", "reason",
  ]));
  assert.deepEqual(tool.inputSchema.properties.policy_kind.enum,
    ["machine_enforceable", "human_only"]);
  assert.equal(tool.inputSchema.properties.enforcement_points, undefined,
    "callers must not claim implementation/test evidence");
});

test("approve-rule delegates one atomic transaction and only returns enforced active coverage", async () => {
  const c = fakeClient((sql) => {
    if (/select ops\.approve_rule/i.test(sql)) return { rows: [{ result: {
      ok: true, rule_id: RULE, policy_status: "active", enforcement_status: "hard_enforced",
      installed_controls: ["platform_metering_pre_dispatch"], pending_controls: [],
      approval_receipt_id: "33333333-3333-4333-8333-333333333333",
    } }] };
    return { rows: [] };
  });
  const out = await executeRegisteredTool(c, ACTOR, "approve-rule", {
    idempotency_key: "approve-1", rule_id: RULE,
    policy_kind: "machine_enforceable",
    control_keys: ["platform_metering_pre_dispatch"], reason: "Joe approved it",
  });
  assert.equal(out.policy_status, "active");
  assert.equal(out.enforcement_status, "hard_enforced");
  assert.equal(c.calls.filter(x => /select ops\.approve_rule/i.test(x.sql)).length, 1);
  assert.equal(c.calls.some(x => /insert into ops\.rule_admission/i.test(x.sql)), false,
    "the MCP layer must not reproduce a partial client-side transaction");
  assert.equal(c.calls.some(x => /update rule set status='active'/i.test(x.sql)), false,
    "activation belongs inside the authority function");
});

test("applicable-rules delegates finite applicability selection to the policy compiler", async () => {
  const c = fakeClient((sql) => /from ops\.applicable_rules/i.test(sql)
    ? { rows: [{ rule_id: RULE, statement: "fixture", enforcement_class: "machine_enforceable" }] }
    : { rows: [] });
  const out = await executeRegisteredTool(c, ACTOR, "applicable-rules", {
    workflow: "nightly", surface: "worker", tier: "operational",
  });
  assert.equal(out.count, 1);
  assert.equal(out.rules[0].rule_id, RULE);
});
