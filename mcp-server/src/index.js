// CARR MCP server — Worker skeleton (2026-07-30 build session).
// Spec: DNA/Deal Management/record-layer/tool-contracts-2026-07-30.md
// This skeleton ships three things: the /health probe (the dead-man's ping
// target), the ingest socket (A1/A11: per-source tokens, size cap, dedup,
// always-2xx on duplicates), and DB connectivity over the reader role.
// The MCP verb surface lands next (auth allow-list + tool_call idempotency
// plumbing are specified; /mcp returns 501 until then, deliberately).
// NO SEND CAPABILITY EXISTS OR WILL EXIST IN THIS WORKER.

import { neon } from "@neondatabase/serverless";

const JSON_HEADERS = { "content-type": "application/json" };

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

async function health(env) {
  try {
    const sql = neon(env.DATABASE_URL_READER);
    // reader role: views only — the probe proves both connectivity AND that
    // the enforcement views exist.
    const rows = await sql`select line from v_integrity_digest`;
    return json({ ok: true, digest_lines: rows.length, ts: new Date().toISOString() });
  } catch (e) {
    // A13: translate a suspended-DB error into plain language + runbook line.
    const suspended = /suspend|quota|compute/i.test(String(e));
    return json({
      ok: false,
      reason: suspended
        ? "Database compute is suspended (Neon budget). Not an emergency: boards render from last exports. Runbook: DNA/Deal Management/record-layer/runbook.md step 2."
        : "Database unreachable: " + String(e).slice(0, 200),
    }, 503);
  }
}

async function ingest(request, env) {
  // Per-source bearer tokens (A11). INGEST_TOKENS = {"source":"token",...}
  const auth = request.headers.get("authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "");
  let tokens;
  try { tokens = JSON.parse(env.INGEST_TOKENS || "{}"); } catch { tokens = {}; }
  const source = Object.keys(tokens).find((s) => tokens[s] && tokens[s] === token);
  if (!source) return json({ error: "unauthorized" }, 401);

  // Size cap (A11); config default 1 MiB, enforced here pre-body-read.
  const len = parseInt(request.headers.get("content-length") || "0", 10);
  if (len > 1048576) return json({ error: "payload_too_large" }, 413);

  let payload;
  try { payload = await request.json(); } catch { return json({ error: "invalid_json" }, 400); }
  const externalId = payload.external_id || request.headers.get("x-external-id") || null;

  try {
    const sql = neon(env.DATABASE_URL_WRITER);
    // dedup on (source, external_id): duplicate inserts return 2xx so webhook
    // retries stop (A1). Payload is stored as UNTRUSTED DATA — nothing here
    // interprets it; triage happens in a hard-framed prompt elsewhere (A12).
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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") return health(env);
    if (url.pathname === "/ingest" && request.method === "POST") return ingest(request, env);
    if (url.pathname === "/mcp") {
      return json({
        error: "not_yet",
        note: "MCP verb surface lands with the OAuth allow-list. Contracts: tool-contracts-2026-07-30.md",
      }, 501);
    }
    return json({ service: "carr-mcp", surfaces: ["/health", "/ingest", "/mcp (pending)"] }, 404);
  },
};
