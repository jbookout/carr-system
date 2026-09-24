-- 0590_v5_r02_workflow_cutover_and_caller_inventory.sql
--
-- DoctorCRE V5-R02 (Workflow cutover, caller migration and retirement
-- readiness). Builds the READINESS AND CUTOVER MACHINERY only -- no live
-- production workflow is retired by this migration. The store and write
-- doors below let a future, separately-scheduled effect retire a real
-- workflow once its evidence is real; this migration cannot do that itself,
-- because ops.retire_workflow_cutover_plan (below) independently re-checks
-- the workflow census at call time and refuses whenever that census is not
-- provably available (see lib/control_plane_workflow_truth_reader.py, which
-- answers available:false today) -- Q157: unavailable evidence is refused,
-- never silently treated as done.
--
-- Q116 (recovered): "avoid uncontrolled dual writes, and migrate one
-- workflow at a time. The steps are: read legacy state, build the new
-- projection, compare outcomes in shadow, establish one write authority, cut
-- over, monitor, preserve a bounded recovery path, then retire the old
-- workflow and its instructions." ops.workflow_cutover_plan.stage is exactly
-- that eight-step sequence, enforced strictly forward by
-- ops.advance_workflow_cutover_stage (no skipping, no going back), and
-- retirement is split into its own authority-only door
-- (ops.retire_workflow_cutover_plan) composing the existing
-- ops.disable_legacy_schedule (0176/0180/0182/0184) and
-- ops.workflow_acceptance (0149) evidence rather than reinventing either.
--
-- Q153 (recovered): "V5 explicitly supersedes [v3, rev4 and stale doctrine],
-- reconciles any valid evidence, and retires contradictory active guidance.
-- Only one master graph controls execution." ops.open_workflow_cutover_plan
-- enforces exactly one ACTIVE plan per (workflow_key, workflow_version): a
-- new open automatically supersedes any prior active plan for the same
-- workflow, recording why, rather than leaving two live plans to disagree.
-- Q153 also added: "completion of a certain slice throughout this build must
-- be clearly marked so that future sessions know explicitly whats been done
-- and whats left" -- ops.slice_completion_mark is that explicit, generic,
-- append-only marker, usable by every V5 slice, not only this one.
--
-- Q157 (recovered) lists conditions that are failures despite green metrics
-- -- "the interface hides uncertainty or stale data" chief among them. Every
-- read door below refuses to report a caller or a workflow "done" from
-- absent or unavailable evidence: ops.record_workflow_caller requires a
-- non-null evidence_ref before accepting status='done', and the MCP read
-- verb (workflow-cutover-board) independently re-checks the workflow census
-- and downgrades to 'unknown' rather than trusting a stale cached figure.
--
-- No explicit transaction control: from 0339 onward tools/migrate.py runs
-- each migration inside its own single transaction.

-- ===========================================================================
-- ops.workflow_cutover_plan -- one active plan per (workflow_key, version).
-- ===========================================================================
create table if not exists ops.workflow_cutover_plan (
  id uuid primary key default gen_random_uuid(),
  workflow_key text not null check (btrim(workflow_key) <> ''),
  workflow_version integer not null check (workflow_version >= 1),
  stage text not null default 'read_legacy' check (stage in (
    'read_legacy', 'build_projection', 'shadow_compare', 'single_write_authority',
    'cutover', 'monitor', 'recovery_ready', 'retired'
  )),
  status text not null default 'active' check (status in ('active', 'superseded', 'blocked')),
  superseded_by uuid references ops.workflow_cutover_plan(id),
  supersede_reason text,
  -- Q116: "preserve a bounded recovery path" is not an afterthought at the
  -- retire step -- the plan cannot open without one stated up front.
  recovery_plan text not null check (btrim(recovery_plan) <> ''),
  opened_by_actor_slug text,
  idempotency_key uuid not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Exactly one ACTIVE plan per workflow identity -- the Q153 "only one master
-- graph" invariant, enforced by the database rather than trusted to callers.
create unique index if not exists workflow_cutover_plan_one_active_idx
  on ops.workflow_cutover_plan (workflow_key, workflow_version)
  where status = 'active';

create index if not exists workflow_cutover_plan_workflow_idx
  on ops.workflow_cutover_plan (workflow_key, workflow_version, created_at desc);

comment on table ops.workflow_cutover_plan is
  'DoctorCRE V5-R02: one workflow-migration plan per open. status=active is unique per (workflow_key,workflow_version) -- Q153 stale-plan supersession. stage follows the fixed Q116 sequence read_legacy..retired, advanced only forward by ops.advance_workflow_cutover_stage / ops.retire_workflow_cutover_plan.';

revoke all on table ops.workflow_cutover_plan from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.workflow_cutover_plan to carr_reader;

-- ===========================================================================
-- ops.workflow_cutover_stage_transition -- append-only audit of every move.
-- ===========================================================================
create table if not exists ops.workflow_cutover_stage_transition (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null references ops.workflow_cutover_plan(id),
  from_stage text,
  to_stage text not null check (to_stage in (
    'read_legacy', 'build_projection', 'shadow_compare', 'single_write_authority',
    'cutover', 'monitor', 'recovery_ready', 'retired'
  )),
  evidence_ref text,
  reason text not null check (btrim(reason) <> ''),
  actor_slug text,
  idempotency_key uuid not null unique,
  occurred_at timestamptz not null default now()
);

create index if not exists workflow_cutover_stage_transition_plan_idx
  on ops.workflow_cutover_stage_transition (plan_id, occurred_at);

comment on table ops.workflow_cutover_stage_transition is
  'DoctorCRE V5-R02: append-only stage-transition audit for ops.workflow_cutover_plan. Never updated or deleted.';

revoke all on table ops.workflow_cutover_stage_transition from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.workflow_cutover_stage_transition to carr_reader;

-- ===========================================================================
-- ops.workflow_caller -- caller inventory: done/remaining/blocked/
-- superseded/retired per caller of a workflow identity.
-- ===========================================================================
create table if not exists ops.workflow_caller (
  id uuid primary key default gen_random_uuid(),
  workflow_key text not null check (btrim(workflow_key) <> ''),
  workflow_version integer not null check (workflow_version >= 1),
  caller_locator text not null check (btrim(caller_locator) <> ''),
  caller_kind text not null check (caller_kind in (
    'script', 'verb', 'worker_route', 'job_definition', 'external'
  )),
  status text not null default 'remaining' check (status in (
    'remaining', 'done', 'blocked', 'superseded', 'retired'
  )),
  blocked_reason text,
  -- Q157: a caller cannot read back 'done' from hidden or absent evidence.
  evidence_ref text,
  updated_by_actor_slug text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workflow_key, workflow_version, caller_locator)
);

create index if not exists workflow_caller_workflow_idx
  on ops.workflow_caller (workflow_key, workflow_version, status);

comment on table ops.workflow_caller is
  'DoctorCRE V5-R02: caller inventory per workflow identity. status=done requires a non-null evidence_ref (enforced by ops.record_workflow_caller); a caller can never read back done from silence.';

revoke all on table ops.workflow_caller from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.workflow_caller to carr_reader;

-- ===========================================================================
-- ops.slice_completion_mark -- Q153's explicit, generic, per-slice
-- completion marker. Append-only; the latest row per slice_id is current.
-- ===========================================================================
create table if not exists ops.slice_completion_mark (
  id uuid primary key default gen_random_uuid(),
  slice_id text not null check (btrim(slice_id) <> ''),
  status text not null check (status in ('in_progress', 'complete', 'blocked')),
  -- One element per checkable_done criterion: {"criterion": "...", "evidence": "...", "pass": true|false}.
  -- ops.mark_slice_completion (the write door) refuses status='complete'
  -- unless every element's pass is literally true -- code enforces this,
  -- not a caller's say-so.
  criteria_receipt jsonb not null,
  reason text,
  marked_by_actor_slug text,
  idempotency_key uuid not null unique,
  created_at timestamptz not null default now()
);

create index if not exists slice_completion_mark_slice_idx
  on ops.slice_completion_mark (slice_id, created_at desc);

comment on table ops.slice_completion_mark is
  'DoctorCRE V5-R02 / Q153: append-only explicit slice-completion marker. The latest row per slice_id (order by created_at desc) is the current state; status=complete only ever exists when ops.mark_slice_completion verified every checkable_done criterion passed.';

revoke all on table ops.slice_completion_mark from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.slice_completion_mark to carr_reader;

-- ===========================================================================
-- Write doors
-- ===========================================================================

-- ops.open_workflow_cutover_plan: Q116 step 1 ("read legacy state") opens a
-- plan at stage='read_legacy'. Q153 stale-plan supersession: any existing
-- ACTIVE plan for the same (workflow_key, workflow_version) is superseded
-- first, in the same transaction, never left to silently coexist.
create or replace function ops.open_workflow_cutover_plan(
  p_workflow_key text,
  p_workflow_version integer,
  p_recovery_plan text,
  p_idempotency_key uuid,
  p_actor_slug text
) returns ops.workflow_cutover_plan
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_existing ops.workflow_cutover_plan%rowtype;
  v_prior ops.workflow_cutover_plan%rowtype;
  v_row ops.workflow_cutover_plan%rowtype;
begin
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing from ops.workflow_cutover_plan where idempotency_key = p_idempotency_key;
  if found then
    return v_existing;
  end if;
  if p_workflow_key is null or btrim(p_workflow_key) = '' then
    raise exception 'workflow_key_required';
  end if;
  if p_workflow_version is null or p_workflow_version < 1 then
    raise exception 'workflow_version_invalid';
  end if;
  if p_recovery_plan is null or btrim(p_recovery_plan) = '' then
    raise exception 'recovery_plan_required';
  end if;

  select * into v_prior from ops.workflow_cutover_plan
   where workflow_key = p_workflow_key and workflow_version = p_workflow_version and status = 'active'
   for update;
  if found then
    update ops.workflow_cutover_plan
       set status = 'superseded', supersede_reason = 'superseded by a new open (idempotency_key ' || p_idempotency_key || ')',
           updated_at = now()
     where id = v_prior.id;
  end if;

  insert into ops.workflow_cutover_plan (
    workflow_key, workflow_version, stage, status, recovery_plan, opened_by_actor_slug, idempotency_key
  ) values (
    p_workflow_key, p_workflow_version, 'read_legacy', 'active', p_recovery_plan, p_actor_slug, p_idempotency_key
  ) returning * into v_row;

  if v_prior.id is not null then
    update ops.workflow_cutover_plan set superseded_by = v_row.id where id = v_prior.id;
  end if;

  insert into ops.workflow_cutover_stage_transition (
    plan_id, from_stage, to_stage, evidence_ref, reason, actor_slug, idempotency_key
  ) values (
    v_row.id, null, 'read_legacy',
    case when v_prior.id is not null then 'supersedes:' || v_prior.id else null end,
    case when v_prior.id is not null
      then 'plan opened; supersedes prior active plan ' || v_prior.id
      else 'plan opened' end,
    p_actor_slug, p_idempotency_key
  );

  return v_row;
end;
$$;

comment on function ops.open_workflow_cutover_plan is
  'DoctorCRE V5-R02 write door (Q116 step 1 / Q153 stale-plan supersession): opens a cutover plan at stage=read_legacy, superseding any existing active plan for the same workflow identity. Idempotent on p_idempotency_key.';

revoke all on function ops.open_workflow_cutover_plan(text, integer, text, uuid, text) from public;
grant execute on function ops.open_workflow_cutover_plan(text, integer, text, uuid, text) to carr_writer;

-- ops.advance_workflow_cutover_stage: moves a plan forward exactly one step
-- in the fixed Q116 sequence, up to and including 'recovery_ready'. Cannot
-- reach 'retired' -- that is ops.retire_workflow_cutover_plan's door alone.
-- shadow_compare/single_write_authority/cutover each require fresh accepted
-- ops.workflow_acceptance evidence, so a plan cannot be talked forward on
-- narrative alone -- "shadow parity and single-writer checks pass" is
-- checked here, not asserted by the caller.
create or replace function ops.advance_workflow_cutover_stage(
  p_plan_id uuid,
  p_to_stage text,
  p_evidence_ref text,
  p_reason text,
  p_idempotency_key uuid,
  p_actor_slug text
) returns ops.workflow_cutover_plan
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_plan ops.workflow_cutover_plan%rowtype;
  v_stages text[] := array['read_legacy','build_projection','shadow_compare',
    'single_write_authority','cutover','monitor','recovery_ready'];
  v_from_idx integer;
  v_to_idx integer;
  v_existing_transition ops.workflow_cutover_stage_transition%rowtype;
begin
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing_transition from ops.workflow_cutover_stage_transition
   where idempotency_key = p_idempotency_key;
  if found then
    select * into v_plan from ops.workflow_cutover_plan where id = v_existing_transition.plan_id;
    return v_plan;
  end if;
  if p_to_stage = 'retired' then
    raise exception 'retired_stage_requires_retire_door';
  end if;
  if p_reason is null or btrim(p_reason) = '' then
    raise exception 'reason_required';
  end if;

  select * into v_plan from ops.workflow_cutover_plan where id = p_plan_id for update;
  if not found then
    raise exception 'workflow_cutover_plan_not_found';
  end if;
  if v_plan.status <> 'active' then
    raise exception 'workflow_cutover_plan_not_active';
  end if;

  v_from_idx := array_position(v_stages, v_plan.stage);
  v_to_idx := array_position(v_stages, p_to_stage);
  if v_to_idx is null then
    raise exception 'workflow_cutover_stage_invalid';
  end if;
  if v_to_idx <> v_from_idx + 1 then
    raise exception 'workflow_cutover_stage_not_sequential: at % requested %', v_plan.stage, p_to_stage;
  end if;

  if p_to_stage in ('shadow_compare', 'single_write_authority', 'cutover') then
    if p_evidence_ref is null or btrim(p_evidence_ref) = '' then
      raise exception 'evidence_ref_required_for_stage: %', p_to_stage;
    end if;
    if p_to_stage = 'shadow_compare' then
      if not exists (
        select 1 from ops.workflow_acceptance
         where workflow_key = v_plan.workflow_key and workflow_version = v_plan.workflow_version
           and mode = 'shadow' and status = 'accepted' and id::text = p_evidence_ref
      ) then
        raise exception 'shadow_acceptance_evidence_not_found_or_not_accepted';
      end if;
    else
      if not exists (
        select 1 from ops.workflow_acceptance
         where workflow_key = v_plan.workflow_key and workflow_version = v_plan.workflow_version
           and mode = 'canary' and status = 'accepted' and id::text = p_evidence_ref
      ) then
        raise exception 'canary_acceptance_evidence_not_found_or_not_accepted';
      end if;
    end if;
  end if;

  update ops.workflow_cutover_plan set stage = p_to_stage, updated_at = now() where id = v_plan.id
    returning * into v_plan;

  insert into ops.workflow_cutover_stage_transition (
    plan_id, from_stage, to_stage, evidence_ref, reason, actor_slug, idempotency_key
  ) values (
    v_plan.id, v_stages[v_from_idx], p_to_stage, p_evidence_ref, p_reason, p_actor_slug, p_idempotency_key
  );

  return v_plan;
end;
$$;

comment on function ops.advance_workflow_cutover_stage is
  'DoctorCRE V5-R02 write door (Q116): advances a cutover plan exactly one step forward through read_legacy..recovery_ready. Refuses to skip stages, refuses a non-active plan, and requires fresh accepted ops.workflow_acceptance evidence for shadow_compare/single_write_authority/cutover. Never reaches retired -- see ops.retire_workflow_cutover_plan.';

revoke all on function ops.advance_workflow_cutover_stage(uuid, text, text, text, uuid, text) from public;
grant execute on function ops.advance_workflow_cutover_stage(uuid, text, text, text, uuid, text) to carr_writer;

-- ops.retire_workflow_cutover_plan: the ONLY door to stage='retired'.
-- Requires the plan to already be at 'recovery_ready' and requires an actual
-- ops.legacy_schedule_disable_receipt row -- i.e. a real, already-performed
-- ops.disable_legacy_schedule call -- referenced by id. It does not perform
-- a disable itself and cannot: disabling a native scheduler already runs
-- exclusively through ops.disable_legacy_schedule (Joe-only authority,
-- migrations 0176/0180/0182/0184).
create or replace function ops.retire_workflow_cutover_plan(
  p_plan_id uuid,
  p_disable_receipt_id uuid,
  p_reason text,
  p_idempotency_key uuid,
  p_actor_slug text
) returns ops.workflow_cutover_plan
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_plan ops.workflow_cutover_plan%rowtype;
  v_existing_transition ops.workflow_cutover_stage_transition%rowtype;
  v_receipt ops.legacy_schedule_disable_receipt%rowtype;
begin
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing_transition from ops.workflow_cutover_stage_transition
   where idempotency_key = p_idempotency_key;
  if found then
    select * into v_plan from ops.workflow_cutover_plan where id = v_existing_transition.plan_id;
    return v_plan;
  end if;
  if p_reason is null or btrim(p_reason) = '' then
    raise exception 'reason_required';
  end if;
  if p_disable_receipt_id is null then
    raise exception 'disable_receipt_id_required';
  end if;

  select * into v_plan from ops.workflow_cutover_plan where id = p_plan_id for update;
  if not found then
    raise exception 'workflow_cutover_plan_not_found';
  end if;
  if v_plan.status <> 'active' then
    raise exception 'workflow_cutover_plan_not_active';
  end if;
  if v_plan.stage <> 'recovery_ready' then
    raise exception 'workflow_cutover_plan_not_recovery_ready';
  end if;

  select * into v_receipt from ops.legacy_schedule_disable_receipt where id = p_disable_receipt_id;
  if not found then
    raise exception 'legacy_schedule_disable_receipt_not_found';
  end if;
  if v_receipt.workflow_key is distinct from v_plan.workflow_key then
    raise exception 'legacy_schedule_disable_receipt_workflow_mismatch';
  end if;

  update ops.workflow_cutover_plan set stage = 'retired', updated_at = now() where id = v_plan.id
    returning * into v_plan;

  insert into ops.workflow_cutover_stage_transition (
    plan_id, from_stage, to_stage, evidence_ref, reason, actor_slug, idempotency_key
  ) values (
    v_plan.id, 'recovery_ready', 'retired', p_disable_receipt_id::text, p_reason, p_actor_slug, p_idempotency_key
  );

  return v_plan;
end;
$$;

comment on function ops.retire_workflow_cutover_plan is
  'DoctorCRE V5-R02 authority write door (Q116 last step): the only path to stage=retired. Requires the plan already at recovery_ready and an existing ops.legacy_schedule_disable_receipt row for the same workflow_key -- it never performs a disable itself. Joe-only at the MCP layer (authorityOnly); this function additionally re-checks the receipt exists rather than trusting the caller.';

revoke all on function ops.retire_workflow_cutover_plan(uuid, uuid, text, uuid, text) from public;
grant execute on function ops.retire_workflow_cutover_plan(uuid, uuid, text, uuid, text) to carr_authority;

-- ops.record_workflow_caller: caller inventory upsert. status='done' is
-- refused without evidence_ref -- Q157, never a claim from silence.
create or replace function ops.record_workflow_caller(
  p_workflow_key text,
  p_workflow_version integer,
  p_caller_locator text,
  p_caller_kind text,
  p_status text,
  p_blocked_reason text,
  p_evidence_ref text,
  p_actor_slug text
) returns ops.workflow_caller
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_row ops.workflow_caller%rowtype;
begin
  if p_workflow_key is null or btrim(p_workflow_key) = '' then
    raise exception 'workflow_key_required';
  end if;
  if p_workflow_version is null or p_workflow_version < 1 then
    raise exception 'workflow_version_invalid';
  end if;
  if p_caller_locator is null or btrim(p_caller_locator) = '' then
    raise exception 'caller_locator_required';
  end if;
  if p_caller_kind not in ('script', 'verb', 'worker_route', 'job_definition', 'external') then
    raise exception 'caller_kind_invalid';
  end if;
  if p_status not in ('remaining', 'done', 'blocked', 'superseded', 'retired') then
    raise exception 'caller_status_invalid';
  end if;
  if p_status = 'done' and (p_evidence_ref is null or btrim(p_evidence_ref) = '') then
    raise exception 'caller_done_requires_evidence_ref';
  end if;
  if p_status = 'blocked' and (p_blocked_reason is null or btrim(p_blocked_reason) = '') then
    raise exception 'caller_blocked_requires_reason';
  end if;

  insert into ops.workflow_caller (
    workflow_key, workflow_version, caller_locator, caller_kind, status,
    blocked_reason, evidence_ref, updated_by_actor_slug
  ) values (
    p_workflow_key, p_workflow_version, p_caller_locator, p_caller_kind, p_status,
    p_blocked_reason, p_evidence_ref, p_actor_slug
  )
  on conflict (workflow_key, workflow_version, caller_locator) do update
    set caller_kind = excluded.caller_kind, status = excluded.status,
        blocked_reason = excluded.blocked_reason, evidence_ref = excluded.evidence_ref,
        updated_by_actor_slug = excluded.updated_by_actor_slug, updated_at = now()
  returning * into v_row;

  return v_row;
end;
$$;

comment on function ops.record_workflow_caller is
  'DoctorCRE V5-R02 write door: upserts one caller row for one workflow identity. status=done is refused without a non-null evidence_ref, and status=blocked is refused without blocked_reason -- Q157, an absent-evidence caller cannot read back done.';

revoke all on function ops.record_workflow_caller(text, integer, text, text, text, text, text, text) from public;
grant execute on function ops.record_workflow_caller(text, integer, text, text, text, text, text, text) to carr_writer;

-- ops.workflow_cutover_board: read projection. Reports the active plan (or
-- none), its stage history, and caller counts by status. Never claims a
-- workflow-level census state -- that independent, fail-closed check is
-- performed by the MCP verb layer (workflow-cutover-board), which calls
-- lib/control_plane_workflow_truth_reader.py itself and merges the result
-- in, never trusting a caller-supplied claim.
create or replace function ops.workflow_cutover_board(
  p_workflow_key text,
  p_workflow_version integer
) returns jsonb
language sql
stable
set search_path = pg_catalog, ops
as $$
  select jsonb_build_object(
    'schema', 'doctorcre-v5-r02-workflow-cutover-board.v1',
    'workflow_key', p_workflow_key,
    'workflow_version', p_workflow_version,
    'plan', (
      select jsonb_build_object(
        'id', p.id, 'stage', p.stage, 'status', p.status,
        'recovery_plan', p.recovery_plan, 'superseded_by', p.superseded_by,
        'created_at', p.created_at, 'updated_at', p.updated_at
      )
      from ops.workflow_cutover_plan p
      where p.workflow_key = p_workflow_key and p.workflow_version = p_workflow_version and p.status = 'active'
    ),
    'stage_history', coalesce((
      select jsonb_agg(jsonb_build_object(
        'from_stage', t.from_stage, 'to_stage', t.to_stage, 'evidence_ref', t.evidence_ref,
        'reason', t.reason, 'occurred_at', t.occurred_at
      ) order by t.occurred_at)
      from ops.workflow_cutover_stage_transition t
      join ops.workflow_cutover_plan p on p.id = t.plan_id
      where p.workflow_key = p_workflow_key and p.workflow_version = p_workflow_version
    ), '[]'::jsonb),
    'caller_counts', coalesce((
      select jsonb_object_agg(status, ct) from (
        select status, count(*) ct from ops.workflow_caller
         where workflow_key = p_workflow_key and workflow_version = p_workflow_version
         group by status
      ) counted
    ), '{}'::jsonb),
    'callers', coalesce((
      select jsonb_agg(jsonb_build_object(
        'caller_locator', c.caller_locator, 'caller_kind', c.caller_kind, 'status', c.status,
        'blocked_reason', c.blocked_reason, 'evidence_ref', c.evidence_ref, 'updated_at', c.updated_at
      ) order by c.caller_locator)
      from ops.workflow_caller c
      where c.workflow_key = p_workflow_key and c.workflow_version = p_workflow_version
    ), '[]'::jsonb)
  );
$$;

comment on function ops.workflow_cutover_board is
  'DoctorCRE V5-R02 read door: active plan, stage history and caller counts for one workflow identity. Carries no census claim -- the MCP verb layer merges an independently fail-closed census read in on top of this.';

revoke all on function ops.workflow_cutover_board(text, integer) from public;
grant execute on function ops.workflow_cutover_board(text, integer) to carr_reader;

-- ops.mark_slice_completion: Q153's explicit slice-completion marker.
-- status='complete' is refused server-side unless every criteria_receipt
-- element has pass=true -- an agent cannot mark a slice complete by saying
-- so; every criterion needs its own recorded evidence and a true verdict.
create or replace function ops.mark_slice_completion(
  p_slice_id text,
  p_status text,
  p_criteria_receipt jsonb,
  p_reason text,
  p_idempotency_key uuid,
  p_actor_slug text
) returns ops.slice_completion_mark
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_existing ops.slice_completion_mark%rowtype;
  v_row ops.slice_completion_mark%rowtype;
  v_unproven_count integer;
begin
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing from ops.slice_completion_mark where idempotency_key = p_idempotency_key;
  if found then
    return v_existing;
  end if;
  if p_slice_id is null or btrim(p_slice_id) = '' then
    raise exception 'slice_id_required';
  end if;
  if p_status not in ('in_progress', 'complete', 'blocked') then
    raise exception 'slice_completion_status_invalid';
  end if;
  if p_criteria_receipt is null or jsonb_typeof(p_criteria_receipt) <> 'array' or jsonb_array_length(p_criteria_receipt) = 0 then
    raise exception 'criteria_receipt_required_nonempty_array';
  end if;

  select count(*) into v_unproven_count
    from jsonb_array_elements(p_criteria_receipt) el
   where coalesce((el->>'pass')::boolean, false) is not true
      or coalesce(btrim(el->>'criterion'), '') = ''
      or coalesce(btrim(el->>'evidence'), '') = '';
  if p_status = 'complete' and v_unproven_count > 0 then
    raise exception 'slice_completion_complete_requires_every_criterion_proven';
  end if;

  insert into ops.slice_completion_mark (
    slice_id, status, criteria_receipt, reason, marked_by_actor_slug, idempotency_key
  ) values (
    p_slice_id, p_status, p_criteria_receipt, p_reason, p_actor_slug, p_idempotency_key
  ) returning * into v_row;

  return v_row;
end;
$$;

comment on function ops.mark_slice_completion is
  'DoctorCRE V5-R02 / Q153 write door: append one explicit completion mark for one slice_id. status=complete is refused unless every criteria_receipt element carries criterion, evidence and pass=true. Idempotent on p_idempotency_key.';

revoke all on function ops.mark_slice_completion(text, text, jsonb, text, uuid, text) from public;
grant execute on function ops.mark_slice_completion(text, text, jsonb, text, uuid, text) to carr_writer;

-- ops.read_slice_completion: latest mark per slice_id.
create or replace function ops.read_slice_completion(p_slice_id text)
returns ops.slice_completion_mark
language sql
stable
set search_path = pg_catalog, ops
as $$
  select * from ops.slice_completion_mark
   where slice_id = p_slice_id
   order by created_at desc
   limit 1;
$$;

comment on function ops.read_slice_completion is
  'DoctorCRE V5-R02 / Q153 read door: the current (most recent) completion mark for one slice_id, or no row when the slice has never been marked.';

revoke all on function ops.read_slice_completion(text) from public;
grant execute on function ops.read_slice_completion(text) to carr_reader;
