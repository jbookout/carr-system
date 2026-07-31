// CARR MCP server — Worker entrypoint.
//
// The Worker's fetch IS an OAuthProvider (Cloudflare's workers-oauth-provider).
// It is the OAuth 2.1 authorization server to the Claude apps, and it wraps the
// MCP verb surface at /mcp as its protected API route. Everything else — the
// dead-man probe, the ingest socket, and the Google sign-in leg — goes to the
// default handler and is NOT behind an access token.
//
//   /mcp        API route. Token validated by the provider; the actor arrives as
//               ctx.props. Also accepts a legacy PARTNER_TOKENS bearer for the
//               duration of the migration (resolveExternalToken, below).
//   /authorize  Google sign-in starts (our code — see google-oidc.js)
//   /callback   Google returns; identity verified; allow-list applied; issue
//   /token      implemented by the provider
//   /register   implemented by the provider (RFC 7591 dynamic client registration)
//   /.well-known/oauth-authorization-server        provider (RFC 8414)
//   /.well-known/oauth-protected-resource[/path]   provider (RFC 9728)
//   /health     unchanged, unauthenticated dead-man probe
//   /ingest     unchanged, INGEST_TOKENS bearer — deliberately OUTSIDE the OAuth wrap
//
// NO SEND CAPABILITY EXISTS OR WILL EXIST IN THIS WORKER.

import { OAuthProvider } from "@cloudflare/workers-oauth-provider";
import { neon } from "@neondatabase/serverless";
import { mcpApiHandler } from "./mcp.js";
import { handleAuthorize, handleCallback } from "./google-oidc.js";
import { slugForLegacyToken, propsForSlug } from "./identity.js";

const JSON_HEADERS = { "content-type": "application/json" };
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

const ACCESS_TOKEN_TTL = 3600; // 1 hour (design: access tokens short)
const REFRESH_TOKEN_TTL = 7776000; // 90 days (design: refresh tokens long)

// ---------- health + ingest (unchanged behavior, unchanged auth) ----------

async function health(env) {
  try {
    const sql = neon(env.DATABASE_URL_READER);
    const rows = await sql`select line from v_integrity_digest`;
    return json({ ok: true, digest_lines: rows.length, ts: new Date().toISOString() });
  } catch (e) {
    const suspended = /suspend|quota|compute/i.test(String(e));
    return json(
      {
        ok: false,
        reason: suspended
          ? "Database compute is suspended (Neon budget). Not an emergency: boards render from last exports. Runbook: DNA/Deal Management/record-layer/runbook.md step 2."
          : "Database unreachable: " + String(e).slice(0, 200),
      },
      503,
    );
  }
}

async function ingest(request, env) {
  const auth = request.headers.get("authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "");
  let tokens;
  try {
    tokens = JSON.parse(env.INGEST_TOKENS || "{}");
  } catch {
    tokens = {};
  }
  const source = Object.keys(tokens).find((s) => tokens[s] && tokens[s] === token);
  if (!source) return json({ error: "unauthorized" }, 401);
  const len = parseInt(request.headers.get("content-length") || "0", 10);
  if (len > 1048576) return json({ error: "payload_too_large" }, 413);
  let payload;
  try {
    payload = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  const externalId = payload.external_id || request.headers.get("x-external-id") || null;
  try {
    const sql = neon(env.DATABASE_URL_WRITER);
    const rows = await sql`
      insert into ingest_inbox (source, external_id, payload)
      values (${source}, ${externalId}, ${JSON.stringify(payload)}::jsonb)
      on conflict (source, external_id) do update set status = ingest_inbox.status
      returning id, (xmax <> 0) as was_duplicate`;
    return json({ ok: true, id: rows[0].id, duplicate: rows[0].was_duplicate });
  } catch (e) {
    return json({ ok: false, error: String(e).slice(0, 200) }, 503);
  }
}

// ---------- default handler: everything that is not the protected API ----------

const defaultHandler = {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/health") return health(env);
    if (url.pathname === "/ingest" && request.method === "POST") return ingest(request, env);
    if (url.pathname === "/authorize") return handleAuthorize(request, env);
    if (url.pathname === "/callback") return handleCallback(request, env);
    return json({ service: "carr-mcp", surfaces: ["/health", "/ingest", "/mcp", "/authorize", "/callback"] }, 404);
  },
};

// ---------- the provider ----------

export default new OAuthProvider({
  apiRoute: "/mcp",
  apiHandler: mcpApiHandler,
  defaultHandler,

  authorizeEndpoint: "/authorize",
  tokenEndpoint: "/token",
  clientRegistrationEndpoint: "/register",

  accessTokenTTL: ACCESS_TOKEN_TTL,
  refreshTokenTTL: REFRESH_TOKEN_TTL,

  // The 2026-07-28 MCP spec revision prefers Client ID Metadata Documents over
  // dynamic client registration; both are on, so clients on either side of that
  // migration work. CIMD needs the global_fetch_strictly_public compatibility
  // flag (set in wrangler.toml) for SSRF protection.
  clientIdMetadataDocumentEnabled: true,

  // ---- MIGRATION ONLY -------------------------------------------------------
  // Consulted only when a bearer is NOT a valid provider-issued token. A valid
  // legacy PARTNER_TOKENS string therefore authenticates exactly as it does
  // today, through the same actor mapping and into the same ctx.props.
  //
  // RETIREMENT (its own commit, after BOTH partners' connectors are verified
  // live on their phones): delete this option, delete slugForLegacyToken in
  // identity.js, and remove the PARTNER_TOKENS secret. Nothing else changes.
  async resolveExternalToken({ token, env }) {
    const slug = slugForLegacyToken(token, env);
    if (!slug) return null;
    return { props: propsForSlug(slug, { via: "partner-token-legacy" }) };
  },

  onError({ code, description, status }) {
    console.warn(`OAuth error response: ${status} ${code} - ${description}`);
  },
});
