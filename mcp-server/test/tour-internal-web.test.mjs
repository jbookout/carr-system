import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createTourInternalWebHandler, isTourInternalRequest, TOUR_INTERNAL_ASSET_DIRECTORY } from "../src/tour-internal-web.js";

const ROOT = fileURLToPath(new URL("../../dealroom", import.meta.url));
const ORIGIN = "https://app.doctorcre.com";
const ACTOR = { id: "partner-actor" };
const SESSION = { key: "opaque-server-session", csrfToken: "csrf-value" };
const tourId = "11111111-1111-4111-8111-111111111111";
const routeId = "22222222-2222-4222-8222-222222222222";
const stopA = "33333333-3333-4333-8333-333333333333";
const stopB = "44444444-4444-4444-8444-444444444444";
const projectionId = "55555555-5555-4555-8555-555555555555";
const grantId = "66666666-6666-4666-8666-666666666666";
const digest = `sha256:${"a".repeat(64)}`;

class Assets {
  constructor() { this.paths = []; }
  async fetch(request) {
    const pathname = new URL(request.url).pathname;
    this.paths.push(pathname);
    try { return new Response(await readFile(`${ROOT}${pathname}`)); }
    catch { return new Response("missing", { status: 404 }); }
  }
}
function request(path, options = {}) { return new Request(`${ORIGIN}${path}`, options); }
function handler(overrides = {}) {
  const success = async () => ({ ok: true, data: { saved: true } });
  return createTourInternalWebHandler({
    listToursFn: async () => ({ ok: true, data: { tours: [] } }), readTourFn: async () => ({ ok: true, data: { id: tourId } }),
    createRouteVersionFn: success, reorderRouteStopsFn: success, acceptRouteVersionFn: success,
    autosaveCheatSheetFn: success, restoreCheatSheetFn: success, createProjectionFn: success,
    readProjectionCandidatesFn: success, sealProjectionFn: success,
    issueShareGrantFn: success, rotateShareGrantFn: success, revokeShareGrantFn: success,
    renderPdfFn: success, readPdfRenderFn: success, reviewPdfFn: success,
    previewPdfFn: async () => ({ ok: true, response: new Response("pdf", { headers: { "content-type": "application/pdf", "content-disposition": "inline" } }) }),
    downloadPdfFn: async () => ({ ok: true, response: new Response("pdf", { headers: { "content-type": "application/pdf" } }) }),
    ...overrides,
  });
}
const postHeaders = { origin: ORIGIN, "sec-fetch-site": "same-origin", "content-type": "application/json", "x-carr-csrf": SESSION.csrfToken };
const issueBody = { projection_id: projectionId, token_digest: digest, permission_scopes: ["view_packet"], expires_at: "2027-01-02T03:04:05.000Z", receipt_digest: digest, idempotency_key: grantId };

test("internal Tour surface requires an injected authenticated actor and CSRF session", async () => {
  const surface = handler(); const assets = new Assets();
  for (const args of [[undefined, undefined], [ACTOR, undefined], [{}, SESSION]]) {
    const response = await surface.fetch(request("/tours"), { APP_HOST: "app.doctorcre.com", ASSETS: assets }, {}, ...args);
    assert.equal(response.status, 401);
  }
  assert.equal((await surface.fetch(request("/tours"), { APP_HOST: "app.doctorcre.com", ASSETS: assets }, {}, ACTOR, SESSION)).status, 200);
  assert.deepEqual(assets.paths, ["/tours/index.html"]);
});

test("exact routes, methods, CSRF, and JSON bodies remain bounded", async () => {
  const surface = handler(); const env = { APP_HOST: "app.doctorcre.com" };
  assert.equal(isTourInternalRequest(request("/tours")), true);
  assert.equal(isTourInternalRequest(request("/api/tours/library")), true);
  assert.equal(isTourInternalRequest(request("/api/v1/tours")), false);
  assert.equal(isTourInternalRequest(request("/api/tours/library/extra")), false);
  assert.equal(isTourInternalRequest(request("/api/tours/interactions/review")), false);
  assert.equal((await surface.fetch(request("/api/tours/share/issue"), env, {}, ACTOR, SESSION)).status, 405);
  assert.equal((await surface.fetch(request("/api/tours/nope"), env, {}, ACTOR, SESSION)).status, 404);
  assert.equal((await surface.fetch(request("/api/tours/share/issue", { method: "POST", headers: { ...postHeaders, "x-carr-csrf": "wrong" }, body: JSON.stringify(issueBody) }), env, {}, ACTOR, SESSION)).status, 403);
  assert.equal((await surface.fetch(request("/api/tours/share/issue", { method: "POST", headers: { ...postHeaders, origin: "https://elsewhere.example" }, body: JSON.stringify(issueBody) }), env, {}, ACTOR, SESSION)).status, 403);
  assert.equal((await surface.fetch(request("/api/tours/share/issue", { method: "POST", headers: postHeaders, body: JSON.stringify({ ...issueBody, actor: "chosen-by-browser" }) }), env, {}, ACTOR, SESSION)).status, 400);
  assert.equal((await surface.fetch(request("/api/tours/share/issue", { method: "POST", headers: postHeaders, body: JSON.stringify({ ...issueBody, token: "never-permitted" }) }), env, {}, ACTOR, SESSION)).status, 400);
  assert.equal((await surface.fetch(request("/api/tours/detail?tour_id=" + tourId + "&x=1"), env, {}, ACTOR, SESSION)).status, 400);
  assert.equal((await surface.fetch(request("/api/tours/share/issue", { method: "POST", headers: { ...postHeaders, "content-length": "40000" }, body: JSON.stringify(issueBody) }), env, {}, ACTOR, SESSION)).status, 413);
});

test("only a SHA-256 digest crosses the confidential share issue seam", async () => {
  let received;
  const surface = handler({ issueShareGrantFn: async (context) => { received = context; return { ok: true, data: { share_grant_id: grantId } }; } });
  const response = await surface.fetch(request("/api/tours/share/issue", { method: "POST", headers: postHeaders, body: JSON.stringify(issueBody) }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION);
  assert.equal(response.status, 200);
  assert.deepEqual(received.input, issueBody);
  assert.equal(received.session, undefined);
  assert.match(received.input.token_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(JSON.stringify(received).includes("token="), false);
  assert.equal(Object.hasOwn(received.input, "token"), false);
  assert.deepEqual(received.actor, ACTOR, "actor is server-injected, not selected in body");
});

test("static shell has no raw-token persistence/logging and stays in dealroom/tours assets", async () => {
  const [html, js, css, handlerSource] = await Promise.all([
    readFile(new URL("../../dealroom/tours/index.html", import.meta.url), "utf8"), readFile(new URL("../../dealroom/tours/app.js", import.meta.url), "utf8"),
    readFile(new URL("../../dealroom/tours/app.css", import.meta.url), "utf8"), readFile(new URL("../src/tour-internal-web.js", import.meta.url), "utf8"),
  ]);
  assert.equal(TOUR_INTERNAL_ASSET_DIRECTORY, "../dealroom/tours");
  assert.match(html, /\/tours\/app\.js/); assert.match(html, /\/tours\/app\.css/); assert.ok(css.length > 300);
  assert.match(js, /new Uint8Array\(32\)/); assert.match(js, /crypto\.getRandomValues/); assert.match(js, /crypto\.subtle\.digest\("SHA-256"/);
  assert.match(js, /https:\/\/reports\.doctorcre\.com\/share#token=\$\{raw\}/);
  assert.match(html, /value="view_map"/);
  assert.doesNotMatch(html, /value="(?:download_pdf|comment|react)"/);
  assert.match(html, /future governed scope amendment/);
  assert.match(css, /#002F6C/); assert.match(css, /#F57F29/);
  for (const source of [js, handlerSource]) { assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|console\.(?:log|warn|error)/); }
  assert.doesNotMatch(handlerSource, /\/api\/v1/);
  assert.doesNotMatch(js, /\/api\/v1|(?:mapbox|leaflet|google\.maps)/i);
});

test("route seams enforce their exact body contracts", async () => {
  const calls = []; const surface = handler({ createRouteVersionFn: async (value) => { calls.push(value); return { ok: true, data: { route_version_id: routeId } }; } });
  const body = { tour_id: tourId, expected_route_version: 0, stop_ids: [stopA, stopB], idempotency_key: routeId };
  const response = await surface.fetch(request("/api/tours/route-version", { method: "POST", headers: postHeaders, body: JSON.stringify(body) }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION);
  assert.equal(response.status, 200); assert.deepEqual(calls[0].input, body);
  const oneStop = { ...body, stop_ids: [stopA] };
  assert.equal((await surface.fetch(request("/api/tours/route-version", { method: "POST", headers: postHeaders, body: JSON.stringify(oneStop) }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION)).status, 200);
  const duplicate = { ...body, stop_ids: [stopA, stopA] };
  assert.equal((await surface.fetch(request("/api/tours/route-version", { method: "POST", headers: postHeaders, body: JSON.stringify(duplicate) }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION)).status, 400);
});

test("known optimistic races remain conflicts rather than service outages", async () => {
  const surface = handler({ createRouteVersionFn: async () => { throw new Error("tour route preparation refuses stale state"); } });
  const body = { tour_id: tourId, expected_route_version: 1, stop_ids: [stopA], idempotency_key: routeId };
  const response = await surface.fetch(request("/api/tours/route-version", { method: "POST", headers: postHeaders, body: JSON.stringify(body) }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION);
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { error: "conflict" });
});

test("internal PDF routes require exact authority-safe contracts and accepted download state", async () => {
  let renderInput; let reviewInput;
  const surface = handler({
    renderPdfFn: async context => { renderInput = context.input; return { ok: true, data: { render_job_id: routeId, status: "review_ready" } }; },
    reviewPdfFn: async context => { reviewInput = context.input; return { ok: true, data: { decision: "accept" } }; },
  });
  const env = { APP_HOST: "app.doctorcre.com" };
  const renderBody = { projection_id: projectionId, idempotency_key: routeId };
  assert.equal((await surface.fetch(request("/api/tours/pdf/render", { method: "POST", headers: postHeaders, body: JSON.stringify(renderBody) }), env, {}, ACTOR, SESSION)).status, 200);
  assert.deepEqual(renderInput, renderBody);
  const reviewBody = { render_job_id: routeId, qc_run_digest: digest, decision: "accept", reviewed_at: "2027-01-02T03:04:05.000Z", review_receipt_digest: digest, reason: "Human checked the rendered pages", idempotency_key: grantId };
  assert.equal((await surface.fetch(request("/api/tours/pdf/review", { method: "POST", headers: postHeaders, body: JSON.stringify(reviewBody) }), env, {}, ACTOR, SESSION)).status, 200);
  assert.deepEqual(reviewInput, reviewBody);
  assert.equal((await surface.fetch(request(`/api/tours/pdf/download?render_job_id=${routeId}`), env, {}, ACTOR, SESSION)).headers.get("content-type"), "application/pdf");
  assert.match((await surface.fetch(request(`/api/tours/pdf/preview?render_job_id=${routeId}`), env, {}, ACTOR, SESSION)).headers.get("content-disposition"), /inline/);
  assert.equal((await surface.fetch(request(`/api/tours/pdf/status?render_job_id=${routeId}&x=1`), env, {}, ACTOR, SESSION)).status, 400);
  assert.equal((await surface.fetch(request("/api/tours/pdf/review", { method: "POST", headers: postHeaders, body: JSON.stringify({ ...reviewBody, decision: "publish" }) }), env, {}, ACTOR, SESSION)).status, 400);
});

test("projection approval binds the human action to the exact reviewed candidate digest", async () => {
  let readInput; let sealInput;
  const surface = handler({
    readProjectionCandidatesFn: async context => { readInput = context.input; return { ok: true, data: { projection_id: projectionId, candidate_digest: digest, preview: [] } }; },
    sealProjectionFn: async context => { sealInput = context.input; return { ok: true, data: { projection_id: projectionId, status: "approved" } }; },
  });
  const env = { APP_HOST: "app.doctorcre.com" };
  assert.equal((await surface.fetch(request(`/api/tours/projection/candidates?projection_id=${projectionId}`), env, {}, ACTOR, SESSION)).status, 200);
  assert.deepEqual(readInput, { projection_id: projectionId });
  const body = { projection_id: projectionId, candidate_digest: digest, receipt_digest: digest, idempotency_key: routeId };
  assert.equal((await surface.fetch(request("/api/tours/projection/seal", { method: "POST", headers: postHeaders, body: JSON.stringify(body) }), env, {}, ACTOR, SESSION)).status, 200);
  assert.deepEqual(sealInput, body);
  assert.equal((await surface.fetch(request("/api/tours/projection/seal", { method: "POST", headers: postHeaders, body: JSON.stringify({ ...body, selected_facts: [] }) }), env, {}, ACTOR, SESSION)).status, 400);
});
