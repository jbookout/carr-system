-- 0148_control_plane_admission.sql
-- Phase 1 exit control: guidance may be captured freely, but a rule does not
-- become authority until its normalized contract and enforcement posture are
-- present in the same database transaction that activates it.

begin;

create table if not exists ops.guidance_intake (
  id                  uuid primary key default gen_random_uuid(),
  lane                text not null check (lane in ('rule','doctrine','preference','fact','action')),
  source_kind         text not null check (source_kind in ('human','source','correction','system')),
  source_ref          text not null,
  statement           text not null check (btrim(statement) <> ''),
  state               text not null default 'captured'
    check (state in ('captured','normalized','proposed','admitted','rejected','superseded')),
  normalized_contract jsonb,
  captured_by         uuid not null references actor(id),
  captured_at         timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  version             integer not null default 1 check (version > 0),
  constraint normalized_guidance_has_a_contract check (
    state not in ('normalized','proposed','admitted') or normalized_contract is not null
  )
);

comment on table ops.guidance_intake is
  'Phase 1 guidance-intake state machine. Capture is not authority: admission is '
  'a later state backed by ops.rule_admission and a human receipt.';

create table if not exists ops.rule_admission (
  rule_id              uuid primary key references rule(id) on delete restrict,
  guidance_intake_id   uuid references ops.guidance_intake(id) on delete restrict,
  enforcement_class    text not null
    check (enforcement_class in ('machine_enforceable','judgment_advisory','human_only')),
  binding_moment       text not null check (btrim(binding_moment) <> ''),
  applicability        jsonb not null default '{}'::jsonb,
  projection           jsonb not null,
  reachability         jsonb not null,
  input_contract       jsonb not null,
  fixture_refs         text[] not null default '{}',
  state                text not null default 'draft'
    check (state in ('draft','admitted','rejected','needs_revision')),
  admitted_by          uuid references actor(id),
  admitted_at          timestamptz,
  reason               text,
  version              integer not null default 1 check (version > 0),
  updated_at           timestamptz not null default now(),
  constraint admitted_contract_is_complete check (
    state <> 'admitted' or (
      admitted_by is not null and admitted_at is not null
      and jsonb_typeof(input_contract) = 'object'
      and jsonb_typeof(applicability) = 'object'
      and jsonb_typeof(projection) = 'object'
      and jsonb_typeof(reachability) = 'object'
      and (enforcement_class <> 'machine_enforceable' or cardinality(fixture_refs) > 0)
    )
  )
);

comment on table ops.rule_admission is
  'Normalized authority contract for one rule. AI may propose this shape; only '
  'an admitted row plus the rule activation route can make it binding.';

create table if not exists ops.rule_enforcement_point (
  id                  uuid primary key default gen_random_uuid(),
  rule_id             uuid not null references rule(id) on delete restrict,
  control_key         text not null,
  implementation_ref text not null,
  test_ref            text not null,
  enforcement_class  text not null
    check (enforcement_class in ('deny_gate','stop_gate','schema','surfacing',
                                 'transactional_schema','judgment_ambient')),
  installed           boolean not null default false,
  verified_at         timestamptz,
  unique (rule_id, control_key),
  constraint installed_control_has_verification check (
    not installed or (btrim(test_ref) <> '')
  )
);

create table if not exists ops.authority_receipt (
  id              uuid primary key default gen_random_uuid(),
  idempotency_key text not null unique,
  kind            text not null
    check (kind in ('admission','activation','rejection','override','amendment')),
  subject_type    text not null check (subject_type in ('rule','guidance','policy')),
  subject_id      uuid not null,
  actor_id        uuid not null references actor(id),
  decision        text not null,
  contract_hash   text,
  evidence_refs   text[] not null default '{}',
  created_at      timestamptz not null default now()
);

create or replace function ops.refuse_authority_receipt_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'authority receipts are append-only';
end $$;

-- The same control first ran under the pre-renumber migration sequence.
-- A staging database may therefore already have this equivalent trigger even
-- though 0148 is new to its schema ledger.  Replace it deliberately.
drop trigger if exists authority_receipt_append_only on ops.authority_receipt;
create trigger authority_receipt_append_only
  before update or delete on ops.authority_receipt
  for each row execute function ops.refuse_authority_receipt_rewrite();

create or replace view ops.v_rule_applicability as
select r.id as rule_id,
       r.status,
       a.enforcement_class,
       a.binding_moment,
       a.applicability,
       a.projection,
       a.reachability,
       a.version as admission_version,
       a.state as admission_state,
       coalesce((select count(*) from ops.rule_enforcement_point ep
                  where ep.rule_id=r.id and ep.installed), 0) as installed_controls
  from rule r
  join ops.rule_admission a on a.rule_id=r.id;

create or replace function ops.applicable_rules(
  p_workflow text default null,
  p_surface text default null,
  p_tier text default null
) returns table (
  rule_id uuid,
  statement text,
  enforcement_class text,
  binding_moment text,
  applicability jsonb
)
language sql stable
as $$
  select r.id, r.statement, a.enforcement_class, a.binding_moment, a.applicability
    from rule r
    join ops.rule_admission a on a.rule_id=r.id
   where r.status='active' and a.state='admitted'
     and (p_workflow is null
          or not (a.applicability ? 'workflows')
          or a.applicability->'workflows' ? p_workflow)
     and (p_surface is null
          or not (a.applicability ? 'surfaces')
          or a.applicability->'surfaces' ? p_surface)
     and (p_tier is null
          or not (a.applicability ? 'tiers')
          or a.applicability->'tiers' ? p_tier)
   order by r.created_at, r.id
$$;

comment on function ops.applicable_rules(text,text,text) is
  'The deterministic policy compiler/applicability index: finite tags select '
  'admitted active rules; no model performs routing.';

create or replace function ops.require_rule_admission()
returns trigger language plpgsql as $$
declare
  a ops.rule_admission%rowtype;
  n_controls integer;
begin
  if not (old.status='proposed' and new.status='active') then
    return new;
  end if;
  select * into a from ops.rule_admission where rule_id=new.id;
  if not found or a.state <> 'admitted' then
    raise exception 'rule % cannot activate: admitted rule contract is missing', new.id;
  end if;
  if a.enforcement_class='machine_enforceable' then
    select count(*) into n_controls
      from ops.rule_enforcement_point
     where rule_id=new.id and installed;
    if n_controls = 0 then
      raise exception 'rule % cannot activate: no installed enforcement point', new.id;
    end if;
  end if;
  if new.activated_by is null then
    raise exception 'rule % cannot activate without a human activator', new.id;
  end if;
  return new;
end $$;

drop trigger if exists rule_activation_requires_admission on rule;
create trigger rule_activation_requires_admission
  before update of status on rule
  for each row execute function ops.require_rule_admission();

-- Re-admit the cognition-token rule through the new contract. It is already
-- active, so the activation trigger does not fire; this is the explicit Phase 1
-- bridge named in the rule's own interim-admission paragraph.
with target as (
  select id, taught_by, statement from rule
   where status='active'
     and statement like 'NEVER SPEND A COGNITION TOKEN ON STATE,%'
   order by created_at desc limit 1
), intake as (
  insert into ops.guidance_intake
    (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
  select 'rule','system','migration:0148',statement,'admitted',
         jsonb_build_object('enforcement_class','machine_enforceable',
                            'binding_moment','workflow admission and dispatch'),
         taught_by
    from target
  returning id
)
insert into ops.rule_admission
  (rule_id,guidance_intake_id,enforcement_class,binding_moment,applicability,
   projection,reachability,input_contract,fixture_refs,state,admitted_by,admitted_at,reason)
select t.id, i.id, 'machine_enforceable',
       'before workflow registration, schedule dispatch, validation, retry or canonical write',
       '{"workflows":["*"]}'::jsonb,
       '{"targets":["standing-context","workflow-manifest","typed-broker"]}'::jsonb,
       '{"paths":["record-layer","CI","session-rail"]}'::jsonb,
       '{"type":"object","required":["uncertainty_boundary","input_schema","output_schema","budget"]}'::jsonb,
       array['ops/control-plane-selftest.py','ops/control-plane-db-gate.py'],
       'admitted', t.taught_by, now(),
       'Phase 1 re-admission through executable control-plane contract'
  from target t cross join intake i
on conflict (rule_id) do nothing;

insert into ops.rule_enforcement_point
  (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
select id, 'control-plane-workflow-manifest',
       'ops/config/control-plane-workflows.v1.json',
       'ops/control-plane-selftest.py', 'deny_gate', true, now()
  from rule
 where status='active' and statement like 'NEVER SPEND A COGNITION TOKEN ON STATE,%'
on conflict (rule_id,control_key) do nothing;

-- Trigger reads run as the writer role; grant every table it reads before the
-- trigger can fire (rule 5409731b).
grant select on ops.guidance_intake, ops.rule_admission, ops.rule_enforcement_point,
                ops.v_rule_applicability to carr_reader, carr_writer, carr_jobs;
grant insert on ops.guidance_intake, ops.rule_admission, ops.rule_enforcement_point,
                ops.authority_receipt to carr_writer;
grant select on ops.authority_receipt to carr_reader, carr_writer;
grant execute on function ops.applicable_rules(text,text,text) to carr_reader, carr_writer, carr_jobs;

commit;

do $$
begin
  if to_regclass('ops.rule_admission') is null then
    raise exception '0148 FAILED: rule_admission missing';
  end if;
  if not exists (select 1 from pg_trigger
                  where tgrelid='rule'::regclass
                    and tgname='rule_activation_requires_admission'
                    and not tgisinternal) then
    raise exception '0148 FAILED: activation trigger missing';
  end if;
end $$;
