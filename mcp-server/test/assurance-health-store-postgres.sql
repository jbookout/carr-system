\set ON_ERROR_STOP on

begin;
select set_config('carr.acting_actor_slug', 'joe', true);

create or replace function pg_temp.a01_evidence(p_layer text, p_status text, p_token text)
returns jsonb language plpgsql as $$
declare d jsonb;
begin
  d := case p_layer
    when 'artifact_assessment' then jsonb_build_object(
      'repository_commit_sha', repeat('a', 40), 'repository_tree_sha', repeat('b', 40),
      'reviewer_fact_id', 'reviewer-fact:' || p_token)
    when 'execution_assessment' then jsonb_build_object(
      'attempt_id', 'attempt:' || p_token, 'envelope_digest', 'sha256:' || repeat('c', 64),
      'plan_hash', 'sha256:' || repeat('d', 64))
    when 'controller_assessment' then jsonb_build_object(
      'controller_state', 'running', 'readback_source', 'controller:' || p_token,
      'readback_at', to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'))
    when 'candidate_outcome_oracle' then jsonb_build_object(
      'governed_data_ref', 'data:' || p_token, 'environment', 'staging',
      'expected_result_ref', 'expected:' || p_token, 'equivalence_comparator', 'exact',
      'component_versions', jsonb_build_object('app', '1'))
    when 'activation_readback' then jsonb_build_object(
      'activation_id', 'activation:' || p_token, 'readback_source', 'provider:' || p_token,
      'readback_at', to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'))
    when 'actual_business_outcome' then jsonb_build_object(
      'outcome_feedback_ref', 'feedback:' || p_token,
      'outcome_feedback_hash', 'sha256:' || repeat('e', 64),
      'acceptance_receipt_id', 'acceptance:' || p_token)
  end;
  return jsonb_build_object(
    'layer', p_layer,
    'basis', case p_layer
      when 'artifact_assessment' then 'independent_artifact_review'
      when 'execution_assessment' then 'attempt_receipt_execution_evidence'
      when 'controller_assessment' then 'controller_readback'
      when 'candidate_outcome_oracle' then 'candidate_outcome_oracle_receipt'
      when 'activation_readback' then 'activation_readback'
      when 'actual_business_outcome' then 'accepted_sourced_outcome_feedback_receipt' end,
    'status', p_status, 'subject_ref', 'release:' || p_token,
    'evidence_ref', 'evidence:' || p_layer || ':' || p_token,
    'evidence_digest', 'sha256:' || encode(digest(p_layer || ':' || p_token || ':' || p_status, 'sha256'), 'hex'),
    'observed_at', to_char((now() - interval '1 minute') at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'expires_at', to_char((now() + interval '1 day') at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'detail', d, 'incident_refs', '[]'::jsonb,
    'recovery_refs', jsonb_build_array('runbook:a01'));
end $$;

do $$
declare
  layers constant text[] := array[
    'artifact_assessment','execution_assessment','controller_assessment',
    'candidate_outcome_oracle','activation_readback','actual_business_outcome'];
  layer text;
  scope_a jsonb := '{"workflow_key":"doctorcre.release","workflow_version":7,"work_request_id":"WR-700"}';
  scope_b jsonb := '{"workflow_key":"doctorcre.release","workflow_version":8,"work_request_id":"WR-800"}';
  scope_missing jsonb := '{"workflow_key":"doctorcre.release","workflow_version":9,"work_request_id":"WR-900"}';
  before_b jsonb;
  after_b jsonb;
  projection jsonb;
  receipt jsonb;
begin
  foreach layer in array layers loop
    receipt := ops.record_assurance_health_evidence(scope_a, pg_temp.a01_evidence(layer, 'pass', 'a-' || layer), gen_random_uuid());
    if receipt->>'evaluator' <> 'joe' or (receipt->>'replayed')::boolean then
      raise exception 'A01 writer did not derive evaluator or reported a first write as replay: %', receipt;
    end if;
    perform ops.record_assurance_health_evidence(scope_b, pg_temp.a01_evidence(layer, 'pass', 'b-' || layer), gen_random_uuid());
    if layer <> 'activation_readback' then
      perform ops.record_assurance_health_evidence(scope_missing, pg_temp.a01_evidence(layer, 'pass', 'm-' || layer), gen_random_uuid());
    end if;
  end loop;

  projection := ops.read_assurance_health('doctorcre.release', 7, 'WR-700');
  if projection->>'state' <> 'healthy' or projection->>'capability_stage' <> 'act'
     or (projection->>'green')::boolean is not true
     or (select count(*) from jsonb_each(projection->'evidence')) <> 6 then
    raise exception 'A01 six-layer projection did not become healthy: %', projection;
  end if;
  if (select count(distinct value->>'evidence_ref') from jsonb_each(projection->'evidence')) <> 6 then
    raise exception 'A01 healthy projection did not trace to six distinct receipts: %', projection;
  end if;

  projection := ops.read_assurance_health('doctorcre.release', 9, 'WR-900');
  if projection->>'state' <> 'not-yet-operational' or projection->>'capability_stage' = 'act'
     or (projection->>'green')::boolean
     or projection#>>'{evidence,activation_readback,state}' <> 'missing' then
    raise exception 'A01 missing layer did not block outcome: %', projection;
  end if;

  before_b := ops.read_assurance_health('doctorcre.release', 8, 'WR-800');
  perform ops.record_assurance_health_evidence(
    scope_a, pg_temp.a01_evidence('actual_business_outcome', 'fail', 'a-outcome-failure'), gen_random_uuid());
  projection := ops.read_assurance_health('doctorcre.release', 7, 'WR-700');
  after_b := ops.read_assurance_health('doctorcre.release', 8, 'WR-800');
  if projection->>'state' <> 'degraded' or projection->>'capability_stage' <> 'draft'
     or projection#>>'{evidence,actual_business_outcome,state}' <> 'failed' then
    raise exception 'A01 scoped failure did not degrade the affected scope: %', projection;
  end if;
  if after_b is distinct from before_b or after_b->>'state' <> 'healthy' then
    raise exception 'A01 failure escaped its exact scope: before %, after %', before_b, after_b;
  end if;

  if has_table_privilege('carr_writer', 'ops.assurance_health_evidence', 'insert') then
    raise exception 'A01 evidence relation admits direct writer DML';
  end if;
  if not has_function_privilege('carr_writer', 'ops.record_assurance_health_evidence(jsonb,jsonb,uuid)', 'execute')
     or not has_function_privilege('carr_writer', 'ops.read_assurance_health(text,integer,text)', 'execute') then
    raise exception 'A01 registered runtime functions are not executable by carr_writer';
  end if;
end $$;

rollback;
