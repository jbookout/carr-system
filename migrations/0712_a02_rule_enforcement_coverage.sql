-- 0712_a02_rule_enforcement_coverage.sql
--
-- V5-A02's live rule-coverage guard.  Coverage is derived from current active
-- rule, approval, installed-control, binding, verification and fallback rows.
-- Nothing in this migration chooses a fallback for an existing rule: that is a
-- Joe authority decision recorded through the append-only function below.

create table ops.rule_enforcement_fallback_receipt (
  id                uuid primary key default gen_random_uuid(),
  idempotency_key   text not null unique check (btrim(idempotency_key) <> ''),
  rule_id           uuid not null references public.rule(id) on delete restrict,
  rule_version      integer not null check (rule_version > 0),
  statement_hash    text not null check (statement_hash ~ '^[0-9a-f]{64}$'),
  fallback_kind     text not null check (fallback_kind in (
    'degraded_read_only',
    'documented_manual_procedure',
    'escalate_to_verified_partner',
    'refuse_closed'
  )),
  procedure_ref     text not null check (btrim(procedure_ref) <> ''),
  reason            text not null check (btrim(reason) <> ''),
  recorded_by       uuid not null references public.actor(id) on delete restrict,
  recorded_by_slug  text not null check (recorded_by_slug = 'joe'),
  created_at        timestamptz not null default now(),
  unique (rule_id, rule_version, statement_hash)
);

comment on table ops.rule_enforcement_fallback_receipt is
  'Append-only Joe-authority selection of the fallback for one exact rule '
  'version and statement. V5-A02 coverage accepts only a current exact receipt; '
  'this migration deliberately seeds none.';

create or replace function ops.refuse_rule_enforcement_fallback_receipt_rewrite()
returns trigger language plpgsql as $fn$
begin
  raise exception 'rule enforcement fallback receipts are append-only';
end
$fn$;

create trigger rule_enforcement_fallback_receipt_append_only
  before update or delete on ops.rule_enforcement_fallback_receipt
  for each row execute function ops.refuse_rule_enforcement_fallback_receipt_rewrite();

create or replace function ops.record_rule_enforcement_fallback(
  p_rule_id uuid,
  p_fallback_kind text,
  p_procedure_ref text,
  p_idempotency_key text,
  p_reason text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $fn$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule public.rule%rowtype;
  v_existing ops.rule_enforcement_fallback_receipt%rowtype;
  v_receipt ops.rule_enforcement_fallback_receipt%rowtype;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug <> 'joe' then
    raise exception 'rule enforcement fallback selection requires Joe authority';
  end if;
  if p_fallback_kind not in (
      'degraded_read_only', 'documented_manual_procedure',
      'escalate_to_verified_partner', 'refuse_closed') then
    raise exception 'unknown rule enforcement fallback kind %', p_fallback_kind;
  end if;
  if btrim(coalesce(p_procedure_ref, '')) = ''
     or btrim(coalesce(p_reason, '')) = ''
     or btrim(coalesce(p_idempotency_key, '')) = '' then
    raise exception 'fallback procedure, reason and idempotency key are required';
  end if;

  select * into v_rule from public.rule where id = p_rule_id;
  if not found then raise exception 'rule % does not exist', p_rule_id; end if;
  select id into v_actor_id from public.actor where slug = v_actor_slug;
  if v_actor_id is null then
    raise exception 'authority actor % is not registered', v_actor_slug;
  end if;

  select * into v_existing
    from ops.rule_enforcement_fallback_receipt
   where idempotency_key = p_idempotency_key;
  if found then
    if v_existing.rule_id is distinct from p_rule_id
       or v_existing.rule_version is distinct from v_rule.version
       or v_existing.statement_hash is distinct from encode(public.digest(v_rule.statement, 'sha256'), 'hex')
       or v_existing.fallback_kind is distinct from p_fallback_kind
       or v_existing.procedure_ref is distinct from btrim(p_procedure_ref)
       or v_existing.reason is distinct from btrim(p_reason) then
      raise exception 'fallback idempotency key was already used for a different request';
    end if;
    v_receipt := v_existing;
  else
    insert into ops.rule_enforcement_fallback_receipt
      (idempotency_key, rule_id, rule_version, statement_hash, fallback_kind,
       procedure_ref, reason, recorded_by, recorded_by_slug)
    values
      (p_idempotency_key, p_rule_id, v_rule.version,
       encode(public.digest(v_rule.statement, 'sha256'), 'hex'), p_fallback_kind,
       btrim(p_procedure_ref), btrim(p_reason), v_actor_id, v_actor_slug)
    returning * into v_receipt;
  end if;

  return jsonb_build_object(
    'schema_version', 'rule-enforcement-fallback-receipt.v1',
    'receipt_id', v_receipt.id,
    'rule_id', v_receipt.rule_id,
    'rule_version', v_receipt.rule_version,
    'fallback_kind', v_receipt.fallback_kind,
    'procedure_ref', v_receipt.procedure_ref,
    'recorded_by', v_receipt.recorded_by_slug,
    'created_at', v_receipt.created_at
  );
end
$fn$;

comment on function ops.record_rule_enforcement_fallback(uuid,text,text,text,text) is
  'Joe-authority-only append of one explicit V5-A02 fallback receipt. Actor, '
  'current rule version and statement hash are server-derived; no default is inferred.';

create or replace function ops.v5_a02_rule_enforcement_coverage()
returns jsonb
language plpgsql stable security definer set search_path=ops,public,pg_temp
as $fn$
declare
  v_active integer;
  v_covered integer;
  v_gap_count integer;
  v_gaps jsonb;
  v_facts jsonb;
  v_complete boolean;
  v_observed_at timestamptz := clock_timestamp();
  v_evidence_digest text;
begin
  with facts as (
    select r.id as rule_id,
           exists (
             select 1
               from ops.rule_approval_receipt ar
               join lateral unnest(ar.installed_control_keys) approved(control_key) on true
               join ops.rule_enforcement_point ep
                 on ep.rule_id = ar.rule_id and ep.control_key = approved.control_key
               join ops.enforcement_control_catalog c
                 on c.control_key = ep.control_key
               join ops.rule_control_binding b
                 on b.rule_id = ep.rule_id and b.control_key = ep.control_key
              where ar.rule_id = r.id
                and ar.rule_version = r.version
                and ar.statement_hash = encode(public.digest(r.statement, 'sha256'), 'hex')
                and ep.installed and c.installed
                and b.statement_hash = encode(public.digest(r.statement, 'sha256'), 'hex')
           ) as control_mapped,
           exists (
             select 1
               from ops.rule_approval_receipt ar
               join lateral unnest(ar.installed_control_keys) approved(control_key) on true
               join ops.rule_enforcement_point ep
                 on ep.rule_id = ar.rule_id and ep.control_key = approved.control_key
               join ops.enforcement_control_catalog c
                 on c.control_key = ep.control_key
               join ops.rule_control_binding b
                 on b.rule_id = ep.rule_id and b.control_key = ep.control_key
              where ar.rule_id = r.id
                and ar.rule_version = r.version
                and ar.statement_hash = encode(public.digest(r.statement, 'sha256'), 'hex')
                and ep.installed and c.installed
                and ep.verified_at is not null and c.verified_at is not null
                and btrim(ep.test_ref) <> '' and btrim(c.test_ref) <> ''
                and b.statement_hash = encode(public.digest(r.statement, 'sha256'), 'hex')
           ) as tests_passing,
           exists (
             select 1
               from ops.rule_enforcement_fallback_receipt fr
              where fr.rule_id = r.id
                and fr.rule_version = r.version
                and fr.statement_hash = encode(public.digest(r.statement, 'sha256'), 'hex')
           ) as fallback_recorded
      from public.rule r
     where r.status = 'active'
  ), classified as (
    select f.*,
           case
             when not control_mapped then 'active_rule_control_unmapped'
             when not tests_passing then 'rule_tests_not_passing'
             when not fallback_recorded then 'active_rule_fallback_absent'
             else null
           end as reason_id,
           case
             when not control_mapped then
               'no current approval-bound installed control matches this rule version and statement'
             when not tests_passing then
               'the current installed control has no current verified test evidence'
             when not fallback_recorded then
               'no Joe-authority fallback receipt matches this rule version and statement'
             else null
           end as detail
      from facts f
  )
  select count(*)::integer,
         count(*) filter (where reason_id is null)::integer,
         count(*) filter (where reason_id is not null)::integer,
         coalesce(jsonb_agg(jsonb_build_object(
           'rule_id', rule_id, 'reason_id', reason_id, 'detail', detail)
           order by rule_id) filter (where reason_id is not null), '[]'::jsonb),
         coalesce(jsonb_agg(jsonb_build_object(
           'rule_id', rule_id, 'control_mapped', control_mapped,
           'tests_passing', tests_passing, 'fallback_recorded', fallback_recorded)
           order by rule_id), '[]'::jsonb)
    into v_active, v_covered, v_gap_count, v_gaps, v_facts
    from classified;

  v_complete := v_gap_count = 0 and v_covered = v_active;
  if (v_complete and (v_gap_count <> 0 or jsonb_array_length(v_gaps) <> 0))
     or v_covered + v_gap_count <> v_active then
    raise exception 'rule_coverage_false_green';
  end if;

  v_evidence_digest := 'sha256:' || encode(public.digest(convert_to(
    jsonb_build_object(
      'schema_version', 'doctorcre-v5-a02-rule-enforcement-coverage.v1',
      'active_rule_count', v_active,
      'covered_rule_count', v_covered,
      'gap_count', v_gap_count,
      'coverage_complete', v_complete,
      'gaps', v_gaps,
      'facts', v_facts
    )::text, 'UTF8'), 'sha256'), 'hex');

  return jsonb_build_object(
    'schema_version', 'doctorcre-v5-a02-rule-enforcement-coverage.v1',
    'observed_at', v_observed_at,
    'active_rule_count', v_active,
    'covered_rule_count', v_covered,
    'gap_count', v_gap_count,
    'coverage_complete', v_complete,
    'gaps', v_gaps,
    'evidence_digest', v_evidence_digest
  );
end
$fn$;

comment on function ops.v5_a02_rule_enforcement_coverage() is
  'Universal V5-A02 read: every active rule must have one current exact '
  'approval-bound installed control, verified test evidence, and an explicit '
  'Joe-authority fallback receipt. Missing evidence is a named gap, never green.';

revoke all on table ops.rule_enforcement_fallback_receipt
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
revoke all on function ops.record_rule_enforcement_fallback(uuid,text,text,text,text)
  from public, carr_reader, carr_writer, carr_jobs;
revoke all on function ops.v5_a02_rule_enforcement_coverage()
  from public, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.record_rule_enforcement_fallback(uuid,text,text,text,text)
  to carr_authority;
grant execute on function ops.v5_a02_rule_enforcement_coverage() to carr_reader;

do $v5_a02_0712$
begin
  if to_regclass('ops.rule_enforcement_fallback_receipt') is null
     or to_regprocedure('ops.record_rule_enforcement_fallback(uuid,text,text,text,text)') is null
     or to_regprocedure('ops.v5_a02_rule_enforcement_coverage()') is null then
    raise exception '0712 FAILED: V5-A02 rule coverage contract is incomplete';
  end if;
  if has_function_privilege('carr_writer',
       'ops.record_rule_enforcement_fallback(uuid,text,text,text,text)'::regprocedure,
       'execute')
     or not has_function_privilege('carr_authority',
       'ops.record_rule_enforcement_fallback(uuid,text,text,text,text)'::regprocedure,
       'execute')
     or not has_function_privilege('carr_reader',
       'ops.v5_a02_rule_enforcement_coverage()'::regprocedure, 'execute') then
    raise exception '0712 FAILED: V5-A02 grants are not closed';
  end if;
  if not exists (
    select 1 from pg_trigger
     where tgrelid = 'ops.rule_enforcement_fallback_receipt'::regclass
       and tgname = 'rule_enforcement_fallback_receipt_append_only'
       and not tgisinternal
  ) then
    raise exception '0712 FAILED: fallback receipt immutability trigger missing';
  end if;
end
$v5_a02_0712$;
