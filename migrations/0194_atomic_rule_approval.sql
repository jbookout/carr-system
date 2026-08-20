-- 0194_atomic_rule_approval.sql
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

-- This trigger is the supported-path non-bypassable invariant. A direct table
-- update, the legacy activate verb, or an incomplete client-side transaction
-- cannot create an active rule without the exact immutable enforced receipt.
create or replace function ops.require_rule_admission()
returns trigger language plpgsql as $$
declare
  a ops.rule_admission%rowtype;
  v_approval ops.rule_approval_receipt%rowtype;
begin
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
  before insert or update of status on rule
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
  values (p_idempotency_key,p_rule_id,v_rule.version,
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

revoke all on ops.enforcement_control_catalog,ops.rule_control_binding,ops.rule_approval_receipt,
  ops.v_rule_enforcement_status from public;
revoke all on function ops.approve_rule(uuid,text,text[],text,text)
  from public,carr_reader,carr_writer,carr_jobs;
grant select on public.rule,ops.enforcement_control_catalog,ops.rule_control_binding,
  ops.rule_approval_receipt,ops.v_rule_enforcement_status to carr_authority;
grant select on ops.enforcement_control_catalog,ops.rule_control_binding,ops.rule_approval_receipt,
  ops.v_rule_enforcement_status to carr_reader,carr_writer,carr_jobs;
grant execute on function ops.approve_rule(uuid,text,text[],text,text) to carr_authority;

do $$
begin
  if to_regprocedure('ops.approve_rule(uuid,text,text[],text,text)') is null then
    raise exception '0194 FAILED: enforced approval function is missing';
  end if;
  if has_function_privilege('carr_writer',
       'ops.approve_rule(uuid,text,text[],text,text)'::regprocedure,'execute') then
    raise exception '0194 FAILED: routine writer may approve rules';
  end if;
  if exists (select 1 from ops.enforcement_control_catalog
              where control_key='standing_context_runtime') then
    raise exception '0194 FAILED: prose surfacing is mislabeled as unbreakable enforcement';
  end if;
  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key='platform_metering_pre_dispatch'
                    and installed and verified_at is not null) then
    raise exception '0194 FAILED: platform metering control is not registered';
  end if;
end $$;

commit;
