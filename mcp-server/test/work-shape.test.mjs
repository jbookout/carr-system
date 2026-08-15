import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { TOOLS } from "../src/tools.js";
import { capabilityProgramTools } from "../src/capability-program.js";
import { shapeDecisionError, workShapeTools } from "../src/work-shape.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
const MIGRATION = path.join(REPO, "migrations/0129_work_shape_revision.sql");

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const actor = { id: "11111111-1111-4111-8111-111111111111", slug: "codex", human: false };

function validShape(overrides = {}) {
  return {
    trinity: {
      workflow_trigger: "A bounded engineering request reaches build readiness",
      output_user: "The accountable requester and the implementing agent",
      runtime: "Cloudflare Worker plus Postgres",
    },
    hidden_assumption: "The work is best expressed as a durable request-linked record rather than a dashboard.",
    repo_searches: ["durable agent approval", "work request decision provenance"],
    maintained_repos: [
      { url: "https://github.com/openai/openai-agents-python", maintenance_evidence: "recent releases and commits" },
      { url: "https://github.com/cloudflare/agents", maintenance_evidence: "recent commits" },
      { url: "https://github.com/langchain-ai/langgraph", maintenance_evidence: "active releases" },
      { url: "https://github.com/microsoft/agent-framework", maintenance_evidence: "active development" },
      { url: "https://github.com/dlt-hub/dlt", maintenance_evidence: "active releases" },
    ],
    archetypes: [
      { key: "workspace", label: "Workspace-first UI", core_assumption: "Work starts when a person opens a screen", scores: { trinity_fit: 2, useful_v1_effort: 2, extension_effort: 4 } },
      { key: "lane", label: "Governed work lane", core_assumption: "Work starts as a bounded request that must survive offline actors", scores: { trinity_fit: 5, useful_v1_effort: 3, extension_effort: 2 } },
      { key: "scheduled", label: "Scheduled ingestion lane", core_assumption: "Most useful work arrives as recurring external deltas", scores: { trinity_fit: 3, useful_v1_effort: 3, extension_effort: 3 } },
    ],
    chosen_key: "lane",
    mind_changing_fact: "Choose scheduled ingestion first if measured external deltas dominate bounded requests.",
    builder_brief: {
      chosen_shape: "A governed work lane linked to the canonical Work Request",
      repo_url: "https://github.com/openai/openai-agents-python",
      trinity: {
        workflow_trigger: "A bounded engineering request reaches build readiness",
        output_user: "The accountable requester and the implementing agent",
        runtime: "Cloudflare Worker plus Postgres",
      },
      must_have_integrations: ["ops.work_request", "event", "tool_call"],
      v1_non_goals: ["generic workflow engine", "browser execution", "autonomous approval"],
      text: "Build a governed work lane around the canonical Work Request. Trigger it when a bounded request reaches build readiness and its implementation surface is still open. The accountable requester consumes the outcome while an attributed agent may implement it. Run the lane in the existing Cloudflare Worker and Postgres record layer. Persist the trinity, evidence-backed alternatives, chosen shape, and the fact that would reverse the choice. Integrate with ops.work_request, event, and tool_call so revisions are attributable and stale writes conflict. Use the OpenAI Agents approval and resume pattern only as a behavioral reference. Keep version one narrow: no generic workflow engine, no browser execution, no autonomous approval, no copied business payload, and no new model-specific authority.",
    },
    source_url: "https://x.com/nurijanian/status/2088524098549018944",
    ...overrides,
  };
}

test("a complete, evidence-backed shape decision passes", () => {
  assert.equal(shapeDecisionError(validShape()), null);
});

test("shape validation refuses fake variety, thin recon, invalid scores, and loose briefs", () => {
  const base = validShape();
  assert.equal(shapeDecisionError({ ...base, repo_searches: ["one"] }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, maintained_repos: base.maintained_repos.slice(0, 4) }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, maintained_repos: base.maintained_repos.map(repo => ({ ...repo, url: base.maintained_repos[0].url })) }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, archetypes: [base.archetypes[0], base.archetypes[0], base.archetypes[2]] }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, archetypes: base.archetypes.map((x, i) => i ? x : { ...x, scores: { ...x.scores, trinity_fit: 6 } }) }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, chosen_key: "missing" }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, builder_brief: { ...base.builder_brief, repo_url: "https://github.com/example/not-researched" } }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, builder_brief: { ...base.builder_brief, trinity: { ...base.trinity, runtime: "Browser memory" } } }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, builder_brief: { ...base.builder_brief, text: "too short" } }).error, "work_shape_invalid");
});

test("the registry exposes a model-agnostic append and read surface", () => {
  assert.equal(TOOLS["read-work-shape"].write, false);
  assert.equal(TOOLS["write-work-shape"].write, true);
  assert.equal(TOOLS["read-work-shape"].fullOnly, true);
  assert.notEqual(TOOLS["write-work-shape"].humanOnly, true);
  assert.equal(TOOLS["write-work-shape"].inputSchema.required.includes("base_version"), true);
  assert.equal(TOOLS["write-work-shape"].inputSchema.required.includes("work_request_base_version"), true);
  assert.equal(TOOLS["write-work-shape"].inputSchema.required.includes("idempotency_key"), true);
  assert.equal(TOOLS["write-work-shape"].inputSchema.additionalProperties, false);
  const routing = fs.readFileSync(path.join(REPO, "mcp-server/src/mcp.js"), "utf8");
  assert.match(routing, /if \(tool\.fullOnly\) return false;[\s\S]*if \(!tool\.write\) return true;/,
    "fullOnly operational reads must be refused before the general read-profile allowance");
});

test("write-work-shape appends one revision and refuses a stale base version", async () => {
  const work = { id: "22222222-2222-4222-8222-222222222222", ref: "WR-TEST-1", title: "Shape test", state: "ready", version: 7, shape_required: true };
  const existing = { id: "33333333-3333-4333-8333-333333333333", work_request_id: work.id, work_request_version: 7, version: 2, ...validShape() };
  const inserts = [];
  const db = { query: async (sql, params = []) => {
    if (sql.includes("from ops.work_request") && sql.includes("for update")) return { rows: [work] };
    if (sql.includes("from ops.work_shape_revision") && sql.includes("limit 1")) return { rows: [existing] };
    if (sql.includes("insert into ops.work_shape_revision")) {
      inserts.push(params);
      return { rows: [{ ...existing, id: "44444444-4444-4444-8444-444444444444", version: 3 }] };
    }
    throw new Error(`unexpected query: ${sql}`);
  }};
  const events = [];
  const tools = workShapeTools({
    withEnvelope: async (_c, _a, _v, _args, fn) => fn(),
    writeEvent: async (...args) => events.push(args),
    ToolError,
  });

  await assert.rejects(
    tools["write-work-shape"].handler(db, actor, { idempotency_key: "stale", work_request: work.ref, base_version: 1, work_request_base_version: 7, ...validShape() }),
    error => error instanceof ToolError && error.payload.error === "version_conflict" && error.payload.current_version === 2,
  );
  assert.equal(inserts.length, 0);

  await assert.rejects(
    tools["write-work-shape"].handler(db, actor, { idempotency_key: "stale-work", work_request: work.ref, base_version: 2, work_request_base_version: 6, ...validShape() }),
    error => error instanceof ToolError && error.payload.error === "work_request_version_conflict" && error.payload.current_version === 7,
  );
  const result = await tools["write-work-shape"].handler(db, actor, { idempotency_key: "fresh", work_request: work.ref, base_version: 2, work_request_base_version: 7, ...validShape() });
  assert.equal(result.shape.version, 3);
  assert.equal(result.shape.work_request_version, 7);
  assert.equal(inserts.length, 1);
  assert.equal(events.length, 1);
  assert.equal(events[0][2], "write-work-shape");
  assert.equal(events[0][3], "ops_work_request");
});

test("the work-shape envelope serializes identical idempotency keys before replay lookup", () => {
  const source = fs.readFileSync(path.join(REPO, "mcp-server/src/tools.js"), "utf8");
  const body = source.slice(source.indexOf("async function withEnvelope"), source.indexOf("async function writeEvent"));
  const lock = body.indexOf("pg_advisory_xact_lock");
  const replayRead = body.indexOf("select request_hash, response from tool_call");
  assert.ok(lock >= 0 && lock < replayRead, "the same-key transaction lock must precede replay lookup");
});

test("an explicitly shape-required capability project cannot be claimed without a decision", async () => {
  const current = {
    id: "22222222-2222-4222-8222-222222222222", ref: "WR-AI-001", title: "Open-shape project",
    program_key: "carr-ai-engineering-suite-v1", program_ordinal: 1, state: "ready", version: 4,
    shape_required: true, project_context: {}, acceptance_criteria: [],
  };
  const db = { query: async sql => {
    if (sql.includes("select w.* from ops.work_request")) return { rows: [current] };
    if (sql.includes("from ops.work_shape_revision")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  await assert.rejects(
    tools["start-capability-project"].handler(db, { ...actor, human: true }, {
      idempotency_key: "claim-without-shape", program_key: "carr-ai-engineering-suite-v1",
      sequence: 1, base_version: 4, executor_actor: "codex",
      source_commit_sha: "a".repeat(40), worktree_ref: "worktree:test",
    }),
    error => error instanceof ToolError && error.payload.error === "work_shape_required",
  );
});

test("a shape decision bound to an older Work Request version cannot satisfy the claim gate", async () => {
  const current = {
    id: "22222222-2222-4222-8222-222222222222", ref: "WR-AI-001", title: "Changed project",
    program_key: "carr-ai-engineering-suite-v1", program_ordinal: 1, state: "ready", version: 5,
    shape_required: true, project_context: {}, acceptance_criteria: [],
  };
  const db = { query: async sql => {
    if (sql.includes("select w.* from ops.work_request")) return { rows: [current] };
    if (sql.includes("from ops.work_shape_revision")) return { rows: [{ work_request_version: 4 }] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  await assert.rejects(
    tools["start-capability-project"].handler(db, { ...actor, human: true }, {
      idempotency_key: "claim-with-stale-shape", program_key: "carr-ai-engineering-suite-v1",
      sequence: 1, base_version: 5, executor_actor: "codex",
      source_commit_sha: "a".repeat(40), worktree_ref: "worktree:test",
    }),
    error => error instanceof ToolError && error.payload.error === "work_shape_required" && error.payload.reason === "stale_after_work_request_change",
  );
});

test("migration makes revisions append-only, versioned, least-privilege, and conditionally required", () => {
  const sql = fs.readFileSync(MIGRATION, "utf8");
  assert.match(sql, /add column if not exists shape_required boolean not null default false/i);
  assert.match(sql, /create table if not exists ops\.work_shape_revision/i);
  assert.match(sql, /work_request_version\s+integer not null/i);
  assert.match(sql, /unique\s*\(work_request_id, version\)/i);
  assert.match(sql, /before update or delete on ops\.work_shape_revision/i);
  assert.match(sql, /raise exception[^;]+append-only/is);
  assert.match(sql, /create or replace view ops\.v_work_shape_current/i);
  assert.match(sql, /grant select on ops\.work_shape_revision to carr_reader/i);
  assert.match(sql, /grant insert on ops\.work_shape_revision to carr_writer/i);
  assert.doesNotMatch(sql, /grant[^;]+update[^;]+work_shape_revision/i);
  assert.doesNotMatch(sql, /grant[^;]+delete[^;]+work_shape_revision/i);
});
