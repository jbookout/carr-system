-- 0574_answer_needs_joe_work_request.sql
--
-- The one door that resolves a Work Request waiting on Joe.
--
-- WHY THIS EXISTS. state-machines.v1.json declares "* -> needs_joe" (guard:
-- "one material human decision is named") and "needs_joe -> triaged" (guard:
-- "authorized human decision and evidence recorded; scope and acceptance
-- criteria revalidated") as canonical side transitions, and ops.work_request's
-- base CHECK (0114) has always admitted 'needs_joe' as a legal state. But no
-- verb, function, or trigger in the deployed system ever implemented the
-- needs_joe -> triaged leg: current-work-item and workspace-command-center
-- (0276, 0546) only ever READ needs_joe rows for the "Needs Joe" queue. The
-- DoctorCRE Model Room's "Answer Waiting for Joe" control has nothing to call.
-- This closes that gap with the narrowest possible door: it records Joe's
-- answer text and makes the sole transition the canonical machine allows out
-- of needs_joe, exactly as review-and-triage (0175) makes the sole transition
-- out of captured. It does not create the needs_joe state on any row, assign,
-- dispatch, approve, execute, or make any later transition.
--
-- SCOPE. ops.work_request_sourced_capture_shape (0426) never admits 'needs_joe'
-- for a SOURCED row (capture_idempotency_key is not null): its three arms name
-- captured/triaged/ready/declined/superseded only. A sourced row can therefore
-- never be found by this function's state='needs_joe' guard, and this
-- migration does not widen that shape check -- narrowing which subtype of Work
-- Request the sourced Program 6 pipeline uses is a separate decision, not
-- this door's to make. General (non-sourced, non-program) and program rows
-- carry no per-state field shape requirement at all, so they may already be
-- needs_joe and this function answers them.
--
-- No explicit transaction control: from 0339 onward tools/migrate.py runs each
-- migration inside its own single transaction.


alter table ops.work_request
  add column if not exists joe_answer_text text,
  add column if not exists joe_answered_by_actor_id uuid references public.actor(id),
  add column if not exists joe_answered_at timestamptz;

-- Shaped after ops.work_request_triage_receipt (0175): a private, append-only-
-- by-convention receipt that authorizes the sole needs_joe-to-triaged
-- transition and carries the idempotency binding for safe replay.
create table if not exists ops.work_request_joe_answer_receipt (
  id uuid primary key default gen_random_uuid(),
  work_request_id uuid not null references ops.work_request(id),
  idempotency_key uuid not null unique,
  base_version integer not null check (base_version > 0),
  answer_text text not null check (btrim(answer_text) <> ''),
  answered_by_actor_id uuid not null references public.actor(id),
  result_version integer not null check (result_version > 0),
  answered_at timestamptz not null default now(),
  unique (work_request_id)
);

comment on table ops.work_request_joe_answer_receipt is
  'Private receipt that authorizes the sole needs_joe-to-triaged transition (ops.answer_work_request_for_joe). Not a dispatch or execution record.';

revoke all on table ops.work_request_joe_answer_receipt from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant select on table ops.work_request_joe_answer_receipt to carr_reader;

create or replace function ops.answer_work_request_for_joe(
  p_work_request text,
  p_base_version integer,
  p_answer_text text,
  p_idempotency_key uuid
)
returns table (
  id uuid,
  ref text,
  state text,
  version integer,
  answer_text text,
  answered_by_actor_slug text,
  answered_at timestamptz,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor_slug text;
  v_actor public.actor%rowtype;
  v_work_request ops.work_request%rowtype;
  v_receipt ops.work_request_joe_answer_receipt%rowtype;
begin
  if coalesce(btrim(p_work_request), '') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1
     or p_answer_text is null or btrim(p_answer_text) = ''
     or p_idempotency_key is null then
    raise exception 'answering a Work Request for Joe requires a ref, exact positive base version, non-empty answer text, and UUID idempotency key';
  end if;

  v_actor_slug := ops.authority_actor_slug();
  select a.* into v_actor from public.actor a
   where a.slug = v_actor_slug and a.active and a.kind = 'human'
   for share;
  if not found then
    raise exception 'authority session user is not an active human actor';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('work-request-joe-answer:' || p_idempotency_key, 0));
  select r.* into v_receipt from ops.work_request_joe_answer_receipt r
   where r.idempotency_key = p_idempotency_key
   for share;
  if found then
    select w.* into v_work_request from ops.work_request w where w.id = v_receipt.work_request_id for share;
    if not found
       or v_receipt.base_version is distinct from p_base_version
       or btrim(v_receipt.answer_text) is distinct from btrim(p_answer_text)
       or v_receipt.answered_by_actor_id is distinct from v_actor.id
       or v_work_request.ref is distinct from p_work_request
       or v_work_request.state is distinct from 'triaged'
       or v_work_request.version is distinct from v_receipt.result_version then
      raise exception 'idempotency key already names a different answer to Joe';
    end if;
    return query select v_work_request.id, v_work_request.ref, v_work_request.state,
      v_work_request.version, v_receipt.answer_text, v_actor.slug, v_receipt.answered_at, true;
    return;
  end if;

  select w.* into v_work_request from ops.work_request w
   where w.ref = p_work_request
   for update;
  if not found
     or v_work_request.state is distinct from 'needs_joe'
     or v_work_request.version is distinct from p_base_version then
    raise exception 'only the exact current needs_joe Work Request may be answered';
  end if;

  insert into ops.work_request_joe_answer_receipt
    (work_request_id,idempotency_key,base_version,answer_text,answered_by_actor_id,result_version)
  values
    (v_work_request.id,p_idempotency_key,p_base_version,btrim(p_answer_text),v_actor.id,v_work_request.version + 1)
  returning * into v_receipt;

  update ops.work_request w
     set state = 'triaged',
         joe_answer_text = v_receipt.answer_text,
         joe_answered_by_actor_id = v_actor.id,
         joe_answered_at = v_receipt.answered_at,
         version = v_receipt.result_version,
         updated_at = now()
   where w.id = v_work_request.id;
  select w.* into v_work_request from ops.work_request w where w.id = v_work_request.id;
  return query select v_work_request.id, v_work_request.ref, v_work_request.state,
    v_work_request.version, v_receipt.answer_text, v_actor.slug, v_receipt.answered_at, false;
end;
$$;

revoke all on function ops.answer_work_request_for_joe(text,integer,text,uuid) from public, carr_reader, carr_writer, carr_jobs;
grant execute on function ops.answer_work_request_for_joe(text,integer,text,uuid) to carr_authority;

comment on function ops.answer_work_request_for_joe(text,integer,text,uuid) is
  'Human-only resolution of the canonical needs_joe -> triaged transition. The answering human is derived from session_user through authority_actor_slug(); no caller actor, tenant, state, assignment, dispatch, approval, or execution field is accepted.';

-- No functional proof block here, matching 0175 (ops.triage_sourced_work_request,
-- the closest analog): ops.authority_actor_slug() derives its answer from
-- session_user, which names an externally provisioned login role
-- (carr_authority_joe / carr_authority_dell, 0273) that this migration lane
-- neither creates nor can SET ROLE into -- only SET SESSION AUTHORIZATION
-- moves session_user, and that is superuser-only and still would not have the
-- role available on a fresh disposable cluster. The DDL, grants, and shapes
-- are proved by mcp-server/test/work-request-joe-answer.test.mjs and
-- mcp-server/test/work-request-joe-answer-migration.test.mjs instead.

