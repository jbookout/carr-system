// CARR MCP server — Worker (2026-07-30 build session, verb surface added).
// Surfaces: /health (dead-man probe), /ingest (socket), /mcp (MCP 2026-06 spec,
// stateless streamable HTTP: initialize / tools/list / tools/call).
// AUTH (interim, documented in tool-contracts §1): per-partner bearer tokens
// (PARTNER_TOKENS secret {"joe": "...", "dell": "..."}); the actor comes from
// the MATCHED TOKEN, never the payload. The OAuth approval flow for the
// phone connector layers on top next session; token issuance stays pinned to
// exactly two identities either way (A10).
// NO SEND CAPABILITY EXISTS OR WILL EXIST IN THIS WORKER.

import { neon } from "@neondatabase/serverless";
import { Pool } from "@neondatabase/serverless";
import { TOOLS, ToolError } from "./tools.js";

const JSON_HEADERS = { "content-type": "application/json" };
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

// ---------- auth ----------

function actorFromToken(request, env) {
  const auth = request.headers.get("authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "");
  if (!token) return null;
  let map;
  try { map = JSON.parse(env.PARTNER_TOKENS || "{}"); } catch { map = {}; }
  for (const [slug, t] of Object.entries(map))
    if (t && t.length >= 32 && t === token)
      return { slug, display: slug === "joe" ? "Joe" : "Dell", human: true };
  return null;
}

// ---------- health + ingest (unchanged behavior) ----------

async function health(env) {
  try {
    const sql = neon(env.DATABASE_URL_READER);
    const rows = await sql`select line from v_integrity_digest`;
    return json({ ok: true, digest_lines: rows.length, ts: new Date().toISOString() });
  } catch (e) {
    const suspended = /suspend|quota|compute/i.test(String(e));
    return json({ ok: false, reason: suspended
      ? "Database compute is suspended (Neon budget). Not an emergency: boards render from last exports. Runbook: DNA/Deal Management/record-layer/runbook.md step 2."
      : "Database unreachable: " + String(e).slice(0, 200) }, 503);
  }
}

async function ingest(request, env) {
  const auth = request.headers.get("authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "");
  let tokens;
  try { tokens = JSON.parse(env.INGEST_TOKENS || "{}"); } catch { tokens = {}; }
  const source = Object.keys(tokens).find((s) => tokens[s] && tokens[s] === token);
  if (!source) return json({ error: "unauthorized" }, 401);
  const len = parseInt(request.headers.get("content-length") || "0", 10);
  if (len > 1048576) return json({ error: "payload_too_large" }, 413);
  let payload;
  try { payload = await request.json(); } catch { return json({ error: "invalid_json" }, 400); }
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

// ---------- MCP transport (stateless streamable HTTP) ----------

const PROTOCOL = "2025-06-18";

function toolList() {
  return Object.entries(TOOLS).map(([name, t]) => ({
    name, description: t.description, inputSchema: t.inputSchema,
  }));
}

async function callTool(env, actor, name, args) {
  const tool = TOOLS[name];
  if (!tool) throw new ToolError({ error: "unknown_tool", name });
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

async function mcp(request, env, ctx) {
  if (request.method !== "POST")
    return json({ error: "method_not_allowed", hint: "MCP streamable HTTP: POST JSON-RPC" }, 405);
  const actor = actorFromToken(request, env);
  if (!actor) return json({ error: "unauthorized" }, 401);

  let rpc;
  try { rpc = await request.json(); } catch { return json({ error: "invalid_json" }, 400); }
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
            "Joe sends.",
        });
      case "notifications/initialized":
        return new Response(null, { status: 202 });
      case "ping":
        return reply({});
      case "tools/list":
        return reply({ tools: toolList() });
      case "tools/call": {
        env.ctx = ctx;
        try {
          const result = await callTool(env, actor, rpc.params?.name, rpc.params?.arguments);
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

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/health") return health(env);
    if (url.pathname === "/ingest" && request.method === "POST") return ingest(request, env);
    if (url.pathname === "/mcp") return mcp(request, env, ctx);
    return json({ service: "carr-mcp", surfaces: ["/health", "/ingest", "/mcp"] }, 404);
  },
};
