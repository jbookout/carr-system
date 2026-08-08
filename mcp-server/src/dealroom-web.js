// Browser entrypoint for dealroom.doctorcre.com.
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

export const DEALROOM_HOST = "dealroom.doctorcre.com";
export const DEALROOM_ASSET_DIRECTORY = "../dealroom"; // mirrors wrangler.toml [assets]

const SESSION_COOKIE = "__Host-dealroom_session";
const PENDING_COOKIE = "__Host-dealroom_oauth";
const PENDING_PREFIX = "dealroom_pending:";
const SESSION_PREFIX = "dealroom_session:";
const PENDING_TTL = 600;
const SESSION_IDLE_TTL = 12 * 60 * 60;
const SESSION_ABSOLUTE_TTL = 7 * 24 * 60 * 60;
const SESSION_REFRESH_WINDOW = 60 * 60;

const JSON_HEADERS = { "content-type": "application/json", "cache-control": "no-store" };
const PUBLIC_SHELL = new Map([
  ["/manifest.webmanifest", "/public-shell/manifest.webmanifest"],
  ["/sw.js", "/public-shell/sw.js"],
  ["/offline.html", "/public-shell/offline.html"],
]);

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

function callbackUri() {
  return `https://${DEALROOM_HOST}/auth/callback`;
}

function safeReturnTo(value) {
  if (!value) return "/";
  try {
    const origin = `https://${DEALROOM_HOST}`;
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

async function startLogin(request, env) {
  if (!env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET || !env.OAUTH_KV) {
    return new Response("Sign-in is not configured.", { status: 503, headers: { "cache-control": "no-store" } });
  }
  const url = new URL(request.url);
  const state = randomString(24);
  const verifier = randomString(48);
  await env.OAUTH_KV.put(PENDING_PREFIX + state, JSON.stringify({
    verifier,
    returnTo: safeReturnTo(url.searchParams.get("return_to")),
  }), { expirationTtl: PENDING_TTL });
  const google = await googleAuthorizationUrl({ clientId: env.GOOGLE_CLIENT_ID,
    redirectUri: callbackUri(), state, verifier });
  return redirect(google.toString(), [pendingCookie(state)]);
}

async function completeLogin(request, env, dependencies) {
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
      redirectUri: callbackUri(), verifier: pending.verifier });
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
  await env.OAUTH_KV.put(sessionKey, JSON.stringify({ props, createdAt: now, expiresAt: now + SESSION_IDLE_TTL * 1000 }),
    { expirationTtl: SESSION_IDLE_TTL });
  return finish(redirect(`https://${DEALROOM_HOST}${safeReturnTo(pending.returnTo)}`,
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

  let refreshCookie = null;
  if (session.expiresAt - now <= SESSION_REFRESH_WINDOW * 1000) {
    const seconds = Math.min(SESSION_IDLE_TTL, Math.floor((absoluteEnd - now) / 1000));
    session.expiresAt = now + seconds * 1000;
    await env.OAUTH_KV.put(key, JSON.stringify(session), { expirationTtl: seconds });
    refreshCookie = sessionCookie(opaque, seconds);
  }
  return { actor, key, refreshCookie };
}

function attachRefresh(response, cookie) {
  if (!cookie) return response;
  const headers = new Headers(response.headers);
  headers.append("set-cookie", cookie);
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function signOut(request, env) {
  const opaque = cookieValue(request, SESSION_COOKIE);
  if (opaque && env.OAUTH_KV) await env.OAUTH_KV.delete(SESSION_PREFIX + await sha256(opaque));
  return redirect(`https://${DEALROOM_HOST}/`, [clearCookie(SESSION_COOKIE), clearCookie(PENDING_COOKIE)]);
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
      const url = new URL(request.url);
      const publicResponse = await publicShellAsset(env, request, url.pathname);
      if (publicResponse) return publicResponse;
      if (url.pathname === "/auth/login" && request.method === "GET") return startLogin(request, env);
      if (url.pathname === "/auth/callback" && request.method === "GET") return completeLogin(request, env, dependencies);
      if (url.pathname === "/auth/signout" && (request.method === "GET" || request.method === "POST")) return signOut(request, env);

      const session = await sessionFor(request, env, dependencies);
      if (!session) {
        if (url.pathname === "/mcp" || url.pathname === "/pipeline/changes") {
          return json({ error: "unauthorized", state: "sign_in_required" }, 401);
        }
        return redirect(`https://${DEALROOM_HOST}/auth/login?return_to=${encodeURIComponent(url.pathname + url.search)}`);
      }

      let response;
      if (url.pathname === "/mcp") {
        // SameSite limits cross-site cookies, but sibling subdomains are still
        // the same site. Origin equality closes that remaining CSRF door for
        // every verb POST authenticated by the browser cookie.
        if (request.headers.get("origin") !== `https://${DEALROOM_HOST}`) {
          return json({ error: "forbidden", reason: "origin_mismatch" }, 403);
        }
        response = await dependencies.mcpHandler(request, env, ctx, session.actor);
      }
      else if (url.pathname === "/pipeline/changes") response = await dependencies.pipelineHandler(request, env, ctx, session.actor);
      else response = await bundleAsset(env, request);
      return attachRefresh(response, session.refreshCookie);
    },
  };
}

export function isDealroomRequest(request) {
  return new URL(request.url).hostname.toLowerCase() === DEALROOM_HOST;
}
