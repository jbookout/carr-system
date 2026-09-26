\set ON_ERROR_STOP on
begin;

do $$
begin
  if to_regclass('ops.rw02_runtime_record') is null then
    raise exception 'rw02_runtime_record missing';
  end if;
  if to_regprocedure('ops.rw02_record(text,text,text,text,text,jsonb)') is null then
    raise exception 'rw02_record writer missing';
  end if;
  if to_regprocedure('ops.rw02_replay(text,text,text)') is null then
    raise exception 'rw02_replay reader missing';
  end if;
  if to_regprocedure('ops.rw02_action_evidence(text)') is null then
    raise exception 'rw02_action_evidence reader missing';
  end if;
end $$;

set session authorization carr_writer;
select set_config('carr.acting_actor_slug', 'synthetic-rw02-writer', true);
select set_config('carr.sponsoring_human_slug', 'joe', true);

select ops.rw02_record(
  'record-salesforce-write-readback',
  'rw02-postgres-fixture-0001',
  'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  'opportunity_create',
  'rw02-step-postgres-1',
  jsonb_build_object(
    'schema_version', 'doctorcre-v5-rw02-runtime-store.v1',
    'operation', 'record-salesforce-write-readback',
    'tenant', 'carr-internal',
    'actor_slug', 'joe',
    'recorded_at', ops.f01_now_text(),
    'evaluation', jsonb_build_object(
      'decision', 'confirmed',
      'action_kind', 'opportunity_create',
      'step_key', 'rw02-step-postgres-1',
      'evidence', jsonb_build_object(
        'schema_version', 'doctorcre-v5-rw02-action-evidence.v1',
        'tenant', 'carr-internal',
        'action_kind', 'opportunity_create',
        'step_key', 'rw02-step-postgres-1',
        'preview_digest', 'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
        'envelope_digest', 'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
        'evidence_class', 'fixture',
        'observed_at', '2026-09-26T05:00:00Z',
        'outcome', 'exact_match',
        'readback_digest', 'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
        'evidence_digest', 'sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
      )
    )
  )
);

-- A duplicate check for the SAME action kind is durable too, but it is not
-- readback evidence: the per-action evidence read must not count it.
select ops.rw02_record(
  'record-salesforce-duplicate-check',
  'rw02-postgres-fixture-0002',
  'sha256:1111111111111111111111111111111111111111111111111111111111111111',
  'opportunity_create',
  'rw02-step-postgres-dup-1',
  jsonb_build_object('evaluation', jsonb_build_object('decision', 'link',
    'action_kind', 'opportunity_create', 'step_key', 'rw02-step-postgres-dup-1'))
);

-- Readback evidence sealed for one action cannot be filed under another.
do $$
begin
  perform ops.rw02_record(
    'record-salesforce-write-readback',
    'rw02-postgres-fixture-0003',
    'sha256:2222222222222222222222222222222222222222222222222222222222222222',
    'opportunity_phase_update',
    'rw02-step-postgres-1',
    jsonb_build_object('evaluation', jsonb_build_object('evidence', jsonb_build_object(
      'action_kind', 'opportunity_create', 'step_key', 'rw02-step-postgres-1'))));
  raise exception 'rw02 fixture: cross-action readback was accepted';
exception when others then
  if position('rw02_evidence_binding_mismatch' in sqlerrm) = 0 then raise; end if;
end $$;

do $$
declare v_replay jsonb; v_count integer; v_evidence jsonb;
begin
  select ops.rw02_replay(
    'record-salesforce-write-readback', 'rw02-postgres-fixture-0001',
    'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa') into v_replay;
  if v_replay is null or v_replay->>'action_kind' <> 'opportunity_create' then
    raise exception 'exact replay missing or changed: %', v_replay;
  end if;
  select count(*) into v_count from ops.rw02_action_evidence('opportunity_create');
  select record into v_evidence from ops.rw02_action_evidence('opportunity_create') limit 1;
  if v_count <> 1 or v_evidence->>'action_kind' <> 'opportunity_create' then
    raise exception 'per-action evidence read failed: count %, record %', v_count, v_evidence;
  end if;
  if exists (select 1 from ops.rw02_action_evidence('opportunity_phase_update')) then
    raise exception 'evidence leaked across action kinds';
  end if;
end $$;

do $$
begin
  perform ops.rw02_replay(
    'record-salesforce-write-readback', 'rw02-postgres-fixture-0001',
    'sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff');
  -- The sentinel must NOT contain the expected error text, or the handler
  -- below would accept this raise as the refusal it is looking for.
  raise exception 'rw02 fixture: replay accepted a changed request digest';
exception when others then
  if position('idempotency_key_reused' in sqlerrm) = 0 then raise; end if;
end $$;

do $$
begin
  if has_table_privilege('carr_writer', 'ops.rw02_runtime_record', 'INSERT')
     or has_table_privilege('carr_writer', 'ops.rw02_runtime_record', 'UPDATE')
     or has_table_privilege('carr_writer', 'ops.rw02_runtime_record', 'DELETE') then
    raise exception 'carr_writer has direct DML on append-only RW02 evidence';
  end if;
end $$;

reset session authorization;
rollback;
