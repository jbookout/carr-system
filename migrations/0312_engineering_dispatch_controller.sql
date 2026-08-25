-- 0312_engineering_dispatch_controller.sql
--
-- Close the execution seam left deliberately open by 0310: a typed slice can
-- be admitted into ops.job, but an unattended controller needs a narrowly
-- derived binding before it may pass that envelope to a fresh native Codex
-- desk.  This migration adds no queue, scheduler, caller-selected identity,
-- or broad jobs grant.

begin;

-- The controller already has SELECT on the append-only engineering evidence,
-- but it intentionally has no read surface over capability sessions.  Return
-- only the exact bindings for a claimed envelope through this definer seam.
-- The job id is part of the lookup so an envelope from another job cannot be
-- used as a source of executor identity or plan scope.
create or replace function ops.engineering_controller_binding(
  p_envelope_id uuid, p_job_id uuid
) returns jsonb
language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select jsonb_build_object(
    'envelope_id', e.id::text,
    'envelope_digest', e.envelope_digest,
    'slice_ref', e.slice_ref,
    'plan_digest', sp.plan_digest,
    'slice_plan', sp.plan,
    'executor_actor', jsonb_build_object('id', a.id::text, 'slug', a.slug),
    'agent_session_id', s.id::text
  )
    from ops.engineering_execution_envelope e
    join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
    join ops.capability_agent_session s on s.id=e.agent_session_id
    join public.actor a on a.id=s.executor_actor_id
   where e.id=p_envelope_id and e.job_id=p_job_id;
$$;

-- 0310 accepted an executor actor id as an argument.  Keep the signature so
-- any in-flight caller gets a crisp refusal rather than a missing-function
-- surprise, but bind that value to the agent session the server issued with
-- the envelope.  The caller no longer has a way to attribute a receipt to a
-- different actor.
create or replace function ops.engineering_record_slice_receipt(
  p_envelope_id uuid, p_lease_token uuid, p_receipt jsonb,
  p_receipt_digest text, p_executor_actor_id uuid
) returns ops.engineering_slice_receipt
language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare e ops.engineering_execution_envelope%rowtype;
        a ops.job_attempt%rowtype;
        session_executor uuid;
        row ops.engineering_slice_receipt%rowtype;
begin
  select * into e from ops.engineering_execution_envelope where id=p_envelope_id;
  if not found then raise exception 'engineering envelope not found'; end if;
  select executor_actor_id into session_executor
    from ops.capability_agent_session where id=e.agent_session_id;
  if session_executor is null or p_executor_actor_id is distinct from session_executor then
    raise exception 'engineering receipt executor is not the server-bound agent session';
  end if;
  select attempt_row.* into a
    from ops.job_attempt attempt_row join ops.job j on j.id=attempt_row.job_id
   where attempt_row.job_id=e.job_id and attempt_row.attempt=j.attempt
     and attempt_row.lease_token=p_lease_token and attempt_row.state='running'
   for update;
  if not found then raise exception 'engineering claim or lease is not current'; end if;
  if p_receipt->>'envelope_digest' <> e.envelope_digest
     or p_receipt->>'slice_ref' <> e.slice_ref
     or p_receipt->>'attempt_id' <> ('attempt:' || a.attempt)
     or p_receipt->>'outcome' not in ('claimed_complete','failed','blocked','reopened') then
    raise exception 'engineering receipt is not bound to the claimed envelope';
  end if;
  insert into ops.engineering_slice_receipt
    (job_attempt_id,envelope_id,work_request_id,slice_ref,attempt_id,
     executor_actor_id,receipt_digest,outcome,receipt)
  values (a.id,e.id,e.work_request_id,e.slice_ref,p_receipt->>'attempt_id',
          session_executor,p_receipt_digest,p_receipt->>'outcome',p_receipt)
  returning * into row;
  return row;
end $$;

-- The registry must name the process that actually claims the scoped job, not
-- the lower-level adapter it happens to call.  This remains on-demand; the
-- room-bridge wake is merely the controller's delivery opportunity.
update ops.job_definition
   set execution_contract =
       '{"entrypoint":"mcp-server/src/engineering-runtime.js","export":"runEngineeringWorker","args":["room-bridge-engineering-controller"],"shadow_args":[],"canary":{"enabled":false,"reason":"fresh native Codex execution has no isolated canary adapter"}}'::jsonb,
       inventory_contract = jsonb_set(
         jsonb_set(inventory_contract, '{external_dependencies}',
           '["room-bridge lease-bound controller","Codex Desktop fresh-native-session adapter"]'::jsonb),
         '{current_completion_signal}',
           to_jsonb('lease-bound typed receipt plus independent reviewer fact'::text)),
       updated_at=now()
 where key='engineering-slice' and version=1;

revoke all on function ops.engineering_controller_binding(uuid,uuid) from public;
revoke all on function ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid) from public;
grant execute on function ops.engineering_controller_binding(uuid,uuid),
  ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid) to carr_jobs;

commit;

do $$
begin
  if not has_function_privilege('carr_jobs',
       'ops.engineering_controller_binding(uuid,uuid)'::regprocedure, 'EXECUTE') then
    raise exception '0312 FAILED: carr_jobs cannot read the fixed controller binding';
  end if;
  if exists (
    select 1
      from pg_proc p
      cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
     where p.oid='ops.engineering_controller_binding(uuid,uuid)'::regprocedure
       and acl.grantee=0 and acl.privilege_type='EXECUTE'
  ) or exists (
    select 1
      from pg_proc p
      cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
     where p.oid='ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid)'::regprocedure
       and acl.grantee=0 and acl.privilege_type='EXECUTE'
  ) then
    raise exception '0312 FAILED: Engineering SECURITY DEFINER function is PUBLIC executable';
  end if;
end $$;
