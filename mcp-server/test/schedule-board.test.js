import test from "node:test";
import assert from "node:assert/strict";

import { scheduleBoardTools } from "../src/schedule-board.js";

const actor = { slug: "joe", human: true };
const now = "2026-09-28T16:00:00.000Z";

test("schedule-board filters to the verified sponsor and marks a missed run and paused job", async () => {
  const calls = [];
  const client = { query: async (sql, args) => {
    calls.push({ sql, args });
    if (sql.includes("ops.service")) return { rows: [
      {
        key: "nightly-record-layer", name: "Nightly record layer", runtime: "launchd",
        owner_actor: "joe", cadence_seconds: 86400, grace_seconds: 3600,
        last_state: "succeeded", last_at: "2026-09-26T07:00:00.000Z",
        last_receipt_ref: "run:nightly-26", last_observed_at: "2026-09-26T07:00:00.000Z",
        scheduler_state: "enabled", schedule_observed_at: "2026-09-28T15:55:00.000Z",
        schedule_receipt_ref: "scheduler:nightly",
      },
      {
        key: "paused-task", name: "Paused task", runtime: "claude-code-scheduled-task",
        owner_actor: "joe", cadence_seconds: 86400, grace_seconds: 3600,
        last_state: "succeeded", last_at: "2026-09-27T07:00:00.000Z",
        last_receipt_ref: "run:paused", last_observed_at: "2026-09-27T07:00:00.000Z",
        scheduler_state: "disabled", schedule_observed_at: "2026-09-28T15:55:00.000Z",
        schedule_receipt_ref: "scheduler:paused",
      },
    ] };
    return { rows: [] };
  } };
  const result = await scheduleBoardTools()["schedule-board"].handler(client, actor, {}, { now: () => now });
  assert.deepEqual(calls.map((call) => call.args), [["joe"], ["joe"]]);
  assert.equal(result.schema, "schedule-board/v1");
  assert.equal(result.jobs[0].state, "missed");
  assert.equal(result.jobs[0].next_due_at, "2026-09-27T07:00:00.000Z");
  assert.equal(result.jobs[0].next_due_basis, "cadence_deadline");
  assert.equal(result.jobs[1].state, "paused");
  assert.equal(result.jobs[1].next_due_at, null);
  assert.deepEqual(result.jobs[0].actions, { pause: false, run: false, stop: false });
  assert.equal(result.sources.find((source) => source.owner === "cron").state, "unknown");
});

test("missing run and scheduler observations stay unknown, never a healthy empty schedule", async () => {
  const client = { query: async (sql) => ({ rows: sql.includes("ops.service") ? [{
    key: "silent-task", name: "Silent task", runtime: "claude-code-scheduled-task",
    owner_actor: "joe", cadence_seconds: 604800, grace_seconds: 0,
    last_state: null, last_at: null, last_receipt_ref: null, last_observed_at: null,
    scheduler_state: null, schedule_observed_at: null, schedule_receipt_ref: null,
  }] : [] }) };
  const result = await scheduleBoardTools()["schedule-board"].handler(client, actor, {}, { now: () => now });
  assert.equal(result.jobs[0].state, "unknown");
  assert.equal(result.jobs[0].next_due_at, null);
  assert.equal(result.sources.find((source) => source.owner === "claude-code").state, "read");
  assert.equal(result.overall_state, "unknown");
});

test("a control-plane result needs a completion receipt, and shared principals cannot read personal schedules", async () => {
  const control = {
    key: "daily-review", version: 1, enabled: true,
    recurrence: { cron: "0 7 * * *", timezone: "America/Chicago" },
    last_state: "succeeded", last_at: "2026-09-28T07:00:00.000Z",
    next_due_at: "2026-09-29T07:00:00.000Z", last_receipt_ref: null,
  };
  const client = { query: async (sql) => ({ rows: sql.includes("ops.service") ? [] : [control] }) };
  const read = scheduleBoardTools()["schedule-board"].handler;
  const unknown = await read(client, actor, {}, { now: () => now });
  assert.equal(unknown.jobs[0].last_run, null);
  assert.equal(unknown.jobs[0].state, "unknown");
  control.last_receipt_ref = "job:daily-review:complete";
  const verified = await read(client, actor, {}, { now: () => now });
  assert.equal(verified.jobs[0].state, "healthy");
  assert.equal(verified.jobs[0].schedule, "Cron: 0 7 * * * · America/Chicago");
  assert.equal(verified.jobs[0].last_run.receipt_ref, control.last_receipt_ref);
  assert.equal(verified.overall_state, "unknown", "unread scheduler owners cannot imply full coverage");
  await assert.rejects(read(client, { slug: "probe", human: false }, {}, { now: () => now }),
    /schedule_board_requires_partner_scope/);
});
