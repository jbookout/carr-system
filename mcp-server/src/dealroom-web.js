// Browser entrypoint for the configured production or staging Deal Room host.
//
// Google proves identity; identity.js reduces it to one of the two partner
// actors; an opaque, server-side session lets the installed PWA reuse that
// actor for the existing MCP and pipeline handlers. No identity or deal data
// is stored in the cookie.

import {
  exchangeGoogleCode,
  googleAuthorizationUrl,
  randomString,
  verifyGoogleIdToken,
} from "./google-oidc.js";
import { actorFromProps, propsForSlug, slugForEmail } from "./identity.js";
import { neon } from "@neondatabase/serverless";
import { sessionForDealRoomCookie } from "./session.js";
import { redeemProgram6BrowserChallenge } from "./program6-browser-challenge.js";
import { program6ActionsEnabled } from "./program6-feature-flag.js";

export const DEALROOM_ASSET_DIRECTORY = "../dealroom"; // mirrors wrangler.toml [assets]

const SESSION_COOKIE = "__Host-dealroom_session";
const PENDING_COOKIE = "__Host-dealroom_oauth";
const PENDING_PREFIX = "dealroom_pending:";
const SESSION_PREFIX = "dealroom_session:";
const ACTION_CHALLENGE_PREFIX = "dealroom_action_challenge:";
const PENDING_TTL = 600;
const SESSION_IDLE_TTL = 12 * 60 * 60;
const SESSION_ABSOLUTE_TTL = 7 * 24 * 60 * 60;
const SESSION_REFRESH_WINDOW = 60 * 60;
const REAUTH_TTL = 10 * 60;
const ACTION_CHALLENGE_TTL = 5 * 60;
const SYSTEM_WORK_MAX_BODY = 16 * 1024;
const SYSTEM_WORK_PREFIX = "/api/system-work";
const APPROVAL_ACTIONS = new Map([
  ["accept-ready-plan", "plan_hash"],
  ["accept-outcome-feedback", "feedback_hash"],
]);

const JSON_HEADERS = { "content-type": "application/json", "cache-control": "no-store" };
const PUBLIC_SHELL = new Map([
  ["/manifest.webmanifest", "/public-shell/manifest.webmanifest"],
  ["/sw.js", "/public-shell/sw.js"],
  ["/offline.html", "/public-shell/offline.html"],
]);
const DEALROOM_HOST_PATTERN = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const DEALROOM_EXACT_PATHS = new Set([
  "/", "/index.html", "/system-work.html",
  "/manifest.webmanifest", "/sw.js", "/offline.html",
]);
const DEALROOM_PATH_PREFIXES = ["/auth/", "/api/system-work/", "/css/", "/js/", "/data/", "/icons/"];

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function redirect(location, cookies = []) {
  const headers = new Headers({ location, "cache-control": "no-store" });
  for (const cookie of cookies) headers.append("set-cookie", cookie);
  return new Response(null, { status: 302, headers });
}

function cookieValue(request, name) {
  const header = request.headers.get("cookie") || "";
  for (const part of header.split(";")) {
    const index = part.indexOf("=");
    if (index < 0) continue;
    if (part.slice(0, index).trim() === name) return part.slice(index + 1).trim();
  }
  return null;
}

function sessionCookie(token, maxAge) {
  return `${SESSION_COOKIE}=${token}; Path=/; Max-Age=${maxAge}; Secure; HttpOnly; SameSite=Lax`;
}

function pendingCookie(state, maxAge = PENDING_TTL) {
  return `${PENDING_COOKIE}=${state}; Path=/; Max-Age=${maxAge}; Secure; HttpOnly; SameSite=Lax`;
}

function clearCookie(name) {
  return `${name}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax`;
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

// The host is a reviewed Worker environment setting, never a request Host
// header.  This keeps OAuth callbacks, browser redirects, and CSRF checks on
// the exact deployment that owns the session.  A missing or malformed setting
// deliberately closes the Deal Room route rather than falling back to prod.
function dealroomOrigin(env) {
  const host = env?.DEALROOM_HOST;
  if (typeof host !== "string" || !DEALROOM_HOST_PATTERN.test(host)) return null;
  return `https://${host}`;
}

function requestMatchesDealroomOrigin(request, origin) {
  try {
    return Boolean(origin) && new URL(request.url).origin === origin;
  } catch {
    return false;
  }
}

function callbackUri(origin) {
  return `${origin}/auth/callback`;
}

function safeReturnTo(value, origin) {
  if (!value) return "/";
  try {
    const parsed = new URL(value, origin);
    if (parsed.origin !== origin) return "/";
    return parsed.pathname + parsed.search + parsed.hash;
  } catch {
    return "/";
  }
}

function withHeaders(response, additions, status = response.status) {
  const headers = new Headers(response.headers);
  for (const [name, value] of Object.entries(additions)) headers.set(name, value);
  return new Response(response.body, { status, statusText: response.statusText, headers });
}

function withSecurityHeaders(response) {
  const headers = new Headers(response.headers);
  // Deal Room is a static asset bundle.  The public shell uses external CSS
  // and JavaScript too, so neither scripts nor styles need unsafe-inline.
  headers.set("content-security-policy", [
    "default-src 'self'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "script-src 'self'",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self' http://127.0.0.1:4682",
    "worker-src 'self'",
    "manifest-src 'self'",
  ].join("; "));
  headers.set("strict-transport-security", "max-age=31536000; includeSubDomains");
  headers.set("x-content-type-options", "nosniff");
  headers.set("x-frame-options", "DENY");
  headers.set("referrer-policy", "no-referrer");
  headers.set("permissions-policy", "camera=(), geolocation=(), microphone=(), payment=(), usb=()");
  headers.set("cross-origin-opener-policy", "same-origin");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function asset(env, request, pathname) {
  if (!env.ASSETS?.fetch) return null;
  const url = new URL(request.url);
  url.pathname = pathname;
  url.search = "";
  const response = await env.ASSETS.fetch(new Request(url, { method: "GET", headers: request.headers }));
  return response.status === 404 ? null : response;
}

async function publicShellAsset(env, request, pathname, status) {
  const mapped = pathname.startsWith("/icons/") ? `/public-shell${pathname}` : PUBLIC_SHELL.get(pathname);
  if (!mapped) return null;
  const response = await asset(env, request, mapped);
  if (!response) return null;
  const headers = { "cache-control": pathname === "/sw.js" ? "no-cache" : "public, max-age=3600" };
  if (pathname === "/sw.js") {
    headers["content-type"] = "application/javascript; charset=utf-8";
    headers["service-worker-allowed"] = "/";
  } else if (pathname === "/manifest.webmanifest") {
    headers["content-type"] = "application/manifest+json; charset=utf-8";
  } else if (pathname.endsWith(".png")) {
    headers["content-type"] = "image/png";
  }
  return withHeaders(response, headers, status ?? response.status);
}

async function refusal(env, request) {
  const response = await asset(env, request, "/public-shell/refusal.html");
  if (response) return withHeaders(response, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" }, 403);
  return new Response("<!doctype html><title>Not authorized</title><h1>Not authorized</h1><p>This Deal Room is limited to the two CARR partner accounts.</p>",
    { status: 403, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}

async function startLogin(request, env, pending = {}) {
  if (!env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET || !env.OAUTH_KV) {
    return new Response("Sign-in is not configured.", { status: 503, headers: { "cache-control": "no-store" } });
  }
  const origin = dealroomOrigin(env);
  if (!origin) return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  const state = randomString(24);
  const verifier = randomString(48);
  await env.OAUTH_KV.put(PENDING_PREFIX + state, JSON.stringify({
    verifier,
    returnTo: safeReturnTo(url.searchParams.get("return_to"), origin),
    ...pending,
  }), { expirationTtl: PENDING_TTL });
  const google = await googleAuthorizationUrl({ clientId: env.GOOGLE_CLIENT_ID,
    redirectUri: callbackUri(origin), state, verifier });
  return redirect(google.toString(), [pendingCookie(state)]);
}

async function completeLogin(request, env, dependencies) {
  const origin = dealroomOrigin(env);
  if (!origin) return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  const state = url.searchParams.get("state");
  const code = url.searchParams.get("code");
  const stateCookie = cookieValue(request, PENDING_COOKIE);
  const finish = (response) => {
    const headers = new Headers(response.headers);
    headers.append("set-cookie", clearCookie(PENDING_COOKIE));
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  };

  if (!state || !code || !stateCookie || stateCookie !== state) {
    return finish(new Response("That sign-in request is invalid or expired.",
      { status: 400, headers: { "cache-control": "no-store" } }));
  }
  const pendingKey = PENDING_PREFIX + state;
  const pending = await env.OAUTH_KV.get(pendingKey, { type: "json" });
  await env.OAUTH_KV.delete(pendingKey);
  if (!pending?.verifier) {
    return finish(new Response("That sign-in request is invalid or expired.",
      { status: 400, headers: { "cache-control": "no-store" } }));
  }

  let tokenResponse;
  let claims;
  try {
    tokenResponse = await dependencies.exchangeGoogleCodeFn({ code,
      clientId: env.GOOGLE_CLIENT_ID, clientSecret: env.GOOGLE_CLIENT_SECRET,
      redirectUri: callbackUri(origin), verifier: pending.verifier });
    claims = await dependencies.verifyGoogleIdTokenFn(tokenResponse.id_token, env.GOOGLE_CLIENT_ID, env);
  } catch {
    return finish(new Response("Google identity could not be verified.",
      { status: 401, headers: { "cache-control": "no-store" } }));
  }

  if (claims.email_verified !== true && claims.email_verified !== "true") return finish(await refusal(env, request));
  const slug = dependencies.slugForEmailFn(claims.email);
  if (!slug) return finish(await refusal(env, request));

  const now = dependencies.now();
  const opaque = randomString(32);
  const sessionKey = SESSION_PREFIX + await sha256(opaque);
  const props = dependencies.propsForSlugFn(slug, { email: claims.email, sub: claims.sub,
    via: "dealroom-cookie", client_id: "dealroom-pwa" });
  if (pending.purpose === "reauth") {
    const current = await sessionFor(request, env, dependencies);
    if (!current || current.key !== pending.sessionKey || current.actor?.slug !== slug) {
      return finish(new Response("That reauthentication request is invalid or expired.",
        { status: 400, headers: { "cache-control": "no-store" } }));
    }
    current.session.reauthAt = now;
    await env.OAUTH_KV.put(current.key, JSON.stringify(current.session), {
      expirationTtl: Math.max(1, Math.floor((current.session.expiresAt - now) / 1000)),
    });
    return finish(redirect(`${origin}${safeReturnTo(pending.returnTo, origin)}`));
  }

  await env.OAUTH_KV.put(sessionKey, JSON.stringify({
    props,
    createdAt: now,
    expiresAt: now + SESSION_IDLE_TTL * 1000,
    csrfToken: randomString(32),
    reauthAt: now,
  }),
    { expirationTtl: SESSION_IDLE_TTL });
  return finish(redirect(`${origin}${safeReturnTo(pending.returnTo, origin)}`,
    [sessionCookie(opaque, SESSION_IDLE_TTL)]));
}

async function sessionFor(request, env, dependencies) {
  const opaque = cookieValue(request, SESSION_COOKIE);
  if (!opaque || !env.OAUTH_KV) return null;
  const key = SESSION_PREFIX + await sha256(opaque);
  const session = await env.OAUTH_KV.get(key, { type: "json" });
  if (!session) return null;
  const now = dependencies.now();
  const actor = dependencies.actorFromPropsFn(session.props);
  const absoluteEnd = Number(session.createdAt) + SESSION_ABSOLUTE_TTL * 1000;
  if (!actor || !Number.isFinite(absoluteEnd) || session.expiresAt <= now || absoluteEnd <= now) {
    await env.OAUTH_KV.delete(key);
    return null;
  }

  let changed = false;

  // THE SECOND DOOR THAT MINTS (migrations 0208/0209/0210). This cookie has all
  // three properties a session identity needs — an issuance instant, an enforced
  // expiry under a hard seven-day ceiling, and a real revocation on sign-out —
  // which is exactly what the bearer doors lack and why they stay on the legacy
  // path with a null link.
  //
  // Minting HERE covers every authenticated Deal Room surface at once, including
  // the Program 6 posts that write evidence, because they all pass through this
  // function. The session rides on the actor the same way the OAuth door's does,
  // so the write path picks it up through auditIdentity with no verb change.
  //
  // A null result is normal: the request proceeds and records legacy evidence.
  // It is reported rather than silent, because a door that has quietly stopped
  // qualifying is indistinguishable from one nobody used.
  const minted = await sessionForDealRoomCookie({
    record: session,
    actor,
    absoluteEndMs: absoluteEnd,
    now,
    mintFn: env.SESSION_MINT_FN
      || (env.DATABASE_URL_SESSION_ISSUER
        ? (text, params) => neon(env.DATABASE_URL_SESSION_ISSUER).query(text, params)
        : null),
    onFailure: dependencies.onSessionMintFailure || (() => {}),
  });
  if (minted.sessionId) {
    actor.application_session_id = minted.sessionId;
    if (minted.changed) {
      // Persisted through the SAME put the refresh path below already makes.
      session.application_session_id = minted.sessionId;
      changed = true;
    }
  }

  // Sessions issued before the browser-action boundary was introduced have no
  // synchronizer token.  Mint it server-side on first use; it never enters the
  // cookie and a legacy session is not magically treated as freshly reauthed.
  if (!session.csrfToken) {
    session.csrfToken = randomString(32);
    changed = true;
  }

  let refreshCookie = null;
  if (session.expiresAt - now <= SESSION_REFRESH_WINDOW * 1000) {
    const seconds = Math.min(SESSION_IDLE_TTL, Math.floor((absoluteEnd - now) / 1000));
    session.expiresAt = now + seconds * 1000;
    changed = true;
    refreshCookie = sessionCookie(opaque, seconds);
  }
  if (changed) {
    const seconds = Math.max(1, Math.min(SESSION_IDLE_TTL, Math.floor((session.expiresAt - now) / 1000)));
    await env.OAUTH_KV.put(key, JSON.stringify(session), { expirationTtl: seconds });
  }
  return { actor, key, session, csrfToken: session.csrfToken, reauthAt: Number(session.reauthAt) || null, refreshCookie };
}

function attachRefresh(response, cookie) {
  if (!cookie) return response;
  const headers = new Headers(response.headers);
  headers.append("set-cookie", cookie);
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function signOut(request, env, origin) {
  const opaque = cookieValue(request, SESSION_COOKIE);
  if (opaque && env.OAUTH_KV) await env.OAUTH_KV.delete(SESSION_PREFIX + await sha256(opaque));
  return redirect(`${origin}/`, [clearCookie(SESSION_COOKIE), clearCookie(PENDING_COOKIE)]);
}

function sameOrigin(request, env) {
  return request.headers.get("origin") === dealroomOrigin(env);
}

function equalStrings(left, right) {
  if (typeof left !== "string" || typeof right !== "string" || left.length !== right.length) return false;
  let mismatch = 0;
  for (let index = 0; index < left.length; index += 1) mismatch |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return mismatch === 0;
}

async function systemWorkBody(request) {
  const length = Number(request.headers.get("content-length"));
  if (Number.isFinite(length) && (length < 0 || length > SYSTEM_WORK_MAX_BODY)) return { error: json({ error: "payload_too_large" }, 413) };
  const raw = await request.clone().text();
  if (new TextEncoder().encode(raw).byteLength > SYSTEM_WORK_MAX_BODY) return { error: json({ error: "payload_too_large" }, 413) };
  try { return { value: JSON.parse(raw) }; }
  catch { return { error: json({ error: "invalid_json" }, 400) }; }
}

async function guardSystemWorkPost(request, env, session, { readBody = false } = {}) {
  const contentType = request.headers.get("content-type") || "";
  if (!/^application\/json(?:\s*;|$)/i.test(contentType)) return { error: json({ error: "unsupported_media_type" }, 415) };
  if (!sameOrigin(request, env)) return { error: json({ error: "forbidden", reason: "origin_mismatch" }, 403) };
  if (request.headers.get("sec-fetch-site") !== "same-origin") return { error: json({ error: "forbidden", reason: "fetch_metadata_mismatch" }, 403) };
  if (!equalStrings(request.headers.get("x-carr-csrf"), session.csrfToken)) return { error: json({ error: "forbidden", reason: "csrf_mismatch" }, 403) };
  const length = Number(request.headers.get("content-length"));
  if (Number.isFinite(length) && (length < 0 || length > SYSTEM_WORK_MAX_BODY)) return { error: json({ error: "payload_too_large" }, 413) };
  // Typed controllers parse their own request body.  Do not clone/read here:
  // cloning after request.json() is a Worker error and would make the security
  // guard depend on handler order.  The challenge endpoint is the one route
  // that owns its body, so it opts into the exact byte-length check below.
  return readBody ? systemWorkBody(request) : { value: null };
}

function approvalTarget(action, args) {
  const hashKey = APPROVAL_ACTIONS.get(action);
  if (!hashKey || !args || typeof args !== "object") return null;
  const humanRef = args.human_ref;
  const baseVersion = args.base_version;
  const proposalHash = args[hashKey];
  const idempotencyKey = args.idempotency_key;
  if (!/^WR-[0-9]{1,12}$/.test(humanRef || "") || !Number.isInteger(baseVersion) || baseVersion < 1 ||
      !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(idempotencyKey || "") ||
      !/^sha256:[0-9a-f]{64}$/.test(proposalHash || "")) return null;
  return { action, human_ref: humanRef, base_version: baseVersion,
    idempotency_key: idempotencyKey, [hashKey]: proposalHash };
}

/** A challenge binds the exact immutable approval target, not a mutable UI label. */
export async function actionDigestFor(action, args) {
  const target = approvalTarget(action, args);
  return target ? sha256(JSON.stringify(target)) : null;
}

/**
 * Reusable authorization seam for the exact typed Program 6 controller.
 * It returns a refusal Response, or null when the action may proceed.
 */
export async function authorizeProgram6Action({ request, env, actor, session, action, args,
  now = Date.now(), redeemChallenge = redeemProgram6BrowserChallenge }) {
  const guard = await guardSystemWorkPost(request, env, session);
  if (guard.error) return guard.error;
  if (!APPROVAL_ACTIONS.has(action)) return null;
  if (!session.reauthAt || now - session.reauthAt > REAUTH_TTL * 1000) {
    return json({ error: "reauth_required", reauth_url: "/auth/reauth?return_to=/" }, 401);
  }
  const token = request.headers.get("x-carr-action-challenge");
  if (!token || token.length > 256) return json({ error: "action_challenge_required" }, 401);
  const key = ACTION_CHALLENGE_PREFIX + token;
  const challenge = await env.OAUTH_KV.get(key, { type: "json" });
  const digest = await actionDigestFor(action, args);
  if (!challenge || challenge.expiresAt <= now || challenge.sessionKey !== session.key ||
      challenge.action !== action || !digest || !equalStrings(challenge.digest, digest)) {
    return json({ error: "invalid_action_challenge" }, 403);
  }
  const redemption = await redeemChallenge({ env, actor,
    tokenDigest: await sha256(token), sessionDigest: await sha256(session.key),
    action, materialDigest: digest, idempotencyKey: args.idempotency_key });
  if (!redemption?.ok) return json({ error: redemption?.error || "invalid_action_challenge" },
    redemption?.error === "authority_connection_unavailable" ? 503 : 403);
  await env.OAUTH_KV.delete(key);
  return null;
}

async function systemWorkSession(session, dependencies) {
  const now = dependencies.now();
  return json({
    actor: { slug: session.actor.slug, display: session.actor.display },
    csrf_token: session.csrfToken,
    reauth_required: !session.reauthAt || now - session.reauthAt > REAUTH_TTL * 1000,
    reauth_url: "/auth/reauth?return_to=/",
    challenge_ttl_seconds: ACTION_CHALLENGE_TTL,
  });
}

async function createActionChallenge(request, env, session, dependencies) {
  const guard = await guardSystemWorkPost(request, env, session, { readBody: true });
  if (guard.error) return guard.error;
  const target = approvalTarget(guard.value?.action, guard.value);
  if (!target || Object.keys(guard.value).sort().join(",") !== Object.keys(target).sort().join(",")) {
    return json({ error: "invalid_action_challenge_target" }, 400);
  }
  const now = dependencies.now();
  if (!session.reauthAt || now - session.reauthAt > REAUTH_TTL * 1000) {
    return json({ error: "reauth_required", reauth_url: "/auth/reauth?return_to=/" }, 401);
  }
  const token = randomString(32);
  const expiresAt = now + ACTION_CHALLENGE_TTL * 1000;
  await env.OAUTH_KV.put(ACTION_CHALLENGE_PREFIX + token, JSON.stringify({
    sessionKey: session.key,
    action: target.action,
    digest: await actionDigestFor(target.action, target),
    expiresAt,
  }), { expirationTtl: ACTION_CHALLENGE_TTL });
  return json({ challenge: token, expires_at: new Date(expiresAt).toISOString() });
}

async function prepareSystemWorkControllerRequest(request, env, session) {
  if (request.method !== "POST") return { request };
  const guard = await guardSystemWorkPost(request, env, session, { readBody: true });
  if (guard.error) return { error: guard.error };
  const headers = new Headers(request.headers);
  headers.delete("content-length");
  return { request: new Request(request.url, { method: "POST", headers,
    body: JSON.stringify(guard.value) }) };
}

async function startReauth(request, env, dependencies) {
  const origin = dealroomOrigin(env);
  if (!origin) return json({ error: "not_found" }, 404);
  const session = await sessionFor(request, env, dependencies);
  if (!session) return redirect(`${origin}/auth/login?return_to=${encodeURIComponent(safeReturnTo(new URL(request.url).searchParams.get("return_to"), origin))}`);
  const response = await startLogin(request, env, { purpose: "reauth", sessionKey: session.key });
  return attachRefresh(response, session.refreshCookie);
}

async function bundleAsset(env, request) {
  const url = new URL(request.url);
  const requested = url.pathname === "/" ? "/index.html" : url.pathname;
  for (const pathname of [requested, `/dist${requested}`]) {
    const response = await asset(env, request, pathname);
    if (response) return withHeaders(response, { "cache-control": "no-cache" });
  }
  if (request.headers.get("accept")?.includes("text/html") || url.pathname === "/") {
    for (const pathname of ["/index.html", "/dist/index.html", "/public-shell/index.html"]) {
      const response = await asset(env, request, pathname);
      if (response) return withHeaders(response, { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" });
    }
  }
  return json({ error: "not_found" }, 404);
}

/** Injectable dependencies keep the gate testable without Google or a database. */
export function createDealroomHandler(overrides = {}) {
  const dependencies = {
    exchangeGoogleCodeFn: exchangeGoogleCode,
    verifyGoogleIdTokenFn: verifyGoogleIdToken,
    slugForEmailFn: slugForEmail,
    propsForSlugFn: propsForSlug,
    actorFromPropsFn: actorFromProps,
    now: () => Date.now(),
    ...overrides,
  };

  return {
    async fetch(request, env, ctx) {
      const response = await handleRequest(request, env, ctx, dependencies);
      return withSecurityHeaders(response);
    },
  };
}

async function handleRequest(request, env, ctx, dependencies) {
      const origin = dealroomOrigin(env);
      if (!origin || !requestMatchesDealroomOrigin(request, origin)) return json({ error: "not_found" }, 404);
      const url = new URL(request.url);
      const publicResponse = await publicShellAsset(env, request, url.pathname);
      if (publicResponse) return publicResponse;
      if (url.pathname === "/auth/login" && request.method === "GET") return startLogin(request, env);
      if (url.pathname === "/auth/callback" && request.method === "GET") return completeLogin(request, env, dependencies);
      if (url.pathname === "/auth/reauth" && request.method === "GET") return startReauth(request, env, dependencies);

      if (url.pathname === "/auth/signout") {
        if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
        const signOutSession = await sessionFor(request, env, dependencies);
        if (!signOutSession) return json({ error: "unauthorized", state: "sign_in_required" }, 401);
        if (!sameOrigin(request, env) || request.headers.get("sec-fetch-site") !== "same-origin" ||
            !equalStrings(request.headers.get("x-carr-csrf"), signOutSession.csrfToken)) {
          return json({ error: "forbidden", reason: "csrf_mismatch" }, 403);
        }
        return attachRefresh(await signOut(request, env, origin), signOutSession.refreshCookie);
      }

      const session = await sessionFor(request, env, dependencies);
      if (!session) {
        if (url.pathname === "/mcp" || url.pathname === "/pipeline/changes" || url.pathname.startsWith(SYSTEM_WORK_PREFIX)) {
          return json({ error: "unauthorized", state: "sign_in_required" }, 401);
        }
        return redirect(`${origin}/auth/login?return_to=${encodeURIComponent(url.pathname + url.search)}`);
      }

      let response;
      if (url.pathname.startsWith(SYSTEM_WORK_PREFIX)) {
        if (!program6ActionsEnabled(env)) return json({ error: "program6_actions_disabled" }, 404);
        if (url.pathname === `${SYSTEM_WORK_PREFIX}/session` && request.method === "GET") response = await systemWorkSession(session, dependencies);
        else if (url.pathname === `${SYSTEM_WORK_PREFIX}/challenge` && request.method === "POST") response = await createActionChallenge(request, env, session, dependencies);
        else if (typeof dependencies.program6Handler === "function") {
          // The injected controller receives a server-derived actor and opaque
          // session metadata.  It never receives a caller-selected verb or
          // tenant and must use authorizeProgram6Action for its typed posts.
          const prepared = await prepareSystemWorkControllerRequest(request, env, session);
          response = prepared.error || await dependencies.program6Handler(prepared.request, env, ctx, session.actor, session);
        } else response = json({ error: "not_found" }, 404);
      } else if (url.pathname === "/mcp") {
        // SameSite limits cross-site cookies, but sibling subdomains are still
        // the same site. Origin equality closes that remaining CSRF door for
        // every verb POST authenticated by the browser cookie.
        if (!sameOrigin(request, env)) {
          return json({ error: "forbidden", reason: "origin_mismatch" }, 403);
        }
        response = await dependencies.mcpHandler(request, env, ctx, session.actor);
      }
      else if (url.pathname === "/pipeline/changes") response = await dependencies.pipelineHandler(request, env, ctx, session.actor);
      else response = await bundleAsset(env, request);
      return attachRefresh(response, session.refreshCookie);
}

export function isDealroomRequest(request, env) {
  const origin = dealroomOrigin(env);
  if (!requestMatchesDealroomOrigin(request, origin)) return false;
  const pathname = new URL(request.url).pathname;
  if (pathname === SYSTEM_WORK_PREFIX || DEALROOM_EXACT_PATHS.has(pathname) ||
      DEALROOM_PATH_PREFIXES.some((prefix) => pathname.startsWith(prefix))) return true;

  // The staging workers.dev origin also carries the machine/OAuth API. Only an
  // opaque Deal Room session cookie selects the browser adapter for these two
  // shared paths; any Authorization header remains owned by the machine doors
  // or OAuthProvider, even if a browser cookie is present too.
  if (pathname === "/mcp" || pathname === "/pipeline/changes") {
    return !request.headers.get("authorization") && Boolean(cookieValue(request, SESSION_COOKIE));
  }
  return false;
}
