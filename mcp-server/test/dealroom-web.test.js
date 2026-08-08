import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createDealroomHandler } from "../src/dealroom-web.js";

const SHELL_ROOT = fileURLToPath(new URL("../../dealroom/public-shell/", import.meta.url));

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
    if (!pathname.startsWith("/public-shell/")) return new Response("missing", { status: 404 });
    try {
      return new Response(await readFile(SHELL_ROOT + pathname.slice("/public-shell/".length)));
    } catch {
      return new Response("missing", { status: 404 });
    }
  }
}

function env() {
  return {
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

async function login(handler, environment, email) {
  const start = await handler.fetch(new Request("https://dealroom.doctorcre.com/auth/login?return_to=%2F"), environment, {});
  assert.equal(start.status, 302);
  const google = new URL(start.headers.get("location"));
  assert.equal(google.hostname, "accounts.google.com");
  const state = google.searchParams.get("state");
  const pendingCookie = namedCookie(start, "__Host-dealroom_oauth");
  const callback = await handler.fetch(new Request(
    `https://dealroom.doctorcre.com/auth/callback?state=${state}&code=stub-code`,
    { headers: { cookie: pendingCookie, "x-test-email": email } },
  ), environment, {});
  return callback;
}

function identityHandler(email, clock) {
  return createDealroomHandler({
    exchangeGoogleCodeFn: async () => ({ id_token: "stub-id-token" }),
    verifyGoogleIdTokenFn: async () => ({ email, email_verified: true, sub: `sub:${email}` }),
    now: () => clock?.now ?? 1_800_000_000_000,
    mcpHandler: async (_request, _env, _ctx, actor) =>
      new Response(JSON.stringify({ surface: "mcp", actor }), { headers: { "content-type": "application/json" } }),
    pipelineHandler: async (_request, _env, _ctx, actor) =>
      new Response(JSON.stringify({ surface: "pipeline", actor }), { headers: { "content-type": "application/json" } }),
  });
}

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
    assert.deepEqual((await api.json()).actor,
      { slug, display, human: true, via: "dealroom-cookie", client_id: "dealroom-pwa" });
  }
});

test("opaque cookie session round-trips, refreshes, expires, and signs out", async () => {
  const clock = { now: 1_800_000_000_000 };
  const environment = env();
  const handler = identityHandler("joe.bookout.carr.us@gmail.com", clock);
  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com");
  const session = namedCookie(callback, "__Host-dealroom_session");
  assert.doesNotMatch(session, /joe|gmail/i);

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
    { method: "POST", headers: { cookie: session } }), environment, {});
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
  const sw = await swResponse.text();
  assert.match(sw, /DATA_PATHS/);
  assert.match(sw, /state: "reconnecting"/);
  assert.match(sw, /Deliberately no Cache API fallback/);
});
