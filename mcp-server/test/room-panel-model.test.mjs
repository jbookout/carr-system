// The Model Room panel's derivation, proved without a browser.
//
// EVERYTHING ON THAT PAGE COMES OUT OF THIS ONE FUNCTION, which is exactly why
// it is worth a suite of its own: the desk roster, each desk's pulse state, the
// cursor lag, the bridge's cycle age, the assignments rail, the backend workers
// on the outer ring, the other partner's presence line.  If deriveModel is
// wrong, the panel is confidently wrong — and a dashboard that is confidently
// wrong about which desks are alive is worse than no dashboard.
//
// The fixtures below are the shapes the live wire actually returns, including
// the one that has bitten before: `seq` arrives as a STRING, because Postgres
// bigints come back as strings, and every comparison in the panel is numeric.

import test from "node:test";
import assert from "node:assert/strict";
import {
  authState, cycleState, deriveModel, deskState, errorState, isErrorReceipt,
  isHeartbeat, isWorkerSpawn, lagState, receiptKey, relativeTime, seatColor,
  sessionState, turnPasses,
} from "../../dealroom/js/room.js";

const NOW = Date.parse("2026-08-22T15:00:00Z");
const at = (offsetSeconds) => new Date(NOW - offsetSeconds * 1000).toISOString();

function heartbeat(desks, cursor, ageSeconds = 20) {
  return { seq: "200", at: at(ageSeconds), sponsor: "joe", seat: "hermes", kind: "receipt",
    msg_id: "hb", body: JSON.stringify({ heartbeat: { desks, cursor, cycle_at: at(ageSeconds) } }) };
}

const DESK_ROWS = [
  { name: "joe-desk", seat: "claude", live: true, last_seen: at(30), auth: true },
  { name: "codex-desk", seat: "codex", live: true, last_seen: at(400), auth: true },
  { name: "old-desk", seat: "sol", live: false, last_seen: at(30), auth: true },
  { name: "unwired-desk", seat: null, live: false, last_seen: at(30), auth: null },
];

test("the wire's string sequences never leak into a numeric comparison", () => {
  const model = deriveModel([
    { seq: "9", at: at(60), sponsor: "joe", seat: "human", kind: "turn", body: "nine", msg_id: "a" },
    { seq: "100", at: at(30), sponsor: "joe", seat: "claude", kind: "turn", body: "hundred", msg_id: "b" },
  ], { now: NOW, viewer: "joe" });
  assert.equal(model.latestSeq, 100, "'9' must not sort above '100'");
});

test("the desk roster, its pulse states, and the dormant case all come from the heartbeat", () => {
  const model = deriveModel([heartbeat(DESK_ROWS, 199)], { now: NOW, viewer: "joe" });
  const byName = Object.fromEntries(model.desks.map((d) => [d.name, d]));
  assert.equal(byName["joe-desk"].state, "healthy", "live, signed in, seen 30s ago");
  assert.equal(byName["codex-desk"].state, "attention", "seen between two and ten minutes ago");
  assert.equal(byName["old-desk"].state, "urgent", "the heartbeat says it is not live");
  assert.equal(byName["unwired-desk"].state, "dormant", "no room seat means nothing is asking for you");
  assert.equal(model.onlineDesks, 2);
});

test("the pulse scale is exactly the ruled one, at both boundaries", () => {
  const base = { seat: "claude", live: true, auth: true };
  assert.equal(deskState({ ...base, seenAt: NOW - 119_000 }, NOW), "healthy");
  assert.equal(deskState({ ...base, seenAt: NOW - 121_000 }, NOW), "attention");
  assert.equal(deskState({ ...base, seenAt: NOW - 599_000 }, NOW), "attention");
  assert.equal(deskState({ ...base, seenAt: NOW - 601_000 }, NOW), "urgent");
  assert.equal(deskState({ ...base, seat: null, seenAt: NOW }, NOW), "dormant");
});

test("a signed-out desk is urgent however recently it was seen, and unknown sign-in is not", () => {
  const fresh = { seat: "claude", live: true, seenAt: NOW - 1000 };
  assert.equal(deskState({ ...fresh, auth: false }, NOW), "urgent");
  assert.equal(deskState({ ...fresh, auth: true }, NOW), "healthy");
  // A probe that could not answer must never be read as "signed out" — a Mac
  // with no vendor CLI installed would otherwise show a wall of red.
  assert.equal(deskState({ ...fresh, auth: null }, NOW), "healthy");
});

test("signed-in counts ignore desks whose probe could not answer", () => {
  const model = deriveModel([heartbeat([
    { name: "a", seat: "claude", live: true, last_seen: at(10), auth: true },
    { name: "b", seat: "codex", live: true, last_seen: at(10), auth: false },
    { name: "c", seat: "sol", live: true, last_seen: at(10), auth: null },
  ], 199)], { now: NOW, viewer: "joe" });
  assert.equal(model.signedIn, 1);
  assert.equal(model.authKnown, 2, "the unknown desk is excluded from the denominator, not counted as out");
  assert.equal(authState(model.signedIn, model.authKnown, model.desks.length), "urgent");
  assert.equal(authState(2, 2, 2), "healthy");
  assert.equal(authState(0, 0, 0), "dormant");
});

test("a backend worker rides the outer ring on its lifecycle turns alone", () => {
  const spawn = { seq: "75", at: at(300), sponsor: "joe", seat: "opus", kind: "system", msg_id: "s75",
    body: "WORKER SPAWNED — seat opus, a backend build worker under Joe's council session. "
      + "Mission: build the Model Room observatory. Executor: Opus, isolated worktree, relay attests." };
  assert.equal(isWorkerSpawn(spawn), true);
  assert.equal(isWorkerSpawn({ ...spawn, kind: "turn" }), false, "only a system turn announces a spawn");

  const running = deriveModel([heartbeat(DESK_ROWS, 199), spawn], { now: NOW, viewer: "joe" });
  const node = running.orbiters.find((o) => o.seat === "opus");
  assert.ok(node?.worker, "a worker has no desk, so it can only appear on the outer ring");
  assert.equal(node.state, "healthy", "it breathes from spawn until its completion receipt");
  assert.match(node.worker.mission, /Model Room observatory/);
  assert.match(node.worker.executor, /Opus/);

  const done = deriveModel([heartbeat(DESK_ROWS, 199), spawn,
    { seq: "300", at: at(10), sponsor: "joe", seat: "opus", kind: "receipt", msg_id: "r300",
      body: JSON.stringify({ shipped: { pr: 999 } }) }], { now: NOW, viewer: "joe" });
  assert.equal(done.orbiters.find((o) => o.seat === "opus").state, "dormant",
    "and it sits still once its completion receipt lands");
});

test("only turns from the last day reach the outer ring", () => {
  const stale = { seq: "1", at: at(48 * 3600), sponsor: "joe", seat: "grok", kind: "turn", body: "old", msg_id: "old" };
  const model = deriveModel([heartbeat(DESK_ROWS, 199), stale], { now: NOW, viewer: "joe" });
  assert.equal(model.orbiters.find((o) => o.seat === "grok"), undefined);
});

test("a seat that owns a desk is never duplicated onto the outer ring", () => {
  const model = deriveModel([heartbeat(DESK_ROWS, 199),
    { seq: "201", at: at(10), sponsor: "joe", seat: "claude", kind: "turn", body: "hi", msg_id: "x" }],
  { now: NOW, viewer: "joe" });
  assert.equal(model.orbiters.find((o) => o.seat === "claude"), undefined);
});

test("cycle age, cursor lag and error counts follow the ruled thresholds", () => {
  assert.equal(cycleState(149), "healthy");
  assert.equal(cycleState(151), "attention");
  assert.equal(cycleState(601), "urgent");
  assert.equal(cycleState(null), "urgent", "no heartbeat at all is the loudest state, not the quietest");

  // A single poll ahead of the bridge is the normal shape of a five-second
  // browser reading a wire a launchd job reads every minute.
  assert.equal(lagState(0, 0), "healthy");
  assert.equal(lagState(3, 1), "healthy");
  assert.equal(lagState(3, 2), "attention");
  assert.equal(lagState(null, 5), "dormant");

  assert.equal(errorState(0, null), "healthy");
  assert.equal(errorState(2, null), "attention");
  assert.equal(errorState(3, 2), "urgent", "a rising count is worse than a standing one");
  assert.equal(errorState(2, 2), "attention");
});

test("cursor lag is measured against the bridge's own published cursor", () => {
  const model = deriveModel([heartbeat(DESK_ROWS, 199),
    { seq: "205", at: at(5), sponsor: "joe", seat: "human", kind: "turn", body: "new", msg_id: "n" }],
  { now: NOW, viewer: "joe" });
  assert.equal(model.cursor, 199);
  assert.equal(model.latestSeq, 205);
  assert.equal(model.cursorLag, 6);
});

test("bridge receipts that report a failure are counted, and ordinary ones are not", () => {
  const receipt = (body) => ({ seq: "1", at: at(5), sponsor: "joe", seat: "hermes", kind: "receipt", body: JSON.stringify(body) });
  assert.equal(isErrorReceipt(receipt({ assignment_rejected: { reason: "malformed" } })), true);
  assert.equal(isErrorReceipt(receipt({ control_refused: { reason: "throttled" } })), true);
  assert.equal(isErrorReceipt(receipt({ desk: "codex-desk", timed_out_after_s: 1800 })), true);
  assert.equal(isErrorReceipt(receipt({ desk: "codex-desk", status: "quota_exhausted" })), true);
  assert.equal(isErrorReceipt(receipt({ desk_restarted: { desk: "joe-desk", restarted: false } })), true);
  assert.equal(isErrorReceipt(receipt({ assignment: { ref: "WR-1" }, status: "recorded_no_queue_verb_yet" })), true);
  assert.equal(isErrorReceipt(receipt({ heartbeat: { desks: [], cursor: 1 } })), false);
  assert.equal(isErrorReceipt(receipt({ control_executed: { action: "login" } })), false);
  assert.equal(isErrorReceipt({ kind: "turn", body: '{"control_refused":{}}' }), false,
    "a conversational turn quoting a receipt is not a receipt");
});

test("assignments come only from receipts carrying the grammar's assignment key, newest first", () => {
  const model = deriveModel([
    { seq: "10", at: at(600), sponsor: "joe", seat: "hermes", kind: "receipt", msg_id: "a1",
      body: JSON.stringify({ assignment: { seat: "codex", verb: "assign", ref: "WR-9", title: "first", by: "joe" } }) },
    { seq: "12", at: at(60), sponsor: "joe", seat: "hermes", kind: "receipt", msg_id: "a2",
      body: JSON.stringify({ assignment: { seat: "claude", verb: "claim", ref: "L-508", title: null, by: "joe" } }) },
    { seq: "13", at: at(30), sponsor: "joe", seat: "human", kind: "turn", msg_id: "a3",
      body: "@codex assign WR-9 not an assignment record, just the command" },
  ], { now: NOW, viewer: "joe" });
  assert.deepEqual(model.assignments.map((a) => a.ref), ["L-508", "WR-9"]);
});

test("presence reads the OTHER partner, and it comes from the session's viewer", () => {
  const turns = [
    { seq: "1", at: at(120), sponsor: "dell", seat: "human", kind: "turn", body: "morning", msg_id: "d" },
    { seq: "2", at: at(10), sponsor: "joe", seat: "human", kind: "turn", body: "morning", msg_id: "j" },
  ];
  const joeSees = deriveModel(turns, { now: NOW, viewer: "joe" });
  assert.equal(joeSees.presence.partner, "dell");
  assert.equal(joeSees.presence.state, "healthy");

  const dellSees = deriveModel(turns, { now: NOW, viewer: "dell" });
  assert.equal(dellSees.presence.partner, "joe");

  const quiet = deriveModel([{ seq: "1", at: at(4000), sponsor: "dell", seat: "human", kind: "turn", body: "hi", msg_id: "q" }],
    { now: NOW, viewer: "joe" });
  assert.equal(quiet.presence.state, "dormant", "beyond ten minutes the presence dot sits still");
});

test("filters compose, and heartbeat receipts stay out of the conversation by default", () => {
  const filters = { seats: new Set(), turns: true, system: true, receipts: true, heartbeats: false, text: "" };
  const hb = heartbeat(DESK_ROWS, 199);
  const chat = { seq: "1", at: at(10), sponsor: "joe", seat: "claude", kind: "turn", body: "deploy is green" };
  const assignment = { seq: "2", at: at(10), sponsor: "joe", seat: "hermes", kind: "receipt",
    body: JSON.stringify({ assignment: { ref: "WR-1" } }) };

  assert.equal(isHeartbeat(hb), true);
  assert.equal(receiptKey(assignment.body), "assignment");
  assert.equal(turnPasses(hb, filters), false);
  assert.equal(turnPasses(assignment, filters), true);
  assert.equal(turnPasses(hb, { ...filters, heartbeats: true }), true);

  assert.equal(turnPasses(chat, { ...filters, seats: new Set(["codex"]) }), false);
  assert.equal(turnPasses(chat, { ...filters, seats: new Set(["claude"]) }), true);
  // seat AND text must both admit it
  assert.equal(turnPasses(chat, { ...filters, seats: new Set(["claude"]), text: "green" }), true);
  assert.equal(turnPasses(chat, { ...filters, seats: new Set(["claude"]), text: "rollback" }), false);
  assert.equal(turnPasses(chat, { ...filters, turns: false }), false);
});

test("seat colour is fixed per seat and falls back rather than throwing", () => {
  assert.equal(seatColor("human"), "#F08A2D");
  assert.equal(seatColor("CLAUDE"), "#2DD496");
  assert.equal(seatColor("codex"), seatColor("sol"));
  assert.equal(seatColor("a-seat-nobody-has-invented"), "#7D8BB0");
  assert.equal(seatColor(undefined), "#7D8BB0");
});

test("relative time is short, human, and never negative", () => {
  assert.equal(relativeTime(NOW - 14_000, NOW), "14s");
  assert.equal(relativeTime(NOW - 3 * 60_000, NOW), "3m");
  assert.equal(relativeTime(NOW - 5 * 3600_000, NOW), "5h");
  assert.equal(relativeTime(NOW + 9000, NOW), "0s", "a clock skew must not render as a negative age");
  assert.equal(relativeTime(0, NOW), "never");
});

test("session context gauges are parsed from status receipts, freshest per session", () => {
  const status = (name, pct, claimed, ageSeconds, sponsor = "joe") => ({
    seq: String(300 + ageSeconds), at: at(ageSeconds), sponsor, seat: "hermes", kind: "receipt",
    msg_id: `s${name}${ageSeconds}`,
    body: JSON.stringify({ session_status: { name, context_pct: pct, claimed } }),
  });

  const model = deriveModel([
    status("release-chain", 20, "PR #491 deploy gate", 600),
    status("release-chain", 82, "PR #491 deploy gate", 90),      // newer wins
    status("observatory-build", 55, "Model Room panel", 120, "dell"),
    status("napping-session", 30, "nothing", 50 * 60),           // stale but listed
    status("long-gone", 90, "finished hours ago", 3 * 60 * 60),  // outside the window
  ], { now: NOW, viewer: "joe" });

  assert.deepEqual(model.sessions.map((s) => s.name),
    ["release-chain", "observatory-build", "napping-session"],
    "loudest first, and a session silent for two hours is gone rather than quiet");
  const release = model.sessions[0];
  assert.equal(release.contextPct, 82, "the freshest receipt wins, not the first one seen");
  assert.equal(release.state, "urgent");
  assert.equal(release.claimed, "PR #491 deploy gate");
  assert.equal(release.stale, false);
  assert.equal(model.sessions[1].state, "attention");
  assert.equal(model.sessions[1].partner, "dell", "the bar takes the sponsoring partner's halo");
  assert.equal(model.sessions[2].stale, true, "past forty-five minutes it fades rather than lying");

  assert.equal(sessionState(49), "healthy");
  assert.equal(sessionState(50), "attention");
  assert.equal(sessionState(75), "attention");
  assert.equal(sessionState(76), "urgent");
  assert.equal(sessionState("not a number"), "dormant");
});

test("an empty wire derives an honest empty model rather than throwing", () => {
  const model = deriveModel([], { now: NOW, viewer: "joe" });
  assert.deepEqual(model.desks, []);
  assert.deepEqual(model.orbiters, []);
  assert.deepEqual(model.assignments, []);
  assert.equal(model.cursor, null);
  assert.equal(model.cursorLag, null);
  assert.equal(model.cycleAgeS, null);
  assert.equal(cycleState(model.cycleAgeS), "urgent");
});
