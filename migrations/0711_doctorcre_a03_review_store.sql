-- DoctorCRE v5 slice V5-A03: authoritative complete-set review and bounded
-- adjudication. Paired atomically with 0712, the provisional SCAC successor.
--
-- PR #987 shipped the deterministic policy and deliberately left four seams
-- unbound. This migration binds them to append-only state. Participants record
-- only their own authenticated actor; review dimensions are closed; every round
-- freezes all eleven submissions before one batch repair; regression checks may
-- grow but never shrink; and a third round is structurally impossible.

do $preconditions$
declare missing text[] := array[]::text[]; name text;
begin
  if to_regnamespace('ops') is null then missing := missing || 'schema ops'; end if;
  if to_regclass('public.actor') is null then missing := missing || 'public.actor'; end if;
  foreach name in array array[
    'ops.portfolio_canonical_json(jsonb)',
    'public.digest(bytea,text)'
  ] loop
    if to_regprocedure(name) is null then missing := missing || name; end if;
  end loop;
  foreach name in array array['carr_authority','carr_reader','carr_writer'] loop
    if not exists(select 1 from pg_roles where rolname=name) then missing := missing || ('role '||name); end if;
  end loop;
  if cardinality(missing)>0 then
    raise exception 'v5_a03_store_blocked: missing %', array_to_string(missing, ', ') using errcode='42704';
  end if;
end
$preconditions$;

do $fresh_install_only$
declare found text[] := array[]::text[]; name text;
begin
  foreach name in array array[
    'ops.v5_a03_review_case','ops.v5_a03_review_participant','ops.v5_a03_finding_set',
    'ops.v5_a03_review_round','ops.v5_a03_adjudication'
  ] loop
    if to_regclass(name) is not null then found := found || name; end if;
  end loop;
  if cardinality(found)>0 then
    raise exception 'v5_a03_store_blocked: fresh install required; found %', array_to_string(found, ', ')
      using errcode='42P07';
  end if;
end
$fresh_install_only$;

create function ops.v5_a03_tenant()
returns text language sql immutable set search_path=pg_catalog
as $$ select 'carr-internal'::text $$;

create function ops.v5_a03_review_dimensions()
returns text[] language sql immutable set search_path=pg_catalog
as $$ select array['architecture','business','context','cost','migration','operations',
  'product','repository','resilience','security','sequencing']::text[] $$;

create function ops.v5_a03_review_roles()
returns text[] language sql immutable set search_path=pg_catalog
as $$ select array['adjudicator','architect','builder','deployment_controller',
  'integration_controller','program_controller','reviewer']::text[] $$;

create function ops.v5_a03_is_sha256_ref(value text)
returns boolean language sql immutable set search_path=pg_catalog
as $$ select value is not null and value ~ '^sha256:[0-9a-f]{64}$' $$;

create function ops.v5_a03_is_ref(value text)
returns boolean language sql immutable set search_path=pg_catalog
as $$ select value is not null and value ~ '^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$' $$;

create function ops.v5_a03_is_session_ref(value text)
returns boolean language sql immutable set search_path=pg_catalog
as $$ select value is not null and value ~ '^session:[A-Za-z0-9][A-Za-z0-9._:-]{1,119}$' $$;

create function ops.v5_a03_refs_sorted_unique(value text[], allow_empty boolean default true)
returns boolean language sql immutable set search_path=pg_catalog,ops
as $$
  select value is not null
    and (allow_empty or cardinality(value)>0)
    and not exists(select 1 from unnest(value) item where not ops.v5_a03_is_ref(item))
    and value=coalesce((select array_agg(distinct item order by item) from unnest(value) item),array[]::text[])
$$;

create function ops.v5_a03_checks_sorted_unique(value text[])
returns boolean language sql immutable set search_path=pg_catalog
as $$
  select value is not null and cardinality(value)>0
    and not exists(select 1 from unnest(value) item where item is null or btrim(item)='' or length(item)>200)
    and value=coalesce((select array_agg(distinct item order by item) from unnest(value) item),array[]::text[])
$$;

create function ops.v5_a03_roles_oppose(left_role text, right_role text)
returns boolean language sql immutable set search_path=pg_catalog
as $$
  select least(left_role,right_role)||'|'||greatest(left_role,right_role)=any(array[
    'adjudicator|builder','adjudicator|reviewer','architect|reviewer',
    'builder|deployment_controller','builder|integration_controller','builder|program_controller',
    'builder|reviewer','deployment_controller|reviewer','program_controller|reviewer'
  ]::text[])
$$;

create table ops.v5_a03_review_case (
  id uuid not null default gen_random_uuid(),
  tenant text not null,
  change_ref text not null,
  delivered_set_digest text not null,
  maker_actor_id uuid not null,
  maker_session_ref text not null,
  idempotency_key uuid not null,
  created_at timestamptz not null default now(),
  constraint v5_a03_review_case_pk primary key(id),
  constraint v5_a03_review_case_idem unique(idempotency_key),
  constraint v5_a03_review_case_identity unique(tenant,change_ref,delivered_set_digest),
  constraint v5_a03_review_case_actor_fk foreign key(maker_actor_id) references public.actor(id),
  constraint v5_a03_review_case_tenant check(tenant=ops.v5_a03_tenant()),
  constraint v5_a03_review_case_change check(ops.v5_a03_is_ref(change_ref)),
  constraint v5_a03_review_case_digest check(ops.v5_a03_is_sha256_ref(delivered_set_digest)),
  constraint v5_a03_review_case_session check(ops.v5_a03_is_session_ref(maker_session_ref))
);

create table ops.v5_a03_review_participant (
  id uuid not null default gen_random_uuid(),
  case_id uuid not null,
  role text not null,
  dimension text,
  actor_id uuid not null,
  session_ref text not null,
  context_binding text,
  idempotency_key uuid not null,
  created_at timestamptz not null default now(),
  constraint v5_a03_review_participant_pk primary key(id),
  constraint v5_a03_review_participant_idem unique(idempotency_key),
  constraint v5_a03_review_participant_case_fk foreign key(case_id) references ops.v5_a03_review_case(id),
  constraint v5_a03_review_participant_actor_fk foreign key(actor_id) references public.actor(id),
  constraint v5_a03_review_participant_role check(role=any(ops.v5_a03_review_roles())),
  constraint v5_a03_review_participant_session check(ops.v5_a03_is_session_ref(session_ref)),
  constraint v5_a03_reviewer_requires_fresh_context check(
    (role='reviewer' and dimension=any(ops.v5_a03_review_dimensions()) and context_binding='fresh')
    or (role<>'reviewer' and dimension is null and context_binding is null)
  )
);

create unique index v5_a03_one_nonreviewer_role
  on ops.v5_a03_review_participant(case_id,role) where role<>'reviewer';
create unique index v5_a03_reviewer_identity_dimension
  on ops.v5_a03_review_participant(case_id,dimension,actor_id,session_ref) where role='reviewer';

create table ops.v5_a03_finding_set (
  id uuid not null default gen_random_uuid(),
  case_id uuid not null,
  round_ordinal integer not null,
  dimension text not null,
  reviewer_participant_id uuid not null,
  reviewed_set_digest text not null,
  submission_state text not null,
  finding_refs text[] not null,
  enumerated_before_repair boolean not null,
  idempotency_key uuid not null,
  created_at timestamptz not null default now(),
  constraint v5_a03_finding_set_pk primary key(id),
  constraint v5_a03_finding_set_idem unique(idempotency_key),
  constraint v5_a03_finding_set_once unique(case_id,round_ordinal,dimension),
  constraint v5_a03_finding_set_case_fk foreign key(case_id) references ops.v5_a03_review_case(id),
  constraint v5_a03_finding_set_participant_fk foreign key(reviewer_participant_id) references ops.v5_a03_review_participant(id),
  constraint v5_a03_finding_set_round check(round_ordinal between 1 and 2),
  constraint v5_a03_finding_set_dimension check(dimension=any(ops.v5_a03_review_dimensions())),
  constraint v5_a03_finding_set_scope check(ops.v5_a03_is_sha256_ref(reviewed_set_digest)),
  constraint v5_a03_finding_set_submitted check(submission_state='submitted'),
  constraint v5_a03_finding_set_pre_repair check(enumerated_before_repair),
  constraint v5_a03_finding_set_refs check(ops.v5_a03_refs_sorted_unique(finding_refs,true))
);

create table ops.v5_a03_review_round (
  id uuid not null default gen_random_uuid(),
  case_id uuid not null,
  round_ordinal integer not null,
  batch_repair_digest text not null,
  repaired_finding_refs text[] not null,
  regression_suite_ref text not null,
  checks_executed text[] not null,
  post_repair_artifact_digest text not null,
  state text not null,
  sealed_by_actor_id uuid not null,
  idempotency_key uuid not null,
  created_at timestamptz not null default now(),
  constraint v5_a03_review_round_pk primary key(id),
  constraint v5_a03_review_round_idem unique(idempotency_key),
  constraint v5_a03_review_round_once unique(case_id,round_ordinal),
  constraint v5_a03_review_round_case_fk foreign key(case_id) references ops.v5_a03_review_case(id),
  constraint v5_a03_review_round_actor_fk foreign key(sealed_by_actor_id) references public.actor(id),
  constraint v5_a03_review_round_ordinal check(round_ordinal between 1 and 2),
  constraint v5_a03_review_round_batch check(ops.v5_a03_is_sha256_ref(batch_repair_digest)),
  constraint v5_a03_review_round_repaired check(ops.v5_a03_refs_sorted_unique(repaired_finding_refs,true)),
  constraint v5_a03_review_round_suite check(ops.v5_a03_is_ref(regression_suite_ref)),
  constraint v5_a03_review_round_checks check(ops.v5_a03_checks_sorted_unique(checks_executed)),
  constraint v5_a03_review_round_artifact check(ops.v5_a03_is_sha256_ref(post_repair_artifact_digest)),
  constraint v5_a03_review_round_state check(state in ('changes_required','no_changes_required'))
);

create table ops.v5_a03_adjudication (
  id uuid not null default gen_random_uuid(),
  case_id uuid not null,
  adjudicator_participant_id uuid not null,
  outcome text not null,
  disputed_finding_refs text[] not null,
  receipt_digest text not null,
  idempotency_key uuid not null,
  created_at timestamptz not null default now(),
  constraint v5_a03_adjudication_pk primary key(id),
  constraint v5_a03_adjudication_idem unique(idempotency_key),
  constraint v5_a03_adjudication_one unique (case_id),
  constraint v5_a03_adjudication_case_fk foreign key(case_id) references ops.v5_a03_review_case(id),
  constraint v5_a03_adjudication_participant_fk foreign key(adjudicator_participant_id) references ops.v5_a03_review_participant(id),
  constraint v5_a03_adjudication_outcome check(outcome in ('pass','fail','quarantine')),
  constraint v5_a03_adjudication_disputed check(ops.v5_a03_refs_sorted_unique(disputed_finding_refs,false)),
  constraint v5_a03_adjudication_digest check(ops.v5_a03_is_sha256_ref(receipt_digest))
);

comment on table ops.v5_a03_review_case is
  'One immutable delivered-set review identity. The maker actor is server-derived; current status is projected from append-only rounds and adjudication.';
comment on table ops.v5_a03_review_participant is
  'Authenticated actors register only their own duty/session. Opposing role/session reuse is refused by the writer.';
comment on table ops.v5_a03_finding_set is
  'One complete dimension submission before repair, bound to the immutable delivered-set digest.';
comment on table ops.v5_a03_review_round is
  'One sealed eleven-dimension round with the full repaired finding set and non-weakened regression evidence; maximum two.';
comment on table ops.v5_a03_adjudication is
  'The one stronger-adjudicator disposition after two unresolved rounds, stored with content digest.';

create function ops.v5_a03_rows_immutable()
returns trigger language plpgsql set search_path=pg_catalog
as $$ begin raise exception 'v5_a03_%_is_append_only',tg_table_name; end $$;

create trigger v5_a03_review_case_immutable before update or delete on ops.v5_a03_review_case
for each row execute function ops.v5_a03_rows_immutable();
create trigger v5_a03_review_case_truncate_immutable before truncate on ops.v5_a03_review_case
for each statement execute function ops.v5_a03_rows_immutable();
create trigger v5_a03_review_participant_immutable before update or delete on ops.v5_a03_review_participant
for each row execute function ops.v5_a03_rows_immutable();
create trigger v5_a03_review_participant_truncate_immutable before truncate on ops.v5_a03_review_participant
for each statement execute function ops.v5_a03_rows_immutable();
create trigger v5_a03_finding_set_immutable before update or delete on ops.v5_a03_finding_set
for each row execute function ops.v5_a03_rows_immutable();
create trigger v5_a03_finding_set_truncate_immutable before truncate on ops.v5_a03_finding_set
for each statement execute function ops.v5_a03_rows_immutable();
create trigger v5_a03_review_round_immutable before update or delete on ops.v5_a03_review_round
for each row execute function ops.v5_a03_rows_immutable();
create trigger v5_a03_review_round_truncate_immutable before truncate on ops.v5_a03_review_round
for each statement execute function ops.v5_a03_rows_immutable();
create trigger v5_a03_adjudication_immutable before update or delete on ops.v5_a03_adjudication
for each row execute function ops.v5_a03_rows_immutable();
create trigger v5_a03_adjudication_truncate_immutable before truncate on ops.v5_a03_adjudication
for each statement execute function ops.v5_a03_rows_immutable();

create function ops.v5_a03_open_review_case(
  p_change_ref text,p_delivered_set_digest text,p_maker_session_ref text,
  p_idempotency_key uuid,p_actor_id uuid)
returns table(case_id uuid,status text)
language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare made uuid;
begin
  if not ops.v5_a03_is_ref(p_change_ref) or not ops.v5_a03_is_sha256_ref(p_delivered_set_digest)
     or not ops.v5_a03_is_session_ref(p_maker_session_ref) then
    raise exception 'v5_a03_open_invalid';
  end if;
  perform 1 from public.actor where id=p_actor_id and active for key share;
  if not found then raise exception 'v5_a03_maker_actor_current'; end if;
  insert into ops.v5_a03_review_case
    (tenant,change_ref,delivered_set_digest,maker_actor_id,maker_session_ref,idempotency_key)
  values(ops.v5_a03_tenant(),p_change_ref,p_delivered_set_digest,p_actor_id,p_maker_session_ref,p_idempotency_key)
  on conflict(idempotency_key) do nothing returning id into made;
  if made is null then
    select id into made from ops.v5_a03_review_case where idempotency_key=p_idempotency_key;
  end if;
  insert into ops.v5_a03_review_participant
    (case_id,role,dimension,actor_id,session_ref,context_binding,idempotency_key)
  values(made,'builder',null,p_actor_id,p_maker_session_ref,null,p_idempotency_key)
  on conflict(idempotency_key) do nothing;
  return query select made,'open'::text;
end
$$;

create function ops.v5_a03_record_participant(
  p_case_id uuid,p_role text,p_dimension text,p_session_ref text,p_context_binding text,
  p_idempotency_key uuid,p_actor_id uuid)
returns table(participant_id uuid)
language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_case ops.v5_a03_review_case%rowtype; made uuid; existing record;
begin
  perform pg_advisory_xact_lock(hashtextextended('v5-a03-case:'||p_case_id::text,0));
  select * into v_case from ops.v5_a03_review_case where id=p_case_id for key share;
  if not found then raise exception 'v5_a03_case_not_found'; end if;
  if exists(select 1 from ops.v5_a03_adjudication where case_id=p_case_id) then
    raise exception 'v5_a03_review_round_reopened_after_adjudication';
  end if;
  perform 1 from public.actor where id=p_actor_id and active for key share;
  if not found then raise exception 'v5_a03_participant_actor_current'; end if;
  if p_role is null or not (p_role=any(ops.v5_a03_review_roles())) or p_role='builder'
     or not ops.v5_a03_is_session_ref(p_session_ref) then
    raise exception 'v5_a03_participant_invalid';
  end if;
  if p_role='reviewer' then
    if p_dimension is null or not (p_dimension=any(ops.v5_a03_review_dimensions()))
       or p_context_binding is distinct from 'fresh' then
      raise exception 'v5_a03_reviewer_requires_fresh_context';
    end if;
  elsif p_dimension is not null or p_context_binding is not null then
    raise exception 'v5_a03_nonreviewer_has_dimension';
  end if;
  for existing in select * from ops.v5_a03_review_participant where case_id=p_case_id order by id for key share
  loop
    if ops.v5_a03_roles_oppose(existing.role,p_role)
       and (existing.actor_id=p_actor_id or existing.session_ref=p_session_ref) then
      raise exception 'v5_a03_opposing_role_identity_reused';
    end if;
  end loop;
  insert into ops.v5_a03_review_participant
    (case_id,role,dimension,actor_id,session_ref,context_binding,idempotency_key)
  values(p_case_id,p_role,p_dimension,p_actor_id,p_session_ref,p_context_binding,p_idempotency_key)
  on conflict(idempotency_key) do nothing returning id into made;
  if made is null then select id into made from ops.v5_a03_review_participant where idempotency_key=p_idempotency_key; end if;
  return query select made;
end
$$;

create function ops.v5_a03_record_finding_set(
  p_case_id uuid,p_round_ordinal integer,p_dimension text,p_reviewer_session_ref text,
  p_reviewed_set_digest text,p_state text,p_finding_refs text[],p_enumerated_before_repair boolean,
  p_idempotency_key uuid,p_actor_id uuid)
returns table(submission_id uuid)
language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_case ops.v5_a03_review_case%rowtype; v_participant uuid; v_round_count integer; made uuid;
begin
  perform pg_advisory_xact_lock(hashtextextended('v5-a03-case:'||p_case_id::text,0));
  select * into v_case from ops.v5_a03_review_case where id=p_case_id for key share;
  if not found then raise exception 'v5_a03_case_not_found'; end if;
  if exists(select 1 from ops.v5_a03_adjudication where case_id=p_case_id) then
    raise exception 'v5_a03_review_round_reopened_after_adjudication';
  end if;
  select count(*) into v_round_count from ops.v5_a03_review_round where case_id=p_case_id;
  if p_round_ordinal>2 or p_round_ordinal<>v_round_count+1 then
    raise exception 'v5_a03_review_round_limit_exhausted';
  end if;
  if p_reviewed_set_digest is distinct from v_case.delivered_set_digest then
    raise exception 'v5_a03_review_scope_narrower_than_delivered_set';
  end if;
  if p_state is distinct from 'submitted' or p_enumerated_before_repair is distinct from true
     or not ops.v5_a03_refs_sorted_unique(p_finding_refs,true) then
    raise exception 'v5_a03_finding_set_invalid';
  end if;
  select id into v_participant from ops.v5_a03_review_participant
   where case_id=p_case_id and role='reviewer' and dimension=p_dimension
     and actor_id=p_actor_id and session_ref=p_reviewer_session_ref
   order by created_at desc,id desc limit 1 for key share;
  if v_participant is null then raise exception 'v5_a03_reviewer_not_registered'; end if;
  insert into ops.v5_a03_finding_set
    (case_id,round_ordinal,dimension,reviewer_participant_id,reviewed_set_digest,
     submission_state,finding_refs,enumerated_before_repair,idempotency_key)
  values(p_case_id,p_round_ordinal,p_dimension,v_participant,p_reviewed_set_digest,
    p_state,p_finding_refs,p_enumerated_before_repair,p_idempotency_key)
  on conflict(idempotency_key) do nothing returning id into made;
  if made is null then select id into made from ops.v5_a03_finding_set where idempotency_key=p_idempotency_key; end if;
  return query select made;
end
$$;

create function ops.v5_a03_seal_review_round(
  p_case_id uuid,p_round_ordinal integer,p_batch_repair_digest text,p_repaired_finding_refs text[],
  p_regression_suite_ref text,p_checks_executed text[],p_post_repair_artifact_digest text,
  p_state text,p_idempotency_key uuid,p_actor_id uuid)
returns table(round_id uuid,round_ordinal integer,state text)
language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_case ops.v5_a03_review_case%rowtype; v_round_count integer; v_dimension_count integer;
        v_repaired text[]; v_prior_checks text[]; v_expected_state text; made uuid;
begin
  perform pg_advisory_xact_lock(hashtextextended('v5-a03-case:'||p_case_id::text,0));
  select * into v_case from ops.v5_a03_review_case where id=p_case_id for key share;
  if not found then raise exception 'v5_a03_case_not_found'; end if;
  if exists(select 1 from ops.v5_a03_adjudication where case_id=p_case_id) then
    raise exception 'v5_a03_review_round_reopened_after_adjudication';
  end if;
  select count(*) into v_round_count from ops.v5_a03_review_round where case_id=p_case_id;
  if p_round_ordinal>2 or p_round_ordinal<>v_round_count+1 then
    raise exception 'v5_a03_review_round_limit_exhausted';
  end if;
  perform 1 from ops.v5_a03_review_participant
   where case_id=p_case_id and role='program_controller' and actor_id=p_actor_id for key share;
  if not found then raise exception 'v5_a03_round_sealer_not_program_controller'; end if;
  select count(distinct dimension) into v_dimension_count
    from ops.v5_a03_finding_set fs where fs.case_id=p_case_id and fs.round_ordinal=p_round_ordinal;
  if v_dimension_count<>cardinality(ops.v5_a03_review_dimensions()) then
    raise exception 'v5_a03_finding_set_dimension_absent';
  end if;
  if exists(select 1 from ops.v5_a03_finding_set fs
     where fs.case_id=p_case_id and fs.round_ordinal=p_round_ordinal
       and fs.reviewed_set_digest is distinct from v_case.delivered_set_digest) then
    raise exception 'v5_a03_review_scope_narrower_than_delivered_set';
  end if;
  select coalesce(array_agg(distinct finding order by finding),array[]::text[]) into v_repaired
    from ops.v5_a03_finding_set submission
    cross join lateral unnest(submission.finding_refs) finding
   where submission.case_id=p_case_id and submission.round_ordinal=p_round_ordinal;
  if v_repaired is distinct from p_repaired_finding_refs then
    raise exception 'v5_a03_batch_repair_not_complete';
  end if;
  if not ops.v5_a03_is_sha256_ref(p_batch_repair_digest)
     or not ops.v5_a03_is_ref(p_regression_suite_ref)
     or not ops.v5_a03_checks_sorted_unique(p_checks_executed)
     or not ops.v5_a03_is_sha256_ref(p_post_repair_artifact_digest) then
    raise exception 'v5_a03_regression_invalid';
  end if;
  if p_round_ordinal>1 then
    select checks_executed into v_prior_checks from ops.v5_a03_review_round rr
     where rr.case_id=p_case_id and rr.round_ordinal=p_round_ordinal-1 for key share;
    if not coalesce(v_prior_checks <@ p_checks_executed,false) then
      raise exception 'v5_a03_test_weakening';
    end if;
    if exists(
      select 1 from ops.v5_a03_review_round prior
      where prior.case_id=p_case_id and prior.round_ordinal<p_round_ordinal
        and exists(select 1 from unnest(prior.repaired_finding_refs) old_ref where old_ref=any(v_repaired))
    ) then raise exception 'v5_a03_repeated_finding'; end if;
    if exists(select 1 from ops.v5_a03_review_round prior
      where prior.case_id=p_case_id and prior.post_repair_artifact_digest=p_post_repair_artifact_digest) then
      raise exception 'v5_a03_circular_reversion';
    end if;
    if exists(
      select 1
        from ops.v5_a03_finding_set current_set
        join ops.v5_a03_review_participant current_reviewer on current_reviewer.id=current_set.reviewer_participant_id
        join ops.v5_a03_finding_set prior_set on prior_set.case_id=current_set.case_id
          and prior_set.dimension=current_set.dimension and prior_set.round_ordinal<current_set.round_ordinal
        join ops.v5_a03_review_participant prior_reviewer on prior_reviewer.id=prior_set.reviewer_participant_id
       where current_set.case_id=p_case_id and current_set.round_ordinal=p_round_ordinal
         and current_reviewer.actor_id=prior_reviewer.actor_id
         and (cardinality(current_set.finding_refs)>0)<>(cardinality(prior_set.finding_refs)>0)
    ) then raise exception 'v5_a03_reviewer_instability'; end if;
  end if;
  v_expected_state:=case when cardinality(v_repaired)>0 then 'changes_required' else 'no_changes_required' end;
  if p_state is distinct from v_expected_state then raise exception 'v5_a03_round_state_mismatch'; end if;
  insert into ops.v5_a03_review_round
    (case_id,round_ordinal,batch_repair_digest,repaired_finding_refs,regression_suite_ref,
     checks_executed,post_repair_artifact_digest,state,sealed_by_actor_id,idempotency_key)
  values(p_case_id,p_round_ordinal,p_batch_repair_digest,p_repaired_finding_refs,p_regression_suite_ref,
    p_checks_executed,p_post_repair_artifact_digest,p_state,p_actor_id,p_idempotency_key)
  on conflict(idempotency_key) do nothing returning id into made;
  if made is null then select id into made from ops.v5_a03_review_round where idempotency_key=p_idempotency_key; end if;
  return query select made,p_round_ordinal,p_state;
end
$$;

create function ops.v5_a03_record_adjudication(
  p_case_id uuid,p_adjudicator_session_ref text,p_outcome text,p_disputed_finding_refs text[],
  p_idempotency_key uuid,p_actor_id uuid)
returns table(adjudication_id uuid,receipt_digest text,outcome text)
language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare v_case ops.v5_a03_review_case%rowtype; v_participant uuid; v_round_count integer;
        v_receipt jsonb; v_digest text; v_outcome text; made uuid;
begin
  perform pg_advisory_xact_lock(hashtextextended('v5-a03-case:'||p_case_id::text,0));
  select * into v_case from ops.v5_a03_review_case where id=p_case_id for key share;
  if not found then raise exception 'v5_a03_case_not_found'; end if;
  select count(*) into v_round_count from ops.v5_a03_review_round where case_id=p_case_id;
  if v_round_count<>2 then raise exception 'v5_a03_adjudication_before_round_limit'; end if;
  if not exists(select 1 from ops.v5_a03_review_round where case_id=p_case_id and round_ordinal=2 and state='changes_required') then
    raise exception 'v5_a03_adjudication_without_dispute';
  end if;
  if p_outcome not in ('pass','fail','quarantine')
     or not ops.v5_a03_refs_sorted_unique(p_disputed_finding_refs,false) then
    raise exception 'v5_a03_adjudication_invalid';
  end if;
  if exists(select 1 from ops.v5_a03_adjudication where case_id=p_case_id) then
    raise exception 'v5_a03_review_round_reopened_after_adjudication';
  end if;
  select id into v_participant from ops.v5_a03_review_participant
   where case_id=p_case_id and role='adjudicator' and actor_id=p_actor_id
     and session_ref=p_adjudicator_session_ref for key share;
  if v_participant is null then raise exception 'v5_a03_adjudicator_not_registered'; end if;
  if exists(select 1 from ops.v5_a03_review_participant party
    where party.case_id=p_case_id and party.role in ('builder','reviewer','deployment_controller')
      and (party.actor_id=p_actor_id or party.session_ref=p_adjudicator_session_ref)) then
    raise exception 'v5_a03_adjudicator_is_a_party';
  end if;
  if exists(select 1 from unnest(p_disputed_finding_refs) disputed
    where not exists(select 1 from ops.v5_a03_finding_set f
      where f.case_id=p_case_id and disputed=any(f.finding_refs))) then
    raise exception 'v5_a03_disputed_finding_unknown';
  end if;
  v_receipt:=jsonb_build_object(
    'kind','adjudication','case_id',p_case_id,'change_ref',v_case.change_ref,
    'delivered_set_digest',v_case.delivered_set_digest,'rounds_completed',2,
    'adjudicator_actor_id',p_actor_id,'adjudicator_session_ref',p_adjudicator_session_ref,
    'outcome',p_outcome,'disputed_finding_refs',to_jsonb(p_disputed_finding_refs));
  v_digest:='sha256:'||encode(public.digest(convert_to(ops.portfolio_canonical_json(v_receipt),'UTF8'),'sha256'),'hex');
  v_outcome:=p_outcome;
  insert into ops.v5_a03_adjudication
    (case_id,adjudicator_participant_id,outcome,disputed_finding_refs,receipt_digest,idempotency_key)
  values(p_case_id,v_participant,p_outcome,p_disputed_finding_refs,v_digest,p_idempotency_key)
  on conflict(idempotency_key) do nothing returning id into made;
  if made is null then
    select a.id,a.receipt_digest,a.outcome into made,v_digest,v_outcome
      from ops.v5_a03_adjudication a where idempotency_key=p_idempotency_key;
  end if;
  return query select made,v_digest,v_outcome;
end
$$;

create function ops.v5_a03_read_review_case(p_case_id uuid)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public
as $$
  select jsonb_build_object(
    'case_id',c.id,'tenant',c.tenant,'change_ref',c.change_ref,
    'delivered_set_digest',c.delivered_set_digest,
    'maker',jsonb_build_object('actor_ref','actor:'||maker.slug,'session_ref',c.maker_session_ref),
    'status',case
      when adjudication.id is not null then 'adjudicated'
      when last_round.round_ordinal=2 and last_round.state='no_changes_required' then 'reviewed_no_changes_required'
      when last_round.round_ordinal=2 then 'awaiting_stronger_adjudication'
      else 'open' end,
    'participants',coalesce((select jsonb_agg(jsonb_build_object(
      'participant_id',p.id,'role',p.role,'dimension',p.dimension,'actor_ref','actor:'||a.slug,
      'session_ref',p.session_ref,'context_binding',p.context_binding,'created_at',p.created_at) order by p.role,p.dimension,p.created_at,p.id)
      from ops.v5_a03_review_participant p join public.actor a on a.id=p.actor_id where p.case_id=c.id),'[]'::jsonb),
    'submissions',coalesce((select jsonb_agg(jsonb_build_object(
      'submission_id',f.id,'round_ordinal',f.round_ordinal,'dimension',f.dimension,
      'reviewed_set_digest',f.reviewed_set_digest,'state',f.submission_state,
      'finding_refs',to_jsonb(f.finding_refs),'enumerated_before_repair',f.enumerated_before_repair,
      'created_at',f.created_at) order by f.round_ordinal,f.dimension)
      from ops.v5_a03_finding_set f where f.case_id=c.id),'[]'::jsonb),
    'rounds',coalesce((select jsonb_agg(jsonb_build_object(
      'round_id',r.id,'round_ordinal',r.round_ordinal,'batch_repair_digest',r.batch_repair_digest,
      'repaired_finding_refs',to_jsonb(r.repaired_finding_refs),'regression_suite_ref',r.regression_suite_ref,
      'checks_executed',to_jsonb(r.checks_executed),'post_repair_artifact_digest',r.post_repair_artifact_digest,
      'state',r.state,'created_at',r.created_at) order by r.round_ordinal)
      from ops.v5_a03_review_round r where r.case_id=c.id),'[]'::jsonb),
    'adjudication',case when adjudication.id is null then null else jsonb_build_object(
      'adjudication_id',adjudication.id,'outcome',adjudication.outcome,
      'disputed_finding_refs',to_jsonb(adjudication.disputed_finding_refs),
      'receipt_digest',adjudication.receipt_digest,'created_at',adjudication.created_at) end,
    'effects','[]'::jsonb
  )
  from ops.v5_a03_review_case c
  join public.actor maker on maker.id=c.maker_actor_id
  left join lateral(select * from ops.v5_a03_review_round r where r.case_id=c.id order by r.round_ordinal desc limit 1) last_round on true
  left join ops.v5_a03_adjudication adjudication on adjudication.case_id=c.id
  where c.id=p_case_id
$$;

revoke all on table ops.v5_a03_review_case,ops.v5_a03_review_participant,
  ops.v5_a03_finding_set,ops.v5_a03_review_round,ops.v5_a03_adjudication
  from public,carr_reader,carr_writer,carr_authority;

revoke all on function
  ops.v5_a03_tenant(),ops.v5_a03_review_dimensions(),ops.v5_a03_review_roles(),
  ops.v5_a03_is_sha256_ref(text),ops.v5_a03_is_ref(text),ops.v5_a03_is_session_ref(text),
  ops.v5_a03_refs_sorted_unique(text[],boolean),ops.v5_a03_checks_sorted_unique(text[]),
  ops.v5_a03_roles_oppose(text,text),ops.v5_a03_rows_immutable(),
  ops.v5_a03_open_review_case(text,text,text,uuid,uuid),
  ops.v5_a03_record_participant(uuid,text,text,text,text,uuid,uuid),
  ops.v5_a03_record_finding_set(uuid,integer,text,text,text,text,text[],boolean,uuid,uuid),
  ops.v5_a03_seal_review_round(uuid,integer,text,text[],text,text[],text,text,uuid,uuid),
  ops.v5_a03_record_adjudication(uuid,text,text,text[],uuid,uuid),
  ops.v5_a03_read_review_case(uuid)
  from public;

grant execute on function ops.v5_a03_open_review_case(text,text,text,uuid,uuid),
  ops.v5_a03_record_participant(uuid,text,text,text,text,uuid,uuid),
  ops.v5_a03_record_finding_set(uuid,integer,text,text,text,text,text[],boolean,uuid,uuid),
  ops.v5_a03_seal_review_round(uuid,integer,text,text[],text,text[],text,text,uuid,uuid),
  ops.v5_a03_record_adjudication(uuid,text,text,text[],uuid,uuid)
  to carr_writer,carr_authority;

grant execute on function ops.v5_a03_read_review_case(uuid)
  to carr_reader,carr_writer,carr_authority;
