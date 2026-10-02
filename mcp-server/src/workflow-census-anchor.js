// V5-F09 workflow census: the anchor OUTSIDE the database.
//
// WHY IT EXISTS. The census chain (migration 0708, ops.workflow_census_record)
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
// PENDING HEADS (round-3 fix, R3-C1). Linkage alone let the database owner
// launder a row: forge a row linked to the anchored head under an idempotency
// key K, call record-workflow-census with K, and the door's replay branch
// hands the forged row back, which links, so the anchor took it. The root
// cause is that the anchor trusted a row the owner can write. So the anchor
// now moves ONLY to a row the Worker itself saw the door INSERT: inside the
// write transaction, before commit, the Worker registers a pending head
// (idempotency key -> seq, prev_hash, row_hash), linked to the anchored head,
// and only for a fresh insert (replayed = false). Every advance that moves the
// head must equal that key's pending entry exactly, and not be expired:
//
//   anchor_pending_missing_refused   no pending entry for the key (the replay
//                                    of a row the Worker never inserted)
//   anchor_pending_expired_refused   older than PENDING_TTL_MS
//   anchor_pending_mismatch_refused  the key's entry names another row
//   anchor_pending_unlinked_refused  (register) not the next linked row
//   anchor_pending_full_refused      (register) MAX_PENDING live entries
//
// A real crash between commit and advance keeps its entry, so the writer's
// same-key retry (a replay) still advances. A forged row replayed under any
// key does not, and the chain reads anchor_gap until a partner re-anchors it
// on the record. An advance clears its entry and every entry at or below the
// new head; registration prunes expired and dead entries; a re-anchor clears
// them all. An entry whose transaction then rolled back is harmless: only the
// exact row it names can ever match it, within the TTL.
//
// WHAT GETS REGISTERED IS VERIFIED FIRST (round-4 fix, R4-C1). The owner can
// also replace the write door so it stores a forged payload and answers with
// that row. So the verb registers nothing it has not recomputed itself: seq,
// prev_hash, principal, payload digest and row hash against the census it sent,
// the anchored head, the server-derived actor and its own session principal
// (workflow-census.js, verifyInsertedCensusRow). This object therefore only
// ever anchors a hash over the census the Worker was given.
//
// SERIALIZED. Every read-modify-write runs inside ctx.blockConcurrencyWhile,
// so no second request is delivered to the object until it finishes. The
// storage input gate alone does not guarantee that across non-storage awaits
// (mcp-server/test/workflow-census-anchor-miniflare.test.mjs shows the race on
// a plain store and its absence here, under real workerd).
//
// RE-ANCHOR. When the database and the anchor legitimately disagree (the
// writer lost the idempotency key of a committed-but-unanchored row; a
// database restore), the only way forward is the record-workflow-census-reanchor verb:
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
const PENDING_KEY_PREFIX = "pending:";
export const PENDING_TTL_MS = 24 * 60 * 60 * 1000;
export const MAX_PENDING = 16;

function seqKey(seq) {
  return `${SEQ_KEY_PREFIX}${String(seq).padStart(16, "0")}`;
}

function pendingKey(idempotencyKey) {
  return `${PENDING_KEY_PREFIX}${idempotencyKey}`;
}

function validIdempotencyKey(key) {
  return typeof key === "string" && key.length >= 1 && key.length <= 200;
}

function validPendingEntry(entry) {
  return validProposal(entry) && typeof entry.expires_at === "string"
    && Number.isFinite(Date.parse(entry.expires_at));
}

// Linked to the anchored head: genesis when there is none, else the next seq
// whose prev_hash is the anchored hash.
function linksToStored(stored, proposed) {
  if (!stored) return proposed.seq === 1 && proposed.prev_hash === null;
  return proposed.seq === stored.seq + 1 && proposed.prev_hash === stored.row_hash;
}

// A pending entry that can still match something: well formed, unexpired, and
// above the anchored head.
function pendingLive(entry, stored, nowMs) {
  return validPendingEntry(entry) && Date.parse(entry.expires_at) > nowMs
    && (!stored || entry.seq > stored.seq);
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

// Pure decision for registering a pending head. `pending` maps idempotency key
// -> entry for every stored entry. The result names the entries to prune
// (expired or at/below the head) besides the one it writes.
export function decidePendingRegister(stored, pending, proposed, nowIso) {
  if (!validProposal(proposed) || !validIdempotencyKey(proposed.idempotency_key))
    return refused(400, "anchor_pending_invalid");
  if (stored !== undefined && stored !== null && !validHead(stored))
    return refused(500, "anchor_state_invalid");
  if (!linksToStored(stored, proposed))
    return refused(409, "anchor_pending_unlinked_refused",
      { anchored_seq: stored?.seq ?? null, proposed_seq: proposed.seq });
  const nowMs = Date.parse(nowIso);
  const key = proposed.idempotency_key;
  const entries = Object.entries(pending ?? {});
  const prune = entries.filter(([k, e]) => k !== key && !pendingLive(e, stored, nowMs)).map(([k]) => k);
  const live = entries.filter(([k, e]) => k !== key && pendingLive(e, stored, nowMs)).length;
  if (live >= MAX_PENDING)
    return refused(409, "anchor_pending_full_refused", { live });
  const entry = { seq: proposed.seq, prev_hash: proposed.prev_hash, row_hash: proposed.row_hash,
    registered_at: nowIso, expires_at: new Date(nowMs + PENDING_TTL_MS).toISOString() };
  return { status: 200, write: true, key, entry, prune,
    response: { ok: true, state: "pending", pending: { seq: entry.seq, row_hash: entry.row_hash,
      expires_at: entry.expires_at } } };
}

// Pure decision, shared by the object and the tests. `anchoredHashAt(seq)`
// answers the row_hash the object anchored at that seq, or undefined;
// `pendingFor(key)` answers the pending entry registered under that
// idempotency key, or undefined. A proposal that would MOVE the head must
// equal its key's live pending entry; one that moves nothing needs none.
export function decideAnchorAdvance(stored, proposed, nowIso, anchoredHashAt = () => undefined,
  pendingFor = () => undefined) {
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
  const key = proposed.idempotency_key;
  const pending = validIdempotencyKey(key) ? pendingFor(key) : undefined;
  if (pending === undefined || pending === null)
    return refused(409, "anchor_pending_missing_refused", { proposed_seq: proposed.seq });
  if (!validPendingEntry(pending) || pending.seq !== proposed.seq
      || pending.row_hash !== proposed.row_hash || pending.prev_hash !== proposed.prev_hash)
    return refused(409, "anchor_pending_mismatch_refused", { proposed_seq: proposed.seq });
  if (Date.parse(pending.expires_at) <= Date.parse(nowIso))
    return refused(409, "anchor_pending_expired_refused", { proposed_seq: proposed.seq });
  const head = { seq: proposed.seq, row_hash: proposed.row_hash, anchored_at: nowIso };
  return { status: 200, write: true, head, clear: key, response: { ok: true, state: "advanced", head } };
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
      const [head, reanchor, pending] = await this.serialized(async () => [
        await this.ctx.storage.get(HEAD_KEY), await this.ctx.storage.get(REANCHOR_KEY),
        await this.ctx.storage.list({ prefix: PENDING_KEY_PREFIX })]);
      // pending_heads: how many stored pending entries could still match
      // (observability only; the reader does not use it).
      const nowMs = Date.now();
      const live = [...pending.values()].filter(e => pendingLive(e, head ?? null, nowMs)).length;
      return json({ ok: true, head: head ?? null, last_reanchor: reanchor ?? null,
        pending_heads: live, pending_entries: pending.size });
    }
    if (request.method === "POST"
        && (url.pathname === "/advance" || url.pathname === "/reanchor" || url.pathname === "/pending")) {
      let body;
      try { body = await request.json(); }
      catch { return json({ ok: false, error: "anchor_head_invalid" }, 400); }
      const verdict = await this.serialized(async () => {
        const storage = this.ctx.storage;
        const stored = await storage.get(HEAD_KEY);
        const now = new Date().toISOString();
        if (url.pathname === "/pending") {
          const pending = {};
          for (const [k, entry] of await storage.list({ prefix: PENDING_KEY_PREFIX }))
            pending[k.slice(PENDING_KEY_PREFIX.length)] = entry;
          const decided = decidePendingRegister(stored, pending, body, now);
          if (decided.write) {
            if (decided.prune.length) await storage.delete(decided.prune.map(pendingKey));
            await storage.put(pendingKey(decided.key), decided.entry);
          }
          return decided;
        }
        if (url.pathname === "/advance") {
          const history = validHead(body) && validHead(stored) && body.seq < stored.seq
            ? await storage.get(seqKey(body.seq)) : undefined;
          const pending = validIdempotencyKey(body?.idempotency_key)
            ? await storage.get(pendingKey(body.idempotency_key)) : undefined;
          const decided = decideAnchorAdvance(stored, body, now, () => history, () => pending);
          if (decided.write) {
            // The matched entry and every entry at or below the new head can
            // never match again.
            const dead = [pendingKey(decided.clear)];
            for (const [k, entry] of await storage.list({ prefix: PENDING_KEY_PREFIX }))
              if (k !== dead[0] && !(validPendingEntry(entry) && entry.seq > decided.head.seq)) dead.push(k);
            await storage.delete(dead);
            await storage.put({ [HEAD_KEY]: decided.head, [seqKey(decided.head.seq)]: decided.head.row_hash });
          }
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
export async function advanceWorkflowCensusAnchor(env, head, idempotencyKey) {
  const answer = await postToAnchor(env, "/advance",
    { seq: head?.seq, row_hash: head?.row_hash, prev_hash: head?.prev_hash ?? null,
      idempotency_key: idempotencyKey });
  return answer.ok ? { ok: true, state: answer.state, head: answer.head } : answer;
}

// Never throws. Registers the row the door just INSERTED (not replayed) as the
// pending head for its idempotency key, before the transaction commits.
export async function registerWorkflowCensusPending(env, row, idempotencyKey) {
  const answer = await postToAnchor(env, "/pending",
    { seq: row?.seq, row_hash: row?.row_hash, prev_hash: row?.prev_hash ?? null,
      idempotency_key: idempotencyKey });
  return answer.ok ? { ok: true, state: answer.state, pending: answer.pending } : answer;
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
export async function anchorCommittedCensusWrite(env, result, refuse, idempotencyKey) {
  const anchored = await advanceWorkflowCensusAnchor(env,
    { seq: result?.seq, row_hash: result?.row_hash, prev_hash: result?.prev_hash ?? null }, idempotencyKey);
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
