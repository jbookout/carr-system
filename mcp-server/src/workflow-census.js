// V5-F09 workflow census: the durable, server-attested store the census reader
// (lib/control_plane_workflow_truth_reader.py) was owed as
// durable_signed_census_store_seam.
//
// THREE VERBS, ONE STORE (migrations/0595_workflow_census_store.sql):
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
// (workflow_census_anchor_gap / workflow_census_tampered); mcp.js advances it
// after the write commits. The read handler reads it BEFORE the chain. A path
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
    "re-send that write's idempotency_key to replay it and advance the anchor, or have a partner " +
    "re-anchor with record-workflow-census-reanchor",
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
