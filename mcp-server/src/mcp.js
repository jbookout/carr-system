// CARR MCP server — the MCP transport (stateless streamable HTTP:
// initialize / tools/list / tools/call). Verb surface unchanged.
//
// AUTH IS NOT DONE HERE. This handler is mounted as the OAuthProvider's
// `apiHandler` for `/mcp`, so it only ever runs on a request whose token the
// provider already validated. The actor comes from `ctx.props` — which the
// provider decrypts from the grant — and NEVER from a header this file parses
// or from the request payload. Two ways to arrive at the same props:
//   1. an OAuth grant issued after a verified Google identity passed the allow-list
//   2. (migration only) a legacy PARTNER_TOKENS bearer, via resolveExternalToken
//
// NO SEND CAPABILITY EXISTS OR WILL EXIST IN THIS WORKER.

import { neon, Pool } from "@neondatabase/serverless";
import { TOOLS, ToolError } from "./tools.js";
import { actorFromProps } from "./identity.js";

const JSON_HEADERS = { "content-type": "application/json" };
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

const PROTOCOL = "2025-06-18";

// ---------- capability profiles (2026-08-02) ----------
//
// WHAT THIS IS, AND WHAT IT IS NOT. This is a BLAST-RADIUS REDUCER for surfaces
// a human configured, not an authorization boundary. Authorization is, and stays,
// the OAuth grant plus `humanOnly` — a caller who wants the full surface simply
// omits the parameter, and that is fine, because the threat this addresses is not
// a hostile caller. It is an UNATTENDED OR DELEGATED session doing something
// destructive by mistake: a 2am scheduled run whose model tier cannot be pinned,
// or a Haiku/Sonnet subagent told to stay mechanical while technically holding
// `reassign-deal` and `confirm-merge`. The profile is set in that surface's MCP
// config by a human, once, and the model inside the session cannot widen it.
//
// SCOPED BY RISK OF THE SESSION, NEVER BY WHICH PARTNER HOLDS THE GRANT.
// Joe and Dell both get `full` on interactive sessions. Dell teaches rules and
// trains workflows exactly as Joe does, and a permission hierarchy between the
// two partners would contradict the two-writer, one-book design (taught rule
// b42e217e, 2026-08-02). A newer partner needs better defaults and clearer verb
// descriptions, never fewer capabilities.
//
// Selected per-request: POST /mcp?profile=capture
const PROFILES = {
  // Everything. The default, and what both partners' interactive sessions use.
  full: null,

  // Reads plus the capture verbs that are safe to run unattended: each one is
  // additive, carries an idempotency key, and cannot destroy or re-point an
  // existing record. This is the profile for scheduled runs and delegated seats
  // — it is deliberately ABLE TO WRITE, because the capture gap (0 of 290 vendors
  // and 0 of 207 leads with a last touch, found 2026-08-02) is not fixed by a
  // profile that can only read.
  capture: new Set([
    "log-activity", "stamp-touch", "add-loop", "update-loop",
    "set-next-action", "complete-action", "add-critical-date", "record-finding",
  ]),

  // Observation only. No writes at all.
  read: new Set(),
};

const PROFILE_NOTICE = {
  capture:
    "\n\n<notice>This session runs on the CAPTURE profile: reads, plus additive capture verbs " +
    "only. Verbs that re-point, merge, retire or delete a record are not available here. This is " +
    "intentional — the session is unattended or delegated. Do not try to work around it; report " +
    "what you would have done and let a partner's interactive session do it.</notice>",
  read:
    "\n\n<notice>This session runs on the READ profile: no write verb is available. This is " +
    "intentional. Do not try to work around it; report what you would have written.</notice>",
};

/** Resolve ?profile= to a name, defaulting to full. An unknown value fails CLOSED to read. */
function profileFor(request) {
  const raw = new URL(request.url).searchParams.get("profile");
  if (!raw) return "full";
  return Object.prototype.hasOwnProperty.call(PROFILES, raw) ? raw : "read";
}

function allowedIn(profile, name, tool) {
  if (profile === "full") return true;
  if (!tool.write) return true;              // reads are allowed in every profile
  return PROFILES[profile].has(name);
}

function toolList(profile = "full") {
  return Object.entries(TOOLS)
    .filter(([name, t]) => allowedIn(profile, name, t))
    .map(([name, t]) => ({
      name,
      description: t.description + (profile === "full" ? "" : (PROFILE_NOTICE[profile] || "")),
      inputSchema: t.inputSchema,
      // Machine-readable danger signal, so a client's permission layer and the
      // model can tell `find` from `reassign-deal` without parsing prose.
      annotations: {
        readOnlyHint: !t.write,
        destructiveHint: Boolean(t.write),
        idempotentHint: true,               // every write runs the idempotency envelope
        openWorldHint: false,
      },
    }));
}

async function callTool(env, actor, name, args, profile = "full") {
  const tool = TOOLS[name];
  if (!tool) throw new ToolError({ error: "unknown_tool", name });
  // ENFORCED AT CALL TIME, not just filtered out of tools/list. Removing a verb
  // from the list is a hint; a model that has seen the full list in an earlier
  // turn, or guesses a name, would otherwise still reach it.
  if (!allowedIn(profile, name, tool))
    throw new ToolError({ error: "not_in_profile", verb: name, profile,
      hint: "this session is scoped; report what you would have done and let an interactive partner session do it" });
  if (tool.humanOnly && !actor.human)
    throw new ToolError({ error: "human_only", hint: "this verb never accepts automation" });

  if (!tool.write) {
    const sql = neon(env.DATABASE_URL_READER);
    const client = { query: async (text, params = []) => ({ rows: await sql.query(text, params) }) };
    return tool.handler(client, actor, args || {});
  }

  // writes: real transaction on the writer pool; actor row resolved inside it
  const pool = new Pool({ connectionString: env.DATABASE_URL_WRITER });
  const client = await pool.connect();
  try {
    await client.query("begin");
    const a = await client.query("select id from actor where slug=$1", [actor.slug]);
    // Guarded 2026-08-03. Unguarded, a missing actor row made this a raw
    // TypeError on `undefined.id` — a 500 with a stack trace where the real
    // answer is "this token names an actor nobody provisioned". The token has
    // already passed the identity allow-list by here, so the two can disagree:
    // adding a partner to ALLOW_LIST without inserting their actor row lands
    // exactly here, and that is a plausible shape of mistake during onboarding.
    if (!a.rows.length) throw new ToolError({ error: "actor_not_provisioned", slug: actor.slug,
      hint: "the token authenticates as this actor but no row exists in the actor table — " +
            "provision the actor before any write verb will run" });
    const fullActor = { ...actor, id: a.rows[0].id };
    const result = await tool.handler(client, fullActor, args || {});
    await client.query("commit");
    return result;
  } catch (e) {
    await client.query("rollback").catch(() => {});
    throw e;
  } finally {
    client.release();
    env.ctx?.waitUntil?.(pool.end());
  }
}

async function dispatch(request, env, ctx, actor) {
  const profile = profileFor(request);
  if (request.method !== "POST")
    return json({ error: "method_not_allowed", hint: "MCP streamable HTTP: POST JSON-RPC" }, 405);

  let rpc;
  try {
    rpc = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  const reply = (result) => json({ jsonrpc: "2.0", id: rpc.id, result });
  const rpcError = (code, message, data) =>
    json({ jsonrpc: "2.0", id: rpc.id, error: { code, message, data } });

  try {
    switch (rpc.method) {
      case "initialize":
        return reply({
          protocolVersion: PROTOCOL,
          capabilities: { tools: {} },
          serverInfo: { name: "carr-record-layer", version: "0.1.0" },
          instructions:
            "CARR's record layer. Writes need a fresh idempotency_key (UUID) per intended action; " +
            "mutations need base_version from a fresh read. version_conflict and needs_confirm are " +
            "questions for the human, never auto-retried. There is no send tool: drafts are produced, " +
            "Joe sends." + (profile === "full" ? "" : ` ACTIVE PROFILE: ${profile}.` + (PROFILE_NOTICE[profile] || "")),
        });
      case "notifications/initialized":
        return new Response(null, { status: 202 });
      case "ping":
        return reply({});
      case "tools/list":
        return reply({ tools: toolList(profile) });
      case "tools/call": {
        env.ctx = ctx;
        try {
          const result = await callTool(env, actor, rpc.params?.name, rpc.params?.arguments, profile);
          return reply({ content: [{ type: "text", text: JSON.stringify(result) }] });
        } catch (e) {
          if (e instanceof ToolError)
            return reply({ isError: true, content: [{ type: "text", text: JSON.stringify(e.payload) }] });
          throw e;
        }
      }
      default:
        return rpcError(-32601, `method not found: ${rpc.method}`);
    }
  } catch (e) {
    return rpcError(-32603, "internal error", String(e).slice(0, 300));
  }
}

/** Mounted as OAuthProvider `apiHandler` for /mcp. ctx.props is already authenticated. */
export const mcpApiHandler = {
  async fetch(request, env, ctx) {
    const actor = actorFromProps(ctx.props);
    // Fails closed: a token whose grant does not name one of the two actors is
    // no better than no token at all.
    if (!actor) return json({ error: "unauthorized" }, 401);
    return dispatch(request, env, ctx, actor);
  },
};
