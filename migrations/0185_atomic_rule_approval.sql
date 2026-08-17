-- 0185_atomic_rule_approval.sql
-- Approval has exactly one meaning: the rule becomes enforced and active in
-- this transaction.  If exact verified enforcement is not ready, approval
-- refuses without recording an approval receipt or changing rule authority.

begin;

create table ops.enforcement_control_catalog (
  control_key        text primary key check (btrim(control_key) <> ''),
  implementation_ref text not null check (btrim(implementation_ref) <> ''),
  test_ref            text not null check (btrim(test_ref) <> ''),
  enforcement_class   text not null check (enforcement_class in
    ('deny_gate','stop_gate','schema','surfacing','transactional_schema','judgment_ambient')),
  installed           boolean not null default true,
  verified_at         timestamptz,
  updated_at          timestamptz not null default now(),
  constraint installed_catalog_control_is_verified check (
    not installed or verified_at is not null
  )
);

comment on table ops.enforcement_control_catalog is
  'Server-side catalog of exact controls an approved rule may claim. Callers '
  'name keys only; implementation and test evidence come from this registry.';

insert into ops.enforcement_control_catalog
  (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
select distinct on (control_key)
       control_key,implementation_ref,test_ref,enforcement_class,true,
       coalesce(verified_at,now())
  from ops.rule_enforcement_point
 where installed
 order by control_key,verified_at desc nulls last
on conflict (control_key) do nothing;

-- Merely showing prose in standing context is not an unbreakable control and
-- therefore is intentionally absent from this catalog.
insert into ops.enforcement_control_catalog
  (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
values
  ('human_authority_runtime',
   'migrations/0161_control_plane_authority_boundary.sql; mcp-server/src/mcp.js',
   'mcp-server/test/control-plane-authority-boundary.test.mjs; ops/control-plane-authority-runtime-preflight-selftest.py',
   'transactional_schema',true,now()),
  ('platform_metering_pre_dispatch',
   'lib/platform_metering.py; ops/platform-metering-gate.py; hooks/guard-unattended.py',
   'ops/platform-metering-gate-selftest.py; ops/platform-metering-policy-selftest.py; ops/guard-selftest.py',
   'deny_gate',true,now())
on conflict (control_key) do update set
  implementation_ref=excluded.implementation_ref,
  test_ref=excluded.test_ref,
  enforcement_class=excluded.enforcement_class,
  installed=excluded.installed,
  verified_at=excluded.verified_at,
  updated_at=now();

create table ops.rule_control_binding (
  rule_id          uuid not null references rule(id) on delete restrict,
  control_key      text not null references ops.enforcement_control_catalog(control_key) on delete restrict,
  statement_hash   text not null check (statement_hash ~ '^[0-9a-f]{64}$'),
  binding_contract jsonb not null check (jsonb_typeof(binding_contract)='object'),
  bound_at         timestamptz not null default now(),
  primary key (rule_id,control_key)
);

comment on table ops.rule_control_binding is
  'Owner/deployment-authored exact semantic binding. A globally installed gate '
  'cannot be claimed for an unrelated rule or changed statement.';

insert into ops.rule_control_binding
  (rule_id,control_key,statement_hash,binding_contract)
select ep.rule_id,ep.control_key,encode(digest(r.statement,'sha256'),'hex'),
       jsonb_build_object('source','existing rule_enforcement_point',
                          'implementation_ref',ep.implementation_ref,
                          'test_ref',ep.test_ref)
  from ops.rule_enforcement_point ep
  join rule r on r.id=ep.rule_id
  join ops.enforcement_control_catalog c on c.control_key=ep.control_key
 where ep.installed and c.installed
on conflict (rule_id,control_key) do nothing;

-- Two Joe-approved system rules were captured before this enforcement
-- architecture existed: sole system authority and permanent cost discipline.
-- They are different rules backed by different decisions and controls.  Pin
-- every UUID, decision event, title, quote and statement digest so deployment
-- cannot bless arbitrary text at a familiar UUID or cross-wire governance to
-- the cost gate.  Fresh databases contain neither row and remain a no-op.
create or replace function ops.sync_system_rule_control_bindings()
returns integer
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v_expected record;
  v_rule rule%rowtype;
  v_rows integer;
  v_inserted integer := 0;
begin
  for v_expected in
    select * from (values
      ('ae44e0c0-e773-456c-a85b-2dc4cf4dd49e'::uuid,
       '9e02f7eee01220fd604ba97d605830ea903d3266f95b626a5ca5d9a73567c8f9',
       '4a0e59ce-728a-49b5-a055-116156e9470e'::uuid,
       '1fe7c57e-c23f-4fb0-9cff-36f6d3cfcf08'::uuid,
       'Joe is the sole required authority for system development and high-level system decisions',
       $q$One thing I need to make sure of, I do not want this system to become dependent on dell’s approval for changes. He is not involved in system development at all. He is basically just a user of the system who may train a new work flow here and there but he will not be involved in building the system or making high level decisions about the way the system functions. He’s relying on me for that. Don’t block him from any of those decisions but don’t require his approval either$q$,
       'human_authority_runtime',
       'Joe-approved sole system authority'),
      ('a57d981a-8f6d-4c18-95ee-0e63a5a90b89'::uuid,
       'c6fd62eb91d3f03b21a6098a6fd6b2848b902a45b8c0430b1717edf4e143f668',
       '8b31938a-e2f2-4b8f-9c29-187efa5c1650'::uuid,
       'f7ea060c-268b-47f1-8a17-7168841b77e0'::uuid,
       'Make cost discipline permanent; expire only the temporary emergency restriction',
       $q$But also, we want a budget rule in affect going forward not just expiring in September. We need to operate the system with cost in mind. Not to the point where it limits the system but just to the point where excessive spending is avoided$q$,
       'platform_metering_pre_dispatch',
       'Joe-approved permanent platform cost policy')
    ) as expected(rule_id,statement_hash,decision_id,decision_event_id,
                  decision_title,human_quote,control_key,source)
  loop
    select * into v_rule from rule where id=v_expected.rule_id;
    if not found then continue; end if;
    if v_rule.status not in ('proposed','active') then
      raise exception 'system rule % is %, expected proposed or active',v_rule.id,v_rule.status;
    end if;
    if v_rule.personal_to is not null or v_rule.scope is distinct from '{}'::jsonb then
      raise exception 'system rule % must retain exact shared system-wide scope',v_rule.id;
    end if;
    if encode(digest(v_rule.statement,'sha256'),'hex') is distinct from v_expected.statement_hash then
      raise exception 'system rule % statement does not match Joe-approved preimage',v_rule.id;
    end if;
    if not exists (
      select 1 from public.v_decision_entry d
       where d.decision_id=v_expected.decision_id
         and d.event_id=v_expected.decision_event_id
         and d.author='joe'
         and d.title=v_expected.decision_title
         and d.human_quote=v_expected.human_quote
    ) then
      raise exception 'system rule % lacks its exact Joe decision evidence',v_rule.id;
    end if;
    if not exists (
      select 1 from ops.enforcement_control_catalog c
       where c.control_key=v_expected.control_key
         and c.installed and c.verified_at is not null
    ) then
      raise exception 'system rule % control % is not installed',v_rule.id,v_expected.control_key;
    end if;

    if not exists (
      select 1 from ops.rule_control_binding
       where rule_id=v_rule.id and control_key=v_expected.control_key
    ) then
      insert into ops.rule_control_binding
        (rule_id,control_key,statement_hash,binding_contract)
      select v_rule.id,v_expected.control_key,v_expected.statement_hash,
             jsonb_build_object(
               'source',v_expected.source,
               'durable_decision_ref',v_expected.decision_id,
               'decision_event_ref',v_expected.decision_event_id,
               'rule_id',v_rule.id,
               'rule_version',v_rule.version,
               'implementation_ref',c.implementation_ref,
               'test_ref',c.test_ref)
        from ops.enforcement_control_catalog c
       where c.control_key=v_expected.control_key;
      get diagnostics v_rows = row_count;
    else
      v_rows := 0;
    end if;
    v_inserted := v_inserted + v_rows;

    if not exists (
      select 1 from ops.rule_control_binding b
       where b.rule_id=v_rule.id and b.control_key=v_expected.control_key
         and b.statement_hash=v_expected.statement_hash
         and b.binding_contract->>'durable_decision_ref'=v_expected.decision_id::text
         and b.binding_contract->>'decision_event_ref'=v_expected.decision_event_id::text
    ) then
      raise exception 'system rule % has a stale or conflicting control binding',v_rule.id;
    end if;
  end loop;
  return v_inserted;
end $$;

revoke all on function ops.sync_system_rule_control_bindings()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
select ops.sync_system_rule_control_bindings();

alter table ops.rule_admission
  add column enforcement_status text,
  add column coverage_detail jsonb not null default '{}'::jsonb;

-- Historical rows predate this invariant. Their classification remains
-- visible for audit; the replacement trigger below governs every new active
-- transition and will never admit blocked coverage.
update ops.rule_admission a
   set enforcement_status=case
     when a.enforcement_class='human_only'
       and exists (select 1 from ops.rule_enforcement_point ep
                    where ep.rule_id=a.rule_id and ep.installed)
       then 'authority_enforced'
     when a.enforcement_class='machine_enforceable'
       and exists (select 1 from ops.rule_enforcement_point ep
                    where ep.rule_id=a.rule_id and ep.installed)
       then 'hard_enforced'
     else 'blocked' end
 where enforcement_status is null;

alter table ops.rule_admission
  alter column enforcement_status set not null,
  alter column enforcement_status set default 'blocked',
  add constraint rule_admission_enforcement_status_check check
    (enforcement_status in ('hard_enforced','authority_enforced','blocked'));

alter table ops.rule_admission drop constraint admitted_contract_is_complete;
alter table ops.rule_admission add constraint admitted_contract_is_complete check (
  state <> 'admitted' or (
    admitted_by is not null and admitted_at is not null
    and jsonb_typeof(input_contract)='object'
    and jsonb_typeof(applicability)='object'
    and jsonb_typeof(projection)='object'
    and jsonb_typeof(reachability)='object'
    and (enforcement_class<>'machine_enforceable' or cardinality(fixture_refs)>0)
  )
) not valid;

create table ops.rule_approval_receipt (
  id                     uuid primary key default gen_random_uuid(),
  idempotency_key        text not null unique,
  rule_id                uuid not null references rule(id) on delete restrict,
  rule_version           integer not null check (rule_version > 0),
  statement_hash         text not null check (statement_hash ~ '^[0-9a-f]{64}$'),
  actor_id               uuid not null references actor(id),
  policy_kind            text not null check
    (policy_kind in ('machine_enforceable','human_only')),
  enforcement_status     text not null check
    (enforcement_status in ('hard_enforced','authority_enforced')),
  requested_control_keys text[] not null check (cardinality(requested_control_keys)>0),
  installed_control_keys text[] not null check (cardinality(installed_control_keys)>0),
  reason                 text not null check (btrim(reason) <> ''),
  normalized_contract    jsonb not null check (jsonb_typeof(normalized_contract)='object'),
  contract_hash          text not null check (contract_hash ~ '^[0-9a-f]{64}$'),
  evidence_refs          text[] not null check (cardinality(evidence_refs)>0),
  created_at             timestamptz not null default now(),
  constraint rule_approval_contract_hash_matches check (
    contract_hash=encode(digest(normalized_contract::text,'sha256'),'hex')
  ),
  constraint approved_controls_are_exact check (
    requested_control_keys=installed_control_keys
  )
);

create or replace function ops.refuse_rule_approval_receipt_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'rule approval receipts are append-only';
end $$;

drop trigger if exists rule_approval_receipt_append_only on ops.rule_approval_receipt;
create trigger rule_approval_receipt_append_only
  before update or delete on ops.rule_approval_receipt
  for each row execute function ops.refuse_rule_approval_receipt_rewrite();

alter table rule
  add column retired_by uuid references actor(id),
  add column retired_at timestamptz;

create table ops.rule_retirement_receipt (
  id                    uuid primary key default gen_random_uuid(),
  idempotency_key       text not null unique check (btrim(idempotency_key)<>''),
  rule_id               uuid not null references rule(id) on delete restrict,
  rule_version_before   integer not null check (rule_version_before>0),
  statement_hash        text not null check (statement_hash ~ '^[0-9a-f]{64}$'),
  previous_status       text not null check (previous_status in ('proposed','active')),
  actor_id              uuid not null references actor(id),
  reason                text not null check (btrim(reason)<>''),
  superseded_by         uuid references rule(id) on delete restrict,
  approval_receipt_id   uuid references ops.rule_approval_receipt(id) on delete restrict,
  contract_hash         text not null check (contract_hash ~ '^[0-9a-f]{64}$'),
  created_at            timestamptz not null default now(),
  constraint active_retirement_has_approval check (
    previous_status<>'active' or approval_receipt_id is not null)
);

create trigger rule_retirement_receipt_append_only
  before update or delete on ops.rule_retirement_receipt
  for each row execute function ops.refuse_rule_approval_receipt_rewrite();

-- Proposed contracts remain editable by the admission workflow.  The moment
-- Joe's immutable approval receipt exists, every input that receipt covers is
-- frozen permanently.  This blocks routine writers (and accidental owner
-- maintenance) from changing applicability or removing a control while the
-- old receipt continues to make the rule look enforced.
create or replace function ops.refuse_approved_rule_contract_rewrite()
returns trigger language plpgsql as $$
declare
  v_rule_id uuid;
begin
  v_rule_id := case when tg_op='INSERT' then new.rule_id else old.rule_id end;
  if exists (select 1 from ops.rule_approval_receipt where rule_id=v_rule_id) then
    raise exception 'approved rule % contract is immutable; approve a replacement rule',v_rule_id;
  end if;
  return case when tg_op='DELETE' then old else new end;
end $$;

drop trigger if exists approved_rule_admission_immutable on ops.rule_admission;
create trigger approved_rule_admission_immutable
  before update or delete on ops.rule_admission
  for each row execute function ops.refuse_approved_rule_contract_rewrite();

drop trigger if exists approved_rule_enforcement_point_immutable on ops.rule_enforcement_point;
create trigger approved_rule_enforcement_point_immutable
  before insert or update or delete on ops.rule_enforcement_point
  for each row execute function ops.refuse_approved_rule_contract_rewrite();

drop trigger if exists approved_rule_control_binding_immutable on ops.rule_control_binding;
create trigger approved_rule_control_binding_immutable
  before insert or update or delete on ops.rule_control_binding
  for each row execute function ops.refuse_approved_rule_contract_rewrite();

create or replace function ops.refuse_live_approved_control_rewrite()
returns trigger language plpgsql as $$
begin
  if exists (
    select 1 from ops.rule_approval_receipt ar
     join rule r on r.id=ar.rule_id and r.status='active'
    where old.control_key=any(ar.requested_control_keys)
  ) then
    raise exception 'control % backs an active approved rule and is immutable',old.control_key;
  end if;
  return case when tg_op='DELETE' then old else new end;
end $$;

drop trigger if exists active_approved_control_immutable on ops.enforcement_control_catalog;
create trigger active_approved_control_immutable
  before update or delete on ops.enforcement_control_catalog
  for each row
  execute function ops.refuse_live_approved_control_rewrite();

create or replace view ops.v_rule_enforcement_status as
select r.id as rule_id,r.status as policy_status,a.enforcement_class,
       a.enforcement_status,a.binding_moment,a.coverage_detail,
       coalesce(array_agg(ep.control_key order by ep.control_key)
                  filter (where ep.installed),'{}'::text[]) as installed_controls,
       ar.id as approval_receipt_id,
       ar.created_at as approved_and_activated_at
  from rule r
  join ops.rule_admission a on a.rule_id=r.id
  left join ops.rule_enforcement_point ep on ep.rule_id=r.id
  left join ops.rule_approval_receipt ar on ar.rule_id=r.id
 group by r.id,r.status,a.enforcement_class,a.enforcement_status,a.binding_moment,
          a.coverage_detail,ar.id,ar.created_at;

-- Replace the 0148 policy compiler with the receipt-bound form.  An active row
-- is delivered only while its rule preimage, normalized admission contract,
-- authority receipt, exact bindings and every requested installed control all
-- still match the immutable approval.  A stale or partially disabled rule is
-- therefore absent rather than active prose masquerading as enforcement.
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
language sql stable security definer
set search_path=ops,public,pg_temp
as $$
  select r.id,r.statement,a.enforcement_class,a.binding_moment,a.applicability
    from rule r
    join ops.rule_admission a on a.rule_id=r.id
    join ops.rule_approval_receipt ar
      on ar.rule_id=r.id and ar.actor_id=r.activated_by
     and ar.rule_version=r.version
     and ar.statement_hash=encode(digest(r.statement,'sha256'),'hex')
     and ar.policy_kind=a.enforcement_class
     and ar.enforcement_status=a.enforcement_status
     and ar.normalized_contract->>'binding_moment'=a.binding_moment
     and ar.normalized_contract->'applicability'=a.applicability
     and ar.normalized_contract->'projection'=a.projection
     and ar.normalized_contract->'reachability'=a.reachability
     and ar.normalized_contract->'input_contract'=a.input_contract
     and ar.evidence_refs=a.fixture_refs
   where r.status='active' and a.state='admitted' and a.admitted_by=ar.actor_id
     and exists (
       select 1 from ops.authority_receipt auth
        where auth.idempotency_key='approval:'||ar.idempotency_key
          and auth.kind='activation' and auth.subject_type='rule'
          and auth.subject_id=r.id and auth.actor_id=ar.actor_id
          and auth.contract_hash=ar.contract_hash)
     and not exists (
       select 1 from unnest(ar.requested_control_keys) requested(control_key)
        where not exists (
          select 1 from ops.rule_enforcement_point ep
          join ops.enforcement_control_catalog c using (control_key)
          join ops.rule_control_binding b
            on b.rule_id=ep.rule_id and b.control_key=ep.control_key
         where ep.rule_id=r.id and ep.control_key=requested.control_key
           and ep.installed and c.installed and c.verified_at is not null
           and b.statement_hash=ar.statement_hash
           and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')))
     and (p_workflow is null or not (a.applicability ? 'workflows')
          or a.applicability->'workflows' ? '*'
          or a.applicability->'workflows' ? p_workflow)
     and (p_surface is null or not (a.applicability ? 'surfaces')
          or a.applicability->'surfaces' ? '*'
          or a.applicability->'surfaces' ? p_surface)
     and (p_tier is null or not (a.applicability ? 'tiers')
          or a.applicability->'tiers' ? '*'
          or a.applicability->'tiers' ? p_tier)
   order by r.created_at,r.id
$$;

-- This trigger is the supported-path non-bypassable invariant. A direct table
-- update, the legacy activate verb, or an incomplete client-side transaction
-- cannot create an active rule without the exact immutable enforced receipt.
create or replace function ops.require_rule_admission()
returns trigger language plpgsql as $$
declare
  a ops.rule_admission%rowtype;
  v_approval ops.rule_approval_receipt%rowtype;
begin
  -- Once the approval receipt exists, the entire rule row is immutable except
  -- for the two exact authority transitions owned below: proposed -> active in
  -- ops.approve_rule, and proposed/active -> retired in ops.retire_rule.  This
  -- also blocks no-op UPDATEs that would otherwise bump the optimistic version
  -- and silently make an active receipt stale through trg_touch_row.
  if tg_op='UPDATE'
     and exists (select 1 from ops.rule_approval_receipt where rule_id=old.id) then
    if old.status='proposed' and new.status='active'
       and new.id is not distinct from old.id
       and new.statement is not distinct from old.statement
       and new.human_quote is not distinct from old.human_quote
       and new.taught_by is not distinct from old.taught_by
       and new.scope is not distinct from old.scope
       and new.personal_to is not distinct from old.personal_to
       and new.supersedes is not distinct from old.supersedes
       and new.created_at is not distinct from old.created_at
       and new.version is not distinct from old.version
       and new.updated_at is not distinct from old.updated_at
       and new.retired_by is not distinct from old.retired_by
       and new.retired_at is not distinct from old.retired_at then
      null; -- exact activation fields are validated below
    elsif old.status in ('proposed','active') and new.status='retired'
       and new.id is not distinct from old.id
       and new.statement is not distinct from old.statement
       and new.human_quote is not distinct from old.human_quote
       and new.taught_by is not distinct from old.taught_by
       and new.scope is not distinct from old.scope
       and new.personal_to is not distinct from old.personal_to
       and new.activated_by is not distinct from old.activated_by
       and new.activated_at is not distinct from old.activated_at
       and new.enforcement is not distinct from old.enforcement
       and new.supersedes is not distinct from old.supersedes
       and new.created_at is not distinct from old.created_at
       and new.version is not distinct from old.version
       and new.updated_at is not distinct from old.updated_at then
      null; -- exact retirement actor/receipt is validated below
    else
      raise exception 'approved rule % is immutable except through exact Joe approval or retirement',new.id;
    end if;
  end if;
  if tg_op='UPDATE' and old.status is distinct from 'retired' and new.status='retired' then
    if new.retired_by is null or new.retired_at is null or not exists (
      select 1 from ops.rule_retirement_receipt rr
       where rr.rule_id=old.id and rr.actor_id=new.retired_by
         and rr.rule_version_before=old.version
         and rr.statement_hash=encode(digest(old.statement,'sha256'),'hex')
         and rr.previous_status=old.status
    ) then
      raise exception 'rule % cannot retire without an exact Joe authority receipt',new.id;
    end if;
  end if;
  if not (new.status='active' and
          (tg_op='INSERT' or old.status is distinct from 'active')) then
    return new;
  end if;
  if new.activated_by is null then
    raise exception 'rule % cannot activate without a human activator',new.id;
  end if;
  select * into a from ops.rule_admission where rule_id=new.id;
  if not found or a.state<>'admitted' then
    raise exception 'rule % cannot activate: admitted rule contract is missing',new.id;
  end if;
  if a.enforcement_status not in ('hard_enforced','authority_enforced') then
    raise exception 'rule % cannot activate: active requires installed enforcement, got %',
      new.id,a.enforcement_status;
  end if;
  select * into v_approval from ops.rule_approval_receipt
   where rule_id=new.id and actor_id=new.activated_by
     and enforcement_status=a.enforcement_status
     and statement_hash=encode(digest(new.statement,'sha256'),'hex')
   order by created_at desc limit 1;
  if not found then
    raise exception 'rule % cannot activate: immutable enforced approval receipt is missing',new.id;
  end if;
  if new.enforcement is distinct from
       (case when v_approval.enforcement_status='hard_enforced' then 'gate' else 'constraint' end) then
    raise exception 'rule % cannot activate: enforcement label does not match approval',new.id;
  end if;
  if exists (
    select 1 from unnest(v_approval.requested_control_keys) as requested(control_key)
     where not exists (
       select 1
         from ops.rule_enforcement_point ep
         join ops.enforcement_control_catalog c using (control_key)
         join ops.rule_control_binding b
           on b.rule_id=ep.rule_id and b.control_key=ep.control_key
        where ep.rule_id=new.id and ep.control_key=requested.control_key
          and ep.installed and c.installed and c.verified_at is not null
          and b.statement_hash=encode(digest(new.statement,'sha256'),'hex')
          and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')
     )
  ) then
    raise exception 'rule % cannot activate: exact requested enforcement is incomplete',new.id;
  end if;
  return new;
end $$;

drop trigger if exists rule_activation_requires_admission on rule;
create trigger rule_activation_requires_admission
  before insert or update on rule
  for each row execute function ops.require_rule_admission();

create or replace function ops.approve_rule(
  p_rule_id uuid,
  p_policy_kind text,
  p_control_keys text[],
  p_idempotency_key text,
  p_reason text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule rule%rowtype;
  v_intake_id uuid;
  v_requested text[];
  v_installed text[];
  v_missing text[];
  v_evidence text[];
  v_status text;
  v_contract jsonb;
  v_contract_hash text;
  v_receipt ops.rule_approval_receipt%rowtype;
  v_prior ops.rule_approval_receipt%rowtype;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug <> 'joe' then
    raise exception 'system rule approval requires Joe authority; % may teach and participate but cannot replace Joe approval',
      v_actor_slug;
  end if;
  select id into v_actor_id from actor
   where slug=v_actor_slug and kind='human' and active;
  if v_actor_id is null then
    raise exception 'authority actor % is not an active human',v_actor_slug;
  end if;
  if p_policy_kind='judgment_advisory' then
    raise exception 'advisory guidance is not an unbreakable rule; build a mechanical control before approval';
  end if;
  if p_policy_kind not in ('machine_enforceable','human_only') then
    raise exception 'unsupported policy kind %',p_policy_kind;
  end if;
  if btrim(coalesce(p_idempotency_key,''))='' or btrim(coalesce(p_reason,''))='' then
    raise exception 'idempotency key and approval reason are required';
  end if;

  v_requested := array(
    select distinct btrim(u.control_key)
      from unnest(coalesce(p_control_keys,'{}'::text[])) as u(control_key)
     where btrim(u.control_key)<>'' order by btrim(u.control_key));
  if p_policy_kind='human_only'
     and not ('human_authority_runtime'=any(v_requested)) then
    v_requested := array_append(v_requested,'human_authority_runtime');
    select array_agg(u.control_key order by u.control_key) into v_requested
      from unnest(v_requested) as u(control_key);
  end if;
  if cardinality(v_requested)=0 then
    raise exception 'exact registered controls must be implemented before approval';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-approval:'||p_idempotency_key,0));
  select * into v_prior from ops.rule_approval_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if v_prior.rule_id is distinct from p_rule_id
       or v_prior.policy_kind is distinct from p_policy_kind
       or v_prior.requested_control_keys is distinct from v_requested
       or v_prior.reason is distinct from btrim(p_reason) then
      raise exception 'rule approval idempotency key was reused with different input';
    end if;
    select * into v_rule from rule where id=p_rule_id for update;
    if not found
       or v_rule.status is distinct from 'active'
       or v_rule.version is distinct from v_prior.rule_version
       or encode(digest(v_rule.statement,'sha256'),'hex') is distinct from v_prior.statement_hash
       or v_rule.activated_by is distinct from v_prior.actor_id then
      raise exception 'rule approval replay refused: current active rule no longer matches the immutable approval';
    end if;
    if not exists (
      select 1 from ops.rule_admission a
       where a.rule_id=v_rule.id and a.state='admitted'
         and a.enforcement_status=v_prior.enforcement_status
         and a.enforcement_class=v_prior.policy_kind
         and a.admitted_by=v_prior.actor_id
         and a.binding_moment=v_prior.normalized_contract->>'binding_moment'
         and a.applicability=v_prior.normalized_contract->'applicability'
         and a.projection=v_prior.normalized_contract->'projection'
         and a.reachability=v_prior.normalized_contract->'reachability'
         and a.input_contract=v_prior.normalized_contract->'input_contract'
         and a.fixture_refs=v_prior.evidence_refs
    ) or exists (
      select 1 from unnest(v_prior.requested_control_keys) requested(control_key)
       where not exists (
         select 1
           from ops.rule_enforcement_point ep
           join ops.enforcement_control_catalog c using (control_key)
           join ops.rule_control_binding b
             on b.rule_id=ep.rule_id and b.control_key=ep.control_key
          where ep.rule_id=v_rule.id
            and ep.control_key=requested.control_key
            and ep.installed and c.installed and c.verified_at is not null
            and b.statement_hash=v_prior.statement_hash
            and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')
       )
    ) or not exists (
      select 1 from ops.authority_receipt ar
       where ar.idempotency_key='approval:'||v_prior.idempotency_key
         and ar.kind='activation' and ar.subject_type='rule'
         and ar.subject_id=v_rule.id and ar.actor_id=v_prior.actor_id
         and ar.contract_hash=v_prior.contract_hash
    ) then
      raise exception 'rule approval replay refused: exact installed enforcement or authority evidence is stale';
    end if;
    return jsonb_build_object(
      'ok',true,'replayed',true,'rule_id',v_prior.rule_id,
      'policy_status','active','enforcement_status',v_prior.enforcement_status,
      'installed_controls',v_prior.installed_control_keys,
      'pending_controls','{}'::text[],'approval_receipt_id',v_prior.id);
  end if;

  select * into v_rule from rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status<>'proposed' then
    raise exception 'rule % is %, only a proposed rule can be approved',p_rule_id,v_rule.status;
  end if;

  select coalesce(array_agg(c.control_key order by c.control_key),'{}'::text[]),
         coalesce(array_agg(c.test_ref order by c.control_key),'{}'::text[])
    into v_installed,v_evidence
    from ops.enforcement_control_catalog c
    join ops.rule_control_binding b using (control_key)
   where c.installed and c.verified_at is not null
     and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')
     and b.rule_id=p_rule_id
     and b.statement_hash=encode(digest(v_rule.statement,'sha256'),'hex')
     and c.control_key=any(v_requested);
  v_missing := array(
    select requested.control_key from unnest(v_requested) as requested(control_key)
     where not (requested.control_key=any(v_installed)) order by requested.control_key);
  if cardinality(v_missing)>0 or cardinality(v_installed)<>cardinality(v_requested) then
    raise exception 'rule approval refused: exact enforcement is not installed; missing %',v_missing;
  end if;
  v_status := case when p_policy_kind='human_only'
                   then 'authority_enforced' else 'hard_enforced' end;

  v_contract := jsonb_build_object(
    'rule_id',p_rule_id,
    'rule_version',v_rule.version,
    'statement_hash',encode(digest(v_rule.statement,'sha256'),'hex'),
    'enforcement_class',p_policy_kind,
    'enforcement_status',v_status,
    'binding_moment','when the approved rule applies',
    'applicability',case when v_rule.scope='{}'::jsonb
      then '{"workflows":["*"],"surfaces":["*"],"tiers":["*"]}'::jsonb
      else v_rule.scope end,
    'projection',jsonb_build_object('targets',jsonb_build_array(
      'standing-context','applicable-rules','rule-enforcement-status')),
    'reachability',jsonb_build_object('paths',jsonb_build_array(
      'record-layer','session-boot','registered-controls')),
    'input_contract','{"type":"object","required":["workflow","surface","tier"]}'::jsonb,
    'requested_controls',v_requested);
  v_contract_hash := encode(digest(v_contract::text,'sha256'),'hex');

  select id into v_intake_id from ops.guidance_intake
   where lane='rule' and source_ref='rule:'||p_rule_id::text
   order by captured_at limit 1;
  if v_intake_id is null then
    insert into ops.guidance_intake
      (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
    values ('rule','human','rule:'||p_rule_id::text,v_rule.statement,'admitted',
            v_contract,v_actor_id) returning id into v_intake_id;
  else
    update ops.guidance_intake
       set state='admitted',normalized_contract=v_contract,updated_at=now(),version=version+1
     where id=v_intake_id;
  end if;

  insert into ops.rule_admission
    (rule_id,guidance_intake_id,enforcement_class,enforcement_status,binding_moment,
     applicability,projection,reachability,input_contract,fixture_refs,state,
     admitted_by,admitted_at,reason,coverage_detail)
  values
    (p_rule_id,v_intake_id,p_policy_kind,v_status,'when the approved rule applies',
     v_contract->'applicability',v_contract->'projection',v_contract->'reachability',
     v_contract->'input_contract',v_evidence,'admitted',v_actor_id,now(),btrim(p_reason),
     jsonb_build_object('requested',v_requested,'installed',v_installed,'missing','{}'::text[]))
  on conflict (rule_id) do update set
    guidance_intake_id=excluded.guidance_intake_id,
    enforcement_class=excluded.enforcement_class,
    enforcement_status=excluded.enforcement_status,
    binding_moment=excluded.binding_moment,
    applicability=excluded.applicability,
    projection=excluded.projection,
    reachability=excluded.reachability,
    input_contract=excluded.input_contract,
    fixture_refs=excluded.fixture_refs,
    state='admitted',admitted_by=excluded.admitted_by,admitted_at=excluded.admitted_at,
    reason=excluded.reason,coverage_detail=excluded.coverage_detail,
    version=ops.rule_admission.version+1,updated_at=now();

  update ops.rule_enforcement_point set installed=false,verified_at=null
   where rule_id=p_rule_id and not (control_key=any(v_installed));
  insert into ops.rule_enforcement_point
    (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
  select p_rule_id,control_key,implementation_ref,test_ref,enforcement_class,true,verified_at
    from ops.enforcement_control_catalog
   where control_key=any(v_installed)
  on conflict (rule_id,control_key) do update set
    implementation_ref=excluded.implementation_ref,test_ref=excluded.test_ref,
    enforcement_class=excluded.enforcement_class,installed=true,
    verified_at=excluded.verified_at;

  insert into ops.rule_approval_receipt
    (idempotency_key,rule_id,rule_version,statement_hash,actor_id,policy_kind,
     enforcement_status,requested_control_keys,installed_control_keys,reason,
     normalized_contract,contract_hash,evidence_refs)
  -- The activation UPDATE below is the one permitted active transition and
  -- trg_touch_row increments the rule version in that same statement.  Store
  -- the post-activation version so replay can prove no later mutation occurred.
  values (p_idempotency_key,p_rule_id,v_rule.version+1,
          encode(digest(v_rule.statement,'sha256'),'hex'),v_actor_id,p_policy_kind,v_status,
          v_requested,v_installed,btrim(p_reason),v_contract,v_contract_hash,v_evidence)
  returning * into v_receipt;

  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values ('approval:'||p_idempotency_key,'activation','rule',p_rule_id,v_actor_id,
          'approved, enforced and activated atomically',v_contract_hash,v_evidence);

  update rule
     set status='active',activated_by=v_actor_id,activated_at=now(),
         enforcement=case when v_status='hard_enforced' then 'gate' else 'constraint' end
   where id=p_rule_id and status='proposed';
  if not found then raise exception 'rule % did not activate',p_rule_id; end if;

  return jsonb_build_object(
    'ok',true,'replayed',false,'rule_id',p_rule_id,'policy_status','active',
    'enforcement_status',v_status,'installed_controls',v_installed,
    'pending_controls','{}'::text[],'approval_receipt_id',v_receipt.id);
end $$;

create or replace function ops.retire_rule(
  p_rule_id uuid,
  p_reason text,
  p_superseded_by uuid,
  p_idempotency_key text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule rule%rowtype;
  v_prior ops.rule_retirement_receipt%rowtype;
  v_receipt ops.rule_retirement_receipt%rowtype;
  v_approval_id uuid;
  v_contract jsonb;
  v_contract_hash text;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug<>'joe' then
    raise exception 'system rule retirement requires Joe authority';
  end if;
  select id into v_actor_id from actor
   where slug=v_actor_slug and kind='human' and active;
  if v_actor_id is null then raise exception 'Joe authority actor is not active'; end if;
  if btrim(coalesce(p_reason,''))='' or btrim(coalesce(p_idempotency_key,''))='' then
    raise exception 'retirement reason and idempotency key are required';
  end if;
  if p_superseded_by is not null and p_superseded_by=p_rule_id then
    raise exception 'a rule cannot supersede itself';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-retirement:'||p_idempotency_key,0));
  select * into v_prior from ops.rule_retirement_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if v_prior.rule_id is distinct from p_rule_id
       or v_prior.reason is distinct from btrim(p_reason)
       or v_prior.superseded_by is distinct from p_superseded_by
       or v_prior.actor_id is distinct from v_actor_id then
      raise exception 'rule retirement idempotency key was reused with different input';
    end if;
    if not exists (select 1 from rule where id=p_rule_id and status='retired'
                     and retired_by=v_prior.actor_id) then
      raise exception 'rule retirement replay refused: current rule is not the receipted retirement';
    end if;
    return jsonb_build_object('ok',true,'replayed',true,'rule_id',p_rule_id,
      'previous_status',v_prior.previous_status,'status','retired',
      'retirement_receipt_id',v_prior.id);
  end if;

  select * into v_rule from rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, expected proposed or active',p_rule_id,v_rule.status;
  end if;
  if p_superseded_by is not null and not exists (select 1 from rule where id=p_superseded_by) then
    raise exception 'superseding rule % does not exist',p_superseded_by;
  end if;
  if v_rule.status='active' then
    select id into v_approval_id from ops.rule_approval_receipt
     where rule_id=v_rule.id and rule_version=v_rule.version
       and statement_hash=encode(digest(v_rule.statement,'sha256'),'hex')
     order by created_at desc limit 1;
    if v_approval_id is null then
      raise exception 'active rule % lacks its exact approval receipt',v_rule.id;
    end if;
  end if;

  v_contract := jsonb_build_object(
    'rule_id',v_rule.id,'rule_version_before',v_rule.version,
    'statement_hash',encode(digest(v_rule.statement,'sha256'),'hex'),
    'previous_status',v_rule.status,'actor_id',v_actor_id,
    'reason',btrim(p_reason),'superseded_by',p_superseded_by,
    'approval_receipt_id',v_approval_id);
  v_contract_hash := encode(digest(v_contract::text,'sha256'),'hex');
  insert into ops.rule_retirement_receipt
    (idempotency_key,rule_id,rule_version_before,statement_hash,previous_status,
     actor_id,reason,superseded_by,approval_receipt_id,contract_hash)
  values (p_idempotency_key,v_rule.id,v_rule.version,
          encode(digest(v_rule.statement,'sha256'),'hex'),v_rule.status,
          v_actor_id,btrim(p_reason),p_superseded_by,v_approval_id,v_contract_hash)
  returning * into v_receipt;
  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values ('retirement:'||p_idempotency_key,'override','rule',v_rule.id,v_actor_id,
          'retired by Joe authority: '||btrim(p_reason),v_contract_hash,
          case when v_approval_id is null then '{}'::text[]
               else array[v_approval_id::text] end);
  update rule set status='retired',retired_by=v_actor_id,retired_at=now()
   where id=v_rule.id and status=v_rule.status;
  if not found then raise exception 'rule % retirement raced',v_rule.id; end if;
  return jsonb_build_object('ok',true,'replayed',false,'rule_id',v_rule.id,
    'previous_status',v_rule.status,'status','retired',
    'retirement_receipt_id',v_receipt.id);
end $$;

revoke all on ops.enforcement_control_catalog,ops.rule_control_binding,ops.rule_approval_receipt,
  ops.rule_retirement_receipt,
  ops.v_rule_enforcement_status from public;
revoke all on function ops.approve_rule(uuid,text,text[],text,text)
  from public,carr_reader,carr_writer,carr_jobs;
revoke all on function ops.retire_rule(uuid,text,uuid,text)
  from public,carr_reader,carr_writer,carr_jobs;
revoke all on function ops.applicable_rules(text,text,text) from public;
grant select on public.rule,ops.enforcement_control_catalog,ops.rule_control_binding,
  ops.rule_approval_receipt,ops.rule_retirement_receipt,ops.v_rule_enforcement_status to carr_authority;
grant select on ops.enforcement_control_catalog,ops.rule_control_binding,ops.rule_approval_receipt,
  ops.rule_retirement_receipt,ops.v_rule_enforcement_status to carr_reader,carr_writer,carr_jobs;
grant execute on function ops.approve_rule(uuid,text,text[],text,text) to carr_authority;
grant execute on function ops.retire_rule(uuid,text,uuid,text) to carr_authority;
grant execute on function ops.applicable_rules(text,text,text)
  to carr_authority,carr_reader,carr_writer,carr_jobs;

do $$
begin
  if to_regprocedure('ops.approve_rule(uuid,text,text[],text,text)') is null then
    raise exception '0185 FAILED: enforced approval function is missing';
  end if;
  if has_function_privilege('carr_writer',
       'ops.approve_rule(uuid,text,text[],text,text)'::regprocedure,'execute') then
    raise exception '0185 FAILED: routine writer may approve rules';
  end if;
  if has_function_privilege('carr_writer',
       'ops.sync_system_rule_control_bindings()'::regprocedure,'execute') then
    raise exception '0185 FAILED: routine writer may install semantic rule bindings';
  end if;
  if has_function_privilege('carr_writer',
       'ops.retire_rule(uuid,text,uuid,text)'::regprocedure,'execute') then
    raise exception '0185 FAILED: routine writer may retire rules';
  end if;
  if exists (select 1 from ops.enforcement_control_catalog
              where control_key='standing_context_runtime') then
    raise exception '0185 FAILED: prose surfacing is mislabeled as unbreakable enforcement';
  end if;
  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key='platform_metering_pre_dispatch'
                    and installed and verified_at is not null) then
    raise exception '0185 FAILED: platform metering control is not registered';
  end if;
end $$;

commit;
