import test from "node:test";
import assert from "node:assert/strict";

import {
  assuranceHealthStoreTools,
  validateAssuranceHealthProjection,
} from "../src/assurance-health-store.v5.js";

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const SCOPE = Object.freeze({
  workflow_key: "doctorcre.release",
  workflow_version: 7,
  work_request_id: "WR-700",
});
const OTHER_SCOPE = Object.freeze({
  workflow_key: "doctorcre.release",
  workflow_version: 8,
  work_request_id: "WR-800",
});
const LAYERS = Object.freeze([
  "artifact_assessment",
  "execution_assessment",
  "controller_assessment",
  "candidate_outcome_oracle",
  "activation_readback",
  "actual_business_outcome",
]);

function healthyProjection(scope = SCOPE) {
  return {
    schema_version: "assurance-health.v1",
    scope: { ...scope },
    state: "healthy",
    green: true,
    capability_stage: "act",
    owner: { kind: "record_layer", ref: "ops.assurance_health_evidence" },
    evidence: Object.fromEntries(LAYERS.map((layer, index) => [layer, {
      layer,
      state: "passing",
      status: "pass",
      scope: { ...scope },
      evidence_ref: `receipt:${layer}:${index}`,
      evidence_digest: `sha256:${String(index + 1).padStart(64, "0")}`,
      observed_at: "2026-09-25T20:00:00.000Z",
      expires_at: "2026-09-26T20:00:00.000Z",
    }])),
    impact: { scope_limited_to: { ...scope }, withdrawn_stages: [] },
    recovery: { required_evidence: [] },
  };
}

function toolsWith(projection) {
  const calls = [];
  const client = { query: async (sql, params) => {
    calls.push({ sql, params });
    return { rows: [{ projection }] };
  } };
  const tools = assuranceHealthStoreTools({ ToolError, withEnvelope: async (_c, _a, _v, _args, fn) => fn() });
  return { calls, client, handler: tools["read-assurance-health"].handler };
}

test("V5-A01: the live read returns a current evidence-traceable projection for one exact scope", async () => {
  const projection = healthyProjection();
  const { calls, client, handler } = toolsWith(projection);
  const answer = await handler(client, { slug: "dell" }, { scope: SCOPE });

  assert.deepEqual(answer, projection);
  assert.equal(calls.length, 1);
  assert.match(calls[0].sql, /ops\.read_assurance_health/);
  assert.deepEqual(calls[0].params.slice(0, 3), [SCOPE.workflow_key, SCOPE.workflow_version, SCOPE.work_request_id]);
  assert.deepEqual(new Set(Object.values(answer.evidence).map(row => row.evidence_ref)).size, LAYERS.length);
});

test("V5-A01: failure injection cannot move an unrelated exact scope", async () => {
  const affected = healthyProjection(SCOPE);
  affected.state = "degraded";
  affected.green = false;
  affected.capability_stage = "draft";
  affected.evidence.actual_business_outcome = {
    ...affected.evidence.actual_business_outcome,
    state: "failed",
    status: "fail",
  };
  affected.impact.withdrawn_stages = ["act"];
  affected.recovery.required_evidence = ["actual_business_outcome"];

  const unaffected = healthyProjection(OTHER_SCOPE);
  assert.equal(validateAssuranceHealthProjection(affected, SCOPE, ToolError).state, "degraded");
  assert.equal(validateAssuranceHealthProjection(unaffected, OTHER_SCOPE, ToolError).state, "healthy");
  assert.deepEqual(unaffected, healthyProjection(OTHER_SCOPE), "the sibling scope is byte-for-byte unchanged");
});

test("V5-A01: missing one evidence layer blocks act and green", () => {
  const projection = healthyProjection();
  projection.state = "not-yet-operational";
  projection.green = false;
  projection.capability_stage = "draft";
  projection.evidence.activation_readback = {
    layer: "activation_readback",
    state: "missing",
    present: false,
    scope: { ...SCOPE },
  };
  projection.impact.withdrawn_stages = ["act"];
  projection.recovery.required_evidence = ["activation_readback"];

  assert.equal(validateAssuranceHealthProjection(projection, SCOPE, ToolError).green, false);
});

test("V5-A01 planted traceability mutant: a green label with one evidence identity erased is killed", () => {
  const mutant = healthyProjection();
  delete mutant.evidence.controller_assessment.evidence_ref;
  assert.throws(
    () => validateAssuranceHealthProjection(mutant, SCOPE, ToolError),
    error => error.payload?.error === "assurance_health_projection_invalid",
  );
});

test("V5-A01 planted scope mutant: accepting one layer from a sibling scope is killed", () => {
  const mutant = healthyProjection();
  mutant.evidence.execution_assessment.scope = { ...OTHER_SCOPE };
  assert.throws(
    () => validateAssuranceHealthProjection(mutant, SCOPE, ToolError),
    error => error.payload?.error === "assurance_health_projection_invalid",
  );
});

test("V5-A01 planted missing-layer mutant: green/act with a deleted layer is killed", () => {
  const mutant = healthyProjection();
  delete mutant.evidence.activation_readback;
  assert.throws(
    () => validateAssuranceHealthProjection(mutant, SCOPE, ToolError),
    error => error.payload?.error === "assurance_health_projection_invalid",
  );
});

test("V5-A01: the verb has a closed exact-scope contract and is a read", () => {
  const tool = assuranceHealthStoreTools({ ToolError, withEnvelope: async () => {} })["read-assurance-health"];
  assert.equal(tool.write, undefined);
  assert.equal(tool.writerConnection, true);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(tool.inputSchema.required, ["scope"]);
  assert.equal(tool.inputSchema.properties.scope.additionalProperties, false);
});

test("V5-A01: evidence ingress derives attribution and cannot accept a label, green state, tenant or evaluator", async () => {
  const calls = [];
  const client = { query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.includes("set_config")) return { rows: [{ ok: true }] };
    return { rows: [{ receipt: { id: "evidence-row-1", replayed: false } }] };
  } };
  const envelopes = [];
  const tools = assuranceHealthStoreTools({
    ToolError,
    withEnvelope: async (_c, actor, verb, args, fn) => {
      envelopes.push({ actor, verb, args });
      return fn();
    },
  });
  const tool = tools["record-assurance-health-evidence"];
  assert.equal(tool.write, true);
  assert.equal(tool.inputSchema.additionalProperties, false);
  for (const forbidden of ["tenant", "evaluator", "state", "green", "capability_stage"]) {
    assert.equal(Object.hasOwn(tool.inputSchema.properties, forbidden), false, forbidden);
  }
  const evidence = {
    layer: "artifact_assessment",
    basis: "independent_artifact_review",
    status: "pass",
    subject_ref: "release:abc123",
    evidence_ref: "review:abc123",
    evidence_digest: `sha256:${"a".repeat(64)}`,
    observed_at: "2026-09-25T20:00:00.000Z",
    expires_at: "2026-09-26T20:00:00.000Z",
    detail: {
      repository_commit_sha: "a".repeat(40),
      repository_tree_sha: "b".repeat(40),
      reviewer_fact_id: "reviewer-fact:1",
    },
    incident_refs: [],
    recovery_refs: ["runbook:a01"],
  };
  const args = { scope: SCOPE, evidence, idempotency_key: "11111111-2222-4333-8444-555555555555" };
  const actor = { slug: "reviewer-a01" };
  assert.deepEqual(await tool.handler(client, actor, args), { id: "evidence-row-1", replayed: false });
  assert.equal(envelopes[0].actor, actor);
  assert.equal(envelopes[0].verb, "record-assurance-health-evidence");
  assert.match(calls[0].sql, /carr\.acting_actor_slug/);
  assert.match(calls[1].sql, /ops\.record_assurance_health_evidence/);
  assert.deepEqual(calls[1].params, [JSON.stringify(SCOPE), JSON.stringify(evidence), args.idempotency_key]);
});
