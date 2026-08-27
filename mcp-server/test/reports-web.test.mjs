import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createReportsWebHandler, isReportsRequest, REPORTS_ORIGIN } from "../src/reports-web.js";

const REPORTS_ROOT = fileURLToPath(new URL("../../dealroom/", import.meta.url));
const SHARE_JS = fileURLToPath(new URL("../../dealroom/reports/share.js", import.meta.url));

class ReportAssets {
  constructor() { this.requests = []; }
  async fetch(request) {
    this.requests.push(request);
    const pathname = new URL(request.url).pathname;
    try { return new Response(await readFile(`${REPORTS_ROOT}${pathname}`), { headers: { "access-control-allow-origin": "*" } }); }
    catch { return new Response("missing", { status: 404 }); }
  }
}

function request(path, options = {}) {
  return new Request(`${REPORTS_ORIGIN}${path}`, options);
}

function cookies(response) {
  return typeof response.headers.getSetCookie === "function" ? response.headers.getSetCookie() : [response.headers.get("set-cookie")].filter(Boolean);
}

async function sha256Digest(value) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value)
    : value instanceof ArrayBuffer ? new Uint8Array(value)
      : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return "sha256:" + [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function handler(overrides = {}) {
  const pdf = new TextEncoder().encode("%PDF-1.7\nfixture\n%%EOF");
  return createReportsWebHandler({
    exchangeShareTokenFn: async () => ({ ok: true }),
    readShareFn: async ({ csrfOnly }) => ({ ok: true, csrfToken: "csrf-token", ...(csrfOnly ? {} : { data: { title: "Client tour", items: [{ name: "Redacted property", status: "Available" }] } }) }),
    commentShareFn: async ({ body }) => ({ ok: true, data: { accepted: body.body === "A helpful note" } }),
    reactionShareFn: async () => ({ ok: true, data: { accepted: true } }),
    readPdfFn: async () => ({ ok: true, body: pdf, artifactDigest: await sha256Digest(pdf) }),
    ...overrides,
  });
}

const sameOriginJson = { origin: REPORTS_ORIGIN, "sec-fetch-site": "same-origin", "content-type": "application/json" };

test("reports adapter is limited to the reports origin and exact share routes", async () => {
  assert.equal(isReportsRequest(request("/share")), true);
  assert.equal(isReportsRequest(request("/sw.js")), false);
  assert.equal(isReportsRequest(new Request("https://app.doctorcre.com/share")), false);

  let exchanges = 0;
  const surface = handler({ exchangeShareTokenFn: async () => { exchanges += 1; return { ok: true }; } });
  const assets = new ReportAssets();
  const share = await surface.fetch(request("/share", { headers: { cookie: "__Host-tour_share_session=not-forwarded", "x-private-header": "not-forwarded" } }), { ASSETS: assets });
  assert.equal(share.status, 200);
  assert.equal(exchanges, 0, "a scanner GET cannot exchange a share bearer");
  assert.equal(assets.requests[0].headers.get("cookie"), null, "asset fetch never forwards the browser cookie");
  assert.equal(assets.requests[0].headers.get("x-private-header"), null, "asset fetch never forwards browser headers");
  assert.equal(share.headers.get("cache-control"), "no-store");
  assert.equal(share.headers.get("access-control-allow-origin"), null);
  assert.match(share.headers.get("content-security-policy"), /worker-src 'none'/);
  assert.match(share.headers.get("content-security-policy"), /manifest-src 'none'/);
  assert.equal((await surface.fetch(request("/sw.js"), { ASSETS: new ReportAssets() })).status, 404);
  assert.equal((await surface.fetch(request("/share", { method: "POST" }), { ASSETS: new ReportAssets() })).status, 405);
  assert.equal((await surface.fetch(new Request("https://app.doctorcre.com/share"), { ASSETS: new ReportAssets() })).status, 404);
});

test("exchange passes only SHA-256 digests to its dependency and sets the host session cookie", async () => {
  const rawToken = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ";
  let exchangeInput;
  const now = 1_800_000_000_000;
  const surface = handler({ now: () => now, exchangeShareTokenFn: async (input) => { exchangeInput = input; return { ok: true }; } });
  const response = await surface.fetch(request("/api/share/exchange", {
    method: "POST", headers: sameOriginJson, body: JSON.stringify({ token: rawToken }),
  }), {});
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true });
  assert.deepEqual(Object.keys(exchangeInput).sort(), ["auditDigest", "env", "sessionDigest", "sessionExpiresAt", "tokenDigest"]);
  assert.equal(exchangeInput.token, undefined);
  assert.equal(exchangeInput.session, undefined);
  assert.equal(exchangeInput.request, undefined);
  assert.equal(exchangeInput.tokenDigest, await sha256Digest(rawToken));
  assert.equal(exchangeInput.sessionExpiresAt, new Date(now + 60 * 60 * 1000).toISOString());
  const cookie = cookies(response).find((value) => value.startsWith("__Host-tour_share_session="));
  assert.match(cookie, /^__Host-tour_share_session=[A-Za-z0-9_-]{43}; Path=\/; Secure; HttpOnly; SameSite=Lax$/);
  const session = cookie.slice("__Host-tour_share_session=".length).split(";", 1)[0];
  assert.equal(cookie.includes(rawToken), false);
  assert.equal(exchangeInput.sessionDigest, await sha256Digest(session));
  assert.equal(exchangeInput.auditDigest, await sha256Digest("tour-share-exchange\n" + exchangeInput.tokenDigest + "\n" + exchangeInput.sessionDigest + "\n" + exchangeInput.sessionExpiresAt));

  for (const options of [
    { method: "GET" },
    { method: "POST", headers: { ...sameOriginJson, "content-type": "text/plain" }, body: "x" },
    { method: "POST", headers: { ...sameOriginJson, origin: "https://evil.example" }, body: JSON.stringify({ token: rawToken }) },
    { method: "POST", headers: sameOriginJson, body: JSON.stringify({ token: rawToken, extra: true }) },
  ]) {
    const refusal = await handler().fetch(request("/api/share/exchange", options), {});
    assert.notEqual(refusal.status, 200);
    assert.equal(refusal.headers.get("access-control-allow-origin"), null);
    assert.equal(refusal.headers.get("cache-control"), "no-store");
  }
});

test("authenticated reads and writes use the opaque cookie plus same-origin CSRF checks", async () => {
  let seenComment;
  let seenReaction;
  const readInputs = [];
  const surface = handler({
    readShareFn: async (input) => {
      readInputs.push(input);
      return { ok: true, csrfToken: "csrf-token", ...(input.csrfOnly ? {} : { data: { title: "Client tour", items: [{ name: "Redacted property", status: "Available" }] } }) };
    },
    commentShareFn: async (input) => { seenComment = input; return { ok: true, data: { accepted: true } }; },
    reactionShareFn: async (input) => { seenReaction = input; return { ok: true, data: { accepted: true } }; },
  });
  const cookie = "__Host-tour_share_session=session_abcdefghijklmnopqrstuvwxyz";
  const report = await surface.fetch(request("/api/share/report", { headers: { cookie } }), {});
  assert.equal(report.status, 200);
  assert.deepEqual(await report.json(), { data: { title: "Client tour", items: [{ name: "Redacted property", status: "Available" }] }, csrf_token: "csrf-token" });
  const rawSession = cookie.slice("__Host-tour_share_session=".length);
  assert.deepEqual(Object.keys(readInputs[0]).sort(), ["env", "sessionDigest"]);
  assert.equal(readInputs[0].session, undefined);
  assert.equal(readInputs[0].request, undefined);
  assert.equal(readInputs[0].sessionDigest, await sha256Digest(rawSession));

  for (const headers of [
    { cookie, ...sameOriginJson, "x-tour-share-csrf": "wrong" },
    { cookie, ...sameOriginJson, origin: "https://evil.example", "x-tour-share-csrf": "csrf-token" },
    { cookie, "content-type": "text/plain", origin: REPORTS_ORIGIN, "sec-fetch-site": "same-origin", "x-tour-share-csrf": "csrf-token" },
  ]) {
    const refusal = await surface.fetch(request("/api/share/comment", { method: "POST", headers, body: JSON.stringify({ property_ref: "property:public:abcdefghijklmnop", body: "A helpful note", idempotency_key: "10000000-0000-4000-8000-000000000001" }) }), {});
    assert.notEqual(refusal.status, 200);
  }
  const accepted = await surface.fetch(request("/api/share/comment", {
    method: "POST", headers: { cookie, ...sameOriginJson, "x-tour-share-csrf": "csrf-token" }, body: JSON.stringify({ property_ref: "property:public:abcdefghijklmnop", body: "A helpful note", idempotency_key: "10000000-0000-4000-8000-000000000001" }),
  }), {});
  assert.equal(accepted.status, 200);
  const reaction = await surface.fetch(request("/api/share/reaction", {
    method: "POST", headers: { cookie, ...sameOriginJson, "x-tour-share-csrf": "csrf-token" },
    body: JSON.stringify({ property_ref: "property:public:abcdefghijklmnop", reaction: "interested", idempotency_key: "10000000-0000-4000-8000-000000000002" }),
  }), {});
  assert.equal(reaction.status, 200);
  assert.deepEqual(Object.keys(seenReaction).sort(), ["body", "env", "sessionDigest"]);
  assert.deepEqual(seenReaction.body, { property_ref: "property:public:abcdefghijklmnop", reaction: "interested", idempotency_key: "10000000-0000-4000-8000-000000000002" });
  assert.equal(seenReaction.session, undefined);
  assert.equal(seenReaction.request, undefined);
  assert.equal(seenReaction.sessionDigest, await sha256Digest(rawSession));
  const internalId = await surface.fetch(request("/api/share/reaction", {
    method: "POST", headers: { cookie, ...sameOriginJson, "x-tour-share-csrf": "csrf-token" },
    body: JSON.stringify({ property_ref: "property:public:abcdefghijklmnop", reaction: "interested", idempotency_key: "10000000-0000-4000-8000-000000000003", internal_id: "never-accepted" }),
  }), {});
  assert.equal(internalId.status, 400);
  const controlCharacter = await surface.fetch(request("/api/share/comment", {
    method: "POST", headers: { cookie, ...sameOriginJson, "x-tour-share-csrf": "csrf-token" },
    body: JSON.stringify({ property_ref: "property:public:abcdefghijklmnop", body: "bad\u0000note", idempotency_key: "10000000-0000-4000-8000-000000000004" }),
  }), {});
  assert.equal(controlCharacter.status, 400);
  const tooLong = await surface.fetch(request("/api/share/comment", {
    method: "POST", headers: { cookie, ...sameOriginJson, "x-tour-share-csrf": "csrf-token" },
    body: JSON.stringify({ property_ref: "property:public:abcdefghijklmnop", body: "x".repeat(4001), idempotency_key: "10000000-0000-4000-8000-000000000005" }),
  }), {});
  assert.equal(tooLong.status, 400);
  assert.equal(seenComment.body.token, undefined);
  assert.deepEqual(Object.keys(seenComment).sort(), ["body", "env", "sessionDigest"]);
  assert.deepEqual(seenComment.body, { property_ref: "property:public:abcdefghijklmnop", body: "A helpful note", idempotency_key: "10000000-0000-4000-8000-000000000001" });
  assert.equal(seenComment.session, undefined);
  assert.equal(seenComment.request, undefined);
  assert.equal(seenComment.sessionDigest, await sha256Digest(rawSession));
  assert.equal(readInputs[1].session, undefined);
  for (const input of readInputs) {
    assert.equal(input.request, undefined);
    assert.equal(input.session, undefined);
    assert.equal(input.token, undefined);
    assert.equal(input.sessionDigest, await sha256Digest(rawSession));
  }
  assert.equal((await surface.fetch(request("/api/share/reaction", { method: "GET", headers: { cookie } }), {})).status, 405);
  assert.equal((await surface.fetch(request("/api/share/report", { method: "POST", headers: sameOriginJson, body: "{}" }), {})).status, 405);
});

test("static bootstrap removes the fragment but waits for an explicit Open tour click", async () => {
  const [html, script, css] = await Promise.all([
    readFile(`${REPORTS_ROOT}reports/share.html`, "utf8"), readFile(SHARE_JS, "utf8"), readFile(`${REPORTS_ROOT}reports/share.css`, "utf8"),
  ]);
  assert.match(html, /<script src="\/share\.js" defer><\/script>/);
  assert.match(html, /<button id="open-tour" type="button" disabled>Open tour<\/button>/);
  assert.match(script, /window\.location\.hash/);
  assert.match(script, /history\.replaceState/);
  const bootstrap = script.slice(script.indexOf("function bootstrap()"), script.indexOf("commentForm.addEventListener"));
  const openTour = script.slice(script.indexOf("async function openTour()"), script.indexOf("function bootstrap()"));
  assert.match(bootstrap, /shareToken = fragmentToken\(\);\n    removeFragment\(\);/);
  assert.match(bootstrap, /openButton\.disabled = false/);
  assert.doesNotMatch(bootstrap, /\/api\/share\/exchange|loadReport\(/);
  assert.match(openTour, /\/api\/share\/exchange/);
  assert.match(script, /openButton\.addEventListener\("click"/);
  assert.match(html, /<select id="property-ref" name="property_ref" required><\/select>/);
  assert.match(script, /route_sequence/);
  assert.match(script, /latest_reaction/);
  assert.match(script, /allow_reactions/);
  assert.match(script, /allow_comments/);
  assert.doesNotMatch(script, /internal_id|property_id/);
  assert.match(script, /property_ref: propertyRef, reaction, idempotency_key: crypto\.randomUUID\(\)/);
  assert.match(script, /property_ref: propertyRef, body, idempotency_key: crypto\.randomUUID\(\)/);
  assert.doesNotMatch(script, /authorization|serviceWorker|\/sw\.js|register\s*\(/i);
  assert.doesNotMatch(html + css, /serviceWorker|\/sw\.js/i);
  assert.match(css, /[@]media/);
  assert.match(css, /#002F6C/);
  assert.match(css, /#F57F29/);
  assert.match(script, /report\?\.stops/);
  assert.match(script, /report\?\.tour_name/);
  assert.match(script, /property:public/);
  assert.match(html, /maxlength="4000"/);
  assert.match(html, /href="\/api\/share\/pdf"/);
  assert.match(script, /allow_pdf_download/);
});

test("PDF download is session-only, digest-verified, bounded, and never forwards the raw cookie", async () => {
  const cookie = "__Host-tour_share_session=session_abcdefghijklmnopqrstuvwxyz";
  const rawSession = cookie.slice("__Host-tour_share_session=".length);
  let input;
  const pdf = new TextEncoder().encode("%PDF-1.7\nfixture\n%%EOF");
  const surface = handler({ readPdfFn: async (value) => { input = value; return { ok: true, body: pdf, artifactDigest: await sha256Digest(pdf) }; } });
  assert.equal((await surface.fetch(request("/api/share/pdf"), {})).status, 401);
  const response = await surface.fetch(request("/api/share/pdf", { headers: { cookie } }), {});
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "application/pdf");
  assert.equal(response.headers.get("content-disposition"), 'attachment; filename="CARR-Tour-Packet.pdf"');
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(Object.keys(input).sort(), ["env", "sessionDigest"]);
  assert.equal(input.sessionDigest, await sha256Digest(rawSession));
  assert.equal(input.session, undefined); assert.equal(input.request, undefined);
  const mismatch = handler({ readPdfFn: async () => ({ ok: true, body: pdf, artifactDigest: `sha256:${"0".repeat(64)}` }) });
  assert.equal((await mismatch.fetch(request("/api/share/pdf", { headers: { cookie } }), {})).status, 503);
  const notPdf = handler({ readPdfFn: async () => { const body = new TextEncoder().encode("not a pdf"); return { ok: true, body, artifactDigest: await sha256Digest(body) }; } });
  assert.equal((await notPdf.fetch(request("/api/share/pdf", { headers: { cookie } }), {})).status, 503);
});
