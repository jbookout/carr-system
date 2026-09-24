// V5-F09 workflow census: the anchor OUTSIDE the database.
//
// WHY IT EXISTS. The census chain (migration 0595, ops.workflow_census_record)
// is hash-chained and guarded inside Postgres, but whoever holds the database
// owner role can disable the guards, delete the history and insert a new,
// internally consistent chain. Nothing inside the database can detect that,
// because everything that would check it lives in the same database. This
// Durable Object holds the one fact such a rewrite cannot reproduce: the head
// (seq, row_hash) the Worker itself committed last.
//
// HOW IT IS USED.
//   write: mcp.js, after the record-workflow-census transaction COMMITS, calls
//          advanceWorkflowCensusAnchor with the new head. The object accepts a
//          head only when its seq is greater than the stored one (an equal seq
//          with the same row_hash is an idempotent replay; an equal seq with a
//          different row_hash, or a lower seq, is refused).
//   read:  the read-workflow-census verb calls readWorkflowCensusAnchor BEFORE
//          it reads the chain and returns the anchor beside it. The reader
//          (lib/workflow_census_attestation.py) refuses a chain whose head does
//          not equal the anchor, as `tampered`.
//
// WHY A DURABLE OBJECT AND NOT KV (Jev architecture_or_design, DO at 1.0).
// KV is eventually consistent and has no compare-and-set, and any Cloudflare
// API token with KV write can overwrite a key from outside. A Durable Object is
// strongly consistent and single-threaded, so it can refuse a head that does
// not advance, and its storage has no external write API: changing what it
// holds means deploying different Worker code through CI and the release
// pipeline. It needs no namespace id; `wrangler deploy` creates it from the
// [[durable_objects.bindings]] and [[migrations]] entries in wrangler.toml.
//
// WHAT IT DOES NOT DO. It does not resist a coordinated rewrite of the database
// AND a deploy of Worker code that rewrites the anchor. It does not make the
// census true. And it holds only the head, so the database's own chain is
// still what proves the rows before it.
//
// ORDER AND ITS ONE WINDOW. The anchor is advanced after commit (Jev 0.79). If
// the advance fails, the database is one row ahead of the anchor and the reader
// shows `tampered` until the writer re-sends the same idempotency key (which
// replays the row and re-advances the anchor) or the next write advances it.
// The write verb reports the failure by name so the writer job's run fails.

export const WORKFLOW_CENSUS_ANCHOR_BINDING = "WORKFLOW_CENSUS_ANCHOR";
export const WORKFLOW_CENSUS_ANCHOR_OBJECT = "workflow-census-head";

const HEX64 = /^[0-9a-f]{64}$/;
const HEAD_KEY = "head";

function validHead(head) {
  return head !== null && typeof head === "object" && !Array.isArray(head)
    && Number.isSafeInteger(head.seq) && head.seq >= 1
    && typeof head.row_hash === "string" && HEX64.test(head.row_hash);
}

// Pure decision, shared by the object and the tests.
export function decideAnchorAdvance(stored, proposed, nowIso) {
  if (!validHead(proposed))
    return { status: 400, write: false, response: { ok: false, error: "anchor_head_invalid" } };
  if (stored !== undefined && stored !== null && !validHead(stored))
    return { status: 500, write: false, response: { ok: false, error: "anchor_state_invalid" } };
  if (stored) {
    if (proposed.seq < stored.seq)
      return { status: 409, write: false,
        response: { ok: false, error: "anchor_regression_refused", anchored_seq: stored.seq } };
    if (proposed.seq === stored.seq) {
      if (proposed.row_hash === stored.row_hash)
        return { status: 200, write: false, response: { ok: true, state: "replayed", head: stored } };
      return { status: 409, write: false,
        response: { ok: false, error: "anchor_fork_refused", anchored_seq: stored.seq } };
    }
  }
  const head = { seq: proposed.seq, row_hash: proposed.row_hash, anchored_at: nowIso };
  return { status: 200, write: true, head, response: { ok: true, state: "advanced", head } };
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json" },
  });
}

// The Durable Object class. index.js re-exports it so the runtime can find it
// under the class_name wrangler.toml declares.
export class WorkflowCensusAnchor {
  constructor(ctx, _env) {
    this.ctx = ctx;
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/head") {
      const head = await this.ctx.storage.get(HEAD_KEY);
      return json({ ok: true, head: head ?? null });
    }
    if (request.method === "POST" && url.pathname === "/advance") {
      let proposed;
      try { proposed = await request.json(); }
      catch { return json({ ok: false, error: "anchor_head_invalid" }, 400); }
      // The object's input gate holds every other request while this storage
      // read is in flight, and nothing else is awaited before the put, so no
      // second advance can interleave with this read-modify-write.
      const stored = await this.ctx.storage.get(HEAD_KEY);
      const verdict = decideAnchorAdvance(stored, proposed, new Date().toISOString());
      if (verdict.write) await this.ctx.storage.put(HEAD_KEY, verdict.head);
      return json(verdict.response, verdict.status);
    }
    return json({ ok: false, error: "anchor_route_unknown" }, 404);
  }
}

function anchorStub(env) {
  const namespace = env?.[WORKFLOW_CENSUS_ANCHOR_BINDING];
  if (!namespace || typeof namespace.idFromName !== "function" || typeof namespace.get !== "function")
    return null;
  return namespace.get(namespace.idFromName(WORKFLOW_CENSUS_ANCHOR_OBJECT));
}

// Never throws: the caller decides what a failed advance means.
export async function advanceWorkflowCensusAnchor(env, head) {
  const stub = anchorStub(env);
  if (!stub) return { ok: false, error: "anchor_not_bound" };
  try {
    const response = await stub.fetch("https://workflow-census-anchor/advance", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ seq: head?.seq, row_hash: head?.row_hash }),
    });
    const body = await response.json();
    if (response.ok && body?.ok === true) return { ok: true, state: body.state, head: body.head };
    return { ok: false, error: String(body?.error || `anchor_http_${response.status}`) };
  } catch (error) {
    return { ok: false, error: "anchor_unreachable", detail: String(error?.name || "Error") };
  }
}

// Never throws. {state: "present", seq, row_hash, anchored_at} | {state: "absent"}
// | {state: "unavailable", detail}.
export async function readWorkflowCensusAnchor(env) {
  const stub = anchorStub(env);
  if (!stub) return { state: "unavailable", detail: "anchor_not_bound" };
  try {
    const response = await stub.fetch("https://workflow-census-anchor/head", { method: "GET" });
    const body = await response.json();
    if (!response.ok || body?.ok !== true) return { state: "unavailable", detail: "anchor_refused" };
    if (body.head === null || body.head === undefined) return { state: "absent" };
    if (!validHead(body.head) || typeof body.head.anchored_at !== "string")
      return { state: "unavailable", detail: "anchor_state_invalid" };
    return { state: "present", seq: body.head.seq, row_hash: body.head.row_hash,
      anchored_at: body.head.anchored_at };
  } catch (error) {
    return { state: "unavailable", detail: "anchor_unreachable" };
  }
}

// The write path's post-commit step, called by mcp.js right after the
// record-workflow-census transaction commits. `refuse` builds the caller's
// error type (mcp.js passes a ToolError factory) so this module imports none.
export async function anchorCommittedCensusWrite(env, result, refuse) {
  const anchored = await advanceWorkflowCensusAnchor(env, { seq: result?.seq, row_hash: result?.row_hash });
  if (!anchored.ok)
    throw refuse({ error: "workflow_census_anchor_not_advanced", detail: anchored.error,
      seq: result?.seq ?? null, row_hash: result?.row_hash ?? null,
      hint: "the census row is committed but the external anchor was not advanced; " +
            "re-send the same idempotency_key to replay it and re-advance the anchor" });
  return { ...result, anchor: anchored.state };
}
