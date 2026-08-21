// read-evidence.test.mjs — durable read evidence for reads that can be cited.
//
// THE TRADE THIS ENCODES. Read audit used to be scheduled through ctx.waitUntil
// with its failure swallowed, so recording could never slow or fail a read. That
// was deliberate and it is still right for most reads. It cannot be right for a
// read whose evidence will be CITED: a source claim resting on a best-effort
// write made after the response is already on the wire rests on a record that
// may simply never exist, and nothing would say so.
//
// So the rule is split by whether the read can ever qualify. These tests drive
// the real recorder rather than asserting on source text.

import { test } from "node:test";
import assert from "node:assert/strict";
import { recordReadCall, recordReadCallDurable } from "../src/mcp.js";
import { ToolError } from "../src/tools.js";

const SID = "aaaaaaaa-1111-2222-3333-444444444444";
const QUALIFIED = { slug: "joe", human: true, via: "oauth-google",
                    application_session_id: SID };
const LEGACY = { slug: "codex", human: false, via: "agent-token" };

test("durable recorder: a successful write returns quietly and records the session", async () => {
  let seen = null;
  await recordReadCallDurable(async (text, params) => { seen = { text, params }; return []; },
                              QUALIFIED, "standing-context", true, null);
  assert.match(seen.text, /insert into tool_read_call/);
  assert.equal(seen.params.at(-1), SID,
    "a qualifying read must record WHICH session read it");
});

test("durable recorder: a FAILED write throws, so the read cannot report success", async () => {
  await assert.rejects(
    () => recordReadCallDurable(async () => { throw new Error("writer unreachable"); },
                               QUALIFIED, "standing-context", true, null),
    (e) => {
      assert.ok(e instanceof ToolError, "must be a named ToolError, not a raw driver error");
      assert.equal(e.payload.error, "read_evidence_not_recorded");
      assert.match(e.payload.detail, /writer unreachable/);
      return true;
    },
    "returning a result while its evidence failed to write is the exact thing "
    + "Phase 4 forbids: a caller believing a provenance record exists when it does not");
});

test("best-effort recorder still NEVER throws — the legacy path is unchanged", async () => {
  // This is the property the original design bought, and it is deliberately
  // kept for reads that could never be cited.
  await recordReadCall(async () => { throw new Error("writer unreachable"); },
                       LEGACY, "standing-context", true, null);
  // reaching here without throwing is the assertion
  assert.ok(true);
});

test("the two recorders build the SAME statement — the split is about durability, not content", async () => {
  let strict = null, loose = null;
  await recordReadCallDurable(async (t, p) => { strict = { t, p }; return []; },
                              QUALIFIED, "catch-me-up", true, null);
  await recordReadCall(async (t, p) => { loose = { t, p }; return []; },
                       QUALIFIED, "catch-me-up", true, null);
  assert.equal(strict.t, loose.t);
  assert.deepEqual(strict.p, loose.p,
    "a qualifying read and a legacy read must record the same shape of evidence; "
    + "only the guarantee about it differs");
});

test("a failed read still records, carrying ok:false and the classified error kind", async () => {
  let seen = null;
  await recordReadCallDurable(async (_t, p) => { seen = p; return []; },
                              QUALIFIED, "get-deal-room", false, "not_a_deal");
  assert.equal(seen[2], false);
  assert.equal(seen[3], "not_a_deal");
  assert.equal(seen.at(-1), SID, "and still names the session that attempted it");
});

test("the durable recorder carries no response body and no argument value", async () => {
  // Same structural guarantee readCallInsertSQL already has: its only inputs are
  // (actor, verb, ok, errorKind), so nothing a caller sent can reach the row.
  assert.equal(recordReadCallDurable.length, 5); // (insertFn, actor, verb, ok, errorKind)
  let seen = null;
  await recordReadCallDurable(async (_t, p) => { seen = p; return []; },
                              QUALIFIED, "catch-me-up", true, null);
  assert.ok(!seen.some(v => typeof v === "object" && v !== null),
    "no structured value can ride into the evidence row");
});
