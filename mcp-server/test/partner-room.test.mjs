// partner-room.test.mjs — the shared room's contract, written BEFORE the verbs
// (rule e65efc68). The room is Idea 78's spectator surface: an append-only turn
// log two brains write and a human watches, served by the Worker so both Macs
// read one transcript.
//
// The contract under test, in one breath: a turn lands verbatim, attributed to
// the VERIFIED partner (server-derived from the credential, never from an
// argument), only from a credential a partner actually sponsors; replays and
// cross-transport duplicates land exactly once; and the read side is a plain
// cursor over the view, oldest first.

import test from "node:test";
import assert from "node:assert/strict";

import { TOOLS } from "../src/tools.js";

// ── actors ──────────────────────────────────────────────────────────────────
// Shapes mirror identity.js: a direct human partner, the two sponsored local
// machine doors, and a locked reviewer machine identity (registered in
// SERVER_MACHINE_IDENTITIES with marker `review` via `review-token`).
const joe = { id: "actor-joe", slug: "joe", human: true, via: "oauth-google" };
const dellLocal = { id: "actor-dell-local", slug: "dell-local", human: false,
  via: "local-token", agent: true, sponsoring_human_slug: "dell" };
const reviewer = { id: "actor-reviewer", slug: "codex-reviewer", human: false,
  via: "review-token", review: true };

// ── fake client ─────────────────────────────────────────────────────────────
// House style: pattern-match on SQL prefixes, track state as fields. It must
// also answer withEnvelope's tool_call queries; `toolCalls` doubles as the
// replay store so the same fake proves the replay path.
class RoomFake {
  constructor({ rows = [], msgIdTaken = null } = {}) {
    this.rows = rows;                 // pre-existing turns, for reads + dedup
    this.msgIdTaken = msgIdTaken;     // a msg_id that already exists
    this.inserted = [];               // insert params captured
    this.toolCalls = new Map();       // idempotency_key -> {request_hash, response}
    this.reads = [];                  // read params captured
    this.nextId = 100;
  }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response from tool_call")) {
      const prior = this.toolCalls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    if (sql.startsWith("insert into partner_room_turn")) {
      const msgId = params[5];
      if (this.msgIdTaken && msgId === this.msgIdTaken) return { rows: [] }; // on conflict do nothing
      this.inserted.push(params);
      return { rows: [{ id: this.nextId++, at: "2026-08-20T01:00:00+00:00" }] };
    }
    if (sql.startsWith("select id, room_id, sponsor, seat from partner_room_turn where msg_id")) {
      const hit = this.rows.find((r) => r.msg_id === params[0]);
      return { rows: hit ? [hit] : [] };
    }
    if (sql.includes("from v_partner_room_turn")) {
      this.reads.push(params);
      const [room, after, limit] = params;
      const match = this.rows
        .filter((r) => r.room_id === room && r.seq > after)
        .slice(0, limit);
      return { rows: match };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

const MSG = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";

// ── add-room-turn ─────────────────────────────────────────────────────────────

test("add-room-turn: a partner's turn lands verbatim, attributed to the verified sponsor", async () => {
  const db = new RoomFake();
  const out = await TOOLS["add-room-turn"].handler(db, joe, {
    idempotency_key: "k-say-1", seat: "claude", body: "raw text, exactly as spoken",
  });
  assert.equal(out.ok, true);
  assert.equal(out.sponsor, "joe");
  assert.equal(out.seat, "claude");
  assert.equal(out.room, "partner-line");
  assert.equal(typeof out.seq, "number");
  assert.equal(db.inserted.length, 1);
  const [room, sponsor, seat, kind, body] = db.inserted[0];
  assert.deepEqual([room, sponsor, seat, kind, body],
    ["partner-line", "joe", "claude", "turn", "raw text, exactly as spoken"]);
});

test("add-room-turn: a caller-supplied sponsor is ignored in favour of the verified one", async () => {
  const db = new RoomFake();
  const out = await TOOLS["add-room-turn"].handler(db, joe, {
    idempotency_key: "k-say-2", seat: "claude", body: "hi", sponsor: "dell",
  });
  assert.equal(out.sponsor, "joe");
  assert.equal(db.inserted[0][1], "joe");
});

test("add-room-turn: a sponsored machine door speaks as its partner (dell-local -> dell)", async () => {
  const db = new RoomFake();
  const out = await TOOLS["add-room-turn"].handler(db, dellLocal, {
    idempotency_key: "k-say-3", seat: "claude", body: "from dell's mac",
  });
  assert.equal(out.sponsor, "dell");
  assert.equal(db.inserted[0][1], "dell");
});

test("add-room-turn: a credential with no sponsoring partner is refused, and writes nothing", async () => {
  const db = new RoomFake();
  await assert.rejects(
    TOOLS["add-room-turn"].handler(db, reviewer, {
      idempotency_key: "k-say-4", seat: "claude", body: "should not land",
    }),
    (err) => err.payload?.error === "no_sponsoring_partner",
  );
  assert.equal(db.inserted.length, 0);
});

test("add-room-turn: an empty turn is refused, and writes nothing", async () => {
  const db = new RoomFake();
  await assert.rejects(
    TOOLS["add-room-turn"].handler(db, joe, { idempotency_key: "k-say-5", seat: "claude", body: "   " }),
    (err) => err.payload?.error === "body_required",
  );
  assert.equal(db.inserted.length, 0);
});

test("add-room-turn: an oversized turn is refused by size, named", async () => {
  const db = new RoomFake();
  await assert.rejects(
    TOOLS["add-room-turn"].handler(db, joe, {
      idempotency_key: "k-say-6", seat: "claude", body: "x".repeat(20001),
    }),
    (err) => err.payload?.error === "body_too_long",
  );
  assert.equal(db.inserted.length, 0);
});

test("add-room-turn: a seat that is not a plain slug is refused", async () => {
  const db = new RoomFake();
  await assert.rejects(
    TOOLS["add-room-turn"].handler(db, joe, {
      idempotency_key: "k-say-7", seat: "Not A Seat!", body: "hi",
    }),
    (err) => err.payload?.error === "seat_invalid",
  );
  assert.equal(db.inserted.length, 0);
});

test("add-room-turn: replaying the same idempotency_key returns the stored response, no second row", async () => {
  const db = new RoomFake();
  const args = { idempotency_key: "k-say-8", seat: "claude", body: "once only" };
  const first = await TOOLS["add-room-turn"].handler(db, joe, args);
  const second = await TOOLS["add-room-turn"].handler(db, joe, args);
  assert.equal(second.replayed, true);
  assert.equal(second.seq, first.seq);
  assert.equal(db.inserted.length, 1);
});

test("add-room-turn: a msg_id already in the room dedups instead of double-landing", async () => {
  const db = new RoomFake({
    msgIdTaken: MSG,
    rows: [{ msg_id: MSG, id: 42, room_id: "partner-line", sponsor: "joe", seat: "claude" }],
  });
  const out = await TOOLS["add-room-turn"].handler(db, joe, {
    idempotency_key: "k-say-9", seat: "claude", body: "same turn via a second transport", msg_id: MSG,
  });
  assert.equal(out.ok, true);
  assert.equal(out.deduplicated, true);
  assert.equal(out.seq, 42);
  assert.equal(db.inserted.length, 0);
});

test("add-room-turn: a malformed msg_id is refused", async () => {
  const db = new RoomFake();
  await assert.rejects(
    TOOLS["add-room-turn"].handler(db, joe, {
      idempotency_key: "k-say-10", seat: "claude", body: "hi", msg_id: "not-a-uuid",
    }),
    (err) => err.payload?.error === "msg_id_invalid",
  );
});

// ── read-room ───────────────────────────────────────────────────────────────

test("read-room: returns turns after the cursor, oldest first, with the new cursor", async () => {
  const db = new RoomFake({
    rows: [
      { seq: 5, room_id: "partner-line", at: "t5", sponsor: "joe", seat: "claude", kind: "turn", body: "a", msg_id: "m5" },
      { seq: 6, room_id: "partner-line", at: "t6", sponsor: "dell", seat: "claude", kind: "turn", body: "b", msg_id: "m6" },
      { seq: 7, room_id: "other-room", at: "t7", sponsor: "joe", seat: "grok", kind: "turn", body: "c", msg_id: "m7" },
    ],
  });
  const out = await TOOLS["read-room"].handler(db, joe, { after_seq: 5 });
  assert.equal(out.ok, true);
  assert.equal(out.room, "partner-line");
  assert.deepEqual(out.turns.map((t) => t.body), ["b"]);
  assert.equal(out.latest_seq, 6);
  assert.deepEqual(db.reads[0], ["partner-line", 5, 50]);
});

test("read-room: defaults — the partner line from the start, quiet room answers honestly", async () => {
  const db = new RoomFake();
  const out = await TOOLS["read-room"].handler(db, joe, {});
  assert.deepEqual(out.turns, []);
  assert.equal(out.latest_seq, 0);
  assert.equal(out.more, false);
});
