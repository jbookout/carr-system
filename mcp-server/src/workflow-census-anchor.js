// V5-F09 workflow census: the anchor OUTSIDE the database.
//
// WHY IT EXISTS. The census chain (migration 0595, ops.workflow_census_record)
// is hash-chained and guarded inside Postgres, but whoever holds the database
// owner role can disable or replace the guards, delete the history and insert
// a new, internally consistent chain. Nothing inside the database can detect
// that, because everything that would check it lives in the same database.
// This Durable Object holds the one fact such a rewrite cannot reproduce: the
// head (seq, row_hash) the Worker itself committed last.
//
// STRICT LINKAGE (round-2 fix, Jev 0.94). The object advances ONLY to the next
// row of the chain it already holds: seq == stored.seq + 1 AND
// prev_hash == stored.row_hash (genesis: no stored head, seq 1, prev_hash
// null). Every other proposal is refused by name. The earlier rule, "any
// higher seq", laundered a rewrite: after an owner replaced the history, the
// next ordinary write was accepted and the forged chain matched again. Under
// strict linkage a rewritten chain can never link to the anchored head, so it
// stays refused until a partner re-anchors it on the record (below).
//
//   anchor_head_invalid        malformed proposal
//   anchor_genesis_refused     no head yet, and the proposal is not seq 1
//   anchor_link_refused        next seq, but prev_hash is not the anchored hash
//   anchor_gap_refused         seq jumps past the next one
//   anchor_fork_refused        an already-anchored seq with a different hash
//   anchor_regression_refused  an older seq the object has no record of
//   state "replayed"           an already-anchored seq with the same hash: a
//                              late retry of a write that did advance
//
// The object keeps the row_hash of every seq it has anchored (one key per
// seq), so a late retry of seq k after k+1 is anchored answers "replayed"
// rather than a refusal.
//
// SERIALIZED. Every read-modify-write runs inside ctx.blockConcurrencyWhile,
// so no second request is delivered to the object until it finishes. The
// storage input gate alone does not guarantee that across non-storage awaits
// (mcp-server/test/workflow-census-anchor-miniflare.test.mjs shows the race on
// a plain store and its absence here, under real workerd).
//
// RE-ANCHOR. When the database and the anchor legitimately disagree (the
// writer lost the idempotency key of a committed-but-unanchored row; a
// database restore), the only way forward is the reanchor-workflow-census verb:
// partner-authority only, recorded first as a receipt row in
// ops.workflow_census_reanchor_receipt (actor, reason, old head, new head,
// rows re-attested), then applied here as a compare-and-set against the old
// head the receipt names. The object keeps the last receipt and the read verb
// returns it beside the head, so a re-anchored chain is visibly re-anchored.
//
// WHY A DURABLE OBJECT AND NOT KV (Jev architecture_or_design, DO at 1.0).
// KV is eventually consistent and has no compare-and-set, and any Cloudflare
// API token with KV write can overwrite a key from outside. A Durable Object is
// strongly consistent and single-threaded, and its storage has no external
// write API: changing what it holds means deploying different Worker code
// through CI and the release pipeline.
//
// WHAT IT DOES NOT DO. It does not resist a coordinated rewrite of the database
// AND a deploy of Worker code that rewrites the anchor, nor a partner-authority
// re-anchor of a forged chain (that act is on the record, not prevented). It
// does not make the census true.

export const WORKFLOW_CENSUS_ANCHOR_BINDING = "WORKFLOW_CENSUS_ANCHOR";
export const WORKFLOW_CENSUS_ANCHOR_OBJECT = "workflow-census-head";

const HEX64 = /^[0-9a-f]{64}$/;
const HEAD_KEY = "head";
const REANCHOR_KEY = "last_reanchor";
const SEQ_KEY_PREFIX = "seq:";

function seqKey(seq) {
  return `${SEQ_KEY_PREFIX}${String(seq).padStart(16, "0")}`;
}

function validHead(head) {
  return head !== null && typeof head === "object" && !Array.isArray(head)
    && Number.isSafeInteger(head.seq) && head.seq >= 1
    && typeof head.row_hash === "string" && HEX64.test(head.row_hash);
}

function validProposal(proposed) {
  if (!validHead(proposed)) return false;
  if (proposed.seq === 1) return proposed.prev_hash === null;
  return typeof proposed.prev_hash === "string" && HEX64.test(proposed.prev_hash);
}

function refused(status, error, extra = {}) {
  return { status, write: false, response: { ok: false, error, ...extra } };
}

// Pure decision, shared by the object and the tests. `anchoredHashAt(seq)`
// answers the row_hash the object anchored at that seq, or undefined.
export function decideAnchorAdvance(stored, proposed, nowIso, anchoredHashAt = () => undefined) {
  if (!validProposal(proposed)) return refused(400, "anchor_head_invalid");
  if (stored !== undefined && stored !== null && !validHead(stored))
    return refused(500, "anchor_state_invalid");
  if (!stored) {
    if (proposed.seq !== 1) return refused(409, "anchor_genesis_refused", { proposed_seq: proposed.seq });
  } else if (proposed.seq <= stored.seq) {
    const known = proposed.seq === stored.seq ? stored.row_hash : anchoredHashAt(proposed.seq);
    if (known === proposed.row_hash)
      return { status: 200, write: false, response: { ok: true, state: "replayed", head: stored } };
    if (typeof known === "string")
      return refused(409, "anchor_fork_refused", { anchored_seq: stored.seq, proposed_seq: proposed.seq });
    return refused(409, "anchor_regression_refused", { anchored_seq: stored.seq, proposed_seq: proposed.seq });
  } else if (proposed.seq !== stored.seq + 1) {
    return refused(409, "anchor_gap_refused", { anchored_seq: stored.seq, proposed_seq: proposed.seq });
  } else if (proposed.prev_hash !== stored.row_hash) {
    return refused(409, "anchor_link_refused", { anchored_seq: stored.seq, proposed_seq: proposed.seq });
  }
  const head = { seq: proposed.seq, row_hash: proposed.row_hash, anchored_at: nowIso };
  return { status: 200, write: true, head, response: { ok: true, state: "advanced", head } };
}

function sameHead(a, b) {
  if (a === null || b === null) return a === b;
  return a.seq === b.seq && a.row_hash === b.row_hash;
}

function validReceipt(receipt) {
  const headOrNull = h => h === null || validHead(h);
  return receipt !== null && typeof receipt === "object" && !Array.isArray(receipt)
    && typeof receipt.receipt_id === "string" && receipt.receipt_id.length > 0
    && typeof receipt.actor === "string" && receipt.actor.length > 0
    && typeof receipt.recorded_at === "string"
    && Number.isSafeInteger(receipt.rows_reattested) && receipt.rows_reattested >= 0
    && headOrNull(receipt.old_head) && headOrNull(receipt.new_head)
    && !sameHead(receipt.old_head, receipt.new_head);
}

// Pure decision for a re-anchor: a compare-and-set against the old head the
// committed receipt names. A second delivery of the same receipt is a replay.
export function decideAnchorReanchor(stored, lastReanchor, receipt, nowIso) {
  if (!validReceipt(receipt)) return refused(400, "anchor_reanchor_invalid");
  if (stored !== undefined && stored !== null && !validHead(stored))
    return refused(500, "anchor_state_invalid");
  const current = stored ? { seq: stored.seq, row_hash: stored.row_hash } : null;
  const oldHead = receipt.old_head && { seq: receipt.old_head.seq, row_hash: receipt.old_head.row_hash };
  const newHead = receipt.new_head && { seq: receipt.new_head.seq, row_hash: receipt.new_head.row_hash };
  if (lastReanchor?.receipt_id === receipt.receipt_id && sameHead(current, newHead))
    return { status: 200, write: false, response: { ok: true, state: "replayed", head: stored ?? null } };
  if (!sameHead(current, oldHead))
    return refused(409, "anchor_reanchor_conflict", { anchored_seq: current?.seq ?? null });
  const head = newHead ? { ...newHead, anchored_at: nowIso } : null;
  const record = { receipt_id: receipt.receipt_id, actor: receipt.actor, recorded_at: receipt.recorded_at,
    rows_reattested: receipt.rows_reattested, old_head: oldHead, new_head: newHead, applied_at: nowIso };
  return { status: 200, write: true, head, record,
    response: { ok: true, state: "reanchored", head, reanchor: record } };
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

  // Every read-modify-write goes through here: nothing else is delivered to
  // the object until `fn` settles.
  serialized(fn) {
    return this.ctx.blockConcurrencyWhile(fn);
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/head") {
      const [head, reanchor] = await this.serialized(async () => [
        await this.ctx.storage.get(HEAD_KEY), await this.ctx.storage.get(REANCHOR_KEY)]);
      return json({ ok: true, head: head ?? null, last_reanchor: reanchor ?? null });
    }
    if (request.method === "POST" && (url.pathname === "/advance" || url.pathname === "/reanchor")) {
      let body;
      try { body = await request.json(); }
      catch { return json({ ok: false, error: "anchor_head_invalid" }, 400); }
      const verdict = await this.serialized(async () => {
        const storage = this.ctx.storage;
        const stored = await storage.get(HEAD_KEY);
        const now = new Date().toISOString();
        if (url.pathname === "/advance") {
          const history = validHead(body) && validHead(stored) && body.seq < stored.seq
            ? await storage.get(seqKey(body.seq)) : undefined;
          const decided = decideAnchorAdvance(stored, body, now, () => history);
          if (decided.write)
            await storage.put({ [HEAD_KEY]: decided.head, [seqKey(decided.head.seq)]: decided.head.row_hash });
          return decided;
        }
        const decided = decideAnchorReanchor(stored, await storage.get(REANCHOR_KEY), body, now);
        if (decided.write) {
          // A re-anchor replaces what the object vouches for: the old per-seq
          // history no longer describes the chain it now anchors.
          await storage.deleteAll();
          const entries = { [REANCHOR_KEY]: decided.record };
          if (decided.head) {
            entries[HEAD_KEY] = decided.head;
            entries[seqKey(decided.head.seq)] = decided.head.row_hash;
          }
          await storage.put(entries);
        }
        return decided;
      });
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

async function postToAnchor(env, path, body) {
  const stub = anchorStub(env);
  if (!stub) return { ok: false, error: "anchor_not_bound" };
  try {
    const response = await stub.fetch(`https://workflow-census-anchor${path}`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    });
    const answer = await response.json();
    if (response.ok && answer?.ok === true) return { ok: true, ...answer };
    return { ok: false, error: String(answer?.error || `anchor_http_${response.status}`) };
  } catch (error) {
    return { ok: false, error: "anchor_unreachable", detail: String(error?.name || "Error") };
  }
}

// Never throws: the caller decides what a failed advance means.
export async function advanceWorkflowCensusAnchor(env, head) {
  const answer = await postToAnchor(env, "/advance",
    { seq: head?.seq, row_hash: head?.row_hash, prev_hash: head?.prev_hash ?? null });
  return answer.ok ? { ok: true, state: answer.state, head: answer.head } : answer;
}

// Never throws. {state: "present", seq, row_hash, anchored_at, last_reanchor}
// | {state: "absent", last_reanchor} | {state: "unavailable", detail}.
export async function readWorkflowCensusAnchor(env) {
  const stub = anchorStub(env);
  if (!stub) return { state: "unavailable", detail: "anchor_not_bound" };
  try {
    const response = await stub.fetch("https://workflow-census-anchor/head", { method: "GET" });
    const body = await response.json();
    if (!response.ok || body?.ok !== true) return { state: "unavailable", detail: "anchor_refused" };
    const lastReanchor = body.last_reanchor ?? null;
    if (body.head === null || body.head === undefined) return { state: "absent", last_reanchor: lastReanchor };
    if (!validHead(body.head) || typeof body.head.anchored_at !== "string")
      return { state: "unavailable", detail: "anchor_state_invalid" };
    return { state: "present", seq: body.head.seq, row_hash: body.head.row_hash,
      anchored_at: body.head.anchored_at, last_reanchor: lastReanchor };
  } catch (error) {
    return { state: "unavailable", detail: "anchor_unreachable" };
  }
}

// The write path's post-commit step, called by mcp.js right after the
// record-workflow-census transaction commits. `refuse` builds the caller's
// error type (mcp.js passes a ToolError factory) so this module imports none.
export async function anchorCommittedCensusWrite(env, result, refuse) {
  const anchored = await advanceWorkflowCensusAnchor(env,
    { seq: result?.seq, row_hash: result?.row_hash, prev_hash: result?.prev_hash ?? null });
  if (!anchored.ok)
    throw refuse({ error: "workflow_census_anchor_not_advanced", detail: anchored.error,
      seq: result?.seq ?? null, row_hash: result?.row_hash ?? null,
      hint: "the census row is committed but the external anchor was not advanced; " +
            "re-send the same idempotency_key to replay it and re-advance the anchor" });
  return { ...result, anchor: anchored.state };
}

// The re-anchor verb's post-commit step: apply the committed receipt.
export async function applyCommittedCensusReanchor(env, result, refuse) {
  const receipt = result?.receipt;
  const answer = await postToAnchor(env, "/reanchor", receipt);
  if (!answer.ok)
    throw refuse({ error: "workflow_census_reanchor_not_applied", detail: answer.error,
      receipt_id: receipt?.receipt_id ?? null,
      hint: "the receipt is committed but the anchor did not take it; re-send the same " +
            "idempotency_key to replay the receipt and re-apply it" });
  return { ...result, anchor: answer.state };
}
