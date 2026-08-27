// Browser adapter for the deliberately small public tour-report share surface.
//
// A share bearer is accepted once, from the fragment bootstrap, and is exchanged
// for an opaque host-only cookie.  This module intentionally has no map, GIS,
// service-worker, CORS, or database implementation: those concerns belong to
// its injected record-layer seams and to a later routing integration.

export const REPORTS_ORIGIN = "https://reports.doctorcre.com";
export const REPORTS_ASSET_DIRECTORY = "../dealroom/reports";

const SESSION_COOKIE = "__Host-tour_share_session";
const MAX_BODY_BYTES = 16 * 1024;
const MAX_PDF_BYTES = 25 * 1024 * 1024;
const SESSION_TTL_MS = 60 * 60 * 1000;
const STATIC_ASSETS = new Map([
  ["/share", "/reports/share.html"],
  ["/share.js", "/reports/share.js"],
  ["/share.css", "/reports/share.css"],
]);
const API_METHODS = new Map([
  ["/api/share/exchange", "POST"],
  ["/api/share/report", "GET"],
  ["/api/share/pdf", "GET"],
  ["/api/share/comment", "POST"],
  ["/api/share/reaction", "POST"],
]);
const REPORTS_CSP = [
  "default-src 'none'",
  "base-uri 'none'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "worker-src 'none'",
  "child-src 'none'",
  "manifest-src 'none'",
].join("; ");

function json(body, status = 200, additions = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...additions },
  });
}

function reportsRequest(request) {
  try { return new URL(request.url).origin === REPORTS_ORIGIN; }
  catch { return false; }
}

function cookieValue(request, name) {
  const header = request.headers.get("cookie") || "";
  for (const part of header.split(";")) {
    const index = part.indexOf("=");
    if (index >= 0 && part.slice(0, index).trim() === name) return part.slice(index + 1).trim();
  }
  return null;
}

function sessionCookie(session) {
  return `${SESSION_COOKIE}=${session}; Path=/; Secure; HttpOnly; SameSite=Lax`;
}

async function sha256Digest(value) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value)
    : value instanceof ArrayBuffer ? new Uint8Array(value)
      : ArrayBuffer.isView(value) ? new Uint8Array(value.buffer, value.byteOffset, value.byteLength) : null;
  if (!bytes) throw new TypeError("SHA-256 input must be text or bytes");
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return `sha256:${[...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

function newSessionToken() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function sameOriginPost(request) {
  return request.headers.get("origin") === REPORTS_ORIGIN && request.headers.get("sec-fetch-site") === "same-origin";
}

function equalStrings(left, right) {
  if (typeof left !== "string" || typeof right !== "string" || left.length !== right.length) return false;
  let mismatch = 0;
  for (let index = 0; index < left.length; index += 1) mismatch |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return mismatch === 0;
}

function isJsonContentType(value) {
  return /^application\/json(?:\s*;|$)/i.test(value || "");
}

const OPAQUE_PROPERTY_REF = /^property:public:[A-Za-z0-9_-]{16,128}$/;
const IDEMPOTENCY_KEY = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const REACTIONS = new Set(["interested", "discuss", "remove"]);
const SHARE_BEARER = /^[A-Za-z0-9_-]{43}$/;

function isPlainRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
}

function hasUnsafeControlCharacters(value) {
  return /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(value);
}

function validMutationBody(body, dependencyName) {
  const expected = dependencyName === "commentShareFn" ? ["body", "idempotency_key", "property_ref"] : ["idempotency_key", "property_ref", "reaction"];
  if (Object.keys(body).sort().join(",") !== expected.join(",")) return false;
  if (typeof body.property_ref !== "string" || !OPAQUE_PROPERTY_REF.test(body.property_ref) ||
      typeof body.idempotency_key !== "string" || !IDEMPOTENCY_KEY.test(body.idempotency_key)) return false;
  if (dependencyName === "commentShareFn") return typeof body.body === "string" && body.body.trim().length > 0 && body.body.length <= 4000 && !hasUnsafeControlCharacters(body.body);
  return typeof body.reaction === "string" && REACTIONS.has(body.reaction);
}

async function jsonBody(request) {
  if (!isJsonContentType(request.headers.get("content-type"))) return { error: json({ error: "unsupported_media_type" }, 415) };
  const declaredLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && (declaredLength < 0 || declaredLength > MAX_BODY_BYTES)) return { error: json({ error: "payload_too_large" }, 413) };
  const raw = await request.clone().text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) return { error: json({ error: "payload_too_large" }, 413) };
  try {
    const value = JSON.parse(raw);
    return isPlainRecord(value) ? { value } : { error: json({ error: "invalid_request" }, 400) };
  } catch {
    return { error: json({ error: "invalid_json" }, 400) };
  }
}

function methodNotAllowed(allow) {
  return json({ error: "method_not_allowed" }, 405, { allow });
}

function dependencyFailure(result) {
  const status = result?.status === 401 || result?.status === 403 || result?.status === 404 ? result.status : 503;
  return json({ error: status === 401 ? "unauthorized" : status === 403 ? "forbidden" : status === 404 ? "not_found" : "share_unavailable" }, status);
}

function publicResponse(result) {
  // Dependencies return the already-redacted projection only in `data`; the
  // session and the original share bearer are never a response shape here.
  return json({ data: result.data, csrf_token: result.csrfToken });
}

async function staticAsset(env, request, pathname) {
  if (!env?.ASSETS?.fetch) return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  url.pathname = pathname;
  url.search = "";
  const response = await env.ASSETS.fetch(new Request(url, { method: "GET" }));
  if (response.status === 404) return json({ error: "not_found" }, 404);
  const headers = new Headers(response.headers);
  headers.set("cache-control", "no-store");
  if (pathname.endsWith(".html")) headers.set("content-type", "text/html; charset=utf-8");
  if (pathname.endsWith(".js")) headers.set("content-type", "application/javascript; charset=utf-8");
  if (pathname.endsWith(".css")) headers.set("content-type", "text/css; charset=utf-8");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function exchange(request, env, dependencies) {
  if (!sameOriginPost(request)) return json({ error: "forbidden" }, 403);
  const body = await jsonBody(request);
  if (body.error) return body.error;
  if (Object.keys(body.value).length !== 1 || typeof body.value.token !== "string" || !SHARE_BEARER.test(body.value.token)) {
    return json({ error: "invalid_share" }, 400);
  }
  if (typeof dependencies.exchangeShareTokenFn !== "function") return json({ error: "not_found" }, 404);
  const session = newSessionToken();
  const tokenDigest = await sha256Digest(body.value.token);
  const sessionDigest = await sha256Digest(session);
  const now = Number(dependencies.now());
  const sessionExpiresAt = new Date((Number.isFinite(now) ? now : Date.now()) + SESSION_TTL_MS).toISOString();
  const auditDigest = await sha256Digest(`tour-share-exchange\n${tokenDigest}\n${sessionDigest}\n${sessionExpiresAt}`);
  let result;
  try {
    result = await dependencies.exchangeShareTokenFn({ env, tokenDigest, sessionDigest, sessionExpiresAt, auditDigest });
  } catch { return json({ error: "share_unavailable" }, 503); }
  if (!result?.ok) return dependencyFailure(result);
  // Deliberately acknowledge only the exchange.  Never return or log the
  // bearer, session, grant, or the dependency's internal result.
  return json({ ok: true }, 200, { "set-cookie": sessionCookie(session) });
}

async function read(request, env, dependencies) {
  const session = cookieValue(request, SESSION_COOKIE);
  if (!session) return json({ error: "unauthorized" }, 401);
  if (typeof dependencies.readShareFn !== "function") return json({ error: "not_found" }, 404);
  let result;
  try { result = await dependencies.readShareFn({ env, sessionDigest: await sha256Digest(session) }); }
  catch { return json({ error: "share_unavailable" }, 503); }
  if (!result?.ok || !isPlainRecord(result.data) || typeof result.csrfToken !== "string" || !result.csrfToken || result.csrfToken.length > 256) return dependencyFailure(result);
  return publicResponse(result);
}

async function downloadPdf(request, env, dependencies) {
  const session = cookieValue(request, SESSION_COOKIE);
  if (!session) return json({ error: "unauthorized" }, 401);
  if (typeof dependencies.readPdfFn !== "function") return json({ error: "not_found" }, 404);
  let result;
  try { result = await dependencies.readPdfFn({ env, sessionDigest: await sha256Digest(session) }); }
  catch { return json({ error: "share_unavailable" }, 503); }
  if (!result?.ok || typeof result.artifactDigest !== "string" || !/^sha256:[0-9a-f]{64}$/.test(result.artifactDigest)) return dependencyFailure(result);
  let bytes;
  if (result.body instanceof ArrayBuffer) bytes = new Uint8Array(result.body);
  else if (ArrayBuffer.isView(result.body)) bytes = new Uint8Array(result.body.buffer, result.body.byteOffset, result.body.byteLength);
  else return json({ error: "share_unavailable" }, 503);
  if (bytes.byteLength < 8 || bytes.byteLength > MAX_PDF_BYTES || new TextDecoder().decode(bytes.subarray(0, 5)) !== "%PDF-") return json({ error: "share_unavailable" }, 503);
  if (!equalStrings(await sha256Digest(bytes), result.artifactDigest)) return json({ error: "share_unavailable" }, 503);
  return new Response(bytes, { headers: {
    "content-type": "application/pdf", "content-length": String(bytes.byteLength),
    "content-disposition": 'attachment; filename="CARR-Tour-Packet.pdf"', "cache-control": "no-store",
  } });
}

async function mutation(request, env, dependencies, dependencyName) {
  if (!sameOriginPost(request)) return json({ error: "forbidden" }, 403);
  const session = cookieValue(request, SESSION_COOKIE);
  if (!session) return json({ error: "unauthorized" }, 401);
  const body = await jsonBody(request);
  if (body.error) return body.error;
  // A bearer has exactly one permitted ingress: the exchange endpoint.  Do
  // not allow a token-shaped field to drift into comments or reactions.
  if (Object.prototype.hasOwnProperty.call(body.value, "token") || !validMutationBody(body.value, dependencyName)) return json({ error: "invalid_request" }, 400);
  if (typeof dependencies.readShareFn !== "function" || typeof dependencies[dependencyName] !== "function") return json({ error: "not_found" }, 404);
  const sessionDigest = await sha256Digest(session);
  let state;
  try { state = await dependencies.readShareFn({ env, sessionDigest, csrfOnly: true }); }
  catch { return json({ error: "share_unavailable" }, 503); }
  if (!state?.ok || typeof state.csrfToken !== "string" || !equalStrings(request.headers.get("x-tour-share-csrf"), state.csrfToken)) return json({ error: "forbidden" }, 403);
  let result;
  try { result = await dependencies[dependencyName]({ env, sessionDigest, body: body.value }); }
  catch { return json({ error: "share_unavailable" }, 503); }
  if (!result?.ok || !isPlainRecord(result.data)) return dependencyFailure(result);
  return json({ data: result.data });
}

function withSecurityHeaders(response) {
  const headers = new Headers(response.headers);
  // An injected asset implementation must not accidentally widen this public
  // surface. No CORS is emitted, including on errors and method refusals.
  for (const name of [...headers.keys()]) if (name.startsWith("access-control-")) headers.delete(name);
  headers.set("cache-control", "no-store");
  headers.set("content-security-policy", REPORTS_CSP);
  headers.set("strict-transport-security", "max-age=31536000; includeSubDomains");
  headers.set("x-content-type-options", "nosniff");
  headers.set("x-frame-options", "DENY");
  headers.set("referrer-policy", "no-referrer");
  headers.set("permissions-policy", "camera=(), geolocation=(), microphone=(), payment=(), usb=()");
  headers.set("cross-origin-opener-policy", "same-origin");
  headers.set("cross-origin-resource-policy", "same-origin");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function handleRequest(request, env, dependencies) {
  if (!reportsRequest(request)) return json({ error: "not_found" }, 404);
  const pathname = new URL(request.url).pathname;
  const asset = STATIC_ASSETS.get(pathname);
  if (asset) return request.method === "GET" ? staticAsset(env, request, asset) : methodNotAllowed("GET");
  const method = API_METHODS.get(pathname);
  if (!method) return json({ error: "not_found" }, 404);
  if (request.method !== method) return methodNotAllowed(method);
  if (pathname === "/api/share/exchange") return exchange(request, env, dependencies);
  if (pathname === "/api/share/report") return read(request, env, dependencies);
  if (pathname === "/api/share/pdf") return downloadPdf(request, env, dependencies);
  return mutation(request, env, dependencies, pathname.endsWith("/comment") ? "commentShareFn" : "reactionShareFn");
}

/** Injectable seams keep this browser gate independent from Worker, DB, and MCP wiring. */
export function createReportsWebHandler(overrides = {}) {
  const dependencies = { now: () => Date.now(), ...overrides };
  return { fetch: async (request, env, _ctx) => withSecurityHeaders(await handleRequest(request, env, dependencies)) };
}

export function isReportsRequest(request) {
  if (!reportsRequest(request)) return false;
  const pathname = new URL(request.url).pathname;
  return STATIC_ASSETS.has(pathname) || API_METHODS.has(pathname);
}
