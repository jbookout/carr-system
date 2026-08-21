// replay-identity.test.mjs — an idempotency key is not an identity.
//
// THE DEFECT. The replay lookup selected on idempotency_key alone and compared
// only request_hash. A second caller replaying with identical arguments got the
// FIRST caller's full response, and because the early return happens before any
// insert, no audit row was written for the second caller at all — the call left
// no trace it had happened. Changed material refused; changed identity did not.
// Idempotency keys are client-chosen strings, so this was reachable by anyone
// who could reuse or guess one.

import { test } from "node:test";
import assert from "node:assert/strict";
import { replayDecision } from "../src/tools.js";

const SID_A = "aaaaaaaa-1111-2222-3333-444444444444";
const SID_B = "bbbbbbbb-1111-2222-3333-444444444444";

const JOE = { id: "actor-joe", slug: "joe", human: true, via: "oauth-google",
              organization_tenant_id: "carr-internal" };
const DELL = { id: "actor-dell", slug: "dell", human: true, via: "oauth-google",
               organization_tenant_id: "carr-internal" };

function rowFor(actor, sid = null, hash = "hash-1") {
  return { request_hash: hash, response: { ok: true }, actor_id: actor.id,
           organization_tenant_id: "carr-internal", application_session_id: sid };
}

test("identical caller, identical material: converges (the half that already worked)", () => {
  const v = replayDecision(rowFor(JOE, SID_A), "hash-1",
                           { ...JOE, application_session_id: SID_A });
  assert.equal(v.ok, true);
  assert.equal(v.error, undefined);
});

test("changed material still refuses, and still as key_reuse", () => {
  const v = replayDecision(rowFor(JOE, SID_A), "DIFFERENT",
                           { ...JOE, application_session_id: SID_A });
  assert.equal(v.error, "key_reuse");
});

test("A DIFFERENT SESSION REFUSES — the defect this closes", () => {
  const v = replayDecision(rowFor(JOE, SID_A), "hash-1",
                           { ...JOE, application_session_id: SID_B });
  assert.equal(v.error, "key_bound_to_another_session",
    "the same human in a NEW authenticated session must not inherit the old "
    + "session's response, and must not do so without leaving an audit row");
});

test("a different actor refuses, by its own name", () => {
  const v = replayDecision(rowFor(JOE, SID_A), "hash-1",
                           { ...DELL, application_session_id: SID_A });
  assert.equal(v.error, "key_bound_to_another_actor");
});

test("a different tenant refuses, by its own name", () => {
  const row = { ...rowFor(JOE, SID_A), organization_tenant_id: "other-tenant" };
  const v = replayDecision(row, "hash-1", { ...JOE, application_session_id: SID_A });
  assert.equal(v.error, "key_bound_to_another_tenant");
});

test("each mismatch raises a DISTINCT error — 'it refused' is not 'the right guard refused'", () => {
  const errors = new Set([
    replayDecision(rowFor(JOE, SID_A), "X", { ...JOE, application_session_id: SID_A }).error,
    replayDecision(rowFor(JOE, SID_A), "hash-1", { ...DELL, application_session_id: SID_A }).error,
    replayDecision({ ...rowFor(JOE, SID_A), organization_tenant_id: "t2" }, "hash-1",
                   { ...JOE, application_session_id: SID_A }).error,
    replayDecision(rowFor(JOE, SID_A), "hash-1", { ...JOE, application_session_id: SID_B }).error,
  ]);
  assert.equal(errors.size, 4, `four mismatches must give four names; got ${[...errors]}`);
});

test("NULL session is a value, not a wildcard: legacy matches only legacy", () => {
  // legacy row, legacy caller -> converges, so pre-deploy retries keep working
  assert.equal(replayDecision(rowFor(JOE, null), "hash-1", JOE).ok, true);
  // legacy row, qualified caller -> refuses rather than handing over a response
  // no session vouches for
  assert.equal(
    replayDecision(rowFor(JOE, null), "hash-1", { ...JOE, application_session_id: SID_A }).error,
    "key_bound_to_another_session");
  // qualified row, legacy caller -> also refuses
  assert.equal(replayDecision(rowFor(JOE, SID_A), "hash-1", JOE).error,
               "key_bound_to_another_session");
});

test("material is checked FIRST, so a changed payload is never reported as an identity problem", () => {
  const v = replayDecision(rowFor(DELL, SID_B), "DIFFERENT",
                           { ...JOE, application_session_id: SID_A });
  assert.equal(v.error, "key_reuse",
    "when everything differs, the caller is told the material changed — the "
    + "cheapest thing to fix and the least revealing about another actor's row");
});
