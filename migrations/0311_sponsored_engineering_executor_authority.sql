-- 0311_sponsored_engineering_executor_authority.sql
--
-- Repair the Engineering Passport bootstrap dead end without mutating 0310 or
-- any issued envelope.  Native Codex/Claude sessions remain machine actors in
-- the audit log, but a server-verified sponsor can supply the partner-scoped DB
-- authority connection.  Engineering envelopes gain only the closed repository
-- action set emitted by the runtime.  A stale/read-only attempt is replaced by
-- an immutable successor, never rewritten in place.

begin;

alter table ops.engineering_execution_envelope
  add column if not exists supersedes_envelope_id uuid
    references ops.engineering_execution_envelope(id) on delete restrict,
  add column if not exists supersession_reason text;

alter table ops.engineering_execution_envelope
  drop constraint if exists engineering_execution_envelope_slice_plan_id_slice_ref_key;

alter table ops.engineering_execution_envelope
  add constraint engineering_envelope_supersession_travels_together check (
    (supersedes_envelope_id is null) = (supersession_reason is null)
    and (supersession_reason is null or btrim(supersession_reason) <> '')
  );

create unique index if not exists engineering_envelope_one_successor
  on ops.engineering_execution_envelope(supersedes_envelope_id)
  where supersedes_envelope_id is not null;

create unique index if not exists engineering_envelope_session_attempt
  on ops.engineering_execution_envelope(slice_plan_id,slice_ref,agent_session_id);

create or replace function ops.guard_engineering_envelope_supersession()
returns trigger language plpgsql
set search_path=pg_catalog,ops,public
as $$
declare prior ops.engineering_execution_envelope%rowtype;
        prior_count integer;
begin
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-envelope:' || new.slice_plan_id::text || ':' || new.slice_ref, 0));
  select count(*) into prior_count
    from ops.engineering_execution_envelope
   where slice_plan_id=new.slice_plan_id and slice_ref=new.slice_ref;
  if prior_count=0 then
    if new.supersedes_envelope_id is not null then
      raise exception 'first engineering envelope cannot supersede another envelope';
    end if;
    return new;
  end if;
  if new.supersedes_envelope_id is null then
    raise exception 'later engineering envelope must name its immutable predecessor';
  end if;
  select * into prior from ops.engineering_execution_envelope
   where id=new.supersedes_envelope_id for key share;
  if not found or prior.slice_plan_id<>new.slice_plan_id or prior.slice_ref<>new.slice_ref
     or prior.accepted_plan_id<>new.accepted_plan_id or prior.work_request_id<>new.work_request_id then
    raise exception 'engineering envelope predecessor is outside the exact slice binding';
  end if;
  if exists (select 1 from ops.engineering_execution_envelope
              where supersedes_envelope_id=prior.id) then
    raise exception 'engineering envelope predecessor already has a successor';
  end if;
  if prior.expires_at>now()
     and coalesce((prior.envelope->'server_binding'->'authority'->>'read_only')::boolean,true)=false
     and not exists (select 1 from ops.engineering_slice_receipt r
                      where r.envelope_id=prior.id and r.outcome in ('failed','blocked','reopened')) then
    raise exception 'current executable engineering envelope cannot be superseded';
  end if;
  return new;
end $$;

drop trigger if exists engineering_envelope_supersession_guard
  on ops.engineering_execution_envelope;
create trigger engineering_envelope_supersession_guard
  before insert on ops.engineering_execution_envelope
  for each row execute function ops.guard_engineering_envelope_supersession();

create or replace function ops.engineering_enqueue_slice_job(
  p_work_request text, p_slice_ref text, p_plan_digest text,
  p_idempotency_key text, p_generation integer
)
returns ops.job
language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare row ops.job%rowtype;
        facts jsonb;
        job_key text;
begin
  if btrim(p_work_request) = '' or btrim(p_slice_ref) = ''
     or p_plan_digest !~ '^sha256:[0-9a-f]{64}$'
     or btrim(p_idempotency_key) = '' or p_generation < 1 then
    raise exception 'engineering job admission fields are invalid';
  end if;
  job_key := 'engineering-slice:' || p_plan_digest || ':' || p_work_request || ':' ||
             p_slice_ref || ':generation:' || p_generation;
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-slice:' || p_plan_digest || ':' || p_slice_ref, 0));
  facts := ops.engineering_passport_facts(p_work_request);
  if not exists (
    select 1
      from jsonb_array_elements(coalesce(facts->'slice_plans','[]'::jsonb)) sp,
           jsonb_array_elements(coalesce(sp->'plan'->'slices','[]'::jsonb)) s
     where sp->>'plan_digest' = p_plan_digest and s->>'slice_ref' = p_slice_ref
  ) then
    raise exception 'engineering slice is not registered for the exact plan';
  end if;
  if exists (
    select 1
      from jsonb_array_elements(coalesce(facts->'slice_plans','[]'::jsonb)) sp,
           jsonb_array_elements(coalesce(sp->'plan'->'slices','[]'::jsonb)) s,
           jsonb_array_elements_text(coalesce(s->'dependency_refs','[]'::jsonb)) dep
     where s->>'slice_ref' = p_slice_ref
       and not exists (
         select 1
           from jsonb_array_elements(coalesce(facts->'receipts','[]'::jsonb)) r,
                jsonb_array_elements(coalesce(facts->'reviewer_facts','[]'::jsonb)) v
          where r->>'slice_ref' = dep and r->>'outcome' = 'claimed_complete'
            and v->>'slice_ref' = dep
            and v->'fact'->>'attempt_id' = r->>'attempt_id'
            and v->>'state' = 'passed'
       )
  ) then
    raise exception 'engineering slice dependencies are not independently verified';
  end if;
  select * into row from ops.job where idempotency_key=job_key;
  if row.id is not null then return row; end if;
  select * into row from ops.enqueue_job(
    'engineering-slice', 1, now(),
    jsonb_build_object('work_request',p_work_request,'slice_ref',p_slice_ref,
                       'plan_digest',p_plan_digest,'generation',p_generation),
    job_key, 'shadow');
  return row;
end $$;

revoke all on function ops.engineering_enqueue_slice_job(text,text,text,text,integer)
  from public,carr_reader,carr_jobs,carr_authority;
grant execute on function ops.engineering_enqueue_slice_job(text,text,text,text,integer)
  to carr_writer;

-- Retire the generation-blind entrypoint. Leaving it executable would preserve
-- a second admission path that can only return the original job and can never
-- bind a valid immutable successor envelope.
revoke all on function ops.engineering_enqueue_slice_job(text,text,text,text)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

update ops.job_definition
   set inventory_contract=jsonb_set(
         inventory_contract,'{authority}',
         to_jsonb('server-derived sponsored Codex execution with a closed repository action allowlist; no caller-selected identity, authority, model, action, or native session'::text)),
       deduplication='{"key_template":"engineering-slice:{plan_digest}:{work_request}:{slice_ref}:generation:{generation}"}'::jsonb,
       updated_at=now()
 where key='engineering-slice' and version=1;

do $$
begin
  if not has_function_privilege('carr_writer',
       'ops.engineering_enqueue_slice_job(text,text,text,text,integer)'::regprocedure,'execute')
     or has_function_privilege('carr_jobs',
       'ops.engineering_enqueue_slice_job(text,text,text,text,integer)'::regprocedure,'execute')
     or has_function_privilege('carr_writer',
       'ops.engineering_enqueue_slice_job(text,text,text,text)'::regprocedure,'execute') then
    raise exception '0311: replacement engineering job admission grants are wrong';
  end if;
  if not exists (select 1 from pg_trigger
                  where tgname='engineering_envelope_supersession_guard' and tgenabled='O') then
    raise exception '0311: envelope supersession guard is missing';
  end if;
  raise notice '0311: sponsored engineering executor authority and immutable envelope replacement ready';
end $$;

commit;
