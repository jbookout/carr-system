import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createDealroomHandler, isDealroomRequest } from "../src/dealroom-web.js";

const SHELL_ROOT = fileURLToPath(new URL("../../dealroom/public-shell/", import.meta.url));
const DEALROOM_ROOT = fileURLToPath(new URL("../../dealroom/", import.meta.url));
const WRANGLER_PATH = fileURLToPath(new URL("../wrangler.toml", import.meta.url));
const INDEX_PATH = fileURLToPath(new URL("../src/index.js", import.meta.url));
const PRODUCTION_HOST = "dealroom.doctorcre.com";
const STAGING_HOST = "carr-mcp-staging.joe-bookout-carr-us.workers.dev";

class MemoryKv {
  constructor() { this.values = new Map(); }
  async put(key, value) { this.values.set(key, value); }
  async get(key, options) {
    const value = this.values.get(key);
    if (value === undefined) return null;
    return options?.type === "json" ? JSON.parse(value) : value;
  }
  async delete(key) { this.values.delete(key); }
}

class ShellAssets {
  async fetch(request) {
    const pathname = new URL(request.url).pathname;
    try {
      if (pathname.startsWith("/public-shell/"))
        return new Response(await readFile(SHELL_ROOT + pathname.slice("/public-shell/".length)));
      return new Response(await readFile(DEALROOM_ROOT + pathname.slice(1)));
    } catch {
      return new Response("missing", { status: 404 });
    }
  }
}

function env(host = PRODUCTION_HOST) {
  return {
    DEALROOM_HOST: host,
    GOOGLE_CLIENT_ID: "google-client.test",
    GOOGLE_CLIENT_SECRET: "not-a-real-secret",
    OAUTH_KV: new MemoryKv(),
    ASSETS: new ShellAssets(),
  };
}

function setCookies(response) {
  return typeof response.headers.getSetCookie === "function"
    ? response.headers.getSetCookie()
    : [response.headers.get("set-cookie")].filter(Boolean);
}

function namedCookie(response, name) {
  const header = setCookies(response).find((value) => value.startsWith(`${name}=`));
  assert.ok(header, `missing ${name} cookie`);
  return header.split(";", 1)[0];
}

async function login(handler, environment, email, { host = environment.DEALROOM_HOST, returnTo = "/" } = {}) {
  const start = await handler.fetch(new Request(
    `https://${host}/auth/login?return_to=${encodeURIComponent(returnTo)}`,
  ), environment, {});
  assert.equal(start.status, 302);
  const google = new URL(start.headers.get("location"));
  assert.equal(google.hostname, "accounts.google.com");
  assert.equal(google.searchParams.get("redirect_uri"), `https://${host}/auth/callback`);
  const state = google.searchParams.get("state");
  const pendingCookie = namedCookie(start, "__Host-dealroom_oauth");
  const callback = await handler.fetch(new Request(
    `https://${host}/auth/callback?state=${state}&code=stub-code`,
    { headers: { cookie: pendingCookie, "x-test-email": email } },
  ), environment, {});
  return callback;
}

function identityOverrides(email, clock) {
  return {
    exchangeGoogleCodeFn: async () => ({ id_token: "stub-id-token" }),
    verifyGoogleIdTokenFn: async () => ({ email, email_verified: true, sub: `sub:${email}` }),
    now: () => clock?.now ?? 1_800_000_000_000,
    mcpHandler: async (_request, _env, _ctx, actor) =>
      new Response(JSON.stringify({ surface: "mcp", actor }), { headers: { "content-type": "application/json" } }),
    pipelineHandler: async (_request, _env, _ctx, actor) =>
      new Response(JSON.stringify({ surface: "pipeline", actor }), { headers: { "content-type": "application/json" } }),
  };
}

function identityHandler(email, clock) {
  return createDealroomHandler(identityOverrides(email, clock));
}

test("Deal Room host is explicit per environment and request matching fails closed", async () => {
  const wrangler = await readFile(WRANGLER_PATH, "utf8");
  assert.match(wrangler, /\[vars\]\nCARR_ENV = "production"\nDEALROOM_HOST = "dealroom\.doctorcre\.com"/);
  assert.match(wrangler,
    /\[env\.staging\.vars\]\nCARR_ENV = "staging"\nDEALROOM_HOST = "carr-mcp-staging\.joe-bookout-carr-us\.workers\.dev"/);
  assert.match(wrangler, /WORKSPACE_COMMAND_CENTER_READ_ENABLED = "false"/);
  assert.match(wrangler, /\[env\.staging\.vars\][\s\S]*WORKSPACE_COMMAND_CENTER_READ_ENABLED = "true"/);

  assert.equal(isDealroomRequest(new Request(`https://${PRODUCTION_HOST}/`), { DEALROOM_HOST: PRODUCTION_HOST }), true);
  assert.equal(isDealroomRequest(new Request(`https://${STAGING_HOST}/`), { DEALROOM_HOST: STAGING_HOST }), true);
  assert.equal(isDealroomRequest(new Request(`https://${STAGING_HOST}/`), { DEALROOM_HOST: PRODUCTION_HOST }), false);
  assert.equal(isDealroomRequest(new Request(`https://${PRODUCTION_HOST}/`), {}), false);
  assert.equal(isDealroomRequest(new Request(`https://${PRODUCTION_HOST}/`), { DEALROOM_HOST: "https://bad.example" }), false);

  const handler = identityHandler("joe.bookout.carr.us@gmail.com");
  const mismatch = await handler.fetch(new Request(`https://${STAGING_HOST}/auth/login`), env(PRODUCTION_HOST), {});
  assert.equal(mismatch.status, 404);
  const missing = await handler.fetch(new Request(`https://${PRODUCTION_HOST}/auth/login`), {
    ...env(), DEALROOM_HOST: undefined,
  }, {});
  assert.equal(missing.status, 404);
});

test("shared staging origin routes only exact Deal Room surfaces", () => {
  const environment = { DEALROOM_HOST: STAGING_HOST };
  const request = (path, headers = {}) => new Request(`https://${STAGING_HOST}${path}`, { headers });

  for (const path of ["/", "/index.html", "/system-work.html", "/auth/login", "/auth/callback",
    "/deals", "/api/system-work/session", "/api/v1/workspace/command-center/deal-attention",
    "/css/app.css", "/js/app.js", "/data/board-seed.json",
    "/manifest.webmanifest", "/sw.js", "/offline.html", "/icons/dealroom.svg"]) {
    assert.equal(isDealroomRequest(request(path), environment), true, path);
  }
  for (const path of ["/release", "/health", "/authorize", "/token", "/register", "/callback",
    "/.well-known/oauth-authorization-server", "/unknown"]) {
    assert.equal(isDealroomRequest(request(path), environment), false, path);
  }

  for (const path of ["/mcp", "/pipeline/changes"]) {
    assert.equal(isDealroomRequest(request(path), environment), false, `${path} without browser session`);
    assert.equal(isDealroomRequest(request(path, { authorization: "Bearer provider-token" }), environment), false,
      `${path} OAuth bearer`);
    assert.equal(isDealroomRequest(request(path, { cookie: "__Host-dealroom_session=browser-session" }), environment), true,
      `${path} browser cookie`);
    assert.equal(isDealroomRequest(request(path, {
      cookie: "__Host-dealroom_session=browser-session", authorization: "Bearer provider-token",
    }), environment), false, `${path} bearer wins over browser cookie`);
  }
});

test("typed Workspace deal-attention read is feature-gated, actor-bound, and deep-links to exact Deal Room rows", async () => {
  const environment = env();
  const calls = [];
  const events = [];
  const handler = createDealroomHandler({
    ...identityOverrides("joe.bookout.carr.us@gmail.com"),
    recordWorkspaceReadFn: (event) => events.push(event),
    dealAttentionReader: async (_env, actor) => {
      calls.push(actor.slug);
      return {
        schema_version: "workspace-command-center-deal-attention/v1",
        state: "attention",
        actor: { slug: actor.slug },
        source: { kind: "canonical_view", ref: "v_deal_room_board" },
        observed_at: "2026-08-22T02:50:00.000Z",
        freshness: { status: "unknown", basis: "read_time_only" },
        summary: { owned_active: 2, owned_flagged: 1 },
        destination: "/deals?workspace=team&filter=flagged&owner=me",
      };
    },
  });
  const path = "/api/v1/workspace/command-center/deal-attention";

  let response = await handler.fetch(new Request(`https://${PRODUCTION_HOST}${path}`), environment, {});
  assert.equal(response.status, 401);
  assert.deepEqual(await response.json(), { error: "unauthorized", state: "sign_in_required" });
  const anonymousDeals = await handler.fetch(new Request(
    `https://${PRODUCTION_HOST}/deals?workspace=team&filter=flagged&owner=me`, { headers: { accept: "text/html" } },
  ), environment, {});
  assert.equal(anonymousDeals.status, 302);
  assert.equal(new URL(anonymousDeals.headers.get("location")).searchParams.get("return_to"),
    "/deals?workspace=team&filter=flagged&owner=me");

  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com");
  const session = namedCookie(callback, "__Host-dealroom_session");
  response = await handler.fetch(new Request(`https://${PRODUCTION_HOST}${path}`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 404);
  assert.deepEqual(calls, []);
  response = await handler.fetch(new Request(`https://${PRODUCTION_HOST}/`, {
    headers: { cookie: session, accept: "text/html" },
  }), environment, {});
  assert.match(await response.text(), /The Deal Room/);

  environment.WORKSPACE_COMMAND_CENTER_READ_ENABLED = "true";
  response = await handler.fetch(new Request(`https://${PRODUCTION_HOST}/`, {
    headers: { cookie: session, accept: "text/html" },
  }), environment, {});
  assert.match(await response.text(), /CARR Workspace/);
  response = await handler.fetch(new Request(`https://${PRODUCTION_HOST}${path}`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual((await response.json()).summary, { owned_active: 2, owned_flagged: 1 });
  assert.deepEqual(calls, ["joe"]);
  assert.deepEqual(events, [{
    ts: "2027-01-15T08:00:00.000Z",
    event: "workspace_command_center_read",
    operation_id: "deal-attention",
    organization_tenant_id: "carr-internal",
    principal_class: "partner",
    outcome: "success",
    freshness_status: "unknown",
    duration_ms: 0,
    correlation_id: null,
  }]);
  assert.doesNotMatch(JSON.stringify(events), /owned_flagged|"outcome":"attention"|"outcome":"empty"|joe|dell/);

  response = await handler.fetch(new Request(`https://${PRODUCTION_HOST}${path}?owner=dell`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 400);
  assert.equal((await response.json()).error, "invalid_request");
  assert.deepEqual(calls, ["joe"]);

  response = await handler.fetch(new Request(`https://${PRODUCTION_HOST}${path}`, { method: "POST", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("allow"), "GET, HEAD, OPTIONS");

  response = await handler.fetch(new Request(`https://${PRODUCTION_HOST}${path}`, { method: "HEAD", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "");
  assert.deepEqual(calls, ["joe", "joe"]);
  assert.equal(events.length, 2);

  response = await handler.fetch(new Request(`https://${PRODUCTION_HOST}${path}`, { method: "OPTIONS", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 204);
  assert.equal(response.headers.get("allow"), "GET, HEAD, OPTIONS");

  response = await handler.fetch(new Request(`https://${PRODUCTION_HOST}/deals?workspace=team&filter=flagged&owner=me`, {
    headers: { cookie: session, accept: "text/html" },
  }), environment, {});
  assert.equal(response.status, 200);
  assert.match(await response.text(), /The Deal Room/);
});

test("Workspace read failures are unavailable, never an authored zero", async () => {
  const environment = { ...env(), WORKSPACE_COMMAND_CENTER_READ_ENABLED: "true" };
  environment.CORRELATION_ID = "9f3b2c1a-4d5e-4f60-8a1b-0123456789ab";
  const events = [];
  const handler = createDealroomHandler({
    ...identityOverrides("dell.mccraney.carr.us@gmail.com"),
    recordWorkspaceReadFn: (event) => events.push(event),
    dealAttentionReader: async () => { throw new Error("reader down"); },
  });
  const callback = await login(handler, environment, "dell.mccraney.carr.us@gmail.com");
  const session = namedCookie(callback, "__Host-dealroom_session");
  const response = await handler.fetch(new Request(
    `https://${PRODUCTION_HOST}/api/v1/workspace/command-center/deal-attention`, { headers: { cookie: session } },
  ), environment, {});
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "canonical_read_unavailable" });
  assert.equal(events.length, 1);
  assert.equal(events[0].principal_class, "partner");
  assert.equal(events[0].outcome, "canonical_read_unavailable");
  assert.equal(events[0].correlation_id, environment.CORRELATION_ID);
  assert.doesNotMatch(JSON.stringify(events), /reader down|joe|dell/);
});

test("machine-token MCP dispatch precedes browser routing on the shared host", async () => {
  const index = await readFile(INDEX_PATH, "utf8");
  const machineDoors = index.indexOf('if (url.pathname === "/mcp")');
  const browserDoor = index.indexOf("if (isDealroomRequest(request, env))");
  assert.ok(machineDoors >= 0 && browserDoor > machineDoors,
    "probe/review/Hermes/agent/local MCP doors must run before the Deal Room browser door");
});

test("staging host controls OAuth callback, safe return, reauth, signout, and CSRF origin", async () => {
  const environment = env(STAGING_HOST);
  environment.DEALROOM_PROGRAM6_ACTIONS_ENABLED = "true";
  const handler = identityHandler("joe.bookout.carr.us@gmail.com");

  const unauthenticatedReauth = await handler.fetch(new Request(
    `https://${STAGING_HOST}/auth/reauth?return_to=%2Fsystem-work`,
  ), environment, {});
  assert.equal(unauthenticatedReauth.status, 302);
  assert.match(unauthenticatedReauth.headers.get("location"),
    new RegExp(`^https://${STAGING_HOST}/auth/login\\?`));

  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com", {
    host: STAGING_HOST,
    returnTo: `https://${PRODUCTION_HOST}/not-on-staging`,
  });
  assert.equal(callback.status, 302);
  assert.equal(callback.headers.get("location"), `https://${STAGING_HOST}/`);
  const session = namedCookie(callback, "__Host-dealroom_session");

  const reauth = await handler.fetch(new Request(
    `https://${STAGING_HOST}/auth/reauth?return_to=%2Fsystem-work`, { headers: { cookie: session } },
  ), environment, {});
  assert.equal(reauth.status, 302);
  assert.equal(new URL(reauth.headers.get("location")).searchParams.get("redirect_uri"),
    `https://${STAGING_HOST}/auth/callback`);

  const bootstrap = await (await handler.fetch(new Request(
    `https://${STAGING_HOST}/api/system-work/session`, { headers: { cookie: session } },
  ), environment, {})).json();
  const target = { action: "accept-ready-plan", human_ref: "WR-41", base_version: 3,
    idempotency_key: "30000000-0000-0000-0000-000000000041", plan_hash: `sha256:${"a".repeat(64)}` };
  const headers = { cookie: session, "content-type": "application/json", "x-carr-csrf": bootstrap.csrf_token,
    "sec-fetch-site": "same-origin" };
  let response = await handler.fetch(new Request(`https://${STAGING_HOST}/api/system-work/challenge`, {
    method: "POST", headers: { ...headers, origin: `https://${PRODUCTION_HOST}` }, body: JSON.stringify(target),
  }), environment, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).reason, "origin_mismatch");
  response = await handler.fetch(new Request(`https://${STAGING_HOST}/api/system-work/challenge`, {
    method: "POST", headers: { ...headers, origin: `https://${STAGING_HOST}` }, body: JSON.stringify(target),
  }), environment, {});
  assert.equal(response.status, 200);

  const signedOut = await handler.fetch(new Request(`https://${STAGING_HOST}/auth/signout`, {
    method: "POST", headers: { cookie: session, origin: `https://${STAGING_HOST}`,
      "sec-fetch-site": "same-origin", "x-carr-csrf": bootstrap.csrf_token },
  }), environment, {});
  assert.equal(signedOut.status, 302);
  assert.equal(signedOut.headers.get("location"), `https://${STAGING_HOST}/`);
});

test("Deal Room gate refuses a verified non-partner without returning deal data", async () => {
  const environment = env();
  const callback = await login(identityHandler("someone@example.com"), environment, "someone@example.com");
  assert.equal(callback.status, 403);
  assert.match(callback.headers.get("content-type"), /^text\/html/);
  const html = await callback.text();
  assert.match(html, /Not authorized/);
  assert.match(html, /no deal data was returned/i);
  assert.doesNotMatch(html, /pipeline|Deal Alpha|someone@example\.com/);
  assert.equal(setCookies(callback).some((cookie) => cookie.startsWith("__Host-dealroom_session=")), false);
});

test("Deal Room gate admits exactly both partner identities with their existing actors", async () => {
  for (const [email, slug, display] of [
    ["joe.bookout.carr.us@gmail.com", "joe", "Joe"],
    ["dell.mccraney.carr.us@gmail.com", "dell", "Dell"],
  ]) {
    const environment = env();
    const callback = await login(identityHandler(email), environment, email);
    assert.equal(callback.status, 302);
    const session = namedCookie(callback, "__Host-dealroom_session");
    assert.match(setCookies(callback).find((cookie) => cookie.startsWith("__Host-dealroom_session=")),
      /; Secure; HttpOnly; SameSite=Lax/);
    const api = await identityHandler(email).fetch(new Request("https://dealroom.doctorcre.com/mcp",
      { method: "POST", headers: { cookie: session, origin: "https://dealroom.doctorcre.com" } }), environment, {});
    assert.equal(api.status, 200);
    // human_slug (loop #227) is null on this path by design: the Deal Room cookie
    // authenticates a partner directly, so the actor already IS the human and there
    // is no outside-model grant standing in front of them. Asserted explicitly
    // rather than dropped from the comparison — the point of this test is that the
    // cookie path mints an ordinary partner identity, and "no agent behind it" is
    // part of that claim, not an incidental field.
    assert.deepEqual((await api.json()).actor,
      { slug, display, human: true, via: "dealroom-cookie", client_id: "dealroom-pwa",
        human_slug: null, sponsoring_human_slug: null, sponsor_required: false });
  }
});

test("opaque cookie session round-trips, refreshes, expires, and signs out", async () => {
  const clock = { now: 1_800_000_000_000 };
  const environment = env();
  const handler = identityHandler("joe.bookout.carr.us@gmail.com", clock);
  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com");
  const session = namedCookie(callback, "__Host-dealroom_session");
  assert.doesNotMatch(session, /joe|gmail/i);

  environment.DEALROOM_PROGRAM6_ACTIONS_ENABLED = "true";
  const bootstrap = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/session",
    { headers: { cookie: session } }), environment, {});
  const { csrf_token: csrfToken } = await bootstrap.json();

  let api = await handler.fetch(new Request("https://dealroom.doctorcre.com/pipeline/changes",
    { headers: { cookie: session } }), environment, {});
  assert.equal(api.status, 200);
  assert.equal((await api.json()).actor.slug, "joe");

  clock.now += 11.5 * 60 * 60 * 1000;
  api = await handler.fetch(new Request("https://dealroom.doctorcre.com/pipeline/changes",
    { headers: { cookie: session } }), environment, {});
  assert.equal(api.status, 200);
  assert.ok(setCookies(api).some((cookie) => cookie.startsWith("__Host-dealroom_session=")));

  const signedOut = await handler.fetch(new Request("https://dealroom.doctorcre.com/auth/signout",
    { method: "POST", headers: { cookie: session, origin: "https://dealroom.doctorcre.com",
      "sec-fetch-site": "same-origin", "x-carr-csrf": csrfToken } }), environment, {});
  assert.equal(signedOut.status, 302);
  api = await handler.fetch(new Request("https://dealroom.doctorcre.com/pipeline/changes",
    { headers: { cookie: session } }), environment, {});
  assert.equal(api.status, 401);

  const expiryEnv = env();
  const expiryClock = { now: 1_800_000_000_000 };
  const expiryHandler = identityHandler("joe.bookout.carr.us@gmail.com", expiryClock);
  const expiryCallback = await login(expiryHandler, expiryEnv, "joe.bookout.carr.us@gmail.com");
  const expiringSession = namedCookie(expiryCallback, "__Host-dealroom_session");
  expiryClock.now += 7 * 24 * 60 * 60 * 1000 + 1;
  api = await expiryHandler.fetch(new Request("https://dealroom.doctorcre.com/pipeline/changes",
    { headers: { cookie: expiringSession } }), expiryEnv, {});
  assert.equal(api.status, 401);
});

test("Program 6 browser bootstrap is feature-gated and its typed boundary refuses cross-site or unbound writes", async () => {
  const environment = env();
  const handler = identityHandler("joe.bookout.carr.us@gmail.com");
  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com");
  const session = namedCookie(callback, "__Host-dealroom_session");
  let response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/session",
    { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 404);
  assert.equal((await response.json()).error, "program6_actions_disabled");

  environment.DEALROOM_PROGRAM6_ACTIONS_ENABLED = "true";
  response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/session",
    { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  const bootstrap = await response.json();
  assert.equal(bootstrap.actor.slug, "joe");
  assert.match(bootstrap.csrf_token, /^[0-9a-f]{64}$/);
  assert.equal(bootstrap.reauth_required, false);
  assert.equal(bootstrap.challenge_ttl_seconds, 300);

  const challengeBody = JSON.stringify({ action: "accept-ready-plan", human_ref: "WR-41", base_version: 3,
    idempotency_key: "10000000-0000-0000-0000-000000000041",
    plan_hash: `sha256:${"a".repeat(64)}` });
  const headers = { cookie: session, "content-type": "application/json", "x-carr-csrf": bootstrap.csrf_token };
  response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/challenge", {
    method: "POST", headers, body: challengeBody,
  }), environment, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).reason, "origin_mismatch");

  response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/challenge", {
    method: "POST", headers: { ...headers, origin: "https://dealroom.doctorcre.com", "sec-fetch-site": "same-site" }, body: challengeBody,
  }), environment, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).reason, "fetch_metadata_mismatch");

  response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/challenge", {
    method: "POST", headers: { ...headers, origin: "https://dealroom.doctorcre.com", "sec-fetch-site": "same-origin" }, body: challengeBody,
  }), environment, {});
  assert.equal(response.status, 200);
  assert.match((await response.json()).challenge, /^[0-9a-f]{64}$/);
});

test("reauthentication is session-bound, and approval challenges are one-time and exact", async () => {
  const clock = { now: 1_800_000_000_000 };
  const environment = env();
  environment.DEALROOM_PROGRAM6_ACTIONS_ENABLED = "true";
  const handler = identityHandler("joe.bookout.carr.us@gmail.com", clock);
  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com");
  const session = namedCookie(callback, "__Host-dealroom_session");
  const bootstrap = await (await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/session",
    { headers: { cookie: session } }), environment, {})).json();
  const target = { action: "accept-outcome-feedback", human_ref: "WR-41", base_version: 3,
    idempotency_key: "20000000-0000-0000-0000-000000000041",
    feedback_hash: `sha256:${"b".repeat(64)}` };
  const csrfHeaders = { cookie: session, origin: "https://dealroom.doctorcre.com", "sec-fetch-site": "same-origin",
    "content-type": "application/json", "x-carr-csrf": bootstrap.csrf_token };
  let response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/challenge", {
    method: "POST", headers: csrfHeaders, body: JSON.stringify(target),
  }), environment, {});
  const { challenge } = await response.json();

  // Model Cloudflare KV's non-atomic visibility: both concurrent-looking
  // requests may still read the same challenge even after delete. The atomic
  // redemption seam below, not KV, must permit exactly one.
  const kvDelete = environment.OAUTH_KV.delete.bind(environment.OAUTH_KV);
  environment.OAUTH_KV.delete = async (key) => {
    if (!String(key).startsWith("dealroom_action_challenge:")) await kvDelete(key);
  };

  // The injected typed controller has no workflow implementation in this test;
  // it exercises the reusable boundary by asking it to authorize a supplied target.
  const redeemed = new Set();
  const controller = createDealroomHandler({
    ...identityOverrides("joe.bookout.carr.us@gmail.com", clock),
    program6Handler: async (request, envArg, _ctx, actor, sessionState) => {
      // The concrete controller must parse first to route/validate the typed
      // body. The header-only browser guard remains valid after that consume.
      const parsed = await request.json();
      const { authorizeProgram6Action } = await import("../src/dealroom-web.js");
      const refusal = await authorizeProgram6Action({ request, env: envArg, actor, session: sessionState,
        action: "accept-outcome-feedback", args: parsed, now: clock.now,
        redeemChallenge: async ({ tokenDigest }) => {
          if (redeemed.has(tokenDigest)) return { ok: false };
          redeemed.add(tokenDigest); return { ok: true };
        } });
      return refusal || new Response(JSON.stringify({ ok: true }));
    },
  });
  const approvalHeaders = { ...csrfHeaders, "x-carr-action-challenge": challenge };
  response = await controller.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/accept-outcome-feedback", {
    method: "POST", headers: approvalHeaders, body: JSON.stringify(target),
  }), environment, {});
  assert.equal(response.status, 200);
  response = await controller.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/accept-outcome-feedback", {
    method: "POST", headers: approvalHeaders, body: JSON.stringify(target),
  }), environment, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, "invalid_action_challenge");
  assert.equal(redeemed.size, 1);

  clock.now += 10 * 60 * 1000 + 1;
  response = await controller.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/accept-outcome-feedback", {
    method: "POST", headers: { ...csrfHeaders, "x-carr-action-challenge": "not-used" }, body: JSON.stringify(target),
  }), environment, {});
  assert.equal(response.status, 401);
  assert.equal((await response.json()).error, "reauth_required");

  const reauth = await handler.fetch(new Request("https://dealroom.doctorcre.com/auth/reauth?return_to=%2Fsystem-work",
    { headers: { cookie: session } }), environment, {});
  assert.equal(reauth.status, 302);
  const reauthGoogle = new URL(reauth.headers.get("location"));
  const reauthState = reauthGoogle.searchParams.get("state");
  const reauthPending = namedCookie(reauth, "__Host-dealroom_oauth");
  const reauthCallback = await handler.fetch(new Request(
    `https://dealroom.doctorcre.com/auth/callback?state=${reauthState}&code=reauth-code`,
    { headers: { cookie: `${session}; ${reauthPending}` } },
  ), environment, {});
  assert.equal(reauthCallback.status, 302);
  assert.equal(reauthCallback.headers.get("location"), "https://dealroom.doctorcre.com/system-work");
  const refreshed = await (await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/session",
    { headers: { cookie: session } }), environment, {})).json();
  assert.equal(refreshed.reauth_required, false);
});

test("manifest and service worker are public with PWA-safe headers and offline truthfulness", async () => {
  const environment = env();
  const handler = identityHandler("joe.bookout.carr.us@gmail.com");
  const manifestResponse = await handler.fetch(new Request("https://dealroom.doctorcre.com/manifest.webmanifest"), environment, {});
  assert.equal(manifestResponse.status, 200);
  assert.match(manifestResponse.headers.get("content-type"), /^application\/manifest\+json/);
  const manifest = await manifestResponse.json();
  assert.equal(manifest.name, "The Deal Room");
  assert.equal(manifest.theme_color, "#002F6C");
  assert.equal(manifest.background_color, "#F57F29");
  assert.deepEqual(manifest.icons.map((icon) => icon.sizes), ["192x192", "512x512"]);

  const swResponse = await handler.fetch(new Request("https://dealroom.doctorcre.com/sw.js"), environment, {});
  assert.equal(swResponse.status, 200);
  assert.match(swResponse.headers.get("content-type"), /^application\/javascript/);
  assert.equal(swResponse.headers.get("service-worker-allowed"), "/");
  assert.match(swResponse.headers.get("content-security-policy"), /script-src 'self'/);
  assert.doesNotMatch(swResponse.headers.get("content-security-policy"), /unsafe-inline/);
  assert.equal(swResponse.headers.get("x-content-type-options"), "nosniff");
  assert.equal(swResponse.headers.get("x-frame-options"), "DENY");
  const sw = await swResponse.text();
  assert.match(sw, /DATA_PATHS/);
  assert.match(sw, /state: "reconnecting"/);
  assert.match(sw, /Deliberately no Cache API fallback/);
});
