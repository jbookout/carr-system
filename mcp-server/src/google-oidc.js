// CARR MCP server — the upstream identity leg: Google sign-in.
//
// The OAuthProvider library is the OAuth SERVER to the Claude client. This file
// is the OAuth CLIENT to Google. Two endpoints, both served by the Worker's
// default handler (they are not API routes, so no access token is involved):
//
//   GET /authorize  — the Claude client lands here; we park the request and
//                     bounce the human to Google.
//   GET /callback   — Google returns; we verify the identity token, apply the
//                     allow-list, and only then ask the library to issue.
//
// There is no consent screen. Identity IS the gate (A10). A non-allow-listed
// Google account gets a plain refusal and nothing is issued.

import { slugForEmail, propsForSlug } from "./identity.js";

const GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";
const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";
const GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs";
const GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"];

const PENDING_PREFIX = "pending_auth:"; // our keys; library owns client:/grant:/token:
const PENDING_TTL = 600; // 10 minutes to finish a sign-in
const JWKS_CACHE_KEY = "google_jwks_cache";
const JWKS_CACHE_TTL = 3600;

const CLOCK_SKEW = 60; // seconds tolerated on exp
const IAT_SKEW = 300; // seconds tolerated on a future iat

// ---------- small helpers ----------

const enc = new TextEncoder();
const dec = new TextDecoder();

export function randomString(bytes) {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return [...buf].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function b64urlToBytes(s) {
  const pad = s.length % 4 === 0 ? "" : "=".repeat(4 - (s.length % 4));
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytesToB64url(bytes) {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function s256(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", enc.encode(verifier));
  return bytesToB64url(new Uint8Array(digest));
}

// Must match the redirect URI registered in the Google Cloud console EXACTLY,
// or Google refuses with redirect_uri_mismatch. Production is HTTPS-only, so
// the scheme is pinned rather than inherited from the request — a single
// plain-HTTP request must not be able to produce a URI Google will not accept.
function callbackUri(request) {
  const u = new URL("/callback", request.url);
  if (u.hostname !== "localhost" && u.hostname !== "127.0.0.1") u.protocol = "https:";
  return u.toString();
}

/** Build the shared Google OIDC authorization request used by both surfaces. */
export async function googleAuthorizationUrl({ clientId, redirectUri, state, verifier }) {
  const u = new URL(GOOGLE_AUTH_URL);
  u.searchParams.set("client_id", clientId);
  u.searchParams.set("redirect_uri", redirectUri);
  u.searchParams.set("response_type", "code");
  u.searchParams.set("scope", "openid email");
  u.searchParams.set("state", state);
  u.searchParams.set("code_challenge", await s256(verifier));
  u.searchParams.set("code_challenge_method", "S256");
  u.searchParams.set("access_type", "online");
  u.searchParams.set("prompt", "select_account");
  return u;
}

/** Exchange one Google authorization code using the same PKCE contract. */
export async function exchangeGoogleCode({ code, clientId, clientSecret, redirectUri, verifier }, fetchImpl = fetch) {
  const res = await fetchImpl(GOOGLE_TOKEN_URL, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: clientId,
      client_secret: clientSecret,
      redirect_uri: redirectUri,
      grant_type: "authorization_code",
      code_verifier: verifier,
    }),
  });
  if (!res.ok) throw new Error(`google token endpoint returned ${res.status}`);
  return res.json();
}

// ---------- pages (plain, no theater) ----------

function page(title, lines, status) {
  const body = lines.map((l) => `    <p>${l}</p>`).join("\n");
  const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${title}</title>
    <style>
      body { font: 16px/1.5 -apple-system, system-ui, sans-serif; margin: 0; padding: 3rem 1.25rem;
             color: #1a1a1a; background: #fafafa; }
      main { max-width: 32rem; margin: 0 auto; }
      h1 { font-size: 1.25rem; margin: 0 0 1rem; }
      p { margin: 0 0 0.85rem; }
      code { background: #eee; padding: 0.1em 0.35em; border-radius: 3px; }
    </style>
  </head>
  <body>
    <main>
    <h1>${title}</h1>
${body}
    </main>
  </body>
</html>`;
  return new Response(html, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function refusalPage(email, why) {
  return page(
    "Not authorized",
    [
      "This connector is limited to CARR's two partner accounts.",
      why
        ? escapeHtml(why)
        : `The Google account you signed in with${email ? ` (<code>${escapeHtml(email)}</code>)` : ""} is not one of them.`,
      "Nothing was issued and no access was granted.",
      "If you meant to use a different Google account, sign out of Google and start the connection again.",
    ],
    403,
  );
}

// ---------- Google identity token verification ----------

function pickKey(jwks, kid) {
  if (!jwks || !Array.isArray(jwks.keys)) return null;
  if (kid) return jwks.keys.find((k) => k.kid === kid) || null;
  return jwks.keys.length === 1 ? jwks.keys[0] : null;
}

async function googleJwks(env, force) {
  if (!force) {
    const cached = await env.OAUTH_KV.get(JWKS_CACHE_KEY, { type: "json" });
    if (cached && Array.isArray(cached.keys) && cached.keys.length) return cached;
  }
  const res = await fetch(GOOGLE_JWKS_URL);
  if (!res.ok) throw new Error(`google jwks fetch failed: ${res.status}`);
  const jwks = await res.json();
  if (!jwks || !Array.isArray(jwks.keys) || !jwks.keys.length) throw new Error("google jwks empty");
  await env.OAUTH_KV.put(JWKS_CACHE_KEY, JSON.stringify(jwks), { expirationTtl: JWKS_CACHE_TTL });
  return jwks;
}

/**
 * Verifies a Google-issued OIDC id_token: RS256 signature against Google's
 * published JWKS, then issuer, audience, expiry and email presence.
 * Throws on any failure. Returns the claims on success.
 *
 * `fetchJwks` is injectable so the verification path can be exercised in a test
 * harness against a synthetic key set; production always passes the KV-cached
 * Google set.
 */
export async function verifyGoogleIdToken(idToken, clientId, env, opts = {}) {
  const nowSec = Math.floor((opts.now ?? Date.now()) / 1000);
  const getJwks = opts.fetchJwks || ((force) => googleJwks(env, force));

  if (typeof idToken !== "string") throw new Error("id_token: missing");
  const parts = idToken.split(".");
  if (parts.length !== 3) throw new Error("id_token: not a compact JWS");

  let header, claims;
  try {
    header = JSON.parse(dec.decode(b64urlToBytes(parts[0])));
    claims = JSON.parse(dec.decode(b64urlToBytes(parts[1])));
  } catch {
    throw new Error("id_token: undecodable");
  }
  if (header.alg !== "RS256") throw new Error(`id_token: unexpected alg ${header.alg}`);

  // Google rotates keys; a miss on the cached set is a reason to refetch once.
  let jwk = pickKey(await getJwks(false), header.kid);
  if (!jwk) jwk = pickKey(await getJwks(true), header.kid);
  if (!jwk) throw new Error("id_token: no matching Google signing key");

  const key = await crypto.subtle.importKey(
    "jwk",
    { kty: jwk.kty, n: jwk.n, e: jwk.e, alg: "RS256", ext: true },
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const ok = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    b64urlToBytes(parts[2]),
    enc.encode(`${parts[0]}.${parts[1]}`),
  );
  if (!ok) throw new Error("id_token: signature verification failed");

  if (!GOOGLE_ISSUERS.includes(claims.iss)) throw new Error(`id_token: untrusted issuer ${claims.iss}`);
  const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!clientId || !aud.includes(clientId)) throw new Error("id_token: audience is not this client");
  if (typeof claims.exp !== "number" || claims.exp + CLOCK_SKEW < nowSec) throw new Error("id_token: expired");
  if (typeof claims.iat === "number" && claims.iat - IAT_SKEW > nowSec) throw new Error("id_token: issued in the future");
  if (typeof claims.email !== "string" || !claims.email) throw new Error("id_token: no email claim");

  return claims;
}

// ---------- /authorize ----------

export async function handleAuthorize(request, env) {
  if (!env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET) {
    return page(
      "Sign-in is not configured",
      [
        "This server has no Google OAuth client configured yet, so it cannot start a sign-in.",
        "Nothing was issued.",
      ],
      503,
    );
  }

  let oauthReq;
  try {
    // Also validates the client and its registered redirect_uri, and throws if either is wrong.
    oauthReq = await env.OAUTH_PROVIDER.parseAuthRequest(request);
  } catch (e) {
    return page("Bad authorization request", [escapeHtml(String(e.message || e)), "Nothing was issued."], 400);
  }

  const state = randomString(24);
  const verifier = randomString(48);
  await env.OAUTH_KV.put(
    PENDING_PREFIX + state,
    JSON.stringify({ req: oauthReq, verifier, at: new Date().toISOString() }),
    { expirationTtl: PENDING_TTL },
  );

  // Always offer the account chooser: the allow-list is per-identity, so the
  // human has to be able to see and pick which identity they are using.
  const u = await googleAuthorizationUrl({ clientId: env.GOOGLE_CLIENT_ID,
    redirectUri: callbackUri(request), state, verifier });

  return Response.redirect(u.toString(), 302);
}

// ---------- /callback ----------

export async function handleCallback(request, env) {
  const url = new URL(request.url);

  const googleError = url.searchParams.get("error");
  if (googleError) {
    return page(
      "Sign-in did not complete",
      [`Google returned <code>${escapeHtml(googleError)}</code>.`, "Nothing was issued."],
      400,
    );
  }

  const state = url.searchParams.get("state");
  const code = url.searchParams.get("code");
  if (!state || !code) {
    return page("Sign-in did not complete", ["The response from Google was missing its code or state.", "Nothing was issued."], 400);
  }

  // Single use: the parked request is consumed whether or not the rest succeeds.
  const key = PENDING_PREFIX + state;
  const pending = await env.OAUTH_KV.get(key, { type: "json" });
  await env.OAUTH_KV.delete(key);
  if (!pending || !pending.req) {
    return page(
      "That sign-in link has expired",
      ["Sign-in links are good for ten minutes and can only be used once.", "Start the connection again from your Claude app."],
      400,
    );
  }

  let tok;
  try {
    tok = await exchangeGoogleCode({ code, clientId: env.GOOGLE_CLIENT_ID,
      clientSecret: env.GOOGLE_CLIENT_SECRET, redirectUri: callbackUri(request),
      verifier: pending.verifier });
  } catch (e) {
    return page("Could not complete Google sign-in", [escapeHtml(String(e.message || e)), "Nothing was issued."], 502);
  }

  let claims;
  try {
    claims = await verifyGoogleIdToken(tok.id_token, env.GOOGLE_CLIENT_ID, env);
  } catch (e) {
    console.warn(`google identity rejected: ${String(e.message || e)}`);
    return page("Identity could not be verified", ["Google's response did not verify.", "Nothing was issued."], 401);
  }

  if (claims.email_verified !== true && claims.email_verified !== "true") {
    return refusalPage(claims.email, "That Google account's email address is not verified by Google.");
  }

  const slug = slugForEmail(claims.email);
  if (!slug) return refusalPage(claims.email);

  const { redirectTo } = await env.OAUTH_PROVIDER.completeAuthorization({
    request: pending.req,
    userId: slug,
    // metadata is stored UNencrypted (it exists so grants can be enumerated and
    // revoked) — keep it to the label. The email rides in props, which is encrypted.
    metadata: { label: slug, signed_in_at: new Date().toISOString() },
    // Grant exactly what was requested. Never widen on our own judgment.
    scope: Array.isArray(pending.req.scope) ? pending.req.scope : [],
    // client_id names the SURFACE holding this grant (Claude Code, a phone connector,
    // a script). Taken from the auth request, never from anything the caller sends.
    props: propsForSlug(slug, { email: claims.email, sub: claims.sub, via: "oauth-google",
                                client_id: pending.req?.clientId || null }),
  });

  return Response.redirect(redirectTo, 302);
}
