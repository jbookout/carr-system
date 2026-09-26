// V5-F05 live rule-context seam — tests precede the implementation.
//
// The pure kernels already prove classification, graph resolution, coverage,
// delivery, and admission.  This suite proves the missing join: the server
// reads one authoritative universe snapshot, derives coverage from it, and
// never lets a caller supply identity, time, completeness, or a permission.

import test from "node:test";
import assert from "node:assert/strict";

import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_F05_RUNTIME_SCHEMA_VERSION,
  readActionContext,
  ruleContextRuntimeTools,
} from "../src/rule-context-runtime.v5.js";
import { V5_F05_UNIVERSE_SCHEMA_VERSION } from "../src/rule-applicability.v5.js";

const NOW = "2026-09-26T06:30:00.000Z";
const ACTOR = Object.freeze({
  id: "10000000-0000-4000-8000-000000000031",
  slug: "joe-local",
  human: false,
  sponsoring_human_slug: "joe",
});

const provenance = (id, fill) => ({
  source_record_id: id,
  source_version: 1,
  source_content_digest: `sha256:${fill.repeat(64)}`,
  retrieved_at: "2026-09-26T06:00:00.000Z",
});

const rule = (overrides = {}) => ({
  rule_id: "rule-worktree",
  version: 3,
  rule_class: "workflow",
  scope: "shared",
  owner: "joe",
  mandatory: true,
  trigger: { action: ["repo.commit"] },
  control_effect: { control_key: "isolated_worktree", effect: "require" },
  binding_text: "Use an isolated worktree before changing tracked source.",
  tests: ["check:worktree-isolated"],
  retirement: { behavior: "permanent_until_superseded" },
  provenance: provenance("rule:worktree", "a"),
  ...overrides,
});

const policy = (rules = [rule()], overrides = {}) => ({
  schema_version: V5_F05_UNIVERSE_SCHEMA_VERSION,
  universe_version: 82,
  tenant: ORGANIZATION_TENANT_ID,
  completeness: "complete_authoritative_universe",
  declared_actions: ["repo.commit"],
  declared_resource_classes: ["repository"],
  rules,
  ...overrides,
});

const facts = (overrides = {}) => ({
  action: "repo.commit",
  audience: "internal",
  environment: "isolated_worktree",
  lifecycle_transition: "create",
  resource_class: "repository",
  risk_tier: "consequential",
  ...overrides,
});

class FakeClient {
  constructor(snapshot) { this.snapshot = structuredClone(snapshot); this.calls = []; }
  async query(sql, params) {
    this.calls.push({ sql: sql.replace(/\s+/g, " ").trim(), params });
    if (!sql.includes("ops.f05_rule_universe")) throw new Error(`unexpected query: ${sql}`);
    return { rows: [{ result: structuredClone(this.snapshot) }] };
  }
}

const snapshot = (overrides = {}) => ({
  observed_at: NOW,
  policy: policy(),
  active_rule_count: 1,
  projected_rule_count: 1,
  missing_rule_ids: [],
  ...overrides,
});

test("same authoritative inputs reproduce the exact universe and coverage digests", async () => {
  const request = { facts: facts() };
  const first = await readActionContext(new FakeClient(snapshot()), ACTOR, request);
  const second = await readActionContext(new FakeClient(snapshot()), ACTOR, request);

  assert.equal(first.schema_version, V5_F05_RUNTIME_SCHEMA_VERSION);
  assert.equal(first.universe_digest, second.universe_digest);
  assert.equal(first.coverage_receipt.receipt_digest, second.coverage_receipt.receipt_digest);
  assert.equal(first.coverage_receipt.coverage_complete, true);
  assert.equal(first.consequential_action_permitted, true);
  assert.deepEqual(first.coverage_receipt.universe_rule_ids, ["rule-worktree"]);
  assert.deepEqual(first.effects, {
    creates_effect: false,
    database_writes: 0,
    network_calls: 0,
    provider_actions: 0,
    notifications: 0,
    schedules: 0,
    deployments: 0,
    activations: 0,
    acceptances: 0,
  });
});

test("database row order cannot change a digest", async () => {
  const a = rule();
  const b = rule({
    rule_id: "rule-send",
    version: 1,
    trigger: { action: ["repo.commit"] },
    control_effect: { control_key: "review-before-send", effect: "require" },
    binding_text: "Require review before a consequential external send.",
    tests: ["check:review-before-send"],
    provenance: provenance("rule:send", "b"),
  });
  const one = snapshot({ policy: policy([a, b]), active_rule_count: 2, projected_rule_count: 2 });
  const two = snapshot({ policy: policy([b, a]), active_rule_count: 2, projected_rule_count: 2 });
  const left = await readActionContext(new FakeClient(one), ACTOR, { facts: facts() });
  const right = await readActionContext(new FakeClient(two), ACTOR, { facts: facts() });
  assert.equal(left.universe_digest, right.universe_digest);
  assert.equal(left.coverage_receipt.receipt_digest, right.coverage_receipt.receipt_digest);
});

test("a missing possible binding rule makes the universe partial and blocks the write", async () => {
  // PLANTED BUG WITNESS: trusting policy.completeness here would grant the
  // consequential action even though the store names an active rule it could
  // not project.  The runtime must re-derive completeness from the census.
  const db = new FakeClient(snapshot({
    active_rule_count: 2,
    projected_rule_count: 1,
    missing_rule_ids: ["rule-not-typed"],
  }));
  const result = await readActionContext(db, ACTOR, { facts: facts() });
  assert.equal(result.coverage_receipt.universe_completeness, "partial_unknown_coverage");
  assert.equal(result.coverage_receipt.coverage_complete, false);
  assert.equal(result.consequential_action_permitted, false);
  assert.deepEqual(result.coverage_receipt.blocking_reasons, ["universe_coverage_unknown"]);
  assert.deepEqual(result.source.missing_rule_ids, ["rule-not-typed"]);
});

test("coverage receipt partitions and enumerates every rule from the store", async () => {
  const notApplicable = rule({
    rule_id: "rule-client-send",
    trigger: { action: ["document.send"] },
    control_effect: { control_key: "client-send-review", effect: "require" },
    binding_text: "Review client documents before sending them.",
    tests: ["check:client-send-review"],
    provenance: provenance("rule:client-send", "c"),
  });
  const result = await readActionContext(new FakeClient(snapshot({
    policy: policy([rule(), notApplicable], {
      declared_actions: ["document.send", "repo.commit"],
    }),
    active_rule_count: 2,
    projected_rule_count: 2,
  })), ACTOR, { facts: facts() });
  assert.deepEqual(result.coverage_receipt.universe_rule_ids,
    ["rule-client-send", "rule-worktree"]);
  assert.deepEqual(result.coverage_receipt.effective.map(x => x.rule_id), ["rule-worktree"]);
  assert.deepEqual(result.coverage_receipt.not_applicable.map(x => x.rule_id), ["rule-client-send"]);
  assert.equal(result.coverage_receipt.coverage_complete, true);
});

test("an unresolved binding conflict refuses and cannot be called permission", async () => {
  const forbid = rule({
    rule_id: "rule-forbid-worktree",
    control_effect: { control_key: "isolated_worktree", effect: "forbid" },
    binding_text: "Do not use an isolated worktree for this action.",
    tests: ["check:no-worktree"],
    provenance: provenance("rule:no-worktree", "d"),
  });
  await assert.rejects(
    () => readActionContext(new FakeClient(snapshot({
      policy: policy([rule(), forbid]), active_rule_count: 2, projected_rule_count: 2,
    })), ACTOR, { facts: facts() }),
    error => error?.code === "unresolved_binding_conflict",
  );
});

test("a census whose counts, projected rules and missing list disagree is refused, never read as partial", async () => {
  // Each snapshot is internally inconsistent in exactly one way. Reading any
  // of them as merely "partial" would let a store that under-reports its
  // missing rules (or over-reports its projections) shape coverage.
  const inconsistent = {
    "missing list shorter than the gap": snapshot({
      active_rule_count: 2, projected_rule_count: 1, missing_rule_ids: [] }),
    "missing list longer than the gap": snapshot({
      active_rule_count: 1, projected_rule_count: 1, missing_rule_ids: ["rule-hidden"] }),
    "projected count disagrees with the policy rules": snapshot({
      active_rule_count: 2, projected_rule_count: 2, missing_rule_ids: [] }),
    "more projected than active": snapshot({
      active_rule_count: 0, projected_rule_count: 1, missing_rule_ids: [] }),
  };
  for (const [label, bad] of Object.entries(inconsistent)) {
    await assert.rejects(
      () => readActionContext(new FakeClient(bad), ACTOR, { facts: facts() }),
      error => error?.code === "runtime_census_mismatch",
      `${label} must refuse as a census mismatch`,
    );
  }
  // The consistent partial census still reads, and still blocks.
  const partial = await readActionContext(new FakeClient(snapshot({
    active_rule_count: 2, projected_rule_count: 1, missing_rule_ids: ["rule-missing"],
  })), ACTOR, { facts: facts() });
  assert.equal(partial.consequential_action_permitted, false);
  assert.deepEqual([...partial.source.missing_rule_ids], ["rule-missing"]);
});

test("server-derived identity, time, completeness, and model advice cannot be supplied by the caller", async () => {
  for (const forbidden of [
    "actor", "tenant", "now", "universe", "completeness",
    "semantic_candidates", "consequential_action_permitted",
  ]) {
    await assert.rejects(
      () => readActionContext(new FakeClient(snapshot()), ACTOR,
        { facts: facts(), [forbidden]: forbidden === "now" ? NOW : {} }),
      error => error?.code === "runtime_authority_injection",
      `caller field ${forbidden} must refuse`,
    );
  }
  await assert.rejects(
    () => readActionContext(new FakeClient(snapshot()), ACTOR,
      { facts: facts({ actor_class: "verified_partner" }) }),
    error => error?.code === "runtime_authority_injection",
    "caller facts.actor_class must refuse",
  );
});

test("tool registration is read-only and closes its request schema", async () => {
  class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
  const tools = ruleContextRuntimeTools({ ToolError });
  assert.deepEqual(Object.keys(tools), ["read-action-context", "bind-rule-context-contract"]);
  assert.equal(tools["read-action-context"].write, false);
  assert.equal(tools["read-action-context"].writerConnection, true);
  assert.deepEqual(tools["read-action-context"].inputSchema.required, ["facts"]);
  assert.equal(tools["read-action-context"].inputSchema.additionalProperties, false);
  const result = await tools["read-action-context"].handler(
    new FakeClient(snapshot()), ACTOR, { facts: facts() });
  assert.equal(result.consequential_action_permitted, true);
  assert.equal(result.coverage_receipt.facts.actor_class, "sponsored_agent");
});

test("the only contract writer is authority-only and validates the DB-derived full rule", async () => {
  class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
  const withEnvelope = async (_c, _actor, _verb, _args, fn) => fn();
  const tools = ruleContextRuntimeTools({ ToolError, withEnvelope });
  const calls = [];
  const db = { query: async (sql, params) => {
    calls.push({ sql: sql.replace(/\s+/g, " ").trim(), params });
    return { rows: [{ result: { ok: true, replayed: false, contract: rule() } }] };
  } };
  const input = {
    idempotency_key: "00000000-0000-4000-8000-000000000082",
    rule_id: "00000000-0000-4000-8000-000000000005",
    contract: {
      rule_class: "workflow", mandatory: true,
      trigger: { action: ["repo.commit"], resource_class: ["repository"] },
      control_effect: { control_key: "isolated_worktree", effect: "require" },
      tests: ["check:worktree-isolated"],
      retirement: { behavior: "permanent_until_superseded" },
    },
  };
  const tool = tools["bind-rule-context-contract"];
  assert.equal(tool.write, true);
  assert.equal(tool.authorityOnly, true);
  assert.equal(tool.inputSchema.additionalProperties, false);
  const result = await tool.handler(db, ACTOR, input);
  assert.equal(result.ok, true);
  assert.match(calls[0].sql, /ops\.bind_f05_rule_contract/);
  assert.deepEqual(calls[0].params, [input.rule_id, input.contract, input.idempotency_key]);
});
