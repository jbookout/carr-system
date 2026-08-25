-- 0304: Hermes runtime admission projection.
--
-- The identity already lives in the server-issued 0303 ExecutionEnvelope.
-- This migration adds no runtime registry, task store, scheduler, token
-- store, or authority source.  It exposes one narrow read function so
-- Bot-Brief can prove that an authenticated non-human Hermes actor is using
-- the exact current activation binding and envelope.

begin;

create or replace function ops.hermes_runtime_admission_for_brief(
  p_runtime_slug text,
  p_profile_key text,
  p_sponsor_slug text,
  p_work_request text,
  p_binding_id text
) returns jsonb
language plpgsql stable security definer
set search_path = pg_catalog, ops, public, pg_temp
as $$
declare
  tenant text := current_setting('carr.organization_tenant_id', true);
  runtime_actor actor%rowtype;
  sponsor actor%rowtype;
  envelope_row ops.execution_envelope_v1%rowtype;
  expected_state_version integer;
  expected_plan_hash text;
  expected_runtime text := 'runtime:' || p_profile_key;
  expected_agent text := 'agent:' || p_profile_key;
  actual_envelope_digest text;
begin
  -- The caller cannot choose any of these values in the tool payload: the
  -- Worker passes the authenticated actor and sponsor, while the rest is the
  -- exact Bot-Brief request. Unknown actors and missing tenant context refuse
  -- without revealing another tenant's registration.
  if p_runtime_slug is distinct from 'hermes-pilot' then
    return jsonb_build_object('status','not_registered','authorized',false,'reason','runtime_identity_not_registered');
  end if;
  select * into runtime_actor from actor
   where slug='hermes-pilot' and kind='automation' and active;
  if runtime_actor.id is null or coalesce(tenant,'')='' or p_work_request is null or p_binding_id is null then
    return jsonb_build_object('status','not_registered','authorized',false,'reason','runtime_or_activation_missing');
  end if;
  select * into sponsor from actor where slug=p_sponsor_slug and kind='human' and active;
  if sponsor.id is null then
    return jsonb_build_object('status','stale','authorized',false,'reason','sponsoring_human_unavailable');
  end if;

  select e.* into envelope_row
    from ops.execution_envelope_v1 e
    join ops.context_activation_binding b on b.id=e.activation_binding_id
    join ops.work_request w on w.id=b.work_request_id
    join ops.sourced_work_request_plan plan on plan.id=b.plan_id
    join ops.sourced_work_request_plan_acceptance_receipt ar
      on ar.work_request_id=w.id and ar.plan_id=plan.id
    join ops.work_request_execution_assignment a on a.work_request_id=w.id
      and a.sponsoring_human_id=sponsor.id
    join agent_profile p on p.id=a.profile_id
   where e.organization_tenant_id=tenant and b.organization_tenant_id=tenant
     and w.organization_tenant_id=tenant and w.ref=p_work_request
     and b.binding_id=p_binding_id and e.activation_binding_id=b.id
     and b.expires_at > now() and e.expires_at > now()
     and w.version=b.work_request_version and plan.work_request_id=w.id
     and plan.plan_hash=b.plan_hash and ar.plan_hash=b.plan_hash
     and ar.result_version=w.version and p.profile_key=p_profile_key
     and e.work_request_id=w.id and e.plan_hash=b.plan_hash
     and p.status='active' and p.current_model is not null and p.current_desk is not null;
  if not found then
    return jsonb_build_object('status','stale','authorized',false,'reason','activation_or_envelope_not_exact');
  end if;

  select b.work_request_version, b.plan_hash
    into expected_state_version, expected_plan_hash
    from ops.context_activation_binding b
   where b.id=envelope_row.activation_binding_id;

  actual_envelope_digest := 'sha256:' || encode(
    public.digest(ops.guidance_import_canonical_json(envelope_row.envelope),'sha256'),'hex');

  if envelope_row.envelope->'server_binding'->'identity'->>'runtime_principal' is distinct from expected_runtime
     or envelope_row.envelope->'server_binding'->'identity'->>'agent_principal_id' is distinct from expected_agent
     or envelope_row.envelope->'server_binding'->'identity'->>'sponsoring_human_id' is distinct from ('human:' || p_sponsor_slug)
     or envelope_row.envelope->'server_binding'->'identity'->>'organization_tenant_id' is distinct from tenant
     or envelope_row.envelope->'server_binding'->'identity'->>'client_mutable' is distinct from 'false'
     or envelope_row.envelope->'server_binding'->'authority'->>'read_only' is distinct from 'true'
     or envelope_row.envelope->'server_binding'->'authority'->>'client_mutable' is distinct from 'false'
     or envelope_row.envelope->'server_binding'->'authority'->>'capability_profile' is distinct from 'capability:metadata-only'
     or envelope_row.envelope->'server_binding'->'adapter'->>'surface' is distinct from 'hermes_desktop'
     or envelope_row.envelope->'server_binding'->'adapter'->>'adapter_id' is distinct from 'adapter:hermes-desktop'
     or envelope_row.envelope->'server_binding'->'adapter'->>'adapter_version' is distinct from 'v1'
     or envelope_row.envelope->'server_binding'->'adapter'->>'native_session_ref' is distinct from ('native:profile-' || p_profile_key)
     or envelope_row.envelope->'server_binding'->'adapter'->>'configuration_fingerprint' is distinct from envelope_row.configuration_digest
     or envelope_row.runtime_profile->>'profile_key' is distinct from p_profile_key
     or envelope_row.runtime_profile->>'profile_version' is distinct from (
       select p.version::text from agent_profile p where p.profile_key=p_profile_key)
     or envelope_row.runtime_profile->>'model_id' is distinct from (
       select 'model:' || p.current_model from agent_profile p where p.profile_key=p_profile_key)
     or envelope_row.runtime_profile->>'desk' is distinct from (
       select p.current_desk from agent_profile p where p.profile_key=p_profile_key)
     or envelope_row.envelope->'server_binding'->'adapter'->>'provider_id' is distinct from envelope_row.runtime_profile->>'provider_id'
     or envelope_row.envelope->'server_binding'->'adapter'->>'model_id' is distinct from envelope_row.runtime_profile->>'model_id'
     or envelope_row.envelope->'state_binding'->>'state_version' is distinct from expected_state_version::text
     or envelope_row.envelope->'state_binding'->>'canonical_record_digest' is distinct from expected_plan_hash
     or envelope_row.envelope->>'work_request_id' is distinct from p_work_request
     or envelope_row.envelope->>'context_activation_ref' is distinct from p_binding_id
     or envelope_row.envelope_digest is distinct from actual_envelope_digest then
    return jsonb_build_object('status','stale','authorized',false,'reason','server_envelope_identity_mismatch');
  end if;

  return jsonb_build_object(
    'status','registered','authorized',true,'reason','exact_server_envelope',
    'registration_scope','execution_envelope','grants_authority',false,
    'runtime_registration_id','envelope:' || envelope_row.id::text,
    'runtime_principal',envelope_row.envelope->'server_binding'->'identity'->>'runtime_principal',
    'agent_principal_id',envelope_row.envelope->'server_binding'->'identity'->>'agent_principal_id',
    'organization_tenant_id',tenant,'sponsoring_human_slug',p_sponsor_slug,
    'work_request',p_work_request,
    'profile_version',(envelope_row.runtime_profile->>'profile_version')::integer,
    'native_session_ref',envelope_row.envelope->'server_binding'->'adapter'->>'native_session_ref',
    'surface',envelope_row.envelope->'server_binding'->'adapter'->>'surface',
    'adapter_id',envelope_row.envelope->'server_binding'->'adapter'->>'adapter_id',
    'adapter_version',envelope_row.envelope->'server_binding'->'adapter'->>'adapter_version',
    'provider_id',envelope_row.envelope->'server_binding'->'adapter'->>'provider_id',
    'model_id',envelope_row.envelope->'server_binding'->'adapter'->>'model_id',
    'configuration_fingerprint',envelope_row.envelope->'server_binding'->'adapter'->>'configuration_fingerprint',
    'capability_profile',envelope_row.envelope->'server_binding'->'authority'->>'capability_profile',
    'read_only',true,'envelope_digest',envelope_row.envelope_digest,
    'activation_binding_id',p_binding_id,'expires_at',envelope_row.expires_at,
    'device_binding_status','not_asserted',
    'operator_surface','job-passport:context-activation',
    'telemetry_ref','observatory:activation-reliability:' || p_binding_id
  );
end $$;

revoke all on function ops.hermes_runtime_admission_for_brief(text,text,text,text,text) from public;
grant execute on function ops.hermes_runtime_admission_for_brief(text,text,text,text,text) to carr_reader, carr_writer;

commit;
