import test from "node:test";
import assert from "node:assert/strict";
import { pipelineChanges } from "../src/dealroom.js";
import { TOOLS } from "../src/tools.js";

const ids = {
  deal: "10000000-0000-0000-0000-000000000001",
  joe: "20000000-0000-0000-0000-000000000001",
  dell: "20000000-0000-0000-0000-000000000002",
};
const actors = {
  joe: { id: ids.joe, slug: "joe", display: "Joe", human: true },
  dell: { id: ids.dell, slug: "dell", display: "Dell", human: true },
};

class FakeClient {
  constructor(now = "2026-08-08T15:00:00.000Z") {
    this.now = new Date(now);
    this.sequence = 1;
    this.toolCalls = new Map();
    this.events = [];
    this.leases = new Map();
    this.notes = [];
    this.conflicts = [];
    this.criticalDates = [];
    this.captureSessions = [];
    this.deals = new Map([[ids.deal, {
      id: ids.deal, name: "Deal Alpha", phase: "research", owner: "joe",
      type: "renewal", city: "Mobile", segment: "healthcare",
      attention: false, next_date: null, version: 1,
    }]]);
  }

  tick(milliseconds) { this.now = new Date(this.now.getTime() + milliseconds); }
  uuid() { return `90000000-0000-0000-0000-${String(this.sequence++).padStart(12, "0")}`; }
  actorSlug(id) { return Object.values(actors).find(actor => actor.id === id)?.slug || "system"; }
  tupleAfter(row, time, id) {
    return row.recorded_at > time || (row.recorded_at === time && row.id > id);
  }
  addEvent({ actor = actors.joe, verb = "seed", subject_type = "deal", subject_id = ids.deal,
    field = null, old_value = null, new_value = null, recorded_at = this.now.toISOString(), id = this.uuid() }) {
    const row = { id, recorded_at, actor: actor.slug, actor_id: actor.id, verb, subject_type,
      subject_id, field, old_value, new_value };
    this.events.push(row);
    return row;
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();

    if (sql.startsWith("select request_hash, response from tool_call")) {
      const prior = this.toolCalls.get(params[0]);
      return { rows: prior ? [{ request_hash: prior.request_hash, response: prior.response }] : [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    if (sql.includes("from v_ref_index") && sql.includes("subject_type='deal'")) {
      const needle = String(params[0]).replaceAll("%", "").toLowerCase();
      const rows = [...this.deals.values()].filter(d => d.name.toLowerCase().includes(needle))
        .map(d => ({ subject_id: d.id, display_name: d.name, status: d.phase, client_ref: "C-1" }));
      return { rows };
    }
    if (sql.includes("dealroom:field-lock")) return { rows: [{}] };
    if (sql.includes("dealroom:base-event")) {
      return { rows: this.events.filter(e => e.id === params[0] && e.subject_type === "deal" &&
        e.subject_id === params[1] && e.field === params[2]).map(e => ({ recorded_at: e.recorded_at, id: e.id })) };
    }
    if (sql.includes("dealroom:latest-field-event")) {
      const [dealId, field, baseTime, baseId] = params;
      const rows = this.events.filter(e => e.subject_type === "deal" && e.subject_id === dealId && e.field === field)
        .filter(e => !baseTime || this.tupleAfter(e, String(baseTime), String(baseId)))
        .sort((a, b) => b.recorded_at.localeCompare(a.recorded_at) || b.id.localeCompare(a.id));
      return { rows: rows.slice(0, 1).map(e => ({ event_id: e.id, actor_id: e.actor_id,
        actor: e.actor, value: e.new_value?.[field] ?? null })) };
    }
    if (/^select (phase|owner|attention|next_date) as value from deal/.test(sql)) {
      const field = sql.match(/^select (\w+) as value/)[1];
      const deal = this.deals.get(params[0]);
      return { rows: deal ? [{ value: deal[field] }] : [] };
    }
    if (sql.includes("dealroom:apply-field")) {
      const field = sql.match(/update deal set (\w+)=/)[1];
      const deal = this.deals.get(params[0]);
      deal[field] = params[1];
      deal.version += 1;
      return { rows: [] };
    }
    if (sql.startsWith("insert into event")) {
      this.addEvent({ actor: { id: params[1], slug: this.actorSlug(params[1]) }, verb: params[2],
        subject_type: params[3], subject_id: params[4], field: params[5],
        old_value: params[6] ? JSON.parse(params[6]) : null,
        new_value: params[7] ? JSON.parse(params[7]) : null,
        recorded_at: this.now.toISOString() });
      return { rows: [] };
    }
    if (sql.includes("dealroom:create-conflict")) {
      const conflict = { id: this.uuid(), deal_id: params[0], field: params[1],
        value_a: JSON.parse(params[2]), actor_a: params[3], event_a: params[4],
        value_b: JSON.parse(params[5]), actor_b: params[6], status: "open",
        resolved_by: null, winner: null };
      this.conflicts.push(conflict);
      return { rows: [{ id: conflict.id, status: conflict.status }] };
    }
    if (sql.includes("dealroom:get-conflict")) {
      const found = this.conflicts.find(conflict => conflict.id === params[0]);
      return { rows: found ? [{ ...found }] : [] };
    }
    if (sql.includes("dealroom:resolve-conflict")) {
      const found = this.conflicts.find(conflict => conflict.id === params[0]);
      found.status = "resolved";
      found.resolved_by = params[1];
      found.winner = params[2];
      return { rows: [] };
    }
    if (sql.includes("dealroom:presence-upsert")) {
      const expires_at = new Date(this.now.getTime() + 3000).toISOString();
      this.leases.set(params.join(":"), { actor: this.actorSlug(params[0]), actor_id: params[0],
        deal_id: params[1], field: params[2], expires_at });
      return { rows: [{ expires_at }] };
    }
    if (sql.includes("dealroom:current-step")) {
      const rows = this.notes.filter(n => n.deal_id === params[0] && n.kind === "next_step")
        .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
      return { rows: rows.slice(0, 1).map(({ id, text: noteText }) => ({ id, text: noteText })) };
    }
    if (sql.includes("dealroom:add-note") || sql.includes("dealroom:add-next-step")) {
      const kind = sql.includes("add-next-step") ? "next_step" : "note";
      const note = { id: this.uuid(), deal_id: params[0], kind, text: params[1], actor_id: params[2],
        actor: this.actorSlug(params[2]), created_at: new Date(this.now.getTime() + this.sequence).toISOString() };
      this.notes.push(note);
      return { rows: [{ id: note.id, created_at: note.created_at }] };
    }
    if (sql.includes("dealroom:set-next-date")) {
      const deal = this.deals.get(params[0]);
      deal.next_date = params[1];
      deal.version += 1;
      return { rows: [] };
    }
    if (sql.includes("from v_deal_room_event") && sql.includes("limit $3")) {
      const [cursorTime, cursorId, limit] = params;
      const rows = this.events.filter(e => e.subject_type === "deal")
        .filter(e => !["sf_commission_placeholder", "sf_close_date_placeholder"].includes(e.field))
        .filter(e => !cursorTime || this.tupleAfter(e, String(cursorTime), String(cursorId)))
        .sort((a, b) => a.recorded_at.localeCompare(b.recorded_at) || a.id.localeCompare(b.id))
        .slice(0, limit).map(e => ({ ...e, actor_id: undefined }));
      return { rows };
    }
    if (sql.includes("from v_deal_room_presence")) {
      const rows = [...this.leases.values()].filter(lease => new Date(lease.expires_at) > this.now)
        .map(({ actor, deal_id, field, expires_at }) => ({ actor, deal_id, field, expires_at }))
        .sort((a, b) => a.actor.localeCompare(b.actor) || a.deal_id.localeCompare(b.deal_id) || a.field.localeCompare(b.field));
      return { rows };
    }
    if (sql.includes("from v_capture_session_status")) return { rows: this.captureSessions.map(row => ({ ...row })) };
    if (sql.includes("from v_deal_room_deal")) {
      const deal = this.deals.get(params[0]);
      return { rows: deal ? [{ phase: deal.phase, owner: deal.owner, type: deal.type, city: deal.city,
        segment: deal.segment, attention: deal.attention, next_date: deal.next_date }] : [] };
    }
    if (sql.includes("from v_deal_room_note")) {
      const rows = this.notes.filter(n => n.deal_id === params[0])
        .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))
        .map(({ id, kind, text: noteText, actor, created_at }) => ({ id, kind, text: noteText, actor, created_at }));
      return { rows };
    }
    if (sql.includes("from v_deal_room_critical_date")) return { rows: [...this.criticalDates] };
    if (sql.includes("from v_deal_room_event") && sql.includes("subject_id=$1")) {
      const rows = this.events.filter(e => e.subject_type === "deal" && e.subject_id === params[0])
        .filter(e => !["sf_commission_placeholder", "sf_close_date_placeholder"].includes(e.field))
        .sort((a, b) => b.recorded_at.localeCompare(a.recorded_at) || b.id.localeCompare(a.id))
        .map(({ id, recorded_at, actor, verb, field, old_value, new_value }) =>
          ({ id, recorded_at, actor, verb, field, old_value, new_value }));
      return { rows };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

async function call(name, client, actor, args) {
  return TOOLS[name].handler(client, actor, args);
}

async function body(response) { return response.json(); }

test("cursor pages are stable, strictly ordered, gap-free, and placeholder-free", async () => {
  const db = new FakeClient();
  const t = "2026-08-08T15:00:00.000Z";
  db.addEvent({ id: "30000000-0000-0000-0000-000000000002", recorded_at: t, field: "phase", new_value: { phase: "research" } });
  db.addEvent({ id: "30000000-0000-0000-0000-000000000001", recorded_at: t, field: "owner", new_value: { owner: "joe" } });
  db.addEvent({ field: "sf_commission_placeholder", new_value: { sf_commission_placeholder: 1 } });
  db.addEvent({ subject_type: "client", field: "phase", new_value: { phase: "closed" } });
  db.tick(1000);
  db.addEvent({ field: null, new_value: { phase: "negotiation", sf_close_date_placeholder: "hidden" } });
  db.tick(1000);
  db.addEvent({ field: "attention", new_value: { attention: true } });

  const firstRequest = new Request("https://example.test/pipeline/changes");
  const first = await body(await pipelineChanges(firstRequest, db, actors.joe, { limit: 2 }));
  const firstRepeat = await body(await pipelineChanges(firstRequest, db, actors.joe, { limit: 2 }));
  assert.deepEqual(firstRepeat, first);
  assert.deepEqual(first.events.map(e => e.id), [
    "30000000-0000-0000-0000-000000000001",
    "30000000-0000-0000-0000-000000000002",
  ]);

  const pageRequest = new Request(`https://example.test/pipeline/changes?cursor=${first.cursor}`);
  const second = await body(await pipelineChanges(pageRequest, db, actors.dell, { limit: 2 }));
  const secondRepeat = await body(await pipelineChanges(pageRequest, db, actors.dell, { limit: 2 }));
  assert.deepEqual(secondRepeat, second);
  assert.equal([...first.events, ...second.events].length, 4);
  assert.equal(JSON.stringify({ first, second }).includes("sf_commission_placeholder"), false);
  assert.equal(JSON.stringify({ first, second }).includes("sf_close_date_placeholder"), false);
  assert.equal((await pipelineChanges(
    new Request("https://example.test/pipeline/changes?cursor=broken"), db, actors.joe)).status, 400);
});

test("presence lease upserts and expires at read time with a fake clock", async () => {
  const db = new FakeClient();
  await call("presence-lease", db, actors.joe,
    { idempotency_key: "lease-1", deal: "Deal Alpha", field: "phase" });
  assert.equal(db.leases.size, 1);
  assert.equal(db.events.length, 0);

  db.tick(1000);
  let polled = await body(await pipelineChanges(new Request("https://example.test/pipeline/changes"), db, actors.dell));
  assert.deepEqual(polled.presence.map(p => [p.actor, p.field]), [["joe", "phase"]]);

  await call("presence-lease", db, actors.joe,
    { idempotency_key: "lease-2", deal: "Deal Alpha", field: "phase" });
  assert.equal(db.leases.size, 1);
  db.tick(2999);
  polled = await body(await pipelineChanges(new Request("https://example.test/pipeline/changes"), db, actors.dell));
  assert.equal(polled.presence.length, 1);
  db.tick(2);
  polled = await body(await pipelineChanges(new Request("https://example.test/pipeline/changes"), db, actors.dell));
  assert.equal(polled.presence.length, 0);
});

test("concurrent edits to different fields both land without conflict", async () => {
  const db = new FakeClient();
  const phase = await call("patch-deal-field", db, actors.joe,
    { idempotency_key: "patch-phase", deal: "Deal Alpha", field: "phase", value: "negotiation", base_event_id: null });
  const attention = await call("patch-deal-field", db, actors.dell,
    { idempotency_key: "patch-attention", deal: "Deal Alpha", field: "attention", value: true, base_event_id: null });
  assert.equal(phase.ok, true);
  assert.equal(attention.ok, true);
  assert.equal(db.deals.get(ids.deal).phase, "negotiation");
  assert.equal(db.deals.get(ids.deal).attention, true);
  assert.equal(db.conflicts.length, 0);
  assert.deepEqual(db.events.map(e => [e.field, e.actor]), [["phase", "joe"], ["attention", "dell"]]);
});

test("same-field conflict retains both actors and values; resolve uses normal attributed path", async () => {
  const db = new FakeClient();
  await call("patch-deal-field", db, actors.joe,
    { idempotency_key: "owner-a", deal: "Deal Alpha", field: "owner", value: "joe", base_event_id: null });
  const collided = await call("patch-deal-field", db, actors.dell,
    { idempotency_key: "owner-b", deal: "Deal Alpha", field: "owner", value: "dell", base_event_id: null });
  assert.equal(collided.ok, false);
  assert.deepEqual({ value_a: collided.conflict.value_a, actor_a: collided.conflict.actor_a,
    value_b: collided.conflict.value_b, actor_b: collided.conflict.actor_b },
  { value_a: "joe", actor_a: "joe", value_b: "dell", actor_b: "dell" });
  assert.equal(db.deals.get(ids.deal).owner, "joe");

  const resolved = await call("resolve-conflict", db, actors.joe,
    { idempotency_key: "resolve-owner", conflict_id: collided.conflict.id, winner: "b" });
  assert.equal(resolved.ok, true);
  assert.equal(db.deals.get(ids.deal).owner, "dell");
  assert.equal(db.conflicts[0].status, "resolved");
  assert.equal(db.conflicts[0].resolved_by, ids.joe);
  assert.deepEqual(db.events.map(e => [e.verb, e.actor]),
    [["patch-deal-field", "joe"], ["resolve-conflict", "joe"]]);
  assert.ok(db.events.every(e => e.actor_id));
});

test("next-step supersede leaves old rows intact and deal thread is stably newest-first", async () => {
  const db = new FakeClient();
  const oldStep = await call("set-next-step", db, actors.joe,
    { idempotency_key: "step-1", deal: "Deal Alpha", text: "Call landlord", next_date: "2026-08-10" });
  db.tick(1000);
  const newStep = await call("set-next-step", db, actors.dell,
    { idempotency_key: "step-2", deal: "Deal Alpha", text: "Review counter", next_date: "2026-08-11" });
  assert.equal(db.notes.length, 2);
  assert.equal(newStep.supersedes, oldStep.next_step_id);
  assert.deepEqual(db.notes.map(n => n.text), ["Call landlord", "Review counter"]);

  const page = await call("get-deal-room", db, actors.joe, { deal: "Deal Alpha" });
  assert.deepEqual(page.thread.map(n => [n.text, n.actor]),
    [["Review counter", "dell"], ["Call landlord", "joe"]]);
  assert.equal(page.next_date, "2026-08-11");
  assert.deepEqual(page.events.map(e => e.actor), ["dell", "joe"]);
  assert.equal(JSON.stringify(page).includes("sf_commission_placeholder"), false);
  assert.equal(JSON.stringify(page).includes("sf_close_date_placeholder"), false);
});

test("cursor round-trips the pg wire timestamp format (microseconds + offset) without JS Date", async () => {
  const db = new FakeClient();
  // Exactly what @neondatabase/serverless hands back for a timestamptz —
  // NOT JS-Date-parseable; the first live poll threw "Invalid time value".
  const pgWire = "2026-08-07 21:44:19.123456+00";
  db.addEvent({ id: "40000000-0000-0000-0000-000000000001", recorded_at: pgWire, field: "phase", new_value: { phase: "legal" } });

  const first = await (await pipelineChanges(new Request("https://example.test/pipeline/changes"), db, actors.joe, { limit: 5 })).json();
  assert.equal(first.events.length, 1);
  assert.ok(first.cursor, "cursor must be produced from a pg-format timestamp");

  // and the produced cursor must be accepted back on the next poll
  const again = await (await pipelineChanges(new Request(`https://example.test/pipeline/changes?cursor=${first.cursor}`), db, actors.joe, { limit: 5 })).json();
  assert.deepEqual(again.events, [], "no new events after the cursor");
  assert.equal(again.cursor, first.cursor);
});

test("pipeline polling includes capture status snapshots with string timestamps", async () => {
  const db = new FakeClient();
  db.captureSessions.push({ session_id: "50000000-0000-0000-0000-000000000001",
    device_id: "mac-studio", state: "distilling",
    started_at: "2026-08-08 14:00:00+00", state_at: "2026-08-08 14:12:00+00" });
  const result = await (await pipelineChanges(
    new Request("https://example.test/pipeline/changes"), db, actors.joe)).json();
  assert.equal(result.capture_sessions[0].state, "distilling");
  assert.equal(typeof result.capture_sessions[0].started_at, "string");
  assert.equal(typeof result.capture_sessions[0].state_at, "string");
});
