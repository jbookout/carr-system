// CARR MCP server — Worker entrypoint.
//
// The Worker's fetch IS an OAuthProvider (Cloudflare's workers-oauth-provider).
// It is the OAuth 2.1 authorization server to the Claude apps, and it wraps the
// MCP verb surface at /mcp as its protected API route. Everything else — the
// dead-man probe, the ingest socket, and the Google sign-in leg — goes to the
// default handler and is NOT behind an access token.
//
//   /mcp        API route for humans: a provider-issued OAuth token, validated
//               by the provider, with the actor arriving as ctx.props. The
//               legacy PARTNER_TOKENS bearer that used to share this route was
//               retired 2026-08-03 (#111b).
//               A SECOND, much narrower door onto the same route was added
//               2026-08-06 (loop #192), for the smoke suite only: a
//               PROBE_TOKENS bearer, checked in THIS file BEFORE the request
//               ever reaches the OAuthProvider (see "probe token" below). It
//               maps to one locked-profile machine actor, never a human, and
//               its profile can never be widened by the request — the OAuth
//               path above is otherwise completely untouched.
//               A THIRD, equally narrow door was added the same day for the
//               Automatic Review Council's Codex lane (see "review token"
//               below): a REVIEW_TOKENS bearer, checked immediately after the
//               probe-token check and on the exact same before-the-provider
//               pattern. It maps a reviewer slug (e.g. 'codex-reviewer') to
//               the 'reviewer' capability profile locked in mcp.js: ALL reads
//               + EXACTLY record-finding. Nothing about the OAuth path or the
//               probe-token path changes because it exists.
//   /pipeline/changes  OAuth-protected Deal Room event cursor + live presence.
//   /authorize  Google sign-in starts (our code — see google-oidc.js)
//   /callback   Google returns; identity verified; allow-list applied; issue
//   /token      implemented by the provider
//   /register   implemented by the provider (RFC 7591 dynamic client registration)
//   /.well-known/oauth-authorization-server        provider (RFC 8414)
//   /.well-known/oauth-protected-resource[/path]   provider (RFC 9728)
//   /health     unchanged, unauthenticated dead-man probe
//   /ingest     unchanged, INGEST_TOKENS bearer — deliberately OUTSIDE the OAuth wrap
//   /capture/*  capture rig bridge, CAPTURE_TOKENS claim then opaque session token
//
// NO SEND CAPABILITY EXISTS OR WILL EXIST IN THIS WORKER.

import { OAuthProvider } from "@cloudflare/workers-oauth-provider";
import { neon, Pool } from "@neondatabase/serverless";
import { mcpApiHandler, dispatch } from "./mcp.js";
import { handleAuthorize, handleCallback } from "./google-oidc.js";
import { actorFromProps, agentActorForToken } from "./identity.js";
import { pipelineChanges } from "./dealroom.js";
import { createDealroomHandler, isDealroomRequest } from "./dealroom-web.js";
import { createCaptureHandler } from "./capture.js";

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
  // INGEST_TOKENS_EXTRA (Phase 1, 2026-08-13): additive second map, merged over
  // the first. `wrangler secret put` REPLACES a secret wholesale and no secret
  // can be read back, so growing the original map means re-pasting every live
  // token — a silent-kill risk for any lane whose value isn't at hand. New
  // sources go in THIS map instead, one key at a time, zero risk to the old
  // ones. Same shape ({source: token}), same one-token-per-source rule.
  try {
    tokens = { ...tokens, ...JSON.parse(env.INGEST_TOKENS_EXTRA || "{}") };
  } catch {
    // a malformed extra map never takes down the primary lanes
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
    return json({ service: "carr-mcp", surfaces: ["/health", "/ingest", "/mcp", "/pipeline/changes", "/authorize", "/callback"] }, 404);
  },
};

// Both protected routes receive the same provider-verified ctx.props. The
// pipeline function itself accepts an actor and query client, so the Deal Room
// session-cookie gate mounts it without changing its contract.
async function pipelineApi(request, env, actor) {
  const sql = neon(env.DATABASE_URL_READER);
  const client = { query: async (text, params = []) => ({ rows: await sql.query(text, params) }) };
  try {
    return await pipelineChanges(request, client, actor);
  } catch (e) {
    // Surface the error class, not data: a blind 503 cost a live debugging
    // round on day one (the real fault was invisible from the client).
    return json({ error: "database_unavailable", detail: String(e && e.message || e).slice(0, 200) }, 503);
  }
}

function captureHandler(env) {
  return createCaptureHandler({
    withWriter: async (fn) => {
      const pool = new Pool({ connectionString: env.DATABASE_URL_WRITER });
      const client = await pool.connect();
      try {
        await client.query("begin");
        const result = await fn(client);
        await client.query("commit");
        return result;
      } catch (error) {
        await client.query("rollback").catch(() => {});
        throw error;
      } finally {
        client.release();
        await pool.end();
      }
    },
  });
}

const protectedApiHandler = {
  async fetch(request, env, ctx) {
    const pathname = new URL(request.url).pathname;
    if (pathname === "/mcp") return mcpApiHandler.fetch(request, env, ctx);
    if (pathname !== "/pipeline/changes") return json({ error: "not_found" }, 404);
    const actor = actorFromProps(ctx.props);
    if (!actor) return json({ error: "unauthorized" }, 401);
    return pipelineApi(request, env, actor);
  },
};

// ---------- probe token (loop #192, 2026-08-06) ----------
//
// A narrow, locked-profile machine identity for the smoke suite, re-credited
// after the PARTNER_TOKENS retirement (#111b) took the suite's old bearer with
// it. Modeled directly on the INGEST_TOKENS check in ingest() above: same
// shape (a JSON map of name -> secret, one Worker secret, replaced whole),
// same lookup style. The differences are deliberate and narrow the blast
// radius further than a second INGEST_TOKENS-style socket would:
//
//   - PROBE_TOKENS authenticates ONLY /mcp, and only a request whose bearer
//     matches. Checked in the wrapper below, BEFORE the request ever reaches
//     the OAuthProvider, so it is never confused with a provider-issued
//     grant, and the whole OAuth path is untouched for every other request —
//     including a /mcp request whose bearer does NOT match PROBE_TOKENS,
//     which falls straight through to oauthProvider.fetch exactly as before.
//   - The matched map key becomes the actor slug. Exactly one is expected to
//     exist — 'smoke-probe' — but the map shape leaves room for a second
//     narrow probe identity later without another code change.
//   - actor.probe = true is the ONLY thing that can put mcp.js's dispatch()
//     into the 'probe' capability profile (see the check there). ?profile=
//     is read for every other caller but is IGNORED here on purpose: the
//     whole point of this token is that it cannot be asked for more than it
//     was provisioned for, no matter what the request says.
//   - human: false, always. A probe actor is a machine and is never eligible
//     for a humanOnly verb (teach, retire-rule, confirm-merge, reassign-deal,
//     …), independent of whatever verbs the profile Set does or does not name.
//   - The actor must exist as a row in `actor` (kind='automation') before any
//     write verb will run — mcp.js's callTool() looks it up by slug inside the
//     write transaction and refuses actor_not_provisioned if it is missing,
//     exactly as it does for every other actor. See
//     pipelines/provision-smoke-probe.sql (prepared, not yet run).
//
// NOT a rebuild of the retired PARTNER_TOKENS bearer: that token authenticated
// as a full human actor (joe or dell) on the full profile. This token
// authenticates as its own machine actor, locked to three write verbs, and
// nothing about the humans' OAuth path changes because it exists.
function probeActorFor(request, env) {
  const auth = request.headers.get("authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "");
  if (!token) return null;
  let tokens;
  try {
    tokens = JSON.parse(env.PROBE_TOKENS || "{}");
  } catch {
    tokens = {};
  }
  const slug = Object.keys(tokens).find((s) => tokens[s] && tokens[s] === token);
  if (!slug) return null;
  return { slug, display: `Probe (${slug})`, human: false, probe: true, via: "probe-token", client_id: null };
}

// ---------- review token (Automatic Review Council, Codex lane, 2026-08-06) ----------
//
// A narrow, locked-profile machine identity for the Deal Council's automated
// Codex reviewer, built directly on the PROBE_TOKENS pattern above — same
// shape (a JSON map of slug -> secret, one Worker secret, replaced whole),
// same lookup style, same "checked before the OAuthProvider ever sees the
// request" placement. The differences mirror why PROBE_TOKENS exists as its
// own thing rather than widening INGEST_TOKENS or PARTNER_TOKENS:
//
//   - REVIEW_TOKENS authenticates ONLY /mcp, and only a request whose bearer
//     matches. A /mcp request whose bearer matches neither PROBE_TOKENS nor
//     REVIEW_TOKENS falls straight through to oauthProvider.fetch exactly as
//     before — this door adds nothing to that path.
//   - The matched map key becomes the actor slug. TWO are expected to hold a
//     live secret as of the 2026-08-06 scope extension: 'codex-reviewer'
//     (the original frozen scope) and 'grok-reviewer' (added the same day
//     once Grok's subscription-covered headless path — OAuth device flow,
//     no XAI_API_KEY — was live-verified; decision 65468572's Codex-only
//     cost override was superseded by that verification, not overridden by
//     assumption). Nothing in this file names either slug specially — this
//     code path is, and was designed to be, generic: adding a THIRD reviewer
//     later is a secret update and an actor row (pipelines/provision-review-
//     council.sql), never another code change here.
//   - actor.review = true is the ONLY thing that can put mcp.js's dispatch()
//     into the 'reviewer' capability profile (see the check there). ?profile=
//     is read for every other caller but is IGNORED here on purpose, exactly
//     like the probe path: the token cannot be asked for more than it was
//     provisioned for, no matter what the request says.
//   - human: false, always. A reviewer actor is a machine and is never
//     eligible for a humanOnly verb, independent of whatever the profile Set
//     does or does not name.
//   - The actor must exist as a row in `actor` (kind='automation') before
//     record-finding — the ONE write this profile allows — will run; mcp.js's
//     callTool() looks it up by slug inside the write transaction and refuses
//     actor_not_provisioned if it is missing, exactly as it does for every
//     other actor. See pipelines/provision-review-council.sql. PROVISIONED
//     AND ACTIVE as of 2026-08-06 (verified live by the #214 audit: both
//     reviewer actor rows exist, active=t) — the refusal backstop below is
//     therefore already open; the profile lock is the operative control.
//
// Checked AFTER probeActorFor so a token that happens to collide with both
// maps (it never will in practice — they are separate secrets) resolves
// deterministically to probe first; in practice a caller only ever holds one
// of the two tokens.
function reviewActorFor(request, env) {
  const auth = request.headers.get("authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "");
  if (!token) return null;
  let tokens;
  try {
    tokens = JSON.parse(env.REVIEW_TOKENS || "{}");
  } catch {
    tokens = {};
  }
  const slug = Object.keys(tokens).find((s) => tokens[s] && tokens[s] === token);
  if (!slug) return null;
  return { slug, display: `Reviewer (${slug})`, human: false, review: true, via: "review-token", client_id: null };
}

// ---------- agent tokens (outside-model CLIs at full scope, loop #227/#239) ----------
//
// WHY A THIRD DOOR. Codex reaches this Worker over OAuth and, since the loop
// #227 deploy, attributes correctly to its own actor (grant metadata reads
// "joe via codex"). Grok cannot: its CLI has no per-MCP-server login command,
// its headless path performs no dynamic client registration, and its TUI
// reports "not authenticated" against an OAuth server. Verified live
// 2026-08-09, three ways. There is no client_name to add to
// AGENT_CLIENT_NAMES because Grok never registers a client at all.
//
// So Grok authenticates the only way it can — `grok mcp add --header
// "Authorization: Bearer ..."` — and this map is the door that bearer opens.
//
// WHAT THIS IS NOT: it is NOT a revival of PARTNER_TOKENS, retired 2026-08-03.
// That secret authenticated as a full HUMAN actor (joe or dell), which meant it
// carried the humanOnly verbs — teach, retire-rule, confirm-merge, new-deal,
// reassign-deal — on a credential sitting in a config file. This one resolves
// to the tool's OWN actor with human:false, so mcp.js's humanOnly gate refuses
// every one of those by construction, not by a list someone has to maintain.
// That is exactly the parity decision 5dd21310 asked for: every verb EXCEPT
// humanOnly.
//
// WHY NO PROFILE LOCK, unlike probe and reviewer. Those two exist to pin a
// narrow surface (probe: reads; reviewer: reads + record-finding) and they set
// actor.probe / actor.review precisely so ?profile= cannot widen them. An agent
// token is the opposite case: full parity IS the grant, so it takes
// profileFor(request) like any other caller. A session that wants to run itself
// narrower can still pass ?profile=capture; it can never run wider, because
// human:false is set here and nothing on the wire can change it.
//
// The slug must be a known actor (identity.js DISPLAY) AND must exist as an
// `actor` row before any write runs — mcp.js's callTool() refuses
// actor_not_provisioned otherwise, same as every other path. codex and grok
// were provisioned by migration 0074_outside_model_actors.
//
// Checked AFTER probe and reviewer so a token colliding across maps resolves
// to the narrowest door first. In practice a caller holds exactly one.
// The lookup itself lives in identity.js (agentActorForToken) so node --test can
// prove it: this file imports from `cloudflare:` and cannot be loaded outside the
// Worker runtime, so anything implemented here is unprovable before a deploy.
function agentActorFor(request, env) {
  return agentActorForToken(request.headers.get("authorization"), env.AGENT_TOKENS);
}

// ---------- the provider ----------

const oauthProvider = new OAuthProvider({
  apiRoute: ["/mcp", "/pipeline/changes"],
  apiHandler: protectedApiHandler,
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

  // PKCE hardening (2026-08-06, un-gated the night Dell's phone was confirmed).
  // The library default ALLOWED plain PKCE (allowPlainPKCE !== false), and live
  // discovery advertised ["plain","S256"]. Held from 2026-08-03 until both
  // partners' phones were verified on the connector, because the check runs at
  // /authorize — tightening breaks nothing live, only the NEXT reconnect of a
  // client that uses plain. Both phones confirmed (Joe 2026-07-31 test 2,
  // Dell 2026-08-06 per Joe): S256 only from here.
  allowPlainPKCE: false,

  // The MIGRATION-ONLY resolveExternalToken option lived here until 2026-08-03.
  // It let a legacy PARTNER_TOKENS bearer authenticate while the OAuth connectors
  // were being rolled out, and it was retired on exactly the terms it was written
  // under. /mcp now accepts a provider-issued token OR the narrow probe-token
  // path below, and nothing else.

  onError({ code, description, status }) {
    console.warn(`OAuth error response: ${status} ${code} - ${description}`);
  },
});

// Same verb and cursor implementations as the bearer-token surface; only the
// authentication adapter differs. The cookie session already resolved to the
// exact actor shape dispatch() and pipelineChanges() accept.
const dealroomHandler = createDealroomHandler({
  mcpHandler: (request, env, ctx, actor) => dispatch(request, env, ctx, actor),
  pipelineHandler: (request, env, _ctx, actor) => pipelineApi(request, env, actor),
});

// Top-level export. Everything not /mcp-with-a-matching-probe-or-review-token
// flows into the OAuthProvider exactly as it always has — this wrapper adds
// two narrow short-circuits and changes nothing else about the fetch surface.
export default {
  async fetch(request, env, ctx) {
    if (new URL(request.url).pathname.startsWith("/capture/")) {
      return captureHandler(env).fetch(request, env, ctx);
    }
    if (isDealroomRequest(request)) return dealroomHandler.fetch(request, env, ctx);
    if (new URL(request.url).pathname === "/mcp") {
      const probeActor = probeActorFor(request, env);
      if (probeActor) return dispatch(request, env, ctx, probeActor);
      const reviewActor = reviewActorFor(request, env);
      if (reviewActor) return dispatch(request, env, ctx, reviewActor);
      const agentActor = agentActorFor(request, env);
      if (agentActor) return dispatch(request, env, ctx, agentActor);
    }
    return oauthProvider.fetch(request, env, ctx);
  },
};
