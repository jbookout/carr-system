-- 0247_system_rule_scope_binding.sql
-- Tighten the two pre-architecture system-rule bindings to their complete,
-- exact approved scopes.  The owner-only synchronization function repeats
-- 0228's exact legacy-retirement tombstone boundary so Joe's later decision
-- cannot be reversed by a scope-binding repair.

begin;

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
       '{}'::jsonb,
       '4a0e59ce-728a-49b5-a055-116156e9470e'::uuid,
       '1fe7c57e-c23f-4fb0-9cff-36f6d3cfcf08'::uuid,
       'Joe is the sole required authority for system development and high-level system decisions',
       $q$One thing I need to make sure of, I do not want this system to become dependent on dell’s approval for changes. He is not involved in system development at all. He is basically just a user of the system who may train a new work flow here and there but he will not be involved in building the system or making high level decisions about the way the system functions. He’s relying on me for that. Don’t block him from any of those decisions but don’t require his approval either$q$,
       'human_authority_runtime',
       'Joe-approved sole system authority'),
      ('a57d981a-8f6d-4c18-95ee-0e63a5a90b89'::uuid,
       'c6fd62eb91d3f03b21a6098a6fd6b2848b902a45b8c0430b1717edf4e143f668',
       $scope${"domain":"system","applies_to":["github","neon","cloudflare","anthropic","openai","google","healthchecks","blotato","make"]}$scope$::jsonb,
       '8b31938a-e2f2-4b8f-9c29-187efa5c1650'::uuid,
       'f7ea060c-268b-47f1-8a17-7168841b77e0'::uuid,
       'Make cost discipline permanent; expire only the temporary emergency restriction',
       $q$But also, we want a budget rule in affect going forward not just expiring in September. We need to operate the system with cost in mind. Not to the point where it limits the system but just to the point where excessive spending is avoided$q$,
       'platform_metering_pre_dispatch',
       'Joe-approved permanent platform cost policy')
    ) as expected(rule_id,statement_hash,rule_scope,decision_id,decision_event_id,
                  decision_title,human_quote,control_key,source)
  loop
    select * into v_rule from rule where id=v_expected.rule_id;
    if not found then continue; end if;
    if v_rule.id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'::uuid
       and exists (select 1 from public.event
                     where id='34f34e23-225b-4d0f-946f-478b59fbce63'::uuid) then
      -- The legacy cost restriction was truthfully retired before 0228 reached
      -- Production.  This is an exact one-row tombstone, not a general escape
      -- hatch for retired rules; any drift remains a hard refusal.
      if not exists (
        select 1
          from rule r
          join public.actor taught_by on taught_by.id=r.taught_by
          join public.event e on e.id='34f34e23-225b-4d0f-946f-478b59fbce63'::uuid
          join public.actor event_actor on event_actor.id=e.actor_id
         where r.id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'::uuid
           and r.status='retired' and r.version=2
           and encode(digest(r.statement,'sha256'),'hex')='c6fd62eb91d3f03b21a6098a6fd6b2848b902a45b8c0430b1717edf4e143f668'
           and r.human_quote=$q$Also, how does this budgeting plan become impossible to overlook? If it’s just prose the system won’t remember it$q$
           and r.scope='{"domain":"system","applies_to":["github","neon","cloudflare","anthropic","openai","google","healthchecks","blotato","make"]}'::jsonb
           and r.personal_to is null and r.enforcement='prose'
           and r.activated_by is null and r.activated_at is null
           and r.supersedes is null
           and r.created_at='2026-08-17T08:29:06.905178Z'::timestamptz
           and r.updated_at='2026-08-21T21:38:29.049309Z'::timestamptz
           and taught_by.id='b6c38b27-d006-4fad-9c38-49edf3130a07'::uuid
           and taught_by.slug='joe' and taught_by.kind='human' and taught_by.active
           and not exists (select 1 from rule successor where successor.supersedes=r.id)
           and e.actor_id='b6c38b27-d006-4fad-9c38-49edf3130a07'::uuid
           and event_actor.slug='joe' and event_actor.kind='human' and event_actor.active
           and e.verb='retire-rule' and e.subject_type='rule' and e.subject_id=r.id
           and e.field='status'
           and e.old_value='{"status":"proposed"}'::jsonb
           and e.new_value='{"status":"retired"}'::jsonb
           and e.cause='automation_job'
           and e.human_quote is null
           and e.occurred_at='2026-08-21T21:38:29.049309Z'::timestamptz
           and e.recorded_at='2026-08-21T21:38:29.049309Z'::timestamptz
           and e.via='oauth-google'
           and e.client_id='https://claude.ai/oauth/mcp-oauth-client-metadata'
           and e.sponsoring_human_slug='joe'
           and e.personal_scope='joe-personal'
           and e.authorization_class='verified_partner'
           and e.organization_tenant_id='carr-internal'
           and e.correlation_id='6923f7f8-4ae6-4db0-93ab-9424d5aea0f1'::uuid
           and e.idempotency_key='c4ad90f7-d8dd-4bf3-8785-659bae3d3f27'
           and encode(digest(coalesce(e.agent_rationale,''),'sha256'),'hex')='82cf84d571cbe49eb61bf9570e2c8f86a114fa216e9ab1b3799181045c881137'
      ) then
        raise exception 'legacy retired system cost rule does not match its exact retirement tombstone preimage';
      end if;
      continue;
    end if;
    if v_rule.status not in ('proposed','active') then
      raise exception 'system rule % is %, expected proposed or active',v_rule.id,v_rule.status;
    end if;
    if v_rule.personal_to is not null
       or v_rule.scope is distinct from v_expected.rule_scope then
      raise exception 'system rule % does not match its exact approved shared scope',v_rule.id;
    end if;
    if encode(digest(v_rule.statement,'sha256'),'hex') is distinct from v_expected.statement_hash then
      raise exception 'system rule % statement does not match Joe-approved preimage',v_rule.id;
    end if;
    if not exists (
      select 1 from public.v_decision_entry d
       where d.decision_id=v_expected.decision_id
         and d.event_id=v_expected.decision_event_id
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

do $$
begin
  if has_function_privilege('carr_reader',
       'ops.sync_system_rule_control_bindings()'::regprocedure,'execute')
     or has_function_privilege('carr_writer',
       'ops.sync_system_rule_control_bindings()'::regprocedure,'execute')
     or has_function_privilege('carr_jobs',
       'ops.sync_system_rule_control_bindings()'::regprocedure,'execute')
     or has_function_privilege('carr_authority',
       'ops.sync_system_rule_control_bindings()'::regprocedure,'execute') then
    raise exception '0247 FAILED: a runtime role can execute system-rule binding synchronization';
  end if;
end $$;

commit;
