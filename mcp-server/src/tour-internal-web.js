// Authenticated internal Tour Operations browser adapter.  This is purposely
// an injectable leaf beneath Deal Room's established cookie/session + CSRF
// gate: it owns no identity, authority selection, database, routing, or map
// implementation.

export const TOUR_INTERNAL_ASSET_DIRECTORY = "../dealroom/tours";

const MAX_BODY_BYTES = 32 * 1024;
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const SHARE_SCOPES = new Set(["view_packet", "view_map"]);
const AUTHORITY_FIELDS = new Set(["actor", "actor_id", "tenant", "tenant_id", "organization_tenant_id", "authorization", "authorization_class", "identity", "reviewer", "sponsor", "human_slug"]);

const STATIC = new Map([
  ["/tours", "/tours/index.html"],
  ["/tours/app.js", "/tours/app.js"],
  ["/tours/app.css", "/tours/app.css"],
]);
const METHODS = new Map([
  ["/api/tours/library", "GET"],
  ["/api/tours/detail", "GET"],
  ["/api/tours/route-version", "POST"],
  ["/api/tours/route-reorder", "POST"],
  ["/api/tours/route-accept", "POST"],
  ["/api/tours/cheat-sheet/autosave", "POST"],
  ["/api/tours/cheat-sheet/restore", "POST"],
  ["/api/tours/projection", "POST"],
  ["/api/tours/projection/candidates", "GET"],
  ["/api/tours/projection/seal", "POST"],
  ["/api/tours/share/issue", "POST"],
  ["/api/tours/share/rotate", "POST"],
  ["/api/tours/share/revoke", "POST"],
  ["/api/tours/pdf/render", "POST"],
  ["/api/tours/pdf/status", "GET"],
  ["/api/tours/pdf/review", "POST"],
  ["/api/tours/pdf/preview", "GET"],
  ["/api/tours/pdf/download", "GET"],
]);

function json(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers } });
}

function methodNotAllowed(allow) { return json({ error: "method_not_allowed" }, 405, { allow }); }
function plain(value) { return Boolean(value) && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype; }
function validId(value) { return typeof value === "string" && ID.test(value); }
function validDigest(value) { return typeof value === "string" && DIGEST.test(value); }
function hasAuthoritySelector(value) { return plain(value) && Object.keys(value).some((key) => AUTHORITY_FIELDS.has(key)); }

function sameOriginPost(request, origin) {
  return request.headers.get("origin") === origin && request.headers.get("sec-fetch-site") === "same-origin";
}
function equal(left, right) {
  if (typeof left !== "string" || typeof right !== "string" || left.length !== right.length) return false;
  let mismatch = 0;
  for (let index = 0; index < left.length; index += 1) mismatch |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return mismatch === 0;
}
function originFor(request, env) {
  const host = env?.APP_HOST || env?.DEALROOM_HOST;
  if (typeof host !== "string" || !/^(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/i.test(host)) return null;
  const origin = `https://${host}`;
  try { return new URL(request.url).origin === origin ? origin : null; } catch { return null; }
}
function actorSession(actor, session) {
  // Deal Room verified actors are keyed by slug; integrations may supply a durable id.
  const actorKey = actor && (actor.id || actor.slug);
  return Boolean(typeof actorKey === "string" && actorKey && session && typeof session.csrfToken === "string" && session.csrfToken);
}

async function body(request) {
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get("content-type") || "")) return { error: json({ error: "unsupported_media_type" }, 415) };
  const length = Number(request.headers.get("content-length"));
  if (Number.isFinite(length) && (length < 0 || length > MAX_BODY_BYTES)) return { error: json({ error: "payload_too_large" }, 413) };
  const raw = await request.clone().text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) return { error: json({ error: "payload_too_large" }, 413) };
  try {
    const value = JSON.parse(raw);
    return plain(value) ? { value } : { error: json({ error: "invalid_request" }, 400) };
  } catch { return { error: json({ error: "invalid_json" }, 400) }; }
}

function exact(value, keys) {
  if (!plain(value) || hasAuthoritySelector(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}
function ids(value, min = 1, max = 100) {
  return Array.isArray(value) && value.length >= min && value.length <= max && new Set(value).size === value.length && value.every(validId);
}
function scopes(value) {
  return Array.isArray(value) && value.length > 0 && value.length <= SHARE_SCOPES.size && new Set(value).size === value.length && value.every((scope) => SHARE_SCOPES.has(scope));
}
function iso(value) {
  if (typeof value !== "string" || value.length > 64 || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/.test(value)) return false;
  const parsed = Date.parse(value);
  const expected = /\.\d{3}Z$/.test(value) ? value : value.replace(/Z$/, ".000Z");
  return Number.isFinite(parsed) && new Date(parsed).toISOString() === expected;
}
function validContent(value) {
  try { return plain(value) && Object.keys(value).length <= 64 && new TextEncoder().encode(JSON.stringify(value)).byteLength <= 24 * 1024; }
  catch { return false; }
}

const VALID = {
  "/api/tours/route-version": (v) => exact(v, ["tour_id", "expected_route_version", "stop_ids", "idempotency_key"]) && validId(v.tour_id) && Number.isInteger(v.expected_route_version) && v.expected_route_version >= 0 && ids(v.stop_ids, 1) && validId(v.idempotency_key),
  "/api/tours/route-reorder": (v) => exact(v, ["tour_id", "route_version_id", "expected_route_version", "stop_ids", "idempotency_key"]) && validId(v.tour_id) && validId(v.route_version_id) && Number.isInteger(v.expected_route_version) && v.expected_route_version >= 0 && ids(v.stop_ids, 1) && validId(v.idempotency_key),
  "/api/tours/route-accept": (v) => exact(v, ["route_version_id", "expected_prior_route_version", "acceptance_digest", "idempotency_key"]) && validId(v.route_version_id) && Number.isInteger(v.expected_prior_route_version) && v.expected_prior_route_version >= 0 && validDigest(v.acceptance_digest) && validId(v.idempotency_key),
  "/api/tours/cheat-sheet/autosave": (v) => exact(v, ["tour_id", "content", "expected_revision_number", "idempotency_key"]) && validId(v.tour_id) && validContent(v.content) && Number.isInteger(v.expected_revision_number) && v.expected_revision_number >= 0 && validId(v.idempotency_key),
  "/api/tours/cheat-sheet/restore": (v) => exact(v, ["tour_id", "restore_revision_id", "expected_revision_number", "idempotency_key"]) && validId(v.tour_id) && validId(v.restore_revision_id) && Number.isInteger(v.expected_revision_number) && v.expected_revision_number >= 0 && validId(v.idempotency_key),
  "/api/tours/projection": (v) => exact(v, ["tour_id", "route_version_id", "as_of", "idempotency_key"]) && validId(v.tour_id) && validId(v.route_version_id) && iso(v.as_of) && validId(v.idempotency_key),
  "/api/tours/projection/seal": (v) => exact(v, ["projection_id", "candidate_digest", "receipt_digest", "idempotency_key"]) && validId(v.projection_id) && validDigest(v.candidate_digest) && validDigest(v.receipt_digest) && validId(v.idempotency_key),
  "/api/tours/share/issue": (v) => exact(v, ["projection_id", "token_digest", "permission_scopes", "expires_at", "receipt_digest", "idempotency_key"]) && validId(v.projection_id) && validDigest(v.token_digest) && scopes(v.permission_scopes) && iso(v.expires_at) && validDigest(v.receipt_digest) && validId(v.idempotency_key),
  "/api/tours/share/rotate": (v) => exact(v, ["share_grant_id", "projection_id", "token_digest", "permission_scopes", "expires_at", "receipt_digest", "idempotency_key"]) && validId(v.share_grant_id) && validId(v.projection_id) && validDigest(v.token_digest) && scopes(v.permission_scopes) && iso(v.expires_at) && validDigest(v.receipt_digest) && validId(v.idempotency_key),
  "/api/tours/share/revoke": (v) => exact(v, ["share_grant_id", "reason", "revoked_at", "receipt_digest", "idempotency_key"]) && validId(v.share_grant_id) && typeof v.reason === "string" && v.reason.trim().length > 0 && v.reason.length <= 240 && iso(v.revoked_at) && validDigest(v.receipt_digest) && validId(v.idempotency_key),
  "/api/tours/pdf/render": (v) => exact(v, ["projection_id", "idempotency_key"]) && validId(v.projection_id) && validId(v.idempotency_key),
  "/api/tours/pdf/review": (v) => exact(v, ["render_job_id", "qc_run_digest", "decision", "reviewed_at", "review_receipt_digest", "reason", "idempotency_key"]) && validId(v.render_job_id) && validDigest(v.qc_run_digest) && ["accept", "reject"].includes(v.decision) && iso(v.reviewed_at) && validDigest(v.review_receipt_digest) && typeof v.reason === "string" && v.reason.trim().length > 0 && v.reason.length <= 500 && validId(v.idempotency_key),
};

const SEAMS = {
  "/api/tours/library": "listToursFn",
  "/api/tours/detail": "readTourFn",
  "/api/tours/route-version": "createRouteVersionFn",
  "/api/tours/route-reorder": "reorderRouteStopsFn",
  "/api/tours/route-accept": "acceptRouteVersionFn",
  "/api/tours/cheat-sheet/autosave": "autosaveCheatSheetFn",
  "/api/tours/cheat-sheet/restore": "restoreCheatSheetFn",
  "/api/tours/projection": "createProjectionFn",
  "/api/tours/projection/candidates": "readProjectionCandidatesFn",
  "/api/tours/projection/seal": "sealProjectionFn",
  "/api/tours/share/issue": "issueShareGrantFn",
  "/api/tours/share/rotate": "rotateShareGrantFn",
  "/api/tours/share/revoke": "revokeShareGrantFn",
  "/api/tours/pdf/render": "renderPdfFn",
  "/api/tours/pdf/status": "readPdfRenderFn",
  "/api/tours/pdf/review": "reviewPdfFn",
  "/api/tours/pdf/preview": "previewPdfFn",
  "/api/tours/pdf/download": "downloadPdfFn",
};

function safeFailure(result) {
  const status = [403, 404, 409].includes(result?.status) ? result.status : 503;
  return json({ error: status === 403 ? "forbidden" : status === 404 ? "not_found" : status === 409 ? "conflict" : "tour_unavailable" }, status);
}
const CONFLICT_MESSAGES = new Set([
  "tour route preparation refuses stale state",
  "route version refuses concurrent or stale route state",
  "route acceptance refuses concurrent or stale route state",
  "cheat sheet revision refuses concurrent or stale version",
  "cheat sheet restore refuses unavailable or stale revision",
  "tour selection refuses stale version",
]);
function dependencyFailure(error) {
  const code = typeof error?.payload?.error === "string" ? error.payload.error : "";
  const message = typeof error?.message === "string" ? error.message : "";
  return { status: code === "version_conflict" || CONFLICT_MESSAGES.has(message) ? 409 : 503 };
}
async function staticAsset(env, request, pathname) {
  if (!env?.ASSETS?.fetch) return json({ error: "not_found" }, 404);
  const url = new URL(request.url); url.pathname = pathname; url.search = "";
  const response = await env.ASSETS.fetch(new Request(url, { method: "GET" }));
  if (response.status === 404) return json({ error: "not_found" }, 404);
  const headers = new Headers(response.headers);
  headers.set("cache-control", "no-store");
  if (pathname.endsWith(".html")) headers.set("content-type", "text/html; charset=utf-8");
  if (pathname.endsWith(".js")) headers.set("content-type", "application/javascript; charset=utf-8");
  if (pathname.endsWith(".css")) headers.set("content-type", "text/css; charset=utf-8");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function api(request, env, ctx, actor, session, dependencies, pathname) {
  const origin = originFor(request, env);
  if (!origin || !actorSession(actor, session)) return json({ error: "unauthorized" }, 401);
  const seamName = SEAMS[pathname];
  if (typeof dependencies[seamName] !== "function") return json({ error: "not_found" }, 404);
  let input = null;
  if (request.method === "GET") {
    const url = new URL(request.url);
    if (pathname === "/api/tours/detail") {
      if ([...url.searchParams.keys()].length !== 1 || !validId(url.searchParams.get("tour_id"))) return json({ error: "invalid_request" }, 400);
      input = { tour_id: url.searchParams.get("tour_id") };
    } else if (pathname === "/api/tours/projection/candidates") {
      if ([...url.searchParams.keys()].length !== 1 || !validId(url.searchParams.get("projection_id"))) return json({ error: "invalid_request" }, 400);
      input = { projection_id: url.searchParams.get("projection_id") };
    } else if (pathname === "/api/tours/pdf/status" || pathname === "/api/tours/pdf/preview" || pathname === "/api/tours/pdf/download") {
      if ([...url.searchParams.keys()].length !== 1 || !validId(url.searchParams.get("render_job_id"))) return json({ error: "invalid_request" }, 400);
      input = { render_job_id: url.searchParams.get("render_job_id") };
    } else if ([...url.searchParams.keys()].length) return json({ error: "invalid_request" }, 400);
  } else {
    if (!sameOriginPost(request, origin) || !equal(request.headers.get("x-carr-csrf"), session.csrfToken)) return json({ error: "forbidden" }, 403);
    const parsed = await body(request);
    if (parsed.error) return parsed.error;
    if (!VALID[pathname](parsed.value)) return json({ error: "invalid_request" }, 400);
    input = parsed.value;
  }
  try {
    const result = await dependencies[seamName]({ env, ctx, actor, input });
    if ((pathname === "/api/tours/pdf/preview" || pathname === "/api/tours/pdf/download") && result?.ok && result.response instanceof Response) return result.response;
    if (!result?.ok || !plain(result.data)) return safeFailure(result);
    return json({ data: result.data, csrf_token: request.method === "GET" ? session.csrfToken : undefined });
  } catch (error) { return safeFailure(dependencyFailure(error)); }
}

function withSecurityHeaders(response) {
  const headers = new Headers(response.headers);
  headers.set("cache-control", "no-store");
  headers.set("content-security-policy", "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; worker-src 'none'");
  headers.set("strict-transport-security", "max-age=31536000; includeSubDomains");
  headers.set("permissions-policy", "camera=(), geolocation=(), microphone=(), payment=(), usb=()");
  headers.set("x-content-type-options", "nosniff"); headers.set("x-frame-options", "DENY"); headers.set("referrer-policy", "no-referrer");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

/** Create the bounded Tour leaf. The caller supplies verified actor/session context. */
export function createTourInternalWebHandler(overrides = {}) {
  return { fetch: async (request, env, _ctx, actor, session) => {
    let url; try { url = new URL(request.url); } catch { return withSecurityHeaders(json({ error: "not_found" }, 404)); }
    const asset = STATIC.get(url.pathname);
    if (asset) {
      if (!originFor(request, env) || !actorSession(actor, session)) return withSecurityHeaders(json({ error: "unauthorized" }, 401));
      return withSecurityHeaders(request.method === "GET" ? await staticAsset(env, request, asset) : methodNotAllowed("GET"));
    }
    const method = METHODS.get(url.pathname);
    if (!method) return withSecurityHeaders(json({ error: "not_found" }, 404));
    if (request.method !== method) return withSecurityHeaders(methodNotAllowed(method));
    return withSecurityHeaders(await api(request, env, _ctx, actor, session, overrides, url.pathname));
  } };
}

export function isTourInternalRequest(request) {
  try { const path = new URL(request.url).pathname; return STATIC.has(path) || METHODS.has(path); }
  catch { return false; }
}
