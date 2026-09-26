// V5-F09 workflow census: the durable, server-attested store the census reader
// (lib/control_plane_workflow_truth_reader.py) was owed as
// durable_signed_census_store_seam.
//
// THREE VERBS, ONE STORE (migrations/0708_workflow_census_store.sql):
//   record-workflow-census    write: append one census snapshot to the chain,
//                                    only when the database head is the
//                                    anchored head
//   read-workflow-census      read:  the chain's metadata, the latest payload,
//                                    the server clock, the guards' enabled
//                                    state and function digests, and the
//                                    external anchor's head
//   record-workflow-census-reanchor  partner authority only: record a receipt and
//                                    move the anchor to the database head
//
// WHAT THE CALLER CANNOT SUPPLY. Neither the principal nor the time. The
// write door reads the principal from carr.acting_actor_slug, which mcp.js
// sets from the server-derived actor before any handler runs, and stamps
// clock_timestamp(); the chain fields (seq, prev_hash, payload_sha256,
// row_hash) are computed by the database and re-checked by its chain guard.
// This module passes the census and an idempotency key, nothing else. The
// break-glass door (local-verb.mjs) never sets the actor setting, so a
// break-glass call reaches the door and is refused there by name.
//
// THE EXTERNAL ANCHOR (workflow-census-anchor.js). mcp.js attaches
// c.workflowCensusAnchor for these three verbs only. The record handler reads
// it before calling the door and passes the anchored head in, and the door
// refuses to append unless the database head is that head
// (workflow_census_anchor_gap / workflow_census_tampered). A fresh insert then
// registers its row as the anchor's pending head for its idempotency key,
// before commit (c.workflowCensusPending, record verb only); mcp.js advances
// the anchor after the write commits, and the anchor moves only to a row equal
// to that key's pending entry, so a row the door merely replays -- which the
// database owner could have written -- never moves it. The read handler reads
// the anchor BEFORE the chain. A path
// that attaches nothing (break-glass, a test double) gets
// {state: "unavailable"}: the write refuses and the reader fails closed.
//
// WHAT THE READ VERB DOES NOT DO. It does not say the chain is sound. The
// reader recomputes every hash itself and applies the writer allowlist and the
// freshness window from config-as-code, so the one component that decides
// "available" is the one that checks.

const CENSUS_SCHEMA_VERSION = "control-plane-workflow-truth.v1";
const MAX_CENSUS_CHARS = 4 * 1024 * 1024;
const READ_MAX_ROWS_DEFAULT = 20000;
const READ_MAX_ROWS_LIMIT = 100000;

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// THE WORKER VERIFIES THE ROW THE DOOR SAYS IT INSERTED (round-4 fix, R4-C1).
// The database owner can replace ops.record_workflow_census itself, store a
// forged payload, and answer the Worker with the forged row; registering that
// answer as the pending head would anchor the forgery. So before registering,
// the Worker recomputes every field it has its own value for, and refuses on
// any mismatch (nothing is registered, the transaction rolls back):
//
//   seq                   = anchored seq + 1 (1 on an empty anchor)
//   prev_hash             = anchored row_hash (null on an empty anchor)
//   principal             = the server-derived actor slug
//   payload_sha256        = sha256(canonical JSON of the census this call sent)
//   row_hash              = sha256(canonical JSON of {seq, recorded_at,
//                           principal, db_session_principal, prev_hash,
//                           payload_sha256}) over the values above, with
//                           db_session_principal = this connection's
//                           session_user as the Worker read it back at the
//                           start of the transaction (trusted_principal)
//   recorded_at           the one field the door still chooses: it must be the
//                           database's time format and within
//                           MAX_RECORDED_AT_SKEW_MS of the Worker's own clock
//
// Canonical JSON here must equal ops.scac_canonical_json and the Python
// reader's json.dumps(sort_keys=True, separators=(",", ":"),
// ensure_ascii=False): keys in code-point order (the database sorts them
// COLLATE "C", i.e. by UTF-8 bytes, which is the same order; JavaScript's
// default sort compares UTF-16 units and is NOT), strings and integers as
// JSON.stringify renders them. The census may hold only safe integers
// (fractions and integers past 2^53 render differently across the three).
// The local PostgreSQL gate checks all three agree over a varied corpus.
export const MAX_RECORDED_AT_SKEW_MS = 5 * 60 * 1000;
const RECORDED_AT_FORMAT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/;

function compareCodePoints(a, b) {
  const x = [...a], y = [...b];
  for (let i = 0; i < Math.min(x.length, y.length); i += 1) {
    const d = x[i].codePointAt(0) - y[i].codePointAt(0);
    if (d !== 0) return d;
  }
  return x.length - y.length;
}

export function canonicalCensusJson(value) {
  if (value === null || typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new Error("canonical_json_number_refused");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalCensusJson).join(",")}]`;
  if (isPlainObject(value))
    return `{${Object.keys(value).sort(compareCodePoints)
      .map(key => `${JSON.stringify(key)}:${canonicalCensusJson(value[key])}`).join(",")}}`;
  throw new Error("canonical_json_value_refused");
}

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

export async function censusPayloadSha256(census) {
  return sha256Hex(canonicalCensusJson(census));
}

export async function censusRowHash(row) {
  return sha256Hex(canonicalCensusJson({
    seq: row.seq, recorded_at: row.recorded_at, principal: row.principal,
    db_session_principal: row.db_session_principal, prev_hash: row.prev_hash,
    payload_sha256: row.payload_sha256,
  }));
}

// Returns {ok: true} or {ok: false, field} naming the first field that is not
// what the Worker expected. `anchored` is {seq, row_hash} (both null when the
// anchor holds no head).
export async function verifyInsertedCensusRow({ row, census, principal, dbSessionPrincipal, anchored,
  nowMs }) {
  const wrong = field => ({ ok: false, field });
  const seq = Number(row?.seq);
  const expectedSeq = anchored?.seq === null || anchored?.seq === undefined ? 1 : anchored.seq + 1;
  const expectedPrev = anchored?.row_hash ?? null;
  if (!Number.isSafeInteger(seq) || seq !== expectedSeq) return wrong("seq");
  if ((row.prev_hash ?? null) !== expectedPrev) return wrong("prev_hash");
  if (typeof principal !== "string" || row.principal !== principal) return wrong("principal");
  if (typeof dbSessionPrincipal !== "string" || dbSessionPrincipal === "") return wrong("db_session_principal");
  let payloadSha;
  try { payloadSha = await censusPayloadSha256(census); } catch { return wrong("payload_sha256"); }
  if (row.payload_sha256 !== payloadSha) return wrong("payload_sha256");
  if (typeof row.recorded_at !== "string" || !RECORDED_AT_FORMAT.test(row.recorded_at)
      || !(Math.abs(Date.parse(row.recorded_at) - nowMs) <= MAX_RECORDED_AT_SKEW_MS))
    return wrong("recorded_at");
  const expectedHash = await censusRowHash({ seq: expectedSeq, recorded_at: row.recorded_at, principal,
    db_session_principal: dbSessionPrincipal, prev_hash: expectedPrev, payload_sha256: payloadSha });
  if (row.row_hash !== expectedHash) return wrong("row_hash");
  return { ok: true };
}

function hasUnsafeInteger(value) {
  if (typeof value === "number") return Number.isInteger(value) && !Number.isSafeInteger(value);
  if (Array.isArray(value)) return value.some(hasUnsafeInteger);
  if (isPlainObject(value)) return Object.values(value).some(hasUnsafeInteger);
  return false;
}

function hasFraction(value) {
  if (typeof value === "number") return !Number.isInteger(value);
  if (Array.isArray(value)) return value.some(hasFraction);
  if (isPlainObject(value)) return Object.values(value).some(hasFraction);
  return false;
}

// The database raises these by name; translate them to a ToolError so a
// caller sees the refusal rather than an unhandled_verb_failure.
const DOOR_REFUSALS = Object.freeze([
  "workflow_census_principal_unavailable",
  "workflow_census_payload_invalid",
  "workflow_census_payload_shape_refused",
  "workflow_census_payload_too_large",
  "workflow_census_payload_fraction_refused",
  "workflow_census_key_reuse",
  "workflow_census_time_regression_refused",
  "workflow_census_chain_splice_refused",
  "workflow_census_principal_forged",
  "workflow_census_session_principal_forged",
  "workflow_census_anchor_gap",
  "workflow_census_anchor_invalid",
  "workflow_census_tampered",
  "workflow_census_reanchor_requires_partner",
  "workflow_census_reanchor_reason_required",
  "workflow_census_reanchor_head_moved",
  "workflow_census_reanchor_not_needed",
  "idempotency_key_required",
]);

const DOOR_HINTS = Object.freeze({
  workflow_census_anchor_gap: "the database holds one committed census row the anchor never took; " +
    "re-send that write's idempotency_key to replay it and advance the anchor (it advances only if " +
    "this Worker inserted that row), or have a partner re-anchor with record-workflow-census-reanchor",
  workflow_census_tampered: "the database head is not the head the external anchor holds; nothing " +
    "was appended. Investigate, then a partner may re-anchor with record-workflow-census-reanchor",
});

function doorRefusal(ToolError, error) {
  const message = String(error?.message || "");
  const name = DOOR_REFUSALS.find(candidate => message === candidate || message.includes(candidate));
  if (!name) return null;
  const detail = typeof error?.detail === "string" && /^[a-z_]{1,80}$/.test(error.detail) ? error.detail : null;
  return new ToolError({ error: name, ...(detail ? { detail } : {}),
    ...(DOOR_HINTS[name] ? { hint: DOOR_HINTS[name] } : {}) });
}

async function anchoredHead(c) {
  const anchor = typeof c.workflowCensusAnchor === "function"
    ? await c.workflowCensusAnchor()
    : { state: "unavailable", detail: "anchor_not_bound" };
  if (anchor?.state === "absent") return { seq: null, row_hash: null };
  if (anchor?.state === "present" && Number.isSafeInteger(anchor.seq) && typeof anchor.row_hash === "string")
    return { seq: anchor.seq, row_hash: anchor.row_hash };
  return null;
}

const HEX64 = /^[0-9a-f]{64}$/;

export function workflowCensusTools({ withEnvelope, ToolError }) {
  return {
    "record-workflow-census": {
      write: true,
      description: "Append one V5-F09 workflow census snapshot (the output of lib/control_plane_workflow_truth.workflow_truth, schema control-plane-workflow-truth.v1) to the append-only, database-hash-chained census store. The server stamps the principal (the Worker-derived actor) and the time; the caller supplies neither. Returns seq, recorded_at, principal, row_hash, prev_hash and payload_sha256. Idempotent on idempotency_key. The record attests who recorded the census and when, not that the census is true.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string", minLength: 1, maxLength: 200 },
          census: { type: "object" },
        },
        required: ["idempotency_key", "census"],
      },
      handler: async (c, actor, args) => {
        const census = args.census;
        if (!isPlainObject(census))
          throw new ToolError({ error: "workflow_census_payload_invalid", hint: "census must be an object" });
        if (census.schema_version !== CENSUS_SCHEMA_VERSION || !Array.isArray(census.rows) ||
            !isPlainObject(census.summary))
          throw new ToolError({ error: "workflow_census_payload_shape_refused",
            hint: `census must carry schema_version ${CENSUS_SCHEMA_VERSION}, a rows array and a summary object` });
        if (hasFraction(census))
          throw new ToolError({ error: "workflow_census_payload_fraction_refused",
            hint: "the chain hashes canonical JSON; non-integer numbers render differently across languages" });
        if (hasUnsafeInteger(census))
          throw new ToolError({ error: "workflow_census_payload_unsafe_integer_refused",
            hint: "the chain hashes canonical JSON; integers beyond 2^53 render differently across languages" });
        const text = JSON.stringify(census);
        if (text.length > MAX_CENSUS_CHARS)
          throw new ToolError({ error: "workflow_census_payload_too_large", limit: MAX_CENSUS_CHARS, got: text.length });
        return withEnvelope(c, actor, "record-workflow-census", args, async () => {
          // Inside the envelope, so a retry the envelope replays never needs
          // the anchor: that is how a committed-but-unanchored row recovers.
          const head = await anchoredHead(c);
          if (!head)
            throw new ToolError({ error: "workflow_census_anchor_unavailable",
              hint: "the external anchor could not be read, so the door cannot check the head; nothing was appended" });
          let row;
          try {
            row = (await c.query(
              `select r.seq, r.recorded_at, r.principal, r.row_hash, r.prev_hash, r.payload_sha256, r.replayed
                 from ops.record_workflow_census($1::jsonb, $2, $3::bigint, $4) r`,
              [text, args.idempotency_key, head.seq, head.row_hash],
            )).rows[0];
          } catch (error) {
            throw doorRefusal(ToolError, error) ?? error;
          }
          if (!row?.row_hash) throw new ToolError({ error: "workflow_census_record_refused" });
          // A FRESH insert registers itself with the anchor as the pending head
          // for this key, still inside the transaction: the post-commit advance
          // must equal it. A replay registers nothing, so a row the door merely
          // handed back (one the owner could have written under this key)
          // cannot move the anchor (R3-C1). A refusal here rolls the row back.
          if (row.replayed !== true) {
            const verified = await verifyInsertedCensusRow({ row, census: JSON.parse(text),
              principal: actor?.slug, dbSessionPrincipal: actor?.trusted_principal?.session_principal,
              anchored: head, nowMs: Date.now() });
            if (!verified.ok)
              throw new ToolError({ error: "workflow_census_row_unverified", field: verified.field,
                hint: "the row the write door returned is not the row this call asked it to insert; " +
                      "nothing was registered with the anchor and the write rolled back. The door " +
                      "itself may have been replaced: investigate before retrying" });
            const pending = typeof c.workflowCensusPending === "function"
              ? await c.workflowCensusPending(
                { seq: Number(row.seq), row_hash: row.row_hash, prev_hash: row.prev_hash ?? null },
                args.idempotency_key)
              : { ok: false, error: "anchor_not_bound" };
            if (!pending?.ok)
              throw new ToolError({ error: "workflow_census_anchor_pending_refused",
                detail: String(pending?.error || "anchor_unreachable"),
                hint: "the anchor did not take this row as its pending head, so nothing was appended; " +
                      "re-send the same idempotency_key" });
          }
          return {
            ok: true,
            seq: Number(row.seq),
            recorded_at: row.recorded_at,
            principal: row.principal,
            row_hash: row.row_hash,
            prev_hash: row.prev_hash ?? null,
            payload_sha256: row.payload_sha256,
            replayed: row.replayed === true,
          };
        });
      },
    },

    "read-workflow-census": {
      write: false,
      description: "Read the V5-F09 workflow census chain: every row's seq, recorded_at, principal, db_session_principal, prev_hash, payload_sha256 and row_hash (oldest first, bounded by max_rows with truncated set when longer), the latest census payload, the server clock (server_now), each store trigger's enabled state (guards) and a sha256 of the function each calls (guard_functions), and the head recorded by the external anchor outside the database (anchor). Verifies nothing itself: a consumer recomputes the chain, compares its head with the anchor, and applies its own writer allowlist and freshness window.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          max_rows: { type: "integer", minimum: 1, maximum: READ_MAX_ROWS_LIMIT },
        },
      },
      handler: async (c, _actor, args) => {
        const maxRows = args?.max_rows === undefined || args?.max_rows === null
          ? READ_MAX_ROWS_DEFAULT : args.max_rows;
        if (!Number.isInteger(maxRows) || maxRows < 1 || maxRows > READ_MAX_ROWS_LIMIT)
          throw new ToolError({ error: "workflow_census_max_rows_invalid",
            hint: `max_rows must be an integer 1..${READ_MAX_ROWS_LIMIT}` });
        // Anchor FIRST, chain second: a write that commits in between leaves the
        // chain ahead of the anchor, which the reader refuses and re-reads,
        // rather than an anchor ahead of the chain it was read beside.
        const anchor = typeof c.workflowCensusAnchor === "function"
          ? await c.workflowCensusAnchor()
          : { state: "unavailable", detail: "anchor_not_bound" };
        const row = (await c.query("select ops.read_workflow_census($1) as result", [maxRows])).rows[0];
        const result = typeof row?.result === "string" ? JSON.parse(row.result) : row?.result;
        if (!isPlainObject(result) || !Array.isArray(result.chain) || typeof result.server_now !== "string"
            || !isPlainObject(result.guards) || !isPlainObject(result.guard_functions))
          throw new ToolError({ error: "workflow_census_unavailable" });
        return {
          ok: true,
          schema_version: result.schema_version,
          server_now: result.server_now,
          row_count: Number(result.row_count),
          truncated: result.truncated === true,
          chain: result.chain,
          latest_payload: result.latest_payload ?? null,
          guards: result.guards,
          guard_functions: result.guard_functions,
          anchor: isPlainObject(anchor) ? anchor : { state: "unavailable", detail: "anchor_state_invalid" },
        };
      },
    },

    "record-workflow-census-reanchor": {
      write: true, humanOnly: true, authorityOnly: true,
      description: "PARTNER AUTHORITY ONLY: move the V5-F09 census anchor (the Durable Object outside the database) to the database's current census head, on the record. Use it only when the two legitimately disagree -- the writer lost the idempotency key of a committed row the anchor never took (anchor_gap), or the database was restored. The server reads the anchor itself; you pass the database head you reviewed and accept (accept_head, from read-workflow-census; null for an empty chain) and a reason. It appends a receipt (actor, verified partner, reason, old anchored head, new head, rows the anchor never vouched for) and then applies it to the anchor as a compare-and-set; the reader shows the latest receipt beside every later attestation. Refuses when the head moved since you reviewed it, when nothing disagrees, and on any writer connection.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string", minLength: 1, maxLength: 200 },
          reason: { type: "string", minLength: 1, maxLength: 1000 },
          accept_head: {
            type: ["object", "null"], additionalProperties: false,
            properties: { seq: { type: "integer", minimum: 1 }, row_hash: { type: "string" } },
            required: ["seq", "row_hash"],
          },
        },
        required: ["idempotency_key", "reason", "accept_head"],
      },
      handler: async (c, actor, args) => {
        const accept = args.accept_head;
        if (accept !== null && (!isPlainObject(accept) || !Number.isSafeInteger(accept.seq) || accept.seq < 1
            || typeof accept.row_hash !== "string" || !HEX64.test(accept.row_hash)))
          throw new ToolError({ error: "workflow_census_anchor_invalid",
            hint: "accept_head is null or {seq, row_hash} exactly as read-workflow-census served the head" });
        if (typeof args.reason !== "string" || !args.reason.trim())
          throw new ToolError({ error: "workflow_census_reanchor_reason_required" });
        return withEnvelope(c, actor, "record-workflow-census-reanchor", args, async () => {
          const head = await anchoredHead(c);
          if (!head)
            throw new ToolError({ error: "workflow_census_anchor_unavailable",
              hint: "the external anchor could not be read; nothing was recorded" });
          let row;
          try {
            row = (await c.query(
              `select r.receipt_id, r.recorded_at, r.actor, r.verified_partner, r.reason, r.old_seq,
                      r.old_row_hash, r.new_seq, r.new_row_hash, r.rows_reattested, r.replayed
                 from ops.reanchor_workflow_census($1::bigint, $2, $3::bigint, $4, $5, $6) r`,
              [head.seq, head.row_hash, accept?.seq ?? null, accept?.row_hash ?? null, args.reason,
               args.idempotency_key],
            )).rows[0];
          } catch (error) {
            throw doorRefusal(ToolError, error) ?? error;
          }
          if (!row?.receipt_id) throw new ToolError({ error: "workflow_census_reanchor_refused" });
          const headOf = (seq, hash) => seq === null || seq === undefined ? null : { seq: Number(seq), row_hash: hash };
          return {
            ok: true,
            replayed: row.replayed === true,
            receipt: {
              receipt_id: String(row.receipt_id),
              recorded_at: row.recorded_at,
              actor: row.actor,
              verified_partner: row.verified_partner,
              reason: row.reason,
              old_head: headOf(row.old_seq, row.old_row_hash),
              new_head: headOf(row.new_seq, row.new_row_hash),
              rows_reattested: Number(row.rows_reattested),
            },
          };
        });
      },
    },
  };
}
