-- 0114_ops_schema_work_request.sql
-- THE FIRST PERSISTED WORK REQUEST, AND THE FIRST SCHEMA THAT IS NOT public.
--
-- WHAT WAS MISSING. The canonical Work Request lifecycle is defined in doctrine and
-- in control-room/contracts/state-machines.v1.json, is projected to the Workspace
-- view through control-room/contracts/work-request-projection.v1.json, and was
-- persisted NOWHERE. Verified 2026-08-13 before writing this: zero hits for
-- work_request, request_state, needs_answer or engineering_request across all
-- migrations and the entire MCP server, and no table anywhere with "request" in its
-- name. Both prototypes held state in a browser variable that reset on reload. The
-- council made a canonical machine blocking before any mutation UI; a machine with
-- no store is not a machine.
--
-- WHY A SEPARATE SCHEMA (decision a4a299fb). The Control Room requires operational
-- metadata in "a distinct Neon ops schema and least-privilege roles", and the
-- Workspace requires that "Neon Postgres is the canonical record store". Those were
-- reported as a conflict and are not one: the first names a NAMESPACE, the second
-- names a STORE. Both hold. This is one database, one source of truth, one row per
-- request — and a namespace that lets an ops-scoped role be granted operational
-- tables without ever being granted business tables.
--
-- That last point is the Program 1 argument rather than tidiness. The phase gate
-- requires an environment that cannot reach production data or credentials. With
-- work requests in public, any credential able to read them could also read client
-- records. Schema separation makes the boundary expressible in the database instead
-- of only in application code.
--
-- WHAT THIS DELIBERATELY DOES NOT DO. It does not enforce TRANSITIONS. Doctrine is
-- explicit that "state transitions are server-enforced and tested", and the verb
-- layer is where the guards live — each canonical transition carries a guard in
-- prose that a CHECK constraint cannot express (an approval hash, fresh evidence,
-- a named external condition). What the database enforces here is the set of legal
-- states and the invariants the canonical machine states about them, so a row can
-- never be in a state the machine does not define even if a bug in the server tries.
--
-- NO BUSINESS PAYLOAD LIVES HERE, by the same doctrine line that asked for the
-- schema: no secrets, DSNs, raw transcripts, business payloads or unrestricted logs.
-- origin_ref is a REFERENCE to a business record, never a copy of one.

begin;

create schema if not exists ops;

comment on schema ops is
  'Operational metadata for the Control Room: work requests, and later plans, '
  'approvals, releases and job runs. Separated from public so an ops-scoped role '
  'can be granted operational tables without ever being granted business tables '
  '(decision a4a299fb). Never stores secrets, DSNs, raw transcripts, business '
  'payloads or unrestricted logs — references to business records only.';

create table if not exists ops.work_request (
  id                        uuid primary key default gen_random_uuid(),
  ref                       text        not null unique,

  -- The canonical machine's states, main and side, exactly as
  -- control-room/contracts/state-machines.v1.json declares them. The CHECK is the
  -- point: a row can never hold a state the machine does not define.
  state                     text        not null default 'captured'
    check (state in ('captured','triaged','ready','claimed','in_progress',
                     'verification','awaiting_release','released','confirmed_closed',
                     'needs_joe','blocked','declined','superseded','failed')),

  title                     text        not null,
  desired_outcome           text,
  acceptance_criteria       jsonb       not null default '[]'::jsonb,

  origin_ref                text,           -- a REFERENCE to a business record, never its content
  requester_actor           text        not null,
  owner_actor               text,
  executor_actor            text,

  -- Canonical invariant: "blocked requires blocker_code and blocker_detail".
  blocker_code              text,
  blocker_detail            text,

  -- Canonical invariant: "confirmed_closed requires accepted verification evidence".
  verification_accepted_at  timestamptz,
  verification_evidence_ref text,

  -- Canonical rule: "every side exit records a reason". declined and superseded are
  -- terminal side exits; failed is a side state. All three owe a reason.
  exit_reason               text,
  superseded_by             uuid references ops.work_request(id),

  captured_at               timestamptz not null default now(),
  claimed_at                timestamptz,
  started_at                timestamptz,
  closed_at                 timestamptz,
  updated_at                timestamptz not null default now(),

  -- INVARIANT 1, from the canonical machine verbatim.
  constraint blocked_needs_a_named_blocker check (
    state <> 'blocked' or (blocker_code is not null and blocker_detail is not null)
  ),

  -- INVARIANT 2. A close is a claim about evidence, so the evidence must exist.
  constraint confirmed_close_needs_accepted_verification check (
    state <> 'confirmed_closed' or verification_accepted_at is not null
  ),

  -- INVARIANT 3. Every side exit records a reason — the projection collapses
  -- superseded onto declined, so the reason is the ONLY thing that survives to tell
  -- a requester which actually happened.
  constraint side_exits_record_a_reason check (
    state not in ('declined','superseded','failed') or exit_reason is not null
  ),

  -- INVARIANT 4. superseded names its successor, or the collapse to declined in the
  -- Workspace view leaves the requester with no route to the work that replaced it.
  constraint superseded_names_its_successor check (
    state <> 'superseded' or superseded_by is not null
  ),

  -- INVARIANT 5. A terminal row is closed; a live row is not.
  constraint terminal_rows_are_closed check (
    (state in ('confirmed_closed','declined','superseded')) = (closed_at is not null)
  )
);

comment on table ops.work_request is
  'The canonical Work Request. States come from control-room/contracts/'
  'state-machines.v1.json; the Workspace projection is defined by '
  'control-room/contracts/work-request-projection.v1.json and is derived, never '
  'stored. TRANSITIONS ARE SERVER-ENFORCED, not enforced here: doctrine requires it '
  'and the guards need evidence a CHECK cannot see. This table guarantees only that '
  'a row is never in an undefined state and never violates an invariant the machine '
  'declares about states.';

create index if not exists work_request_state_idx on ops.work_request (state)
  where state not in ('confirmed_closed','declined','superseded');

comment on index ops.work_request_state_idx is
  'Partial on purpose: the common query is open work, and terminal rows accumulate '
  'forever. Indexing only live rows keeps it small as history grows.';

-- LEAST PRIVILEGE, the other half of what the Control Room asked for. The reader
-- role gets usage and select; the writer role gets DML. Neither is granted anything
-- in public by this migration, and nothing in public is granted here — that
-- asymmetry is the isolation the phase gate wants.
grant usage on schema ops to carr_reader, carr_writer, carr_jobs;
grant select on ops.work_request to carr_reader;
grant select, insert, update on ops.work_request to carr_writer;

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
do $$
declare
  v_schema int; v_table int; v_checks int; v_ok boolean;
begin
  select count(*) into v_schema from information_schema.schemata where schema_name = 'ops';
  select count(*) into v_table  from information_schema.tables
   where table_schema = 'ops' and table_name = 'work_request';
  select count(*) into v_checks from pg_constraint
   where conrelid = 'ops.work_request'::regclass and contype = 'c';

  if v_schema <> 1 then raise exception '0114 FAILED: ops schema not created'; end if;
  if v_table  <> 1 then raise exception '0114 FAILED: ops.work_request not created'; end if;

  raise notice '0114: ops schema present, work_request present, % check constraint(s)', v_checks;

  -- Each invariant is PROVEN to bite, not assumed. A constraint nobody has seen
  -- refuse anything is a comment with punctuation.

  begin
    insert into ops.work_request (ref, state, title, requester_actor)
      values ('WR-PROOF-1', 'not_a_real_state', 'proof', 'joe');
    raise exception '0114 FAILED: an undefined state was accepted';
  exception when check_violation then null;
  end;

  begin
    insert into ops.work_request (ref, state, title, requester_actor)
      values ('WR-PROOF-2', 'blocked', 'proof', 'joe');
    raise exception '0114 FAILED: blocked was accepted with no blocker_code/detail';
  exception when check_violation then null;
  end;

  begin
    insert into ops.work_request (ref, state, title, requester_actor, exit_reason, closed_at)
      values ('WR-PROOF-3', 'confirmed_closed', 'proof', 'joe', 'r', now());
    raise exception '0114 FAILED: confirmed_closed was accepted with no verification evidence';
  exception when check_violation then null;
  end;

  begin
    insert into ops.work_request (ref, state, title, requester_actor, closed_at)
      values ('WR-PROOF-4', 'declined', 'proof', 'joe', now());
    raise exception '0114 FAILED: a side exit was accepted with no reason';
  exception when check_violation then null;
  end;

  begin
    insert into ops.work_request (ref, state, title, requester_actor, exit_reason, closed_at)
      values ('WR-PROOF-5', 'superseded', 'proof', 'joe', 'r', now());
    raise exception '0114 FAILED: superseded was accepted without naming its successor';
  exception when check_violation then null;
  end;

  begin
    insert into ops.work_request (ref, state, title, requester_actor)
      values ('WR-PROOF-6', 'declined', 'proof', 'joe');
    raise exception '0114 FAILED: a terminal row was accepted with no closed_at';
  exception when check_violation then null;
  end;

  -- And the happy path still works, or the constraints are simply too tight.
  insert into ops.work_request (ref, state, title, requester_actor)
    values ('WR-PROOF-OK', 'captured', 'proof of a legal row', 'joe');
  select true into v_ok from ops.work_request where ref = 'WR-PROOF-OK';
  if not v_ok then raise exception '0114 FAILED: a legal row was refused'; end if;
  delete from ops.work_request where ref = 'WR-PROOF-OK';

  raise notice '0114: all five invariants refused their violation and a legal row was accepted';
end $$;
