// V5-F03: new engineering-slice-plan.v1 registrations are retired at the
// registration doors only (migration 0610 and the register-engineering-slice-plan
// handler).  Stored v1 plans keep the read path requirePlan has always given them.
import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";

import {
  ENGINEERING_SLICE_PLAN_REGISTRABLE_VERSIONS,
  ENGINEERING_SLICE_PLAN_VERSIONS,
  canonicalDigest,
  engineeringRuntimeTools,
  requirePlan,
  requireRegistrablePlanVersion,
} from "../src/engineering-runtime.js";

class EngineeringToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const corpus = JSON.parse(fs.readFileSync(new URL("./fixtures/f03-design-contract-parity.v1.json", import.meta.url), "utf8"));
const MIGRATION = fs.readFileSync(new URL("../../migrations/0610_f03_retire_legacy_slice_plan_registration.sql", import.meta.url), "utf8");
const RUNTIME = fs.readFileSync(new URL("../src/engineering-runtime.js", import.meta.url), "utf8");

function sealed(base) {
  const plan = structuredClone(corpus.bases[base]);
  delete plan.plan_digest;
  return { ...plan, plan_digest: canonicalDigest(plan) };
}

function registerHarness() {
  const queries = [];
  const client = {
    async query(sql, params) {
      queries.push({ sql, params });
      return { rows: [{ id: "00000000-0000-4000-8000-000000000001", work_request_id: "wr", accepted_plan_id: "p", plan_digest: params?.[2] }] };
    },
  };
  const tools = engineeringRuntimeTools({
    withEnvelope: async (_c, _a, _verb, _args, fn) => fn(),
    writeEvent: async () => {},
    ToolError: EngineeringToolError,
  });
  return { client, queries, register: args => tools["register-engineering-slice-plan"].handler(client, { slug: "codex" }, args) };
}

const args = plan => ({
  idempotency_key: "3f2c1f8e-6d7a-4b1e-9c8d-2a1b3c4d5e6f",
  work_request: "WR-000001",
  plan,
  plan_digest: plan.plan_digest,
});

async function refusalOf(promise) {
  try { await promise; } catch (error) { return error.payload; }
  return null;
}

test("a fresh v1 registration refuses before any database statement", async () => {
  const { queries, register } = registerHarness();
  const v1 = sealed("v1-legacy");
  assert.equal(requirePlan(structuredClone(v1), EngineeringToolError).schema_version, "engineering-slice-plan.v1");
  const refusal = await refusalOf(register(args(v1)));
  assert.equal(refusal?.error, "engineering_slice_plan_version_not_registrable");
  assert.equal(refusal.schema_version, "engineering-slice-plan.v1");
  assert.deepEqual(refusal.registrable, ["engineering-slice-plan.v2"]);
  assert.equal(queries.length, 0, "no statement may run for a refused registration");
});

test("a contract-valid v2 registration still reaches the database seam", async () => {
  const { queries, register } = registerHarness();
  const v2 = sealed("v2-short");
  const result = await register(args(v2));
  assert.equal(result.ok, true);
  assert.equal(queries.length, 1);
  assert.match(queries[0].sql, /ops\.engineering_register_slice_plan\(/);
});

test("stored v1 plans keep their read path: requirePlan still accepts v1", () => {
  assert.deepEqual([...ENGINEERING_SLICE_PLAN_VERSIONS], ["engineering-slice-plan.v1", "engineering-slice-plan.v2"]);
  assert.deepEqual([...ENGINEERING_SLICE_PLAN_REGISTRABLE_VERSIONS], ["engineering-slice-plan.v2"]);
  for (const base of ["v1-legacy", "v2-short", "v2-full", "v2-pair"])
    assert.equal(requirePlan(sealed(base), EngineeringToolError).plan_digest, sealed(base).plan_digest, base);
});

test("the registration gate is called only from the register handler, never from a read path", () => {
  const calls = RUNTIME.match(/requireRegistrablePlanVersion\(/g) || [];
  // one declaration plus exactly one call site
  assert.equal(calls.length, 2);
  const register = RUNTIME.slice(RUNTIME.indexOf('"register-engineering-slice-plan": {'), RUNTIME.indexOf('"admit-engineering-slice": {'));
  assert.match(register, /requireRegistrablePlanVersion\(requirePlan\(args\.plan, ToolError\), ToolError\)/);
  const requirePlanBody = RUNTIME.slice(RUNTIME.indexOf("export function requirePlan("), RUNTIME.indexOf("function sourceParts("));
  assert.doesNotMatch(requirePlanBody, /requireRegistrablePlanVersion|REGISTRABLE/);
});

test("unknown and missing versions are not registrable either", () => {
  for (const schema_version of [undefined, null, "engineering-slice-plan.v3", "engineering-slice-plan.V2", ""])
    assert.throws(() => requireRegistrablePlanVersion({ schema_version }, EngineeringToolError),
      error => error.payload.error === "engineering_slice_plan_version_not_registrable");
  assert.equal(requireRegistrablePlanVersion({ schema_version: "engineering-slice-plan.v2" }, EngineeringToolError).schema_version,
    "engineering-slice-plan.v2");
});

test("0610 re-issues only the register seam, refusing non-v2 before the replay and the insert", () => {
  const creates = MIGRATION.match(/create or replace function ops\.[a-z_]+/gi) || [];
  assert.deepEqual(creates.map(row => row.toLowerCase()), ["create or replace function ops.engineering_register_slice_plan"]);
  assert.doesNotMatch(MIGRATION, /engineering_slice_plan_refusal\s*\(p_plan jsonb\)\s*returns/i);
  assert.doesNotMatch(MIGRATION, /^\s*(grant|revoke)\b/im);
  const gate = MIGRATION.indexOf("if p_plan->>'schema_version' is distinct from 'engineering-slice-plan.v2' then");
  const planCheck = MIGRATION.indexOf("plan_refusal := ops.engineering_slice_plan_refusal(p_plan);");
  const insert = MIGRATION.indexOf("insert into ops.engineering_slice_plan");
  assert.ok(planCheck > 0 && gate > planCheck && insert > gate, "order: whole-plan check, version gate, insert/replay");
});

test("0610 carries the 0507a register body verbatim except the one marked block", () => {
  const body = sql => {
    const start = sql.indexOf("create or replace function ops.engineering_register_slice_plan(");
    return sql.slice(start, sql.indexOf("end $$;", start));
  };
  const prior = body(fs.readFileSync(new URL("../../migrations/0507a_engineering_slice_plan_validators.sql", import.meta.url), "utf8"));
  const current = body(MIGRATION);
  const blockStart = current.indexOf("  -- V5-F03 v1 retirement (0610)");
  const blockEnd = current.indexOf("  end if;\n", current.indexOf("is not registrable", blockStart)) + "  end if;\n".length;
  assert.ok(blockStart > 0 && blockEnd > blockStart);
  assert.equal(current.slice(0, blockStart) + current.slice(blockEnd), prior);
});
