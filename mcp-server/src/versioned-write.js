import { TOOLS } from "./tool-registry.js";
import { ToolError } from "./tool-error.js";
import { mutationManifestIdentity } from "./mutation-registry.js";
import { authorizationClassForActor, organizationTenantForActor, personalScopeForActor } from "./identity.js";
function canon(v) {
  if (Array.isArray(v)) return v.map(canon);
  if (v && typeof v === "object")
    return Object.keys(v).sort().reduce((o, k) => {
      if (v[k] !== undefined) o[k] = canon(v[k]);
      return o;
    }, {});
  return v;
}

async function requestHash(args) {
  const data = new TextEncoder().encode(JSON.stringify(canon(args)));
  const d = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, "0")).join("");
}

export function auditIdentity(actor) {
  const scope = personalScopeForActor(actor);
  return {
    organization_tenant_id: organizationTenantForActor(actor),
    sponsoring_human_slug: scope.status === "personal" ? scope.sponsor : null,
    personal_scope: scope.status === "personal" ? `${scope.sponsor}-personal` : "none",
    authorization_class: actor.authorization_class || authorizationClassForActor(actor),
    // Program 4 Gap A2 (2026-08-14, defect cae5be2e): the x-correlation-id of the
    // Worker request that produced this write, set on the actor object by
    // mcp.js's dispatch() from env.CORRELATION_ID (correlation.js). null for any
    // caller that reaches a write handler without going through dispatch() —
    // tests, and anything constructing an actor object by hand.
    correlation_id: actor.correlation_id || null,
  };
}

export async function withEnvelope(client, actor, verb, args, fn, { serialized = false } = {}) {
  const key = args.idempotency_key;
  if (!key) throw new ToolError({ error: "missing_idempotency_key",
    hint: "generate a UUID per intended action; retries reuse the SAME key" });
  const identity = auditIdentity(actor);
  // SIEP-11: replay authority is the exact operation manifest, never the bare
  // caller key. Binding the canonical operation and server-derived principal
  // makes cross-verb, cross-actor, cross-client, cross-sponsor, and cross-tenant
  // reuse fail as key_reuse before a stored response can be returned. The
  // session/token/epoch fields are added by SIEP-12/17/21; their absence here
  // cannot widen authority because this manifest is monotonic and deny-only.
  const hash = await requestHash({
    manifest_version: "scac-application-mutation.v1",
    ...mutationManifestIdentity(),
    operation: verb,
    principal: {
      actor_id: actor.id,
      actor_slug: actor.slug,
      human: actor.human === true,
      via: actor.via || null,
      client_id: actor.client_id || null,
      sponsoring_human_slug: identity.sponsoring_human_slug,
      authorization_class: identity.authorization_class,
      organization_tenant_id: identity.organization_tenant_id,
    },
    args: { ...args, idempotency_key: undefined },
  });
  if (serialized || TOOLS[verb]?.verbFacts.serialization === "idempotency-key")
    await client.query("select pg_advisory_xact_lock(hashtextextended($1, 0))", [key]);
  const prior = await client.query("select request_hash, response from tool_call where idempotency_key=$1", [key]);
  if (prior.rows.length) {
    if (prior.rows[0].request_hash !== hash) throw new ToolError({ error: "key_reuse" });
    return { replayed: true, ...prior.rows[0].response };          // A1: replay, no second write
  }
  const result = await fn();                                        // inside the open transaction
  await client.query(
    `insert into tool_call (idempotency_key, verb, actor_id, request_hash, response, via, client_id,
       organization_tenant_id, sponsoring_human_slug, personal_scope, authorization_class, correlation_id)
     values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`,
    [key, verb, actor.id, hash, JSON.stringify(result), actor.via || null, actor.client_id || null,
     identity.organization_tenant_id, identity.sponsoring_human_slug, identity.personal_scope,
     identity.authorization_class, identity.correlation_id]);
  return result;
}

export async function writeEvent(client, actor, verb, subjectType, subjectId, fields = {}) {
  const identity = auditIdentity(actor);
  const allowedCauses = new Set(["human_stated", "human_correction", "ingest_email",
    "ingest_calendar", "ingest_webhook", "import_migration", "import_salesforce",
    "automation_job", "learning_job", "system"]);
  // THE DEFAULT USED TO BE 'human_stated' UNCONDITIONALLY, and it made the column
  // a lie. Measured 2026-08-13: 2,822 of 3,946 events read human_stated, including
  // every row written by an automated sweep — 173 research findings, 109 org
  // consolidations, 38 measurement pulls, and this run's own defect records, none
  // of which a human stated. A provenance column that says "a human said this"
  // about a nightly job is worse than an absent one, because a reader trusts it.
  //
  // DERIVED FROM WHO IS WRITING, not from an optimistic default. An explicit cause
  // from the caller still wins, because a verb that knows it is replaying an email
  // or a Salesforce import knows better than this rule does. Otherwise: a write
  // carrying the partner's verbatim words is human-stated by definition — that is
  // the intent signal the write-provenance ruling settled on — and a write from a
  // non-human actor with no quote is an automation job, which is what it is.
  //
  // HISTORY IS NOT REWRITTEN. The 2,822 wrong rows stay wrong. Backfilling an
  // audit trail so a metric reads better is the one repair that would be worse
  // than the defect: the log's value is that it records what happened, including
  // that this column was unreliable before today.
  // THE ACTOR'S human FLAG IS NOT THE DISCRIMINATOR, and trying it first is how
  // this fix was nearly shipped wrong. A scheduled unattended run authenticates as
  // Joe — his OAuth grant, his slug, human:true — so keying on the actor recorded
  // a 2am cron as "a human said this", which is the same lie in a new place. There
  // IS no transport signal separating "Joe decided this" from "the agent decided
  // this"; the write-provenance ruling settled that, and this rule obeys it.
  //
  // So the only honest signal is the one that ruling named: the partner's verbatim
  // words. A write carrying them is human-stated because a session cannot invent a
  // quote without writing a false sentence a human would recognise. A write without
  // them is an automation job, whichever account authenticated — and that is the
  // stricter, more truthful reading, because Joe never types into this database.
  // He tells Claude and Claude writes.
  //
  // An explicit cause from the caller still wins: a verb replaying an email or a
  // Salesforce import knows better than this rule does.
  let cause;
  if (allowedCauses.has(fields.cause)) {
    cause = fields.cause;
  } else if (fields.human_quote && String(fields.human_quote).trim()) {
    cause = "human_stated";
  } else {
    cause = "automation_job";
  }
  await client.query(
    `insert into event (occurred_at, recorded_at, actor_id, verb, subject_type, subject_id, field,
       old_value, new_value, cause, human_quote, agent_rationale, idempotency_key, via, client_id,
       organization_tenant_id, sponsoring_human_slug, personal_scope, authorization_class, correlation_id)
     values (coalesce($1::timestamptz, now()),
       case when $19::boolean then clock_timestamp() else now() end,
       $2, $3, $4, $5, $6, $7, $8, '${cause}', $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)`,
    [fields.occurred_at || null, actor.id, verb, subjectType, subjectId, fields.field || null,
     fields.old ? JSON.stringify(fields.old) : null, fields.new ? JSON.stringify(fields.new) : null,
     fields.human_quote || null, fields.agent_rationale || null, fields.idempotency_key || null,
     actor.via || null, actor.client_id || null, identity.organization_tenant_id,
     identity.sponsoring_human_slug, identity.personal_scope, identity.authorization_class,
     identity.correlation_id, fields.recorded_at_after_lock === true]);
}


export function compareVersion(current, baseVersion) {
  if (baseVersion === undefined || baseVersion === null)
    return { ok: false, kind: "missing_base_version" };
  const cv = Number(current);
  const bv = Number(baseVersion);
  if (!Number.isFinite(bv))
    return { ok: false, kind: "invalid_base_version" };
  if (cv !== bv) return { ok: false, kind: "conflict" };
  return { ok: true };
}


// AUTO-REBASE FOR A PURE VERSION-NUMBER RACE (WR-000019 slice S6, CONFLICT
// TIERING). version_conflict is deliberately "ask the human, never auto-retry"
// (rule 14181e60) whenever the intervening write touched a field THIS call is
// also about to touch — that is a real collision and must surface. But most
// verbs bump `version` on every write regardless of which columns changed, so
// two callers editing DISJOINT fields (Joe corrects `city` while a nightly
// sweep updates `salesforce_id`) still collide on version alone even though
// nothing they wrote actually conflicts. That shape is mechanical bookkeeping,
// not a judgment call, and this is where it is caught.
//
// touchedFields is OPT IN and defaults to null, which preserves every existing
// caller's exact behaviour (14 sites call this with 3 args; none of them
// change). Only a caller that can name, up front, exactly which columns its
// own write is about to set may opt in — passing the wrong set would let a
// real collision rebase silently, so this is deliberately per-verb, not a
// blanket default. update-deal is the first (and, for now, only) caller wired
// this way: its `fields{}` PATCH already computes the exact touched-column set
// before it needs a version at all.
export function disjointFromIntervening(touchedFields, interveningEvents) {
  if (!Array.isArray(touchedFields) || !touchedFields.length) return false;
  if (!interveningEvents.length) return false; // nothing intervened at all — not a race, just a stale read of nothing
  const touched = new Set(touchedFields);
  return interveningEvents.every(row => !row.field || !touched.has(row.field));
}

export async function versionGuard(client, table, id, baseVersion, touchedFields = null) {
  // Every write handler runs inside mcp.js's writer transaction.  Locking the
  // row makes the optimistic check real: a concurrent writer waits, then sees
  // the incremented version instead of letting two same-version writes through.
  const r = await client.query(`select version from ${table} where id=$1 for update`, [id]);
  if (!r.rows.length) throw new ToolError({ error: "not_found", table, id });
  const current = r.rows[0].version;
  const cmp = compareVersion(current, baseVersion);
  if (cmp.kind === "missing_base_version")
    throw new ToolError({ error: "missing_base_version", current_version: current,
      hint: "read the record first; pass its version back as base_version" });
  if (cmp.kind === "invalid_base_version")
    throw new ToolError({ error: "invalid_base_version", got: baseVersion, current_version: current,
      hint: "base_version must be the integer version from a fresh read, not a non-numeric value" });
  if (!cmp.ok) {
    // Exclude the record's OWN creation event from the "intervening" list.
    // A caller holding any base_version >= 1 has, by construction, already
    // read the record after it existed — its birth is not news to them, so
    // citing it as an intervening event is misleading regardless of the fix
    // above. created_at and the creation event's recorded_at are written in
    // the same transaction (both default to now()), so they are exactly
    // equal; `recorded_at > created_at` keeps every REAL subsequent edit and
    // drops only that one founding row. Fetched here, lazily, only on the
    // conflict path, rather than folded into the query above.
    const created = await client.query(`select created_at from ${table} where id=$1`, [id]);
    const ev = await client.query(
      `select a.slug as actor, e.verb, e.field, e.old_value, e.new_value, e.recorded_at
       from event e join actor a on a.id=e.actor_id
       where e.subject_id=$1 and e.recorded_at > $2 order by e.recorded_at desc limit 5`,
      [id, created.rows[0]?.created_at ?? null]);
    // TRIVIAL RACE: every intervening event's field lies outside this call's
    // own touched set. Rebase transparently onto the row this transaction
    // already holds locked (current is fresh and safe to act on — the `for
    // update` above means nobody else can move it again until this
    // transaction commits) and hand the caller a receipt instead of a refusal.
    // SAME-FIELD OR UNDECLARED: unchanged, still asks the human.
    if (disjointFromIntervening(touchedFields, ev.rows)) {
      return { version: current, rebased: true, rebase_receipt: {
        from_base_version: Number(baseVersion), rebased_to_version: current,
        disjoint_intervening_events: ev.rows.map(row => ({
          actor: row.actor, verb: row.verb, field: row.field, recorded_at: row.recorded_at })),
        hint: "version advanced from a write to a different field; re-applied against the current row with no human confirmation needed",
      } };
    }
    throw new ToolError({ error: "version_conflict", current_version: current,
      intervening_events: ev.rows,
      hint: "surface this to the human and re-read; NEVER auto-retry" });
  }
  return { version: current, rebased: false, rebase_receipt: null };
}
