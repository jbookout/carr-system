// application-session.test.mjs — the minting layer for migration 0204/0206.
//
// Every test here drives the real exported function with injected fakes. None
// asserts on source text: this file exists because the substrate below it was
// rejected twice for evidence that looked trustworthy and was not.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  SESSION_CAP_MS, sessionExpiry, accessTokenIdentity, sessionMapKey,
  mintApplicationSession, sessionForAccessToken,
} from "../src/session.js";

const NOW = 1_760_000_000_000;
const JOE = { id: "actor-joe", slug: "joe", human: true, via: "oauth-google",
              authorization_class: "verified_partner" };

function fakeKv(initial = {}) {
  const store = new Map(Object.entries(initial));
  return {
    store,
    puts: [],
    async get(key) { return store.has(key) ? store.get(key) : null; },
    async put(key, value, opts) {
      this.puts.push({ key, value, opts });
      store.set(key, JSON.parse(value));
    },
  };
}

function bearer(token) {
  return { headers: { get: (k) => (k.toLowerCase() === "authorization" ? `Bearer ${token}` : null) } };
}

// ───────────────────────────────────────────────────────── expiry ─────
test("sessionExpiry: the credential's own expiry wins when it is sooner than the cap", () => {
  const oneHour = NOW + 3600_000;
  assert.equal(sessionExpiry(oneHour, NOW), oneHour);
});

test("sessionExpiry: 0204's 30-day cap wins over a longer-lived credential", () => {
  const ninetyDays = NOW + 90 * 24 * 3600_000;
  assert.equal(sessionExpiry(ninetyDays, NOW), NOW + SESSION_CAP_MS,
    "a 90-day refresh lifetime must not become a 90-day session");
});

test("sessionExpiry: an unknown or past expiry becomes the cap, never forever", () => {
  for (const bad of [undefined, null, NaN, "nonsense", NOW - 1]) {
    assert.equal(sessionExpiry(bad, NOW), NOW + SESSION_CAP_MS,
      `"unknown" must not read as "unbounded" (input ${String(bad)})`);
  }
});

// ─────────────────────────────────────────────── token identity ─────
test("accessTokenIdentity: recovers the library's own token id and KV key", async () => {
  const id = await accessTokenIdentity(bearer("joe:grant7:secretpart"));
  assert.equal(id.userId, "joe");
  assert.equal(id.grantId, "grant7");
  assert.match(id.tokenId, /^[0-9a-f]{64}$/, "token id is a sha-256 hex digest");
  assert.equal(id.kvKey, `token:joe:grant7:${id.tokenId}`);
});

test("accessTokenIdentity: the id is a function of the WHOLE token, so two tokens never collide", async () => {
  const a = await accessTokenIdentity(bearer("joe:grant7:aaa"));
  const b = await accessTokenIdentity(bearer("joe:grant7:bbb"));
  assert.notEqual(a.tokenId, b.tokenId);
});

test("accessTokenIdentity: anything not the internal token format yields null", async () => {
  for (const bad of ["nocolons", "two:parts", "a:b:c:d"]) {
    assert.equal(await accessTokenIdentity(bearer(bad)), null, `rejected: ${bad}`);
  }
  assert.equal(await accessTokenIdentity({ headers: { get: () => null } }), null);
  assert.equal(await accessTokenIdentity({ headers: { get: () => "Basic abc" } }), null);
});

// ──────────────────────────────────────────────────────── minting ─────
test("mintApplicationSession: sends no authentication instant — the server clock decides", async () => {
  let seen = null;
  await mintApplicationSession(async (text, params) => {
    seen = { text, params }; return [{ id: "sid-1" }];
  }, { id: "sid-1", actorId: "actor-joe", organizationTenantId: "carr-internal",
       sponsoringHumanSlug: "joe", via: "oauth-google", authIssuer: "accounts.google.com",
       authorizationClass: "verified_partner", verifiedSubject: "joe",
       expiresAt: new Date(NOW + 3600_000).toISOString() });
  assert.match(seen.text, /ops\.mint_application_session/);
  assert.equal(seen.params.length, 9,
    "nine parameters: an authenticated_at parameter would make backdating expressible");
  assert.ok(!seen.params.some(p => String(p).includes("authenticated_at")));
});

test("mintApplicationSession: a mint that returns no id throws rather than reporting success", async () => {
  await assert.rejects(
    () => mintApplicationSession(async () => [], { id: "x" }),
    /returned no id/);
});

// ──────────────────────────────────────────────── the OAuth door ─────
test("sessionForAccessToken: mints once, then reuses for the same token", async () => {
  const kv = fakeKv({ "token:joe:g1:PLACEHOLDER": null });
  let mints = 0;
  const mintFn = async () => { mints += 1; return [{ id: `sid-${mints}` }]; };
  const req = bearer("joe:g1:secret");
  const deps = { kv, mintFn, now: () => NOW, uuid: () => "uuid-1" };

  const first = await sessionForAccessToken(req, {}, JOE, deps);
  const second = await sessionForAccessToken(req, {}, JOE, deps);
  assert.equal(first, "sid-1");
  assert.equal(second, "sid-1", "a second request on the same token must reuse its session");
  assert.equal(mints, 1, "one access token is one session, not one per request");
});

test("sessionForAccessToken: a DIFFERENT access token gets its own session", async () => {
  const kv = fakeKv();
  let mints = 0;
  const mintFn = async () => { mints += 1; return [{ id: `sid-${mints}` }]; };
  const deps = { kv, mintFn, now: () => NOW, uuid: () => "u" };
  const a = await sessionForAccessToken(bearer("joe:g1:aaa"), {}, JOE, deps);
  const b = await sessionForAccessToken(bearer("joe:g1:bbb"), {}, JOE, deps);
  assert.notEqual(a, b, "refreshing is a new authenticated period, so a new session");
  assert.equal(mints, 2);
});

test("sessionForAccessToken: the session never outlives the token, and is capped", async () => {
  const tokenExpiresSec = Math.floor((NOW + 1800_000) / 1000);   // 30 minutes
  const id = await accessTokenIdentity(bearer("joe:g1:secret"));
  const kv = fakeKv({ [id.kvKey]: { expiresAt: tokenExpiresSec } });
  let sentExpiry = null;
  const mintFn = async (_t, params) => { sentExpiry = params[8]; return [{ id: "sid" }]; };
  await sessionForAccessToken(bearer("joe:g1:secret"), {}, JOE,
    { kv, mintFn, now: () => NOW, uuid: () => "u" });
  assert.equal(Date.parse(sentExpiry), tokenExpiresSec * 1000,
    "the access token's own expiry is the session's expiry");
});

test("sessionForAccessToken: nothing caller-supplied reaches the mint", async () => {
  const kv = fakeKv();
  let params = null;
  const mintFn = async (_t, p) => { params = p; return [{ id: "sid" }]; };
  // An actor carrying hostile extras, as a compromised or stale grant might.
  const hostile = { ...JOE, organization_tenant_id: "other-tenant",
                    application_session_id: "forged", authenticated_at: "1999-01-01" };
  await sessionForAccessToken(bearer("joe:g1:s"), {}, hostile,
    { kv, mintFn, now: () => NOW, uuid: () => "u" });
  assert.equal(params.length, 9, "no extra parameter can ride along");
  assert.ok(!params.includes("forged"), "a session id on the actor is never re-sent");
  assert.ok(!params.includes("1999-01-01"), "an authentication instant is never sent");
});

test("sessionForAccessToken: a minting failure downgrades this request, it does not fail it", async () => {
  const kv = fakeKv();
  const mintFn = async () => { throw new Error("issuer credential unreachable"); };
  const got = await sessionForAccessToken(bearer("joe:g1:s"), {}, JOE,
    { kv, mintFn, now: () => NOW, uuid: () => "u" });
  assert.equal(got, null,
    "null means legacy evidence for this request; the substrate must not become a "
    + "single point of failure for every authenticated write");
});

test("sessionForAccessToken: a malformed token mints NOTHING", async () => {
  const kv = fakeKv();
  let mints = 0;
  const mintFn = async () => { mints += 1; return [{ id: "sid" }]; };
  const got = await sessionForAccessToken(bearer("garbage"), {}, JOE,
    { kv, mintFn, now: () => NOW, uuid: () => "u" });
  assert.equal(got, null);
  assert.equal(mints, 0,
    "a malformed token must not become a way to spawn unlimited sessions");
});

test("sessionForAccessToken: without an issuer credential it mints nothing at all", async () => {
  const got = await sessionForAccessToken(bearer("joe:g1:s"), { OAUTH_KV: fakeKv() }, JOE, {});
  assert.equal(got, null, "no mintFn means no session, never a silent fallback");
});

test("sessionMapKey: this module's mapping never collides with the provider's own records", () => {
  const key = sessionMapKey("abc123");
  assert.ok(!key.startsWith("token:"), "must not shadow a provider token record");
  assert.ok(!key.startsWith("grant:"), "must not shadow a provider grant record");
});

// ─────────────────────────────────────── the doors that must NOT mint ─────
// These are the credentials ruled out for Phase 4 qualification: a static
// secret map has no issuance instant, no expiry and no revocation state, so a
// session minted for one would be a fiction dressed as evidence.
//
// The risk is not that these doors break. It is that a later reader sees "these
// produce no qualified evidence" as a defect and fixes it by minting on their
// behalf. That change would pass every other test in this repo, so it is
// asserted here explicitly rather than left to the shape of the code.
import { agentActorForToken, hermesActorForToken } from "../src/identity.js";

test("bearer-token doors build actors carrying NO session, and none may gain one", () => {
  const cases = [
    ["agent", agentActorForToken("Bearer codex-secret", JSON.stringify({ codex: "codex-secret" }), "agent-token")],
    ["local", agentActorForToken("Bearer local-secret", JSON.stringify({ "joe-local": "local-secret" }), "local-token")],
    ["hermes", hermesActorForToken("Bearer hermes-secret", JSON.stringify({ hermes: "hermes-secret" }))],
  ];
  for (const [label, built] of cases) {
    assert.ok(built, `${label}: fixture should authenticate`);
    assert.equal(built.application_session_id, undefined,
      `${label}: a static shared secret has no issuance instant, no expiry and no `
      + `revocation state, so it must never carry an application session`);
  }
});

test("a static-secret actor produces legacy evidence, and 0204 keeps it that way forever", async () => {
  const { auditIdentity } = await import("../src/tools.js");
  const agent = agentActorForToken("Bearer codex-secret", JSON.stringify({ codex: "codex-secret" }), "agent-token");
  assert.equal(auditIdentity(agent).application_session_id, null,
    "null is the permanent legacy marker, not missing data to backfill later");
});
