-- Joe's approval is the single authority act. Delivery advice is kept distinct
-- from installed deny controls; historical receipts remain immutable.
alter table ops.rule_admission drop constraint rule_admission_enforcement_status_check;
alter table ops.rule_admission add constraint rule_admission_enforcement_status_check
  check (enforcement_status in ('hard_enforced','authority_enforced','blocked','delivered_advisory'));
alter table ops.rule_approval_receipt drop constraint rule_approval_receipt_policy_kind_check;
alter table ops.rule_approval_receipt add constraint rule_approval_receipt_policy_kind_check
  check (policy_kind in ('machine_enforceable','human_only','judgment_advisory'));
alter table ops.rule_approval_receipt drop constraint rule_approval_receipt_enforcement_status_check;
alter table ops.rule_approval_receipt add constraint rule_approval_receipt_enforcement_status_check
  check ((policy_kind='judgment_advisory' and enforcement_status='delivered_advisory')
      or (policy_kind='machine_enforceable' and enforcement_status='hard_enforced')
      or (policy_kind='human_only' and enforcement_status='authority_enforced'));
alter table ops.rule_approval_receipt drop constraint rule_approval_receipt_requested_control_keys_check;
alter table ops.rule_approval_receipt add constraint rule_approval_receipt_requested_control_keys_check
  check (case when policy_kind='judgment_advisory' then cardinality(requested_control_keys)=0
              else cardinality(requested_control_keys)>0 end);
alter table ops.rule_approval_receipt drop constraint rule_approval_receipt_installed_control_keys_check;
alter table ops.rule_approval_receipt add constraint rule_approval_receipt_installed_control_keys_check
  check (case when policy_kind='judgment_advisory' then cardinality(installed_control_keys)=0
              else cardinality(installed_control_keys)>0 end);

-- One shape validator for explicit admission and the database write gate.
create or replace function ops.validate_rule_delivery(p_projection jsonb) returns void
language plpgsql immutable set search_path=ops,public,pg_temp as $$
declare
  d jsonb := p_projection->'delivery';
  layer text;
begin
  if jsonb_typeof(d) is distinct from 'object' then
    raise exception using message='Delivery must be an object: supply projection.delivery with load_layer, packs, and why.',
      detail='{"error":"rule_delivery_invalid"}';
  end if;
  layer := d->>'load_layer';
  if jsonb_typeof(d->'load_layer') is distinct from 'string'
     or layer not in ('layer0','control','pack') then
    raise exception using message='Delivery load_layer must be layer0, control, or pack.',
      detail='{"error":"rule_delivery_invalid"}';
  end if;
  if jsonb_typeof(d->'packs') is distinct from 'array' then
    raise exception using message='Delivery packs must be an array of named packs.',
      detail='{"error":"rule_delivery_invalid"}';
  end if;
  if exists (select 1 from jsonb_array_elements(d->'packs') p
             where jsonb_typeof(p)<>'string' or btrim(p#>>'{}') in ('','*')) then
    raise exception using message='Delivery packs must contain nonempty names, without wildcards.',
      detail='{"error":"rule_delivery_invalid"}';
  end if;
  if d ? 'why' and jsonb_typeof(d->'why') is distinct from 'string' then
    raise exception using message='Delivery why must be text.', detail='{"error":"rule_delivery_invalid"}';
  end if;
  if layer='layer0' and (jsonb_array_length(d->'packs')<>0 or nullif(btrim(d->>'why'),'') is null) then
    raise exception using message='Layer0 delivery must have no packs and must explain why it is always loaded.',
      detail='{"error":"rule_delivery_invalid"}';
  end if;
  if layer='pack' and jsonb_array_length(d->'packs')=0 then
    raise exception using message='Pack delivery must name at least one pack.', detail='{"error":"rule_delivery_invalid"}';
  end if;
end $$;
revoke all on function ops.validate_rule_delivery(jsonb) from public;
grant execute on function ops.validate_rule_delivery(jsonb) to carr_writer,carr_authority;

create or replace function ops.require_valid_rule_delivery() returns trigger
language plpgsql set search_path=ops,public,pg_temp as $$
begin
  if new.state='admitted' then perform ops.validate_rule_delivery(new.projection); end if;
  return new;
end $$;
revoke all on function ops.require_valid_rule_delivery() from public;
create trigger rule_admission_delivery_shape before insert or update on ops.rule_admission
  for each row execute function ops.require_valid_rule_delivery();

create or replace function ops.approve_rule(
  p_rule_id uuid,p_policy_kind text,p_control_keys text[],p_idempotency_key text,p_reason text
) returns jsonb language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare
  r rule%rowtype;
  a ops.rule_admission%rowtype;
  meta jsonb;
  home text;
  control text;
  kind text := p_policy_kind;
  controls text[] := p_control_keys;
  packs jsonb;
  delivery jsonb;
  contract jsonb;
  intake uuid;
  joe uuid;
  missing text;
begin
  if ops.authority_actor_slug() is distinct from 'joe' then
    raise exception using message='Only Joe authority can approve a system rule.', detail='{"error":"rule_approval_authority_required"}';
  end if;
  if nullif(btrim(p_idempotency_key),'') is null or nullif(btrim(p_reason),'') is null then
    raise exception using message='Approval requires an idempotency key and a reason.', detail='{"error":"rule_approval_input_required"}';
  end if;
  select * into r from rule where id=p_rule_id for update;
  if not found then raise exception using message='The rule to approve was not found.', detail='{"error":"rule_not_found"}'; end if;
  if r.status not in ('proposed','active') then
    raise exception using message='Only a proposed rule can be approved; active approvals can be replayed.', detail='{"error":"rule_not_proposed"}';
  end if;
  select * into a from ops.rule_admission where rule_id=p_rule_id;
  select new_value into meta from event where verb='teach' and subject_type='rule'
    and subject_id=p_rule_id and new_value ? 'enforcement_home'
    order by recorded_at,id limit 1;
  home := meta->>'enforcement_home';
  control := nullif(btrim(meta->>'carrying_control'),'');
  if home='gate' then
    if control is null then
      raise exception using message='This gate rule is missing its carrying_control; name the control to build.', detail='{"error":"rule_control_required"}';
    end if;
    if not exists (select 1 from ops.enforcement_control_catalog c where c.control_key=control
      and c.installed and c.verified_at is not null
      and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')) then
      raise exception using message=format('Build and verify the installed control "%s" before approving this gate rule.',control),
        detail=jsonb_build_object('error','rule_control_not_installed','control',control)::text;
    end if;
    if kind is not null and kind<>'machine_enforceable' then
      raise exception using message=format('Gate rule approval must enforce its named control "%s"; advisory approval is forbidden.',control),
        detail=jsonb_build_object('error','rule_gate_policy_mismatch','control',control)::text;
    end if;
    kind := 'machine_enforceable';
    if controls is not null and not (control=any(controls)) then
      raise exception using message=format('Approval must include the gate rule carrying_control "%s".',control),
        detail=jsonb_build_object('error','rule_gate_control_mismatch','control',control)::text;
    end if;
    controls := coalesce(controls,array[control]);
  elsif a.rule_id is null and home in ('core','jit','judgment_advisory') then
    if kind is not null and kind<>'judgment_advisory' then
      raise exception using message='Core, jit, and judgment advisory delivery must be approved as judgment_advisory.', detail='{"error":"rule_delivery_policy_mismatch"}';
    end if;
    if cardinality(coalesce(controls,'{}'))<>0 then
      raise exception using message='Advisory delivery does not claim installed deny controls.', detail='{"error":"rule_delivery_policy_mismatch"}';
    end if;
    kind := 'judgment_advisory'; controls := '{}';
  else
    -- Existing explicit admissions and verified owner-prebound controls have
    -- current callers. They retain the exact guarded approval route.
    kind := coalesce(kind,a.enforcement_class);
    if home is null and a.rule_id is null and not exists (select 1 from ops.rule_load_layer where rule_id=r.id) then
      raise exception using message='This rule has no teach enforcement metadata or admission. Record its enforcement home before approval.', detail='{"error":"rule_teach_metadata_required"}';
    end if;
  end if;
  if a.enforcement_class='machine_enforceable' and kind is distinct from 'machine_enforceable' then
    raise exception using message='This admitted gate rule requires installed mechanical controls; it cannot be downgraded to advisory delivery.',
      detail='{"error":"rule_gate_policy_mismatch"}';
  end if;
  if controls is null and a.rule_id is not null then
    select coalesce(array_agg(control_key order by control_key),'{}'::text[]) into controls
      from ops.rule_enforcement_point where rule_id=p_rule_id and installed;
  end if;
  if a.rule_id is null and home is not null then
    packs := coalesce(meta->'packs','[]');
    if packs='[]'::jsonb then packs := coalesce(r.scope->'packs',
      case when r.scope ? 'pack' then jsonb_build_array(r.scope->'pack') end,'[]'); end if;
    if jsonb_typeof(packs) is distinct from 'array' then
      raise exception using message='Teach packs or scope.packs must be an array of pack names.', detail='{"error":"rule_delivery_invalid"}';
    end if;
    if home='jit' and jsonb_array_length(packs)=0 then
      raise exception using message='The just-in-time rule is missing a pack. Name the required pack in teach packs or scope.packs.', detail='{"error":"rule_pack_required"}';
    end if;
    delivery := jsonb_build_object('load_layer',case when home='gate' then 'control'
      when home='jit' or (home='judgment_advisory' and jsonb_array_length(packs)>0) then 'pack' else 'layer0' end,
      'packs',case when home in ('core','gate') then '[]'::jsonb else packs end,'why',r.statement);
    perform ops.validate_rule_delivery(jsonb_build_object('delivery',delivery));
    select p into missing from jsonb_array_elements_text(delivery->'packs') p
      where not exists (select 1 from ops.rule_pack where pack=p) order by p limit 1;
    if missing is not null then
      raise exception using message=format('Rule pack "%s" is missing from the pack catalog. Register that pack before approval.',missing),
        detail=jsonb_build_object('error','rule_pack_unknown','pack',missing)::text;
    end if;
    select actor.id into joe from actor where actor.slug='joe' and actor.kind='human' and actor.active;
    select id into intake from ops.guidance_intake where lane='rule' and source_ref='rule:'||r.id order by captured_at limit 1;
    if intake is null then
      raise exception using message='The teach intake record is missing; approval cannot reconstruct its provenance.', detail='{"error":"rule_teach_intake_required"}';
    end if;
    contract := jsonb_build_object('enforcement_class',kind,'binding_moment','when the taught rule applies',
      'applicability',case when r.scope='{}'::jsonb then '{"workflows":["*"],"surfaces":["*"],"tiers":["*"]}'::jsonb else r.scope end,
      'projection',jsonb_build_object('delivery',delivery),
      'reachability','{"paths":["record-layer","session-boot","registered-controls"]}'::jsonb,
      'input_contract','{"type":"object"}'::jsonb,
      'teach',meta);
    insert into ops.rule_admission(rule_id,guidance_intake_id,enforcement_class,binding_moment,
      applicability,projection,reachability,input_contract,state,admitted_by,admitted_at,reason,fixture_refs)
      values(r.id,intake,kind,contract->>'binding_moment',contract->'applicability',contract->'projection',
        contract->'reachability',contract->'input_contract','admitted',joe,now(),btrim(p_reason),array['rule:'||r.id]);
    insert into ops.authority_receipt(idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
      values('admission:'||p_idempotency_key,'admission','rule',r.id,joe,btrim(p_reason),
        encode(digest(contract::text,'sha256'),'hex'),array['rule:'||r.id]);
  end if;
  perform ops.bind_rule_delivery(p_rule_id,p_reason);
  if kind='human_only' and not ('human_authority_runtime'=any(coalesce(controls,'{}'))) then
    controls := array_append(coalesce(controls,'{}'),'human_authority_runtime');
  end if;
  if kind is distinct from 'judgment_advisory' then
    perform ops.bind_rule_controls(p_rule_id,controls,p_reason);
  end if;
  return ops.approve_rule_receipt_activation_v1(p_rule_id,kind,controls,p_idempotency_key,p_reason);
end $$;
revoke all on function ops.approve_rule(uuid,text,text[],text,text) from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.approve_rule(uuid,text,text[],text,text) to carr_authority;

-- Receipt creation and replay retain their exact authority and preimage guards.
create or replace function ops.approve_rule_receipt_activation_v1(p_rule_id uuid, p_policy_kind text, p_control_keys text[], p_idempotency_key text, p_reason text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'ops', 'public', 'pg_temp'
    AS $$
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
  if p_policy_kind is null or p_policy_kind not in ('machine_enforceable','human_only','judgment_advisory') then
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
  if cardinality(v_requested)=0 and p_policy_kind<>'judgment_advisory' then
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
       or not (v_rule.version=v_prior.rule_version or exists (
         select 1 from ops.rule_approval_lifecycle_anchor legacy
          where legacy.approval_receipt_id=v_prior.id and legacy.rule_id=v_rule.id
            and legacy.rule_version_after=v_rule.version
            and legacy.statement_hash=v_prior.statement_hash))
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
  if p_policy_kind='judgment_advisory' then
    if cardinality(v_requested)<>0 then
      raise exception 'advisory delivery must not claim deny controls';
    end if;
    v_evidence := array['rule:'||p_rule_id::text];
  end if;
  v_status := case when p_policy_kind='judgment_advisory' then 'delivered_advisory'
                   when p_policy_kind='human_only'
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
    'projection',coalesce(
      (select a.projection from ops.rule_admission a where a.rule_id=p_rule_id),
      (select jsonb_build_object('delivery',jsonb_build_object(
        'load_layer',l.load_layer,'packs',l.packs,'why',coalesce(l.why,v_rule.statement)))
       from ops.rule_load_layer l where l.rule_id=p_rule_id)),
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
  -- trg_touch_row increments the rule version in that same statement. Store
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
         enforcement=case when v_status='hard_enforced' then 'gate'
                          when v_status='delivered_advisory' then 'prose' else 'constraint' end
   where id=p_rule_id and status='proposed';
  if not found then raise exception 'rule % did not activate',p_rule_id; end if;

  return jsonb_build_object(
    'ok',true,'replayed',false,'rule_id',p_rule_id,'policy_status','active',
    'enforcement_status',v_status,'installed_controls',v_installed,
    'pending_controls','{}'::text[],'approval_receipt_id',v_receipt.id);
end $$;

-- Keep the existing freeze/retirement/amendment checks; add only delivered advice.
create or replace function ops.require_rule_admission() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
declare
  a ops.rule_admission%rowtype;
  v_approval ops.rule_approval_receipt%rowtype;
begin
  if tg_op='UPDATE' and old.status='retired' then
    raise exception 'retired rule % is immutable',old.id;
  end if;
  -- Once the approval receipt exists, the entire rule row is immutable except
  -- for the three exact authority transitions owned below: proposed -> active
  -- in ops.approve_rule, proposed/active -> retired in ops.retire_rule, and
  -- (new, 0349) active -> active with ONLY the statement changed, in
  -- ops.amend_rule_statement. This also blocks no-op UPDATEs that would
  -- otherwise bump the optimistic version and silently make an active receipt
  -- stale through trg_touch_row.
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
    elsif old.status='active' and new.status='active'
       and new.id is not distinct from old.id
       and new.statement is distinct from old.statement
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
       and new.updated_at is not distinct from old.updated_at
       and new.retired_by is not distinct from old.retired_by
       and new.retired_at is not distinct from old.retired_at
       and exists (
         select 1 from ops.rule_amendment_receipt ar
          where ar.rule_id=old.id
            and ar.rule_version_before=old.version
            and ar.prior_statement_hash=encode(digest(old.statement,'sha256'),'hex')
            and ar.new_statement=new.statement
       ) then
      null; -- exact amendment receipt is validated below
    else
      raise exception 'approved rule % is immutable except through exact Joe approval, retirement or amendment',new.id;
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
  if a.enforcement_status not in ('hard_enforced','authority_enforced','delivered_advisory') then
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
       (case when v_approval.enforcement_status='hard_enforced' then 'gate'
             when v_approval.enforcement_status='delivered_advisory' then 'prose' else 'constraint' end) then
    raise exception 'rule % cannot activate: enforcement label does not match approval',new.id;
  end if;
  if a.enforcement_status='delivered_advisory' and (
       a.enforcement_class<>'judgment_advisory'
       or v_approval.policy_kind<>'judgment_advisory'
       or cardinality(v_approval.requested_control_keys)<>0
       or a.projection is distinct from v_approval.normalized_contract->'projection'
       or exists (select 1 from event e where e.verb='teach' and e.subject_id=new.id
                    and e.new_value->>'enforcement_home'='gate')
       or not exists (select 1 from ops.rule_load_layer l where l.rule_id=new.id
                        and l.load_layer in ('layer0','pack'))
     ) then
    raise exception 'rule % cannot activate: advisory delivery cannot bypass a gate control',new.id;
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
