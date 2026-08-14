// trace.js — Program 4, Gap A2 (defect cae5be2e-267c-40ba-9424-b4618845e905,
// 2026-08-14). correlation.js mints and echoes an x-correlation-id on every
// route and sets env.CORRELATION_ID, but nothing wrote it anywhere a failed
// request could later be found by it. An outage drill proved the gap live:
// an unauthenticated /mcp call to staging returned a truthful 401 carrying the
// id, and ops.v_trace on staging had nothing for it. See migrations/
// 0122_worker_trace.sql for the schema half of this fix (tool_call.correlation_id
// and event.correlation_id — the MATERIAL WRITE half, wired in mcp.js's
// dispatch() and tools.js's auditIdentity()/withEnvelope()/writeEvent()) and
// that migration's header for why FAILURES land in ops.incident rather than a
// new ops.run row per request.
//
// LIKE correlation.js, THE CLASSIFIER HALF OF THIS FILE IS DB-FREE AND PURE —
// httpFailureClass, rpcInternalErrorFailureClass, and incidentSignature take
// no client, no env, no cloudflare: import, so node --test exercises them
// directly. Only recordWorkerFailure touches a database, and it does so
// through an injected `query` function (text, params) => Promise<{rows}>>,
// the exact convention mcp.js's readCallInsertSQL/recordReadCall already use —
// so ITS logic is also unit-testable with a fake, and only the thin
// production wiring (neon(env.DATABASE_URL_WRITER)) goes untested here, same
// division tool-read-call.test.mjs already draws.
//
// ── THE INCLUSION RULE, STATED ONCE, DEFENDED HERE ─────────────────────────
// Record exactly two things a failed request can be. Never a row per request —
// see the migration header for why that would flood ops.run specifically, and
// the same "signal, not traffic" reasoning applies here to ops.incident too:
// a hot loop of undifferentiated rows is what 0116's signature/dedup exists to
// prevent, and this recorder leans on that mechanism rather than writing
// around it.
//
//   1. httpFailureClass(status) — any HTTP response the Worker itself returns
//      with status >= 500. This covers /health, /ingest and /pipeline/changes'
//      explicit 503s (database unreachable) and the top-level 500 correlation.js's
//      own wrapWithCorrelation produces for an uncaught throw anywhere in the
//      stack. These are unambiguous: the Worker is telling a caller it failed.
//
//   2. rpcInternalErrorFailureClass(code) — the /mcp JSON-RPC transport's own
//      "internal error" envelope, code -32603. THIS ROUTE NEEDS ITS OWN RULE
//      because it deliberately returns HTTP 200 for a JSON-RPC-level error (the
//      MCP spec's isError-in-body convention — see mcp.js's `reply`/`rpcError`,
//      neither of which ever passes a non-200 status). An uncaught exception
//      inside a tool call, a raw database error tools.js's pgConstraintError()
//      does not recognise, or any other unhandled throw during dispatch() all
//      surface as this ONE code, on this ONE route, at HTTP 200 — treating 200
//      as "nothing to record" would hide every one of them, which is exactly
//      the "unhandled throw / database error / verb fails mid-write" class the
//      brief names. Every OTHER JSON-RPC error code is a ToolError — a refusal
//      the system got right (not_in_profile, missing_idempotency_key,
//      version_conflict, key_reuse, actor_not_provisioned, invalid_field_value,
//      …) — and recording those would flood the ledger with the system doing
//      its job, not with the system malfunctioning. -32603 is the one code
//      that can ONLY mean an uncaught exception (dispatch()'s own outer catch
//      in mcp.js is its sole producer), so it is the one JSON-RPC code this
//      file records.
//
//   3. actorUnresolvedFailureClass — mcpApiHandler's and protectedApiHandler's
//      own `if (!actor) return json({error:"unauthorized"},401)` (index.js): a
//      request whose OAuth grant the provider ALREADY validated, decrypted,
//      and handed to our code as ctx.props — but whose props do not map to a
//      known actor. That is a real identity/config problem (a stale grant, an
//      actor row missing, code drift in identity.js), not a caller mistake.
//
// EXPLICITLY EXCLUDED, ON PURPOSE: any 401/403 the OAuthProvider library itself
// returns before either of the two routes above ever runs — "a routine 401 on
// an unauthenticated probe is noise" (the brief's own words, and the exact
// case the outage drill's own unauthenticated call produced). This code
// structurally cannot see WHY the library refused a request — no token, wrong
// token, and an expired grant are all indistinguishable from here — so nothing
// this file could record about them would be signal rather than a guess
// dressed as one. Also excluded: every ToolError (ordinary business-logic
// refusals returned at HTTP 200 with isError:true) and actor_not_provisioned
// specifically (a well-handled, well-messaged refusal with its own hint — not
// an unhandled anything).

import { neon } from "@neondatabase/serverless";

const SERVICE_KEY = "carr-mcp";
const DEFAULT_SEVERITY = "SEV-3"; // contained: one request failed, not the whole service —
// see the migration header for why this is deliberately NOT
// assess()'s SEVERITY_BY_CRITICALITY (that mapping answers
// "is the whole service down"; this answers "one request failed").
const MONITORING_HOURS = 24; // same window assess() uses; keeps a human's read of
// "how stale can this get before it's ignored" consistent
// regardless of which collector opened the incident.
const VALID_ENVIRONMENTS = new Set(["local", "rehearsal", "staging", "production"]);

export const RPC_INTERNAL_ERROR_CODE = -32603;

/** Pure. status >= 500 is unambiguous Worker-reported failure; everything else
 * (including every 4xx) is out of scope for THIS classifier — see the actor-
 * unresolved 401 case, which is deliberately its own explicit call site rather
 * than folded in here, because this function has no way to know WHY a 401
 * happened and the brief is explicit that most 401s are noise. */
export function httpFailureClass(status) {
  return typeof status === "number" && status >= 500 ? "http_5xx" : null;
}

/** Pure. See the file header for why -32603 is the one JSON-RPC error code
 * this file ever records. */
export function rpcInternalErrorFailureClass(code) {
  return code === RPC_INTERNAL_ERROR_CODE ? "verb_internal_error" : null;
}

/** Pure. A provider-validated grant with no resolvable actor — see the file
 * header's item 3. No input: this failure class has exactly one cause. */
export function actorUnresolvedFailureClass() {
  return "actor_unresolved";
}

/** Pure. Same shape tools/ops-record.py's assess() already uses —
 * service|environment|run_key|failure_class — so a live-request incident and a
 * batch job/check incident interleave under one dedup rule (0116's partial
 * unique index) rather than two incompatible ones. routeKey plays run_key's
 * role: the path for a generic failure, or `mcp:<verb>` where the verb is
 * known (a more specific signature than the bare route gives). */
export function incidentSignature({ serviceKey, environment, routeKey, failureClass }) {
  return `${serviceKey}|${environment}|${routeKey}|${failureClass}`;
}

/** Pure. One redacted line: route, outcome, correlation id — never a payload,
 * an argument value, or client content. Callers pass an already-truncated
 * `detail` (the same short string, if any, the response already carries — this
 * never adds a NEW leak surface beyond what the caller already sent back). */
export function incidentFactText({ routeKey, failureClass, correlationId, detail }) {
  const base = `${routeKey} failed (${failureClass}), correlation ${correlationId}`;
  return detail ? `${base} — ${String(detail).slice(0, 200)}` : base;
}

// ── the recorder ─────────────────────────────────────────────────────────────
//
// NEVER THROWS. Every branch below is inside one outer try/catch that swallows
// everything — a recording failure must never become a request failure, and
// this is called exclusively via ctx.waitUntil, after the response the caller
// is waiting on has already been decided. Not transactional: each statement is
// its own round trip against the HTTP-based neon() driver, and correctness
// under a concurrent duplicate is enforced by 0116's own partial unique index
// (a 23505 on it is caught below and treated as "another writer already opened
// this incident", not as an error) rather than by an application-level lock.
//
// NEVER WRITES state, resolved_at, recovery_evidence_ref or monitoring_until
// on an EXISTING incident — only observed_at/expires_at, a pure freshness
// bump. carr_writer (unlike carr_jobs — 0117's column-scoped grant) holds a
// table-level UPDATE on ops.incident and COULD write those columns; the
// migration's own proof exercises the 0115 CHECK constraint as the real
// backstop precisely because this file's discipline is the only thing
// stopping it otherwise. Closing an incident is a human's call, always.
export async function recordWorkerFailure(query, {
  environment, routeKey, failureClass, correlationId, detail,
  severity = DEFAULT_SEVERITY, serviceKey = SERVICE_KEY,
}) {
  try {
    if (!VALID_ENVIRONMENTS.has(environment)) return; // an unlabelled deploy is never guessed at
    if (!failureClass || !routeKey || !correlationId) return;

    const svc = await query("select id from ops.service where key=$1 and retired_at is null", [serviceKey]);
    if (!svc.rows.length) return; // an unregistered service is never invented — see ops-record.py's own posture

    const signature = incidentSignature({ serviceKey, environment, routeKey, failureClass });
    const factSourceRef = `correlation:${correlationId}`;
    const factText = incidentFactText({ routeKey, failureClass, correlationId, detail });

    let incidentId = await findOpenIncident(query, signature);

    if (!incidentId) {
      incidentId = await openIncident(query, {
        environment, routeKey, failureClass, severity, signature, correlationId, serviceKey,
      });
    }
    if (!incidentId) return; // opening lost a race and the re-check (inside openIncident) still found nothing

    await appendFactIfNew(query, incidentId, factSourceRef, factText);
  } catch {
    // swallow — see file header: a recording failure must never surface as a
    // request failure, and by the time this runs (ctx.waitUntil) the response
    // is already on the wire.
  }
}

async function findOpenIncident(query, signature) {
  const r = await query(
    "select id from ops.incident where signature=$1 and state not in ('resolved','reviewed') limit 1",
    [signature]);
  return r.rows[0]?.id || null;
}

async function nextIncidentRef(query) {
  // SAME QUERY SHAPE tools/ops-record.py's _next_incident_ref uses, so refs
  // from both writers share one numbering space per day rather than two
  // sequences that could collide (rule a8c55a47: the same job, done from two
  // runtimes, stays one piece of logic — here expressed as one shared query
  // shape rather than one shared process, since a Cloudflare Worker cannot
  // invoke Python).
  const r = await query(
    `select coalesce(max(substring(ref from '[0-9]+$')::int), 0) + 1 as n
       from ops.incident where ref like 'INC-' || to_char(now(), 'YYYYMMDD') || '-%'`,
    []);
  const n = r.rows[0]?.n || 1;
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  return `INC-${stamp}-${String(n).padStart(2, "0")}`;
}

async function openIncident(query, { environment, routeKey, failureClass, severity, signature, correlationId, serviceKey }) {
  const ref = await nextIncidentRef(query);
  const title = `${routeKey} failed on ${serviceKey} (${environment})`;
  try {
    const r = await query(
      `insert into ops.incident
           (ref, correlation_id, title, severity, state, environment, owner_actor,
            next_action, detected_source, detected_at, source_kind, source_ref,
            signature, observed_at, expires_at)
         values ($1,$2,$3,$4,'detected',$5,'joe',
                 $6,'carr-mcp-worker', now(), 'collector','mcp-server/src/trace.js',
                 $7, now(), now() + interval '${MONITORING_HOURS} hours')
         returning id`,
      [ref, correlationId, title, severity, environment,
       `read the trace: tools/ops-record.py trace ${correlationId}`, signature]);
    return r.rows[0]?.id || null;
  } catch (e) {
    // 0116's partial unique index refused a second OPEN incident on this
    // signature — another concurrent failure beat this one to it. That is
    // success, not an error: fall back to finding the incident it just opened.
    if (e && e.code === "23505") return findOpenIncident(query, signature);
    throw e;
  }
}

async function appendFactIfNew(query, incidentId, sourceRef, text) {
  const dup = await query(
    "select 1 from ops.incident_fact where incident_id=$1 and source_ref=$2 limit 1",
    [incidentId, sourceRef]);
  if (dup.rows.length) return; // this exact correlation id already left a fact — never grow the list on a retry
  await query(
    "insert into ops.incident_fact (incident_id, text, source_ref) values ($1,$2,$3)",
    [incidentId, text, sourceRef]);
  // A pure freshness bump — never state, resolved_at, recovery_evidence_ref or
  // monitoring_until. See the file header: closing an incident is a human's
  // call, and this function structurally never touches the columns that do it.
  await query(
    `update ops.incident set observed_at = now(), expires_at = now() + interval '${MONITORING_HOURS} hours'
       where id = $1`,
    [incidentId]);
}

/** Production wiring: builds the injected `query` from env.DATABASE_URL_WRITER
 * and schedules recordWorkerFailure via ctx.waitUntil so it never adds latency
 * to the response the caller is waiting on — the exact pattern mcp.js's
 * recordReadCall wiring already uses for tool_read_call. A no-op, not a throw,
 * when the writer credential is absent (mirrors that same precedent). */
export function scheduleFailureRecord(env, ctx, { routeKey, failureClass, detail }) {
  if (!env || !env.DATABASE_URL_WRITER || !ctx || typeof ctx.waitUntil !== "function") return;
  const query = (text, params) => neon(env.DATABASE_URL_WRITER).query(text, params);
  ctx.waitUntil(recordWorkerFailure(query, {
    environment: env.CARR_ENV || null,
    routeKey,
    failureClass,
    correlationId: env.CORRELATION_ID || null,
    detail,
  }));
}

function safeRouteKey(request) {
  try {
    return new URL(request.url).pathname || "/";
  } catch {
    return "/unparseable";
  }
}

/** The second wrapping layer, composed INSIDE wrapWithCorrelation in index.js:
 * `wrapWithCorrelation(withFailureRecording(routeRequest))`. Covers inclusion-
 * rule class 1 (httpFailureClass) two ways — a route that itself returns a
 * >=500 Response (health/ingest/pipeline's explicit 503s), and a handler that
 * throws, which is re-thrown UNCHANGED after scheduling the record so
 * wrapWithCorrelation's own catch still builds the exact 500 body it already
 * builds and every existing correlation.test.mjs assertion about that shape
 * keeps holding. Never awaits the recording itself — ctx.waitUntil schedules
 * it, the response (or throw) proceeds immediately. */
export function withFailureRecording(handler) {
  return async function tracedFetch(request, env, ctx) {
    let response;
    try {
      response = await handler(request, env, ctx);
    } catch (e) {
      scheduleFailureRecord(env, ctx, {
        routeKey: safeRouteKey(request),
        failureClass: httpFailureClass(500),
        detail: String((e && e.message) || e).slice(0, 200),
      });
      throw e;
    }
    const failureClass = response && httpFailureClass(response.status);
    if (failureClass) {
      scheduleFailureRecord(env, ctx, {
        routeKey: safeRouteKey(request),
        failureClass,
        detail: null, // the response body may carry detail; never re-read/duplicate it here
      });
    }
    return response;
  };
}
