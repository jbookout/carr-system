// V5-F09 workflow census: the durable, server-attested store the census reader
// (lib/control_plane_workflow_truth_reader.py) was owed as
// durable_signed_census_store_seam.
//
// TWO VERBS, ONE STORE (migrations/0595_workflow_census_store.sql):
//   record-workflow-census  write: append one census snapshot to the chain
//   read-workflow-census    read:  the chain's metadata, the latest payload,
//                                  and the server clock
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
  "idempotency_key_required",
]);

function doorRefusal(ToolError, error) {
  const message = String(error?.message || "");
  const name = DOOR_REFUSALS.find(candidate => message.includes(candidate));
  return name ? new ToolError({ error: name }) : null;
}

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
          let row;
          try {
            row = (await c.query(
              `select r.seq, r.recorded_at, r.principal, r.row_hash, r.prev_hash, r.payload_sha256, r.replayed
                 from ops.record_workflow_census($1::jsonb, $2) r`,
              [text, args.idempotency_key],
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
      description: "Read the V5-F09 workflow census chain: every row's seq, recorded_at, principal, db_session_principal, prev_hash, payload_sha256 and row_hash (oldest first, bounded by max_rows with truncated set when longer), the latest census payload, and the server clock (server_now). Verifies nothing itself: a consumer recomputes the chain and applies its own writer allowlist and freshness window.",
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
        const row = (await c.query("select ops.read_workflow_census($1) as result", [maxRows])).rows[0];
        const result = typeof row?.result === "string" ? JSON.parse(row.result) : row?.result;
        if (!isPlainObject(result) || !Array.isArray(result.chain) || typeof result.server_now !== "string")
          throw new ToolError({ error: "workflow_census_unavailable" });
        return {
          ok: true,
          schema_version: result.schema_version,
          server_now: result.server_now,
          row_count: Number(result.row_count),
          truncated: result.truncated === true,
          chain: result.chain,
          latest_payload: result.latest_payload ?? null,
        };
      },
    },
  };
}
