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

function toolList() {
  return Object.entries(TOOLS).map(([name, t]) => ({
    name,
    description: t.description,
    inputSchema: t.inputSchema,
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

async function dispatch(request, env, ctx, actor) {
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
