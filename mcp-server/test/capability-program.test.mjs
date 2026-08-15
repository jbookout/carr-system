import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { TOOLS, executeRegisteredTool, ToolError as RegistryToolError } from "../src/tools.js";
import {
  COMPLETION_KINDS,
  capabilityProgramTools,
  completionEvidenceError,
  nextProjectState,
} from "../src/capability-program.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
const MIGRATION = path.join(REPO, "migrations/0125_ai_capability_program.sql");
const PROGRAM = "carr-ai-engineering-suite-v1";
const APPROVED_TITLES = [
  "LLM evaluation harness", "Structured-output parser", "Function-calling router",
  "Guardrails system", "AI gateway", "RAG pipeline", "Agent loop / ReAct",
  "Data-curation and deduplication pipeline", "Synthetic-data generator",
  "Knowledge-graph builder", "Semantic router", "Prompt caching",
  "Code-interpreter sandbox", "Text-to-SQL", "Graph RAG", "Vector database / HNSW",
  "Embedding model", "Adversarial-attack generator", "Whisper-style ASR",
  "Text-to-speech pipeline", "Small language model", "Inference server",
  "Quantization library", "Feature store", "Recommendation system", "Vector database driver",
  "Reasoner / Chain-of-Thought implementation", "Interpretability / SAE tooling",
  "LoRA trainer", "PEFT library", "Model-distillation pipeline", "DPO loss",
  "RLHF / PPO pipeline", "Model merger", "KV-cache paging", "Speculative decoding",
  "Tokenizer", "Transformer", "Vision Transformer", "Multimodal projector / CLIP",
  "Diffusion model", "Audio Spectrogram Transformer", "Logit processor",
  "State Space Model / Mamba", "Mixture-of-Experts routing layer",
  "Distributed training / FSDP / tensor parallelism", "Autograd engine",
  "Matrix multiplication kernel", "Softmax optimization", "FlashAttention CUDA kernel",
  "Neural Architecture Search",
];

function seededRows() {
  const sql = fs.readFileSync(MIGRATION, "utf8");
  const rows = [...sql.matchAll(/\(\s*(\d+)\s*,\s*'carr-ai-engineering-suite-v1'\s*,\s*'(WR-AI-\d+)'\s*,\s*'([^']+)'\s*,\s*'(build|extend|adopt|decline)'\s*,[\s\S]*?'(\{[^']+\})'::jsonb,\s*'joe',\s*'joe'\)/g)]
    .map(([, sequence, ref, title, disposition, context]) => ({
      sequence: Number(sequence), ref, title, disposition, context: JSON.parse(context),
    }));
  return rows;
}

test("the capability portfolio is seeded once in the approved usefulness order", () => {
  const rows = seededRows();

  assert.equal(rows.length, 51);
  assert.deepEqual(rows.map(row => row.sequence), Array.from({ length: 51 }, (_, i) => i + 1));
  assert.deepEqual(rows.map(row => row.title), APPROVED_TITLES);
  for (const row of rows) {
    assert.deepEqual(Object.keys(row.context).sort(), [
      "completion_definition", "data_risk", "effort", "evidence", "first_deliverable",
      "non_goals", "prerequisites", "rollback_exit", "scope",
    ]);
    assert.equal(typeof row.context.scope, "string", `${row.ref}: scope`);
    assert.equal(typeof row.context.first_deliverable, "string", `${row.ref}: first deliverable`);
    assert.equal(typeof row.context.rollback_exit, "string", `${row.ref}: rollback/exit`);
    assert.equal(typeof row.context.data_risk, "string", `${row.ref}: data/risk`);
    assert.equal(typeof row.context.effort, "string", `${row.ref}: effort`);
    assert.equal(typeof row.context.completion_definition, "string", `${row.ref}: completion definition`);
    assert.ok(Array.isArray(row.context.non_goals), `${row.ref}: non-goals`);
    assert.ok(Array.isArray(row.context.prerequisites), `${row.ref}: prerequisites`);
    assert.ok(Array.isArray(row.context.evidence), `${row.ref}: evidence`);
    assert.ok(row.context.scope.trim() && row.context.first_deliverable.trim() &&
      row.context.rollback_exit.trim() && row.context.completion_definition.trim(), `${row.ref}: no placeholder context`);
  }
});

test("completion evidence is conditional on what completion means", () => {
  assert.deepEqual(COMPLETION_KINDS, ["built", "extended", "adopted", "declined"]);

  assert.equal(completionEvidenceError("built", {
    artifact_ref: "pr:201",
    acceptance_test_refs: ["ci:run:501"],
    independent_verifier_ref: "finding:review-9",
  }), null);

  assert.equal(completionEvidenceError("extended", {
    artifact_ref: "commit:abc",
    acceptance_test_refs: ["test:contract"],
    independent_verifier_ref: "review:fresh-context",
  }), null);

  assert.equal(completionEvidenceError("adopted", {
    artifact_ref: "manifest:runtime-v1",
    acceptance_test_refs: ["rehearsal:12"],
    independent_verifier_ref: "review:13",
    rollback_ref: "runbook:remove-runtime",
    decision_ref: "decision:adopt-runtime",
  }), null);

  assert.equal(completionEvidenceError("declined", {
    decision_ref: "decision:decline-transformer",
    independent_verifier_ref: "review:cost-case",
  }), null);

  assert.equal(completionEvidenceError("built", {
    artifact_ref: "pr:201",
    acceptance_test_refs: [],
    independent_verifier_ref: "review:9",
  }).error, "completion_evidence_incomplete");

  assert.equal(completionEvidenceError("adopted", {
    artifact_ref: "manifest:runtime-v1",
    acceptance_test_refs: ["rehearsal:12"],
    independent_verifier_ref: "review:13",
  }).missing.includes("rollback_ref"), true);

  assert.equal(completionEvidenceError("declined", {
    decision_ref: "decision:decline-transformer",
  }).missing.includes("independent_verifier_ref"), true);
});

test("advancement activates exactly one successor and never skips a queued row", () => {
  assert.deepEqual(nextProjectState(1, [
    { sequence: 2, state: "ready" },
    { sequence: 3, state: "ready" },
  ]), { completeProgram: false, nextSequence: 2 });

  assert.deepEqual(nextProjectState(50, [
    { sequence: 51, state: "ready" },
  ]), { completeProgram: false, nextSequence: 51 });

  assert.deepEqual(nextProjectState(51, []), {
    completeProgram: true,
    nextSequence: null,
  });

  assert.throws(() => nextProjectState(1, [
    { sequence: 3, state: "ready" },
  ]), /queue_gap/);
  assert.throws(() => nextProjectState(1, [
    { sequence: 2, state: "in_progress" },
  ]), /successor_not_ready/);
});

test("the registry exposes a read context and only human-governed lifecycle writes", () => {
  assert.equal(Boolean(TOOLS["capability-program"]?.write), false);
  assert.equal(Boolean(TOOLS["start-capability-project"]?.write), true);
  assert.equal(Boolean(TOOLS["complete-capability-project"]?.write), true);
  assert.equal(Boolean(TOOLS["begin-capability-project"]?.write), true);
  assert.equal(Boolean(TOOLS["attest-capability-project"]?.write), true);
  assert.equal(TOOLS["complete-capability-project"].humanOnly, true);
  assert.equal(TOOLS["start-capability-project"].humanOnly, true);
  assert.equal(TOOLS["begin-capability-project"].humanOnly, true);
  assert.equal(TOOLS["attest-capability-project"].humanOnly, true);

  const completion = TOOLS["complete-capability-project"].inputSchema;
  assert.equal(completion.additionalProperties, false);
  assert.equal(completion.required.includes("base_version"), true);
  assert.equal(completion.required.includes("completion_evidence"), true);
  assert.deepEqual(completion.properties.completion_kind.enum, COMPLETION_KINDS);
  assert.equal(TOOLS["prepare-capability-project"].humanOnly, true);
  assert.equal(TOOLS["capability-program"].inputSchema.properties.program_key.const, PROGRAM,
    "the public reader must not select an arbitrary program");
  for (const name of ["start-capability-project", "begin-capability-project", "prepare-capability-project", "attest-capability-project", "complete-capability-project"])
    assert.equal(TOOLS[name].inputSchema.properties.program_key.const, PROGRAM,
      `${name} must reject cross-program writes`);
});

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const actor = { id: "human-owner", human: true, slug: "joe" };
const row = (overrides = {}) => ({
  id: "wr-1", ref: "WR-AI-001", program_ordinal: 1, version: 7, state: "verification",
  disposition: "build", title: "LLM evaluation harness", project_context: {
    active_session_ref: "session:owned-1",
    candidate_evidence: { artifact_ref: "commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", acceptance_test_refs: ["ci:100"] },
  }, ...overrides,
});

function programToolsFor(rowValue) {
  const writes = [];
  const db = { query: async (sql, params = []) => {
    if (sql.includes("select w.* from ops.work_request")) return { rows: [rowValue] };
    if (sql.includes("program_ordinal>$2")) return { rows: [{ ...rowValue, id: "wr-2", ref: "WR-AI-002", program_ordinal: 2, state: "ready" }] };
    if (sql.includes("update ops.work_request")) { writes.push({ sql, params }); return { rows: [{ ...rowValue, state: "confirmed_closed" }] }; }
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({
    withEnvelope: async (_c, _a, _verb, _args, fn) => fn(),
    writeEvent: async () => {}, ToolError,
  });
  return { db, tools, writes };
}

test("completion refuses fake, same-actor, mismatched, or unbound verification evidence", async () => {
  const { db, tools, writes } = programToolsFor(row());
  const complete = tools["complete-capability-project"].handler;
  const base = {
    idempotency_key: "test-complete-1", program_key: PROGRAM, sequence: 1, base_version: 7,
    completion_kind: "built", completion_evidence: {
      artifact_ref: "commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", acceptance_test_refs: ["ci:100"],
      independent_verifier_ref: "finding:independent-pass",
    },
  };
  for (const completion_evidence of [
    { ...base.completion_evidence, independent_verifier_ref: "totally-made-up" },
    { ...base.completion_evidence, independent_verifier_ref: "finding:human-owner" },
    { ...base.completion_evidence, artifact_ref: "commit:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" },
    { ...base.completion_evidence, acceptance_test_refs: ["ci:999"] },
  ]) await assert.rejects(
    complete(db, actor, { ...base, completion_evidence }),
    error => error instanceof ToolError && /verification|candidate|evidence/.test(error.payload.error),
  );
  assert.equal(writes.length, 0, "a rejected completion must not close or advance the queue");
});

test("persisted candidate plus an independent pass closes exactly one project and exposes its successor", async () => {
  const sessionId = "11111111-1111-4111-8111-111111111111";
  const executorId = "22222222-2222-4222-8222-222222222222";
  const verifierId = "33333333-3333-4333-8333-333333333333";
  const passId = "44444444-4444-4444-8444-444444444444";
  const fingerprint = "a".repeat(32);
  const current = row();
  const session = {
    id: sessionId, work_request_id: current.id, state: "verification", candidate_kind: "built",
    candidate_fingerprint: fingerprint, executor_actor_id: executorId,
    candidate_evidence: { artifact_ref: "commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", candidate_commit_sha: "a".repeat(40), acceptance_test_refs: ["ci:100"] },
  };
  const writes = [];
  const db = { query: async (sql, params = []) => {
    if (sql.includes("select w.* from ops.work_request")) return { rows: [current] };
    if (sql.includes("from ops.capability_agent_session")) return { rows: [session] };
    if (sql.includes("from ops.capability_verification")) return { rows: [{
      id: passId, verifier_actor_id: verifierId, verification_evidence_ref: "test:ci-100",
      source_ref: "independent review", attested_at: "2026-08-15T00:00:00.000Z", candidate_fingerprint: fingerprint,
    }] };
    if (sql.includes("update ops.work_request")) { writes.push({ kind: "close", params }); return { rows: [{ ...current, state: "confirmed_closed" }] }; }
    if (sql.includes("update ops.capability_agent_session")) { writes.push({ kind: "session", params }); return { rows: [] }; }
    if (sql.includes("program_ordinal>$2")) return { rows: [{ ...current, id: "wr-2", ref: "WR-AI-002", program_ordinal: 2, state: "ready" }] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  const result = await tools["complete-capability-project"].handler(db, { ...actor, id: verifierId }, {
    idempotency_key: "happy-path-1", program_key: PROGRAM, sequence: 1, base_version: 7,
    capability_agent_session_id: sessionId, completion_kind: "built", completion_evidence: { candidate_fingerprint: fingerprint },
  });
  assert.equal(result.completed_project.state, "confirmed_closed");
  assert.equal(result.next_project.ref, "WR-AI-002");
  assert.deepEqual(writes.map(w => w.kind), ["close", "session"]);

  for (const bad of [
    { actor: { ...actor, id: executorId }, evidence: { candidate_fingerprint: fingerprint }, label: "self actor" },
    { actor: { ...actor, id: verifierId }, evidence: { candidate_fingerprint: "b".repeat(32) }, label: "candidate mismatch" },
  ]) await assert.rejects(
    tools["complete-capability-project"].handler(db, bad.actor, {
      idempotency_key: `reject-${bad.label}`, program_key: PROGRAM, sequence: 1, base_version: 7,
      capability_agent_session_id: sessionId, completion_kind: "built", completion_evidence: bad.evidence,
    }), error => error instanceof ToolError && /self|fingerprint/.test(error.payload.error), bad.label,
  );

  const noPass = { query: async (sql, params) => {
    if (sql.includes("from ops.capability_verification")) return { rows: [] };
    return db.query(sql, params);
  }};
  await assert.rejects(
    tools["complete-capability-project"].handler(noPass, { ...actor, id: verifierId }, {
      idempotency_key: "reject-no-pass", program_key: PROGRAM, sequence: 1, base_version: 7,
      capability_agent_session_id: sessionId, completion_kind: "built", completion_evidence: { candidate_fingerprint: fingerprint },
    }), error => error instanceof ToolError && error.payload.error === "independent_capability_pass_required",
  );
});

test("lifecycle binds a persisted session and does not falsely collapse ready through claimed", async () => {
  const source = fs.readFileSync(path.join(REPO, "mcp-server/src/capability-program.js"), "utf8");
  assert.match(source, /insert into ops\.capability_agent_session/i,
    "start must create/validate a persisted session, not accept an arbitrary string");
  assert.match(source, /state='claimed'/i,
    "ready→claimed must be a real durable transition");
  assert.match(source, /active_session_id|agent_session_id/i,
    "prepare and complete must bind to the same persisted session");
});

test("the scheduled builder definition cannot certify, merge, deploy, or communicate", () => {
  const prompt = fs.readFileSync(
    path.join(REPO, "ops/scheduled-tasks/ai-capability-builder.SKILL.md"), "utf8");
  for (const boundary of [
    "NEVER mark a project complete",
    "NEVER merge",
    "NEVER deploy",
    "NEVER communicate externally",
    "one current project",
    "reminder_only_pending_runner_identity",
    "must not edit\\s+files",
    "must not[\\s\\S]*delegate implementation",
  ]) assert.match(prompt, new RegExp(boundary, "i"));
});

test("the dispatcher refuses every capability lifecycle write to a non-human actor", async () => {
  const nonHuman = { id: "scheduled-builder", human: false, slug: "scheduled-builder" };
  for (const name of ["start-capability-project", "begin-capability-project", "prepare-capability-project", "attest-capability-project", "complete-capability-project"]) {
    await assert.rejects(
      executeRegisteredTool({ query: async () => { throw new Error("handler must not run"); } }, nonHuman, name, {}),
      error => error instanceof RegistryToolError && error.payload.error === "human_only",
      `${name} must be stopped by the dispatcher before its handler`);
  }
});
