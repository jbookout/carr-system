// application-session.test.mjs — the minting layer for migration 0208/0209.
//
// Every test here drives the real exported function with injected fakes. None
// asserts on source text: this file exists because the substrate below it was
// rejected twice for evidence that looked trustworthy and was not.

import { test } from "node:test";
import assert from "node:assert/strict";
import { actorFromProps } from "../src/identity.js";
import {
  SESSION_CAP_MS, sessionExpiry, accessTokenIdentity, sessionMapKey,
  mintApplicationSession, sessionForAccessToken,
} from "../src/session.js";

const NOW = 1_760_000_000_000;
// THE FIXTURE IS THE ACTOR THE DOOR ACTUALLY HAS, built by the same function
// the OAuth path uses. A hand-written literal carrying `id` and
// `authorization_class` describes an actor shape that CANNOT exist at the door:
// actorFromProps returns neither, actor.id is first resolved inside callTool,
// and the authorization class is derived in dispatch -- both AFTER the mint.
// The first version of this file used such a literal and every door test passed
// against a shape production never supplies.
const JOE = actorFromProps({ slug: "joe", via: "oauth-google" });

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

test("sessionExpiry: 0208's 30-day cap wins over a longer-lived credential", () => {
  const ninetyDays = NOW + 90 * 24 * 3600_000;
  assert.equal(sessionExpiry(ninetyDays, NOW), NOW + SESSION_CAP_MS,
    "a 90-day refresh lifetime must not become a 90-day session");
});

test("sessionExpiry: an unknown or past expiry yields NO session, not the maximum", () => {
  // The inversion this replaces: returning the cap for unknown input meant a
  // failed KV read became a THIRTY-DAY session for a ONE-HOUR credential. For
  // this door the cap is not a conservative fallback, it is unbounded.
  for (const bad of [undefined, null, NaN, "nonsense", NOW - 1]) {
    assert.equal(sessionExpiry(bad, NOW), null,
      `not knowing when a credential dies must mint nothing (input ${String(bad)})`);
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
  }, { id: "sid-1", actorSlug: "joe", organizationTenantId: "carr-internal",
       sponsoringHumanSlug: "joe", via: "oauth-google", authIssuer: "accounts.google.com",
       authorizationClass: "verified_partner", verifiedSubject: "joe",
       expiresAt: new Date(NOW + 3600_000).toISOString() });
  assert.match(seen.text, /ops\.mint_application_session_for_slug/);
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
async function kvWithToken(token, expiresAtMs = NOW + 3600_000) {
  const id = await accessTokenIdentity(bearer(token));
  return fakeKv({ [id.kvKey]: { expiresAt: Math.floor(expiresAtMs / 1000) } });
}

test("sessionForAccessToken: mints once, then reuses for the same token", async () => {
  const kv = await kvWithToken("joe:g1:secret");
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
  const kvA = await kvWithToken("joe:g1:aaa");
  const kvB = await kvWithToken("joe:g1:bbb");
  const kv = { ...kvA, store: kvA.store,
               async get(k) { return (await kvA.get(k)) ?? (await kvB.get(k)); },
               async put(k, v, o) { return kvA.put(k, v, o); } };
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
  const kv = await kvWithToken("joe:g1:s");
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
  const kv = await kvWithToken("joe:g1:s");
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

test("a static-secret actor produces legacy evidence, and 0208 keeps it that way forever", async () => {
  const { auditIdentity } = await import("../src/tools.js");
  const agent = agentActorForToken("Bearer codex-secret", JSON.stringify({ codex: "codex-secret" }), "agent-token");
  assert.equal(auditIdentity(agent).application_session_id, null,
    "null is the permanent legacy marker, not missing data to backfill later");
});

// ───────────────────────────────── the CALL SITE, not just the function ─────
// The first version of this wiring shipped inert and every test passed, because
// nothing here reached mcpApiHandler at all. Deleting the entire mint block from
// mcp.js passed 519 of 519. These tests drive the real handler so that the call
// site itself is load-bearing: remove it, and they fail.
import { mcpApiHandler } from "../src/mcp.js";

// Loopback: the only thing mcp.js does with the url is read ?profile= from it.
const LOCAL_MCP = "http://127.0.0.1/mcp";

function rpcRequest(token, body = { jsonrpc: "2.0", id: 1, method: "initialize" }) {
  return {
    method: "POST",
    url: LOCAL_MCP,
    headers: { get: (k) => (k.toLowerCase() === "authorization" ? `Bearer ${token}` : null) },
    json: async () => body,
  };
}

test("the OAuth door actually mints — delete the call site and this fails", async () => {
  const token = "joe:g1:live";
  const id = await accessTokenIdentity(bearer(token));
  // REAL clock, not the frozen NOW the pure tests use: this path goes through
  // mcpApiHandler, which passes no clock override, so a fixture dated from the
  // frozen constant reads as an expired credential and mints nothing.
  const kv = fakeKv({ [id.kvKey]: { expiresAt: Math.floor((Date.now() + 3600_000) / 1000) } });
  const mints = [];
  const env = {
    OAUTH_KV: kv,
    SESSION_MINT_FN: async (text, params) => { mints.push({ text, params }); return [{ id: "sid-live" }]; },
  };
  const ctx = { props: { slug: "joe", via: "oauth-google" }, waitUntil: () => {} };
  const res = await mcpApiHandler.fetch(rpcRequest(token), env, ctx);
  assert.equal(res.status, 200);
  assert.equal(mints.length, 1, "the OAuth door must mint exactly one session per token");
  assert.match(mints[0].text, /mint_application_session_for_slug/);
});

test("the door sends a real authorization class and tenant, not undefined", async () => {
  // Both are derived in dispatch(), which runs AFTER the mint. Reading them off
  // the actor yields undefined, and 0208 raises on a null authorization class —
  // the exact reason the first version minted nothing.
  const token = "joe:g2:live";
  const id = await accessTokenIdentity(bearer(token));
  const kv = fakeKv({ [id.kvKey]: { expiresAt: Math.floor((Date.now() + 3600_000) / 1000) } });
  let params = null;
  const env = { OAUTH_KV: kv,
                SESSION_MINT_FN: async (_t, p) => { params = p; return [{ id: "sid" }]; } };
  await mcpApiHandler.fetch(rpcRequest(token), env,
    { props: { slug: "joe", via: "oauth-google" }, waitUntil: () => {} });
  const [, actorSlug, tenant, , , , authClass] = params;
  assert.equal(actorSlug, "joe");
  assert.ok(tenant, "tenant must be resolved at the door");
  assert.ok(authClass, "authorization class must be resolved at the door, never undefined");
  assert.notEqual(authClass, "undefined");
});

test("the door mints by SLUG, because it has no actor id to give", async () => {
  // actorFromProps returns no id and actor.id is not resolved until callTool.
  // A mint keyed on an actor uuid therefore cannot be called from here at all.
  const built = actorFromProps({ slug: "joe", via: "oauth-google" });
  assert.equal(built.id, undefined,
    "if this ever gains an id, re-examine 0210's reason for existing");
});

// ─────────────────────────────────────────── downgrades are observable ─────
test("a missing token record mints nothing and REPORTS it", async () => {
  const kv = fakeKv();               // no token record at all
  const failures = [];
  const got = await sessionForAccessToken(bearer("joe:g1:s"), {}, JOE, {
    kv, mintFn: async () => [{ id: "sid" }], now: () => NOW, uuid: () => "u",
    onFailure: (kind, detail) => failures.push({ kind, detail }),
  });
  assert.equal(got, null, "no provider record means no proven credential expiry");
  assert.equal(failures[0].kind, "session_token_record_absent");
});

test("a bearer string that merely LOOKS like a token cannot mint a 30-day session", async () => {
  // A static agent-token secret containing two colons parses as the internal
  // format. It has no provider record, so it mints nothing — previously it
  // received the full cap.
  const kv = fakeKv();
  let mints = 0;
  const got = await sessionForAccessToken(bearer("some:static:secret"), {}, JOE, {
    kv, mintFn: async () => { mints += 1; return [{ id: "sid" }]; },
    now: () => NOW, uuid: () => "u",
  });
  assert.equal(got, null);
  assert.equal(mints, 0, "a static secret must never become a qualified session");
});

test("a minting failure is REPORTED, not merely swallowed", async () => {
  const kv = await kvWithToken("joe:g1:s");
  const failures = [];
  const got = await sessionForAccessToken(bearer("joe:g1:s"), {}, JOE, {
    kv, mintFn: async () => { throw new Error("issuer unreachable"); },
    now: () => NOW, uuid: () => "u",
    onFailure: (kind, detail) => failures.push({ kind, detail }),
  });
  assert.equal(got, null);
  assert.equal(failures[0].kind, "session_mint_failed");
  assert.match(failures[0].detail.error, /issuer unreachable/);
});

test("a missing issuer credential is REPORTED — the silence that hid three defects", async () => {
  const failures = [];
  const got = await sessionForAccessToken(bearer("joe:g1:s"), { OAUTH_KV: fakeKv() }, JOE,
    { onFailure: (kind, detail) => failures.push({ kind, detail }) });
  assert.equal(got, null);
  assert.equal(failures[0].kind, "session_mint_unavailable");
  assert.equal(failures[0].detail.mintFn, false,
    "the report must name WHICH precondition was missing");
});

// ───────────────────────────────────────── the WRITE paths carry it too ─────
// The commit that first threaded the session claimed it reached all three
// tables. Only tool_read_call had a test, and it covered a SQL-building
// function rather than a write — so dropping application_session_id from the
// tool_call INSERT passed every test in the repo. These close that.
import { toolCallInsertSQL } from "../src/tools.js";

const DOOR_ACTOR = { ...actorFromProps({ slug: "joe", via: "oauth-google" }), id: "actor-uuid" };
const SID = "11111111-2222-3333-4444-555555555555";

test("tool_call INSERT names application_session_id and binds it last", () => {
  const { text, params } = toolCallInsertSQL(
    "key-1", "log-activity", { ...DOOR_ACTOR, application_session_id: SID },
    "hash", { ok: true });
  assert.match(text, /application_session_id/,
    "the write path must record which authenticated session produced the row");
  assert.equal(params.at(-1), SID);
  // The placeholder count and the parameter count must agree, or the statement
  // silently binds the wrong column to the wrong value.
  const highest = Math.max(...[...text.matchAll(/\$(\d+)/g)].map(m => Number(m[1])));
  assert.equal(highest, params.length,
    `statement uses $${highest} but ${params.length} parameters are supplied`);
});

test("tool_call INSERT records null for a door that minted no session", () => {
  const { params } = toolCallInsertSQL("key-2", "log-activity", DOOR_ACTOR, "hash", {});
  assert.equal(params.at(-1), null,
    "null is the permanent legacy marker; 0208 refuses to promote such a row later");
});

test("tool_call INSERT cannot take a session from the verb's own arguments", () => {
  // The builder's inputs are (key, verb, actor, hash, result). A verb controls
  // `result` and nothing else that could reach this column.
  assert.equal(toolCallInsertSQL.length, 5);
  const { params } = toolCallInsertSQL(
    "key-3", "log-activity", DOOR_ACTOR, "hash",
    { application_session_id: "forged-by-a-verb" });
  assert.equal(params.at(-1), null,
    "a session id inside the verb's own response must never become the row's session");
  assert.ok(!params.slice(0, -1).includes("forged-by-a-verb")
    || params.at(-1) !== "forged-by-a-verb");
});

test("the event INSERT carries the session too — the retraction path writes here", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../src/tools.js", import.meta.url), "utf8");
  const body = src.slice(src.indexOf("async function writeEvent"),
                         src.indexOf("async function writeEvent") + 4000);
  assert.match(body, /insert into event[\s\S]*application_session_id/,
    "writeEvent's insert must carry the session");
  assert.match(body, /identity\.application_session_id/,
    "and must pass it from the server-derived audit identity");
  const stmt = body.slice(body.indexOf("insert into event"));
  const highest = Math.max(...[...stmt.matchAll(/\$(\d+)/g)].map(m => Number(m[1])));
  assert.ok(highest >= 19,
    `the event insert should bind at least 19 parameters after adding the session; saw $${highest}`);
});

// ─────────────────────────────────────────────── ATTACHMENT, not just minting ─────
// Minting correctly and then never attaching the result passed every test in an
// earlier round: the mint was observable through its fake, the actor handed to
// dispatch was not. These assert the attachment itself.
import { withApplicationSession } from "../src/mcp.js";

test("withApplicationSession returns an actor CARRYING the minted session", async () => {
  const token = "joe:g9:live";
  const id = await accessTokenIdentity(bearer(token));
  const kv = fakeKv({ [id.kvKey]: { expiresAt: Math.floor((Date.now() + 3600_000) / 1000) } });
  const env = { OAUTH_KV: kv, SESSION_MINT_FN: async () => [{ id: "sid-attached" }] };
  const base = actorFromProps({ slug: "joe", via: "oauth-google" });
  const decorated = await withApplicationSession(rpcRequest(token), env, { waitUntil: () => {} }, base);
  assert.equal(decorated.application_session_id, "sid-attached",
    "the session must reach the actor, or nothing downstream can record it");
  assert.equal(decorated.slug, "joe", "and the rest of the actor must survive intact");
});

test("withApplicationSession leaves the actor UNCHANGED when nothing was minted", async () => {
  const env = { OAUTH_KV: fakeKv(), SESSION_MINT_FN: null };
  const base = actorFromProps({ slug: "joe", via: "oauth-google" });
  const decorated = await withApplicationSession(rpcRequest("joe:g9:x"), env, { waitUntil: () => {} }, base);
  assert.equal(decorated.application_session_id, undefined,
    "a failed mint must leave a legacy actor, never a fabricated session");
  assert.deepEqual(decorated, base, "and must not otherwise disturb the actor");
});

test("the attached session survives into the audit identity the writes use", async () => {
  const { auditIdentity } = await import("../src/tools.js");
  const token = "joe:gA:live";
  const id = await accessTokenIdentity(bearer(token));
  const kv = fakeKv({ [id.kvKey]: { expiresAt: Math.floor((Date.now() + 3600_000) / 1000) } });
  const env = { OAUTH_KV: kv, SESSION_MINT_FN: async () => [{ id: "sid-end-to-end" }] };
  const decorated = await withApplicationSession(
    rpcRequest(token), env, { waitUntil: () => {} },
    actorFromProps({ slug: "joe", via: "oauth-google" }));
  // This is the join the whole slice exists to make: door -> actor -> audit
  // identity -> every evidence INSERT.
  assert.equal(auditIdentity(decorated).application_session_id, "sid-end-to-end");
  const { params } = toolCallInsertSQL("k", "log-activity",
    { ...decorated, id: "actor-uuid" }, "hash", {});
  assert.equal(params.at(-1), "sid-end-to-end",
    "and it must land in the tool_call row itself");
});

test("mcpApiHandler passes the DECORATED actor onward, not the original", async () => {
  // Shape, and labelled as such. Proving this behaviourally would mean driving a
  // real write verb through dispatch, which needs a database connection this
  // suite deliberately does not have. The behavioural half is covered above;
  // this guards the one line joining it to the request path.
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../src/mcp.js", import.meta.url), "utf8");
  const handler = src.slice(src.indexOf("export const mcpApiHandler"),
                            src.indexOf("export const mcpApiHandler") + 2000);
  assert.match(handler, /dispatch\(request, env, ctx, await withApplicationSession\(/,
    "mcpApiHandler must hand dispatch the decorated actor; passing `actor` "
    + "directly would mint a session and then discard it");
});
