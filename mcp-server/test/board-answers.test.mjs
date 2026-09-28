import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { TOOLS, ToolError } from "../src/tools.js";

const joe = {
  id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe",
  human: true, via: "oauth-google", client_id: "doctorcre-app",
};
const robot = { ...joe, id: "10000000-0000-0000-0000-000000000009", slug: "board-job", human: false };

class Fake {
  constructor(plan = {}) { this.plan = plan; this.calls = []; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.calls.push([sql, params]);
    for (const [fragment, value] of Object.entries(this.plan)) {
      if (sql.includes(fragment)) return { rows: typeof value === "function" ? value(params) : value };
    }
    return { rows: [] };
  }
}

test("board verbs expose typed versioned writes and a partner-only answer", () => {
  for (const name of ["publish-board-snapshot", "ask-board-question", "revise-board-question",
    "answer-board-question", "acknowledge-board-answer", "record-board-answer-applied"]) {
    assert.equal(TOOLS[name]?.write, true, name);
    assert.ok(TOOLS[name].inputSchema.required.includes("idempotency_key"), name);
    assert.ok(TOOLS[name].inputSchema.required.includes("base_version"), name);
  }
  assert.equal(TOOLS["answer-board-question"].humanOnly, true);
  for (const name of ["read-progress-board", "read-board-answers"])
    assert.equal(TOOLS[name]?.write, false, name);
  assert.deepEqual(TOOLS["read-board-answers"].inputSchema.required,
    ["after_cursor", "asker_ref"]);
});

test("board snapshot publication is tenant scoped and refuses stale versions", async () => {
  const fake = new Fake({ "update board_snapshot": [] });
  await assert.rejects(
    () => TOOLS["publish-board-snapshot"].handler(fake, robot, {
      board_id: "project", base_version: 1, snapshot: { title: "Project", tasks: {} },
      idempotency_key: "publish-1",
    }),
    error => error instanceof ToolError && error.payload.error === "board_version_conflict",
  );
  const [sql, params] = fake.calls.find(([statement]) => statement.includes("update board_snapshot"));
  assert.match(sql, /organization_tenant_id/);
  assert.match(sql, /version/);
  assert.ok(params.includes("carr-internal"));
});

test("answer uses the authenticated partner, never a caller supplied answered_by", async () => {
  const answer = { id: "answer-1", question_id: "q1", question_revision: 1,
    answer_text: "Proceed", answered_by: "joe", version: 1, status: "Sent" };
  const fake = new Fake({
    "select request_hash, response from tool_call": [],
    "from board_question": [{ question_id: "q1", revision: 1, choices: [], allow_free_text: true,
      default_answer: null, asker_ref: "orchestrator:project" }],
    "insert into board_answer": [answer],
    "insert into event": [{ id: "event-1" }],
  });
  const result = await TOOLS["answer-board-question"].handler(fake, joe, {
    idempotency_key: "answer-1", board_id: "project", question_id: "q1",
    base_version: 1, answer_text: "Proceed",
  });
  assert.equal(result.answer.answered_by, "joe");
  const [, params] = fake.calls.find(([sql]) => sql.includes("insert into board_answer"));
  assert.ok(params.includes(joe.id));
  assert.ok(params.includes("joe"));
  assert.equal(TOOLS["answer-board-question"].inputSchema.properties.answered_by, undefined);
});

test("a machine cannot answer even when it has a verified sponsor", async () => {
  const fake = new Fake();
  await assert.rejects(
    () => TOOLS["answer-board-question"].handler(fake,
      { ...robot, native_agent_verified: true, sponsoring_human_slug: "joe" }, {
        idempotency_key: "answer-machine", board_id: "project", question_id: "q1",
        base_version: 1, answer_text: "Proceed",
      }),
    error => error instanceof ToolError && error.payload.error === "board_answer_requires_human_partner",
  );
  assert.deepEqual(fake.calls, []);
});

test("Received and Applied require exact versions and record the actor and effect", async () => {
  const answerId = "20000000-0000-0000-0000-000000000001";
  const received = { id: answerId, version: 2, status: "Received" };
  const applied = { id: answerId, version: 3, status: "Applied", effect_ref: "pr:1400" };
  const fake = new Fake({
    "select request_hash, response from tool_call": [],
    "update board_answer a set received_at": [received],
    "update board_answer a set applied_at": [applied],
  });
  const ack = await TOOLS["acknowledge-board-answer"].handler(fake, robot, {
    idempotency_key: "ack-1", answer_id: answerId,
    asker_ref: "orchestrator:project", base_version: 1,
  });
  assert.equal(ack.answer.status, "Received");
  const [ackSql, ackParams] = fake.calls.find(([sql]) => sql.includes("update board_answer a set received_at"));
  assert.match(ackSql, /received_for_ref/);
  assert.match(ackSql, /version=\$5/);
  assert.deepEqual(ackParams, [robot.id, "orchestrator:project", "carr-internal", answerId, 1]);
  const result = await TOOLS["record-board-answer-applied"].handler(fake, robot, {
    idempotency_key: "applied-1", answer_id: answerId, base_version: 2,
    effect_ref: "pr:1400",
  });
  assert.equal(result.answer.status, "Applied");
  const [applySql, applyParams] = fake.calls.find(([sql]) => sql.includes("update board_answer a set applied_at"));
  assert.match(applySql, /received_at is not null/);
  assert.deepEqual(applyParams, [robot.id, "pr:1400", "carr-internal", answerId, 2]);
});

test("the board read leaves unanswered questions without a status", async () => {
  const fake = new Fake({
    "from board_snapshot": [{ board_id: "project", version: 1, snapshot_json: { title: "Project" } }],
    "from board_question q": [{ question_id: "q1", answer_id: null, status: null }],
  });
  const result = await TOOLS["read-progress-board"].handler(fake, joe, { board_id: "project" });
  assert.equal(result.questions[0].status, null);
  const [sql] = fake.calls.find(([statement]) => statement.includes("from board_question q"));
  assert.match(sql, /case when a\.id is null then null/);
  assert.match(sql, /q\.current=true/);
});

test("read cursor and asker reference are both in the tenant scoped query", async () => {
  const fake = new Fake({ "from board_answer": [] });
  const result = await TOOLS["read-board-answers"].handler(fake, robot,
    { after_cursor: 12, asker_ref: "orchestrator:project" });
  assert.deepEqual(result.answers, []);
  const [sql, params] = fake.calls.find(([statement]) => statement.includes("from board_answer"));
  assert.match(sql, /organization_tenant_id/);
  assert.match(sql, /asker_ref/);
  assert.match(sql, /cursor > /);
  assert.deepEqual(params.slice(0, 3), ["carr-internal", "orchestrator:project", 12]);
});

test("migration pairs the typed records with a sealed successor", () => {
  const schema = readFileSync(new URL("../../migrations/0740_board_answers.sql", import.meta.url), "utf8");
  const seal = readFileSync(new URL("../../migrations/0741_board_answers_scac_successor.sql", import.meta.url), "utf8");
  const migrate = readFileSync(new URL("../../tools/migrate.py", import.meta.url), "utf8");
  for (const table of ["board_snapshot", "board_question", "board_answer"])
    assert.match(schema, new RegExp(`create table public\\.${table}`));
  assert.match(schema, /unique \(organization_tenant_id,board_id,question_id,question_revision\)/i);
  assert.match(seal, /scac-mutation-registry\.v93/);
  assert.equal((migrate.match(/0740_board_answers\.sql/g) || []).length, 2);
  assert.equal((migrate.match(/0741_board_answers_scac_successor\.sql/g) || []).length, 2);
});
