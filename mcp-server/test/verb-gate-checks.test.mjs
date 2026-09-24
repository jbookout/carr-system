// verb-gate-checks.test.mjs — unit tests for the ported deterministic logic
// (bypass audit C33/C34, 2026-09-24) plus door-level tests proving the check
// fires the same way whether add-loop is called directly or through the
// call-verb passthrough — the exact two doors the redesign was asked to make
// indistinguishable.
//
// These run purely in memory: mcp.js's callTool() throws
// capability_no_decider / internal_decision_parked BEFORE any DB connection
// opens (see mcp.js's comment beside the check), so no Postgres, no fake
// client, no env at all is needed for the deny path. That is also what makes
// this a real "every door" test rather than a partial one: it exercises the
// SAME code path a live server sees for every caller — direct MCP,
// tools/call-verb.py, mcp-server/local-verb.mjs, ./run.sh call, and
// mcp__*__call-verb — because all of them terminate in this callTool().

import test from "node:test";
import assert from "node:assert/strict";
import { callTool } from "../src/mcp.js";
import {
  classifyLoopText, needsDecider, parksADecision, loopRowText,
} from "../src/verb-gate-checks.js";

const ACTOR = { slug: "joe", human: true, via: "oauth-google" };

async function addLoop(args, profile = "full") {
  try {
    await callTool({}, ACTOR, "add-loop", args, profile);
    return null;
  } catch (e) {
    return e && e.payload ? e.payload : { error: e && e.message };
  }
}

async function addLoopViaCallVerb(args, profile = "full") {
  try {
    await callTool({}, ACTOR, "call-verb", { verb: "add-loop", args }, profile);
    return null;
  } catch (e) {
    return e && e.payload ? e.payload : { error: e && e.message };
  }
}

// ── unit tests: classifyLoopText (ported from escalation-gate.py's classify(),
// minus the HUMAN_WANTS_CHOICE transcript exemption) ────────────────────────

test("classifyLoopText: empty blob allows", () => {
  assert.deepEqual(classifyLoopText(""), { allow: true, why: "empty" });
  assert.deepEqual(classifyLoopText("   "), { allow: true, why: "empty" });
});

test("classifyLoopText: an internal-subject row denies", () => {
  const r = classifyLoopText("should we rename the loop_domain table's slug column?");
  assert.equal(r.allow, false);
  assert.equal(r.why, "internal_decision");
});

test("classifyLoopText: internal plural forms are caught too (the \\b + s? fix)", () => {
  const r = classifyLoopText("should loops sort by created date or by severity?");
  assert.equal(r.allow, false);
  assert.equal(r.why, "internal_decision");
});

test("classifyLoopText: a boundary-change row is exempt even if it names a gate", () => {
  const r = classifyLoopText("should we loosen the escalation gate's rule?");
  assert.equal(r.allow, true);
  assert.equal(r.why, "boundary_change_is_constitutional");
});

test("classifyLoopText: fact-capture (only Joe knows) is exempt", () => {
  const r = classifyLoopText("how did it go at the site visit?");
  assert.equal(r.allow, true);
  assert.equal(r.why, "fact_capture_only_joe_knows");
});

test("classifyLoopText: protected class (money) is exempt, figure or word", () => {
  assert.equal(classifyLoopText("should we let it renew at $240?").allow, true);
  assert.equal(classifyLoopText("should we send this to the client?").allow, true);
});

test("classifyLoopText: unclassified text is allowed, not denied by default", () => {
  const r = classifyLoopText("the weather today is nice");
  assert.equal(r.allow, true);
  assert.equal(r.why, "unclassified_allowed");
});

// ── unit tests: needsDecider (ported from blocker-decider-gate.py) ─────────

test("needsDecider: capability blocker naming Joe is fine", () => {
  assert.equal(needsDecider({ blocker: "capability", blocker_detail: "needs a key only Joe holds" }), false);
});

test("needsDecider: capability blocker naming Dell is fine", () => {
  assert.equal(needsDecider({ blocker: "capability", blocker_detail: "only Dell can grant this" }), false);
});

test("needsDecider: capability blocker stating impossibility is fine", () => {
  assert.equal(needsDecider({ blocker: "capability", blocker_detail: "no such API on this plan" }), false);
});

test("needsDecider: capability blocker with neither is refused", () => {
  assert.equal(needsDecider({ blocker: "capability", blocker_detail: "cant do it somehow" }), true);
});

test("needsDecider: a non-capability blocker is untouched", () => {
  assert.equal(needsDecider({ blocker: "ruling", blocker_detail: "cant do it somehow" }), false);
});

// ── unit tests: parksADecision ──────────────────────────────────────────────

test("parksADecision: marker=decision or blocker=ruling only", () => {
  assert.equal(parksADecision({ marker: "decision" }), true);
  assert.equal(parksADecision({ blocker: "ruling" }), true);
  assert.equal(parksADecision({ marker: "bell" }), false);
  assert.equal(parksADecision({ blocker: "capability" }), false);
});

// ── unit test: loopRowText flattens the whole call, same as loop_text() ────

test("loopRowText: subject cannot hide in blocker_detail while title stays neutral", () => {
  const text = loopRowText({ title: "housekeeping", blocker_detail: "should we rename the schema?" });
  assert.match(text, /rename the schema/);
});

// ── DOOR TESTS: direct call vs. call-verb passthrough get the SAME verdict ─

test("DOOR 1 (direct mcp__*__add-loop): capability blocker with no decider is refused", async () => {
  const out = await addLoop({
    idempotency_key: "11111111-1111-1111-1111-111111111111",
    kind: "open_loop", owner: "Joe", blocker: "capability",
    blocker_detail: "cant do it somehow",
  });
  assert.equal(out?.error, "capability_no_decider");
});

test("DOOR 2 (call-verb passthrough): the SAME row gets the SAME refusal", async () => {
  const out = await addLoopViaCallVerb({
    idempotency_key: "22222222-2222-2222-2222-222222222222",
    kind: "open_loop", owner: "Joe", blocker: "capability",
    blocker_detail: "cant do it somehow",
  });
  assert.equal(out?.error, "capability_no_decider");
});

test("DOOR 1: marker=decision parking an internal question is refused", async () => {
  const out = await addLoop({
    idempotency_key: "33333333-3333-3333-3333-333333333333",
    kind: "open_loop", owner: "Joe", marker: "decision",
    body: "should the record layer rename this table?",
    blocker: "human_only", blocker_detail: "needs Joe to rule",
  });
  assert.equal(out?.error, "internal_decision_parked");
  assert.equal(out?.why, "internal_decision");
});

test("DOOR 2: the SAME parked-decision row gets the SAME refusal through call-verb", async () => {
  const out = await addLoopViaCallVerb({
    idempotency_key: "44444444-4444-4444-4444-444444444444",
    kind: "open_loop", owner: "Joe", marker: "decision",
    body: "should the record layer rename this table?",
    blocker: "human_only", blocker_detail: "needs Joe to rule",
  });
  assert.equal(out?.error, "internal_decision_parked");
});

test("a capability blocker WITH a named decider reaches past the check (not capability_no_decider)", async () => {
  const out = await addLoop({
    idempotency_key: "55555555-5555-5555-5555-555555555555",
    kind: "open_loop", owner: "Joe", blocker: "capability",
    blocker_detail: "needs the NEON_API_KEY only Joe holds — Joe grants it",
  });
  // No live DB in this test, so the call still fails -- but on a LATER error
  // (a DB connection attempt), never on capability_no_decider. That is what
  // proves the check itself passed.
  assert.notEqual(out?.error, "capability_no_decider");
});

test("a parked decision whose text is fact-capture (only Joe knows) reaches past the check", async () => {
  const out = await addLoop({
    idempotency_key: "66666666-6666-6666-6666-666666666666",
    kind: "open_loop", owner: "Joe", marker: "decision",
    body: "what did the vendor say on the call yesterday?",
    blocker: "human_only", blocker_detail: "needs Joe",
  });
  assert.notEqual(out?.error, "internal_decision_parked");
});

test("an ordinary add-loop with no parked decision and no capability blocker is untouched by these checks", async () => {
  const out = await addLoop({
    idempotency_key: "77777777-7777-7777-7777-777777777777",
    kind: "open_loop", owner: "Joe", blocker: "human_only",
    blocker_detail: "needs Joe's signature on the lease",
  });
  assert.notEqual(out?.error, "capability_no_decider");
  assert.notEqual(out?.error, "internal_decision_parked");
});
