-- 0247_system_rule_scope_binding.sql
-- Tighten the remaining pre-architecture governance-rule binding to its exact
-- approved shared scope. The cost rule formerly paired with it was retired by
-- Joe on 2026-08-21 and must remain unbound; the reviewed deploy-wrapper
-- control stays registered independently. This migration replaces only the
-- owner-only synchronization function and revalidates existing records.

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
       'Joe-approved sole system authority')
    ) as expected(rule_id,statement_hash,rule_scope,decision_id,decision_event_id,
                  decision_title,human_quote,control_key,source)
  loop
    select * into v_rule from rule where id=v_expected.rule_id;
    if not found then continue; end if;
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
