-- 0122_worker_trace.sql
-- PROGRAM 4, GAP A2 (defect cae5be2e-267c-40ba-9424-b4618845e905, 2026-08-14):
-- correlation.js mints and echoes an x-correlation-id on every Worker route and
-- exposes env.CORRELATION_ID — but nothing downstream ever wrote it anywhere.
-- Proven live by an outage drill: an unauthenticated /mcp call to the staging
-- Worker returned a truthful 401 carrying the id, and ops.v_trace on staging
-- had nothing for that id. The observability contract's own sentence — "One
-- correlation ID connects UI request, Worker route, record verb, database
-- operation, agent session, approval, release, and incident" — was half true:
-- a failed Worker request could not be traced afterward.
--
-- READ BEFORE BUILT. tools.js's withEnvelope() and writeEvent() (plus
-- log-decision's own bespoke event insert) are the ONLY writers of tool_call
-- and event, and every material write already runs through them. Neither
-- table carries a correlation_id column. Attaching one to THOSE existing rows
-- is the cheaper, truer fix for "a material write is traceable" than minting a
-- parallel ops.run row per write — exactly the "check whether the existing
-- event/idempotency machinery already carries a column for this" instruction.
-- ops.work_request got its own correlation_id column in 0115 for the same
-- reason; this is the same move applied to the two tables that actually see
-- every verb call.
--
-- WHAT THIS FILE DOES NOT DO: it does not add a new ops.run row per request.
-- A read-heavy Worker recording one ops.run row per inbound request would
-- flood the ledger, cost money, and bury the failures ops.run exists to
-- surface — and ops.run's OWN health view (ops.v_service_environment_health)
-- takes the SINGLE LATEST terminal run per (service_id, environment) as the
-- whole service's health, with no run_key discrimination. carr-mcp's health is
-- deliberately driven by synthetic golden-workflow checks (services.json's own
-- comment: "carr-mcp is not scheduled... its health comes from synthetic
-- golden-workflow checks, and until those run it reads unknown"). Writing live
-- request failures into ops.run would silently hijack that signal — the
-- latest failed live request would outrank the latest golden check, and a
-- service failing one edge-case request would report unhealthy status derived
-- from traffic noise instead of the deliberate synthetic probe. So failures
-- go to ops.incident instead (this file adds no new columns there — 0115
-- already granted carr_writer select/insert/update on it and 0116 already
-- built the exact "repeated identical failures deduplicate into one incident"
-- mechanism the observability contract asks for, keyed on a signature). The
-- Worker becomes a SECOND, legitimate source alongside tools/ops-record.py's
-- nightly `assess` collector — not a duplicate of its logic (rule a8c55a47
-- concerns the same JOB done two different ways; a live per-request signal and
-- a batch reassessment of yesterday's job/check runs are different jobs that
-- happen to share one target table and, because of 0116's partial unique
-- index, share the SAME dedup guarantee regardless of which one writes first).
-- 0115 anticipated exactly this: source_kind's enum already includes
-- 'collector' beside 'wrapper' and 'operator', and carr_writer — the Worker's
-- own role — was granted insert on ops.incident before this Worker ever wrote
-- to it.

begin;

alter table tool_call add column if not exists correlation_id uuid;
alter table event     add column if not exists correlation_id uuid;

comment on column tool_call.correlation_id is
  'The x-correlation-id of the Worker request that produced this write, set '
  'from env.CORRELATION_ID (mcp-server/src/correlation.js) via the actor object '
  'mcp.js''s dispatch() decorates it onto. Nullable: rows predate this column, '
  'and a write made through local-verb.mjs''s direct-database break-glass path '
  'or any caller outside this Worker carries no Worker-minted id.';
comment on column event.correlation_id is
  'Same column, same source, same nullability reasoning as tool_call.correlation_id — '
  'see that comment. Threaded through writeEvent() and log-decision''s own direct '
  'insert, the only two places that write this table.';

-- Lookup indexes, partial on purpose: most historical rows carry no correlation
-- id, and an index over millions of NULLs answers no query anyone asks. Same
-- posture as 0115's run/deployment/incident correlation indexes, just partial
-- here because those tables are correlation_id NOT NULL by default and these
-- two are not.
create index if not exists tool_call_correlation_idx
  on tool_call (correlation_id) where correlation_id is not null;
create index if not exists event_correlation_idx
  on event (correlation_id) where correlation_id is not null;

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
-- Every invariant this file depends on is PROVEN rather than assumed: the new
-- columns exist and are writable, and — the load-bearing check for the failure
-- half of this defect, which adds no new grant — carr_writer already holds
-- exactly the ops.incident privileges the Worker's recorder needs. A comment
-- claiming a grant already exists is worth nothing until something has tried it.
do $$
declare
  v_key        text := gen_random_uuid()::text;
  v_corr       uuid := gen_random_uuid();
  v_actor      uuid;
  v_event_id   uuid;
  v_service    uuid;
  v_incident   uuid;
begin
  select id into v_actor from actor where slug = 'joe' limit 1;
  if v_actor is null then
    raise exception '0122 FAILED: no actor row for joe — cannot exercise the proof insert';
  end if;

  -- 1. tool_call carries and returns a correlation id.
  insert into tool_call (idempotency_key, verb, actor_id, request_hash, response, correlation_id)
    values (v_key, 'migration-0122-proof', v_actor, 'proofhash', '{"ok":true}'::jsonb, v_corr);
  if not exists (select 1 from tool_call where idempotency_key = v_key and correlation_id = v_corr) then
    raise exception '0122 FAILED: tool_call.correlation_id did not round-trip';
  end if;
  delete from tool_call where idempotency_key = v_key;

  -- 2. event carries and returns a correlation id, and stays nullable for a
  --    write with no Worker request behind it (the historical-row shape).
  insert into event (occurred_at, actor_id, verb, subject_type, subject_id, cause, correlation_id)
    values (now(), v_actor, 'migration-0122-proof', 'party', gen_random_uuid(), 'system', v_corr)
    returning id into v_event_id;
  if not exists (select 1 from event where id = v_event_id and correlation_id = v_corr) then
    raise exception '0122 FAILED: event.correlation_id did not round-trip';
  end if;
  insert into event (occurred_at, actor_id, verb, subject_type, subject_id, cause)
    values (now(), v_actor, 'migration-0122-proof-null', 'party', gen_random_uuid(), 'system');
  if exists (select 1 from event where verb = 'migration-0122-proof-null' and correlation_id is not null) then
    raise exception '0122 FAILED: correlation_id must default to null, not invent a value';
  end if;
  delete from event where verb in ('migration-0122-proof', 'migration-0122-proof-null');

  -- 3. THE GRANT CLAIM THIS FILE'S HEADER MAKES: carr_writer already holds what
  --    the Worker's failure recorder needs on ops.incident and ops.incident_fact,
  --    with NO new grant statement in this file. If either is false, the
  --    recorder silently no-ops in production and this migration is the wrong
  --    fix — fail loudly here instead of finding out from a second outage drill.
  if not has_table_privilege('carr_writer', 'ops.incident', 'insert') then
    raise exception '0122 FAILED: carr_writer cannot insert ops.incident — the failure recorder cannot open one';
  end if;
  if not has_table_privilege('carr_writer', 'ops.incident', 'select') then
    raise exception '0122 FAILED: carr_writer cannot select ops.incident — the failure recorder cannot find the open incident to dedupe against';
  end if;
  if not has_table_privilege('carr_writer', 'ops.incident', 'update') then
    raise exception '0122 FAILED: carr_writer cannot update ops.incident — the failure recorder cannot bump observed_at/expires_at on a recurrence';
  end if;
  if not has_table_privilege('carr_writer', 'ops.incident_fact', 'insert') then
    raise exception '0122 FAILED: carr_writer cannot insert ops.incident_fact — the failure recorder cannot attach a fact to an incident';
  end if;
  if not has_table_privilege('carr_writer', 'ops.incident_fact', 'select') then
    raise exception '0122 FAILED: carr_writer cannot select ops.incident_fact — the failure recorder cannot dedupe a recurrence against the same correlation id';
  end if;
  if not has_table_privilege('carr_writer', 'ops.service', 'select') then
    raise exception '0122 FAILED: carr_writer cannot select ops.service — the failure recorder cannot resolve carr-mcp''s service id';
  end if;

  -- UNLIKE carr_jobs (0117's column-scoped grant), carr_writer's 0115 grant is
  -- table-level UPDATE — it CAN write resolved_at. There is no role-level
  -- interlock stopping this Worker from closing its own incident, so the
  -- backstop here is the 0115 CHECK CONSTRAINT itself (binds any role,
  -- including the owner — 0117 already proved this the same way) plus
  -- application discipline: the recorder in mcp-server/src/trace.js never
  -- writes state, resolved_at, recovery_evidence_ref or monitoring_until.
  -- Proven for real, not assumed, and on a FRESH row this proof owns —
  -- picking an arbitrary existing incident would risk grabbing one already
  -- resolved, whose recovery_evidence_ref/monitoring_until/resolved_at are
  -- already set, which would let the transition through and prove nothing.
  insert into ops.service (key, name, family, criticality, owner_actor)
    values ('migration-0122-proof', 'proof', 'Data', 'low', 'system')
    returning id into v_service;
  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature)
    values ('INC-0122-PROOF', 'proof', 'SEV-3', 'detected', 'local',
            'proof', 'collector', 'proof', 'sig-0122|local|proof|boom')
    returning id into v_incident;
  begin
    update ops.incident set state = 'resolved' where id = v_incident;
    raise exception '0122 FAILED: an incident reached resolved with no recovery evidence — the constraint backstop is not binding';
  exception when check_violation then null;
  end;
  delete from ops.incident where id = v_incident;
  delete from ops.service where id = v_service;

  raise notice '0122: tool_call and event both round-trip a correlation id and default '
               'to null; carr_writer already holds exactly the ops.incident/ops.incident_fact/'
               'ops.service privileges the Worker''s failure recorder needs, no new grant added; '
               'carr_writer CAN write resolved_at (unlike carr_jobs), so the resolved-without-'
               'evidence CHECK constraint is the real backstop and it still refuses the transition';
end $$;
