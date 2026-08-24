import test from "node:test";
import assert from "node:assert/strict";
import { createDealroomHandler, isDealroomRequest } from "../src/dealroom-web.js";

const HOST = "dealroom.doctorcre.com";
class Kv {
  constructor() { this.values = new Map(); }
  async put(key, value) { this.values.set(key, value); }
  async get(key, options) { const value = this.values.get(key); return value == null ? null : options?.type === "json" ? JSON.parse(value) : value; }
  async delete(key) { this.values.delete(key); }
}
class Assets {
  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === "/workspace.html") return new Response("<main>CARR Command Center</main>", { headers: { "content-type": "text/html" } });
    if (path === "/index.html") return new Response("<main>Deal Room</main>", { headers: { "content-type": "text/html" } });
    return new Response("missing", { status: 404 });
  }
}
const makeEnvironment = () => ({ DEALROOM_HOST: HOST, GOOGLE_CLIENT_ID: "client", GOOGLE_CLIENT_SECRET: "secret", CORRELATION_ID: "corr-web", OAUTH_KV: new Kv(), ASSETS: new Assets(), WORKSPACE_COMMAND_CENTER_READ_ENABLED: "true" });
const overrides = (email, reader = null) => ({
  exchangeGoogleCodeFn: async () => ({ id_token: "stub" }),
  verifyGoogleIdTokenFn: async () => ({ email, email_verified: true, sub: `sub:${email}` }),
  commandCenterReader: reader || (async (env, actor, correlationId) => ({ viewer: actor.slug, needs_you_now: [], this_week: [], metrics: [{ owned_active_deals: 2, owned_flagged_deals: 1 }], recent_calls: [], doc_at_work: [], recent_activity: [], source: { source: "command_center", observed_at: "2026-08-24T15:00:00.000Z", valid_until: "2026-08-24T15:01:00.000Z", freshness: "fresh", correlation_id: correlationId, safe_explanation: "Fresh because this is a no-store request-time canonical database aggregate; valid for 60 seconds." } })),
});
const cookie = (response) => (typeof response.headers.getSetCookie === "function" ? response.headers.getSetCookie() : [response.headers.get("set-cookie")]).find((value) => value?.startsWith("__Host-dealroom_session=")).split(";", 1)[0];

test("Command Center is a protected same-origin read route and leaves root Deal Room unchanged", async () => {
  assert.equal(isDealroomRequest(new Request(`https://${HOST}/workspace`), { DEALROOM_HOST: HOST }), true);
  assert.equal(isDealroomRequest(new Request(`https://${HOST}/api/v1/command-center`), { DEALROOM_HOST: HOST }), true);
  const environment = makeEnvironment();
  const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com"));
  let response = await handler.fetch(new Request(`https://${HOST}/workspace`, { headers: { accept: "text/html" } }), environment, {});
  assert.equal(response.status, 302);
  assert.match(response.headers.get("location"), /auth\/login\?return_to=%2Fworkspace/);
  response = await handler.fetch(new Request(`https://${HOST}/api/v1/command-center`), environment, {});
  assert.equal(response.status, 401);
  assert.equal((await response.json()).error, "AUTHENTICATION_REQUIRED");
  const start = await handler.fetch(new Request(`https://${HOST}/auth/login`), environment, {});
  const google = new URL(start.headers.get("location"));
  const pending = typeof start.headers.getSetCookie === "function" ? start.headers.getSetCookie()[0] : start.headers.get("set-cookie");
  const callback = await handler.fetch(new Request(`https://${HOST}/auth/callback?state=${google.searchParams.get("state")}&code=stub`, { headers: { cookie: pending, "x-test-email": "joe.bookout.carr.us@gmail.com" } }), environment, {});
  const session = cookie(callback);
  response = await handler.fetch(new Request(`https://${HOST}/workspace`, { headers: { cookie: session, accept: "text/html" } }), environment, {});
  assert.equal(response.status, 200);
  assert.match(await response.text(), /CARR Command Center/);
  response = await handler.fetch(new Request(`https://${HOST}/`, { headers: { cookie: session, accept: "text/html" } }), environment, {});
  assert.match(await response.text(), /CARR Command Center/);
  const fallback = await handler.fetch(new Request(`https://${HOST}/`, { headers: { cookie: session, accept: "text/html" } }), { ...environment, WORKSPACE_COMMAND_CENTER_READ_ENABLED: "false" }, {});
  assert.match(await fallback.text(), /Deal Room/);
  const misconfigured = await handler.fetch(new Request(`https://${HOST}/`, { headers: { cookie: session, accept: "text/html" } }), { ...environment, WORKSPACE_COMMAND_CENTER_READ_ENABLED: "yes" }, {});
  assert.match(await misconfigured.text(), /Deal Room/);
  response = await handler.fetch(new Request(`https://${HOST}/api/v1/command-center`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  assert.equal((await response.json()).metrics[0].owned_flagged_deals, 1);
  response = await handler.fetch(new Request(`https://${HOST}/api/v1/command-center?viewer=joe`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  response = await handler.fetch(new Request(`https://${HOST}/api/v1/command-center?owner=dell`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, "AUTHORIZATION_REFUSED");
  response = await handler.fetch(new Request(`https://${HOST}/api/v1/command-center`, { method: "POST", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 405);
  assert.equal((await response.json()).error, "METHOD_NOT_ALLOWED");
  const dependencyHandler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com", async () => { throw Object.assign(new Error("db"), { code: "DEPENDENCY_UNAVAILABLE" }); }));
  response = await dependencyHandler.fetch(new Request(`https://${HOST}/api/v1/command-center`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error, "DEPENDENCY_UNAVAILABLE");
  const internalHandler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com", async () => { throw new Error("unexpected"); }));
  response = await internalHandler.fetch(new Request(`https://${HOST}/api/v1/command-center`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 500);
  assert.equal((await response.json()).error, "INTERNAL_ERROR");
});

test("disabled Command Center routes do not serve its asset and API query scope is server-bound", async () => {
  const environment = makeEnvironment();
  const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com"));
  let response = await handler.fetch(new Request(`https://${HOST}/workspace`, { headers: { accept: "text/html" } }), { ...environment, WORKSPACE_COMMAND_CENTER_READ_ENABLED: "false" }, {});
  assert.equal(response.status, 302);
  assert.match(response.headers.get("location"), /\/deals$/);
  response = await handler.fetch(new Request(`https://${HOST}/workspace.html`, { headers: { accept: "text/html" } }), { ...environment, WORKSPACE_COMMAND_CENTER_READ_ENABLED: "false" }, {});
  assert.equal(response.status, 302);
  assert.match(response.headers.get("location"), /\/deals$/);
  response = await handler.fetch(new Request(`https://${HOST}/api/v1/command-center?viewer=dell`), environment, {});
  assert.equal(response.status, 401);
  assert.equal((await response.json()).error, "AUTHENTICATION_REQUIRED");
});
