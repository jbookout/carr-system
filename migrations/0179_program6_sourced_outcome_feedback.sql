-- Program 6: append outcome-feedback proposals for an accepted sourced ready
-- plan, then require an authority-authenticated human to accept one.
-- Neither half executes, dispatches, assigns, approves execution, or closes
-- work.  The Work Request deliberately remains in `ready` throughout.

begin;

create table ops.sourced_work_request_outcome_feedback (
  id                          uuid primary key default gen_random_uuid(),
  work_request_id             uuid not null references ops.work_request(id),
  feedback_version            integer not null check (feedback_version > 0),
  idempotency_key             uuid not null unique,
  work_request_version        integer not null check (work_request_version > 0),
  plan_id                     uuid not null references ops.sourced_work_request_plan(id),
  plan_acceptance_receipt_id  uuid not null references ops.sourced_work_request_plan_acceptance_receipt(id),
  preimage                    jsonb not null check (jsonb_typeof(preimage) = 'object'),
  criterion_results           jsonb not null check (jsonb_typeof(criterion_results) = 'array'),
  evidence_refs               jsonb not null check (jsonb_typeof(evidence_refs) = 'array'),
  outcome                     text not null check (outcome in ('criteria_met','criteria_not_met','inconclusive')),
  blocker_code                text not null check (blocker_code in ('none','evidence_missing','criterion_not_met','external_dependency','system_error')),
  result_summary              text not null check (btrim(result_summary) <> '' and char_length(result_summary) <= 500),
  observed_minutes            integer not null check (observed_minutes between 1 and 1440),
  interaction_surface         text not null check (interaction_surface in ('workspace','control_room','mcp','codex','claude_code','other')),
  heavy_session_used          boolean not null,
  manual_context_transfers    integer not null check (manual_context_transfers between 0 and 100),
  feedback_hash               text not null check (feedback_hash ~ '^sha256:[0-9a-f]{64}$'),
  feedback_ref                text not null unique check (feedback_ref ~ '^OUTCOME-[0-9a-f]{12}-v[1-9][0-9]*$'),
  created_at                  timestamptz not null default now(),
  unique (work_request_id, feedback_version),
  unique (work_request_id, feedback_hash)
);

create table ops.sourced_work_request_outcome_feedback_acceptance_receipt (
  id                    uuid primary key default gen_random_uuid(),
  work_request_id       uuid not null references ops.work_request(id),
  feedback_id           uuid not null unique references ops.sourced_work_request_outcome_feedback(id),
  idempotency_key       uuid not null unique,
  base_version          integer not null check (base_version > 0),
  feedback_hash         text not null check (feedback_hash ~ '^sha256:[0-9a-f]{64}$'),
  accepted_by_actor_id  uuid not null references public.actor(id),
  -- `now()` is transaction-stable, so two accepted trials in one transaction
  -- would share a timestamp and make chronological history depend on a random
  -- UUID tie-breaker.  Acceptance time is an observed event; wall-clock time
  -- keeps A then B ordering deterministic in the same transaction as well.
  accepted_at           timestamptz not null default clock_timestamp(),
  result_version        integer not null check (result_version > 0),
  unique (work_request_id, feedback_hash)
);

comment on table ops.sourced_work_request_outcome_feedback is
  'Append-only bounded Program 6 outcome-feedback proposal. It binds every criterion result, evidence reference, blocker and measured operating fact to one accepted ready plan; it is not a claim of execution.';
comment on table ops.sourced_work_request_outcome_feedback_acceptance_receipt is
  'Private append-only human-authority receipt for an outcome-feedback proposal. Independence is the routine-writer proposal versus human acceptance; acceptance leaves the Work Request ready and grants no execution authority.';

create or replace function ops.sourced_work_outcome_feedback_rows_immutable()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception 'Program 6 sourced outcome feedback rows are append-only';
end;
$$;

create trigger sourced_work_request_outcome_feedback_immutable
before update or delete on ops.sourced_work_request_outcome_feedback
for each row execute function ops.sourced_work_outcome_feedback_rows_immutable();
create trigger sourced_work_request_outcome_feedback_acceptance_immutable
before update or delete on ops.sourced_work_request_outcome_feedback_acceptance_receipt
for each row execute function ops.sourced_work_outcome_feedback_rows_immutable();

create or replace function ops.sourced_work_request_outcome_feedback_preimage(
  p_work_request_id uuid, p_plan_id uuid, p_plan_acceptance_receipt_id uuid,
  p_criterion_results jsonb, p_evidence_refs jsonb, p_outcome text,
  p_blocker_code text, p_result_summary text, p_observed_minutes integer,
  p_interaction_surface text, p_heavy_session_used boolean,
  p_manual_context_transfers integer
)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select jsonb_build_object(
    'contract', 'carr-sourced-outcome-feedback/v2',
    'work_request', jsonb_build_object(
      'id', w.id, 'ref', w.ref, 'state', w.state, 'version', w.version,
      'title', w.title, 'desired_outcome', w.desired_outcome,
      'acceptance_criteria', w.acceptance_criteria, 'origin_ref', w.origin_ref,
      'doctrine_section_id', w.doctrine_section_id,
      'doctrine_revision_id', w.doctrine_revision_id,
      'triage_classification', w.triage_classification,
      'triaged_by_actor_id', w.triaged_by_actor_id, 'triaged_at', w.triaged_at,
      'shape_fixed_surface_ref', w.shape_fixed_surface_ref
    ),
    'accepted_plan', jsonb_build_object(
      'id', p.id, 'ref', p.plan_ref, 'hash', p.plan_hash,
      'work_request_version', p.work_request_version, 'runbook_ref', p.runbook_ref,
      'runbook_section_id', p.runbook_section_id, 'runbook_revision_id', p.runbook_revision_id,
      'runbook_content_hash', 'sha256:' || p.runbook_content_hash,
      'scope_summary', p.scope_summary, 'dependency_refs', p.dependency_refs,
      'recovery_ref', p.recovery_ref, 'observability_ref', p.observability_ref,
      'caps', p.caps
    ),
    'acceptance', jsonb_build_object(
      'receipt_id', ar.id, 'base_version', ar.base_version,
      'result_version', ar.result_version, 'plan_hash', ar.plan_hash,
      'accepted_by_actor_id', ar.accepted_by_actor_id, 'accepted_at', ar.accepted_at,
      'shape_fixed_surface_ref', ar.shape_fixed_surface_ref
    ),
    'feedback', jsonb_build_object(
      'criterion_results', p_criterion_results, 'evidence_refs', p_evidence_refs,
      'outcome', p_outcome, 'blocker_code', p_blocker_code,
      'result_summary', p_result_summary, 'observed_minutes', p_observed_minutes,
      'interaction_surface', p_interaction_surface, 'heavy_session_used', p_heavy_session_used,
      'manual_context_transfers', p_manual_context_transfers
    )
  )
    from ops.work_request w
    join ops.sourced_work_request_plan p on p.work_request_id=w.id and p.id=p_plan_id
    join ops.sourced_work_request_plan_acceptance_receipt ar
      on ar.work_request_id=w.id and ar.plan_id=p.id and ar.id=p_plan_acceptance_receipt_id
   where w.id=p_work_request_id;
$$;

create or replace function ops.sourced_work_request_outcome_feedback_digest(p_preimage jsonb)
returns text language sql immutable security definer
set search_path = pg_catalog, ops
as $$
  select 'sha256:' || encode(public.digest(p_preimage::text, 'sha256'), 'hex');
$$;

revoke all on function ops.sourced_work_request_outcome_feedback_preimage(uuid,uuid,uuid,jsonb,jsonb,text,text,text,integer,text,boolean,integer) from public;
revoke all on function ops.sourced_work_request_outcome_feedback_digest(jsonb) from public;

create or replace function ops.propose_sourced_work_request_outcome_feedback(
  p_work_request text, p_base_version integer, p_plan_hash text,
  p_criterion_results jsonb, p_evidence_refs jsonb, p_blocker_code text,
  p_result_summary text, p_observed_minutes integer, p_interaction_surface text,
  p_heavy_session_used boolean, p_manual_context_transfers integer,
  p_idempotency_key uuid
)
returns table (
  feedback_id uuid, feedback_ref text, feedback_hash text, work_request_id uuid,
  ref text, state text, version integer, plan_id uuid, plan_ref text,
  plan_hash text,
  outcome text, criterion_results jsonb, evidence_refs jsonb, blocker_code text,
  result_summary text, observed_minutes integer, interaction_surface text,
  heavy_session_used boolean, manual_context_transfers integer, replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  w ops.work_request%rowtype; p ops.sourced_work_request_plan%rowtype;
  ar ops.sourced_work_request_plan_acceptance_receipt%rowtype;
  f ops.sourced_work_request_outcome_feedback%rowtype;
  normalized_summary text; derived_outcome text; next_feedback_version integer;
  canonical_preimage jsonb; canonical_hash text;
begin
  normalized_summary := btrim(p_result_summary);
  if coalesce(btrim(p_work_request),'') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1
     or coalesce(p_plan_hash,'') !~ '^sha256:[0-9a-f]{64}$'
     or p_idempotency_key is null
     or coalesce(normalized_summary,'')='' or char_length(normalized_summary)>500
     or p_observed_minutes is null or p_observed_minutes not between 1 and 1440
     or p_interaction_surface not in ('workspace','control_room','mcp','codex','claude_code','other')
     or p_heavy_session_used is null
     or p_manual_context_transfers is null or p_manual_context_transfers not between 0 and 100
     or p_blocker_code not in ('none','evidence_missing','criterion_not_met','external_dependency','system_error')
     or jsonb_typeof(p_criterion_results) is distinct from 'array'
     or jsonb_typeof(p_evidence_refs) is distinct from 'array'
     or jsonb_array_length(p_evidence_refs) not between 1 and 12
     or exists (select 1 from jsonb_array_elements(p_evidence_refs) v
                 where jsonb_typeof(v) <> 'string'
                    or v #>> '{}' !~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
                    or char_length(v #>> '{}') > 300)
     or exists (select v #>> '{}' from jsonb_array_elements(p_evidence_refs) v
                 group by v #>> '{}' having count(*) > 1)
     or exists (select 1 from jsonb_array_elements(p_criterion_results) v
                 where jsonb_typeof(v) <> 'object'
                    or (select array_agg(k order by k) from jsonb_object_keys(v) k)
                         is distinct from array['id','result']::text[]
                    or coalesce(v->>'id','') !~ '^[A-Z][A-Z0-9-]{1,63}$'
                    or v->>'result' not in ('met','not_met','not_observed'))
     or exists (select v->>'id' from jsonb_array_elements(p_criterion_results) v
                 group by v->>'id' having count(*) > 1) then
    raise exception 'outcome feedback requires exact ready plan/version, full criterion results, safe evidence references, bounded observed facts, blocker, summary, and UUID idempotency key';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-outcome-feedback-proposal:' || p_idempotency_key,0));
  select x.* into f from ops.sourced_work_request_outcome_feedback x
   where x.idempotency_key=p_idempotency_key for share;
  if found then
    select x.* into w from ops.work_request x where x.id=f.work_request_id for share;
    select x.* into p from ops.sourced_work_request_plan x where x.id=f.plan_id for share;
    if not found or w.ref is distinct from p_work_request
       or f.work_request_version is distinct from p_base_version
       or p.plan_hash is distinct from p_plan_hash or f.criterion_results is distinct from p_criterion_results
       or f.evidence_refs is distinct from p_evidence_refs or f.blocker_code is distinct from p_blocker_code
       or f.result_summary is distinct from normalized_summary or f.observed_minutes is distinct from p_observed_minutes
       or f.interaction_surface is distinct from p_interaction_surface or f.heavy_session_used is distinct from p_heavy_session_used
       or f.manual_context_transfers is distinct from p_manual_context_transfers then
      raise exception 'idempotency key already names different sourced outcome feedback proposal';
    end if;
    return query select f.id,f.feedback_ref,f.feedback_hash,w.id,w.ref,'ready'::text,f.work_request_version,
      p.id,p.plan_ref,p.plan_hash,f.outcome,f.criterion_results,f.evidence_refs,f.blocker_code,
      f.result_summary,f.observed_minutes,f.interaction_surface,f.heavy_session_used,
      f.manual_context_transfers,true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref=p_work_request for update;
  if not found then raise exception 'exact sourced Work Request not found'; end if;
  select x.* into p from ops.sourced_work_request_plan x
   join ops.sourced_work_request_plan_acceptance_receipt ar0 on ar0.plan_id=x.id
   where x.work_request_id=w.id and x.plan_hash=p_plan_hash for share of x;
  select x.* into ar from ops.sourced_work_request_plan_acceptance_receipt x
   where x.work_request_id=w.id and x.plan_id=p.id for share;
  if not found or w.capture_idempotency_key is null
     or w.organization_tenant_id is distinct from 'carr-internal'
     or w.state is distinct from 'ready' or w.version is distinct from p_base_version
     or p.work_request_version >= w.version or ar.result_version is distinct from w.version
     or ar.plan_hash is distinct from p.plan_hash
     or w.shape_fixed_surface_ref is distinct from ar.shape_fixed_surface_ref then
    raise exception 'only an exact current ready sourced plan may receive outcome feedback';
  end if;
  if (select array_agg(v->>'id' order by v->>'id') from jsonb_array_elements(p_criterion_results) v)
       is distinct from
     (select array_agg(v->>'id' order by v->>'id') from jsonb_array_elements(w.acceptance_criteria) v) then
    raise exception 'outcome feedback criterion results must name the exact full Work Request acceptance-criteria ID set';
  end if;
  if exists (select 1 from jsonb_array_elements(p_criterion_results) v where v->>'result'='not_met') then
    derived_outcome := 'criteria_not_met';
  elsif exists (select 1 from jsonb_array_elements(p_criterion_results) v where v->>'result'='not_observed') then
    derived_outcome := 'inconclusive';
  else
    derived_outcome := 'criteria_met';
  end if;
  if (derived_outcome='criteria_met' and p_blocker_code <> 'none')
     or (derived_outcome='criteria_not_met' and p_blocker_code='none')
     or (derived_outcome='inconclusive' and p_blocker_code not in ('evidence_missing','external_dependency','system_error')) then
    raise exception 'outcome feedback blocker is inconsistent with its derived criterion outcome';
  end if;

  select coalesce(max(x.feedback_version),0)+1 into next_feedback_version
    from ops.sourced_work_request_outcome_feedback x where x.work_request_id=w.id;
  canonical_preimage := ops.sourced_work_request_outcome_feedback_preimage(
    w.id,p.id,ar.id,p_criterion_results,p_evidence_refs,derived_outcome,p_blocker_code,
    normalized_summary,p_observed_minutes,p_interaction_surface,p_heavy_session_used,
    p_manual_context_transfers);
  canonical_hash := ops.sourced_work_request_outcome_feedback_digest(canonical_preimage);
  if canonical_preimage is null or canonical_hash is null then
    raise exception 'exact ready-plan outcome-feedback preimage is unavailable';
  end if;
  if exists (select 1 from ops.sourced_work_request_outcome_feedback x
              where x.work_request_id=w.id and x.feedback_hash=canonical_hash) then
    raise exception 'the exact sourced outcome feedback already exists under a different idempotency key';
  end if;
  insert into ops.sourced_work_request_outcome_feedback
    (work_request_id,feedback_version,idempotency_key,work_request_version,plan_id,
     plan_acceptance_receipt_id,preimage,criterion_results,evidence_refs,outcome,
     blocker_code,result_summary,observed_minutes,interaction_surface,heavy_session_used,
     manual_context_transfers,feedback_hash,feedback_ref)
  values
    (w.id,next_feedback_version,p_idempotency_key,w.version,p.id,ar.id,canonical_preimage,
     p_criterion_results,p_evidence_refs,derived_outcome,p_blocker_code,normalized_summary,
     p_observed_minutes,p_interaction_surface,p_heavy_session_used,p_manual_context_transfers,
     canonical_hash,'OUTCOME-' || substr(canonical_hash,8,12) || '-v' || next_feedback_version)
  returning * into f;
  return query select f.id,f.feedback_ref,f.feedback_hash,w.id,w.ref,w.state,w.version,
    p.id,p.plan_ref,p.plan_hash,f.outcome,f.criterion_results,f.evidence_refs,f.blocker_code,
    f.result_summary,f.observed_minutes,f.interaction_surface,f.heavy_session_used,
    f.manual_context_transfers,false;
end;
$$;

create or replace function ops.accept_sourced_work_request_outcome_feedback(
  p_work_request text, p_base_version integer, p_feedback_hash text,
  p_idempotency_key uuid
)
returns table (
  work_request_id uuid, ref text, state text, version integer,
  feedback_id uuid, feedback_ref text, feedback_hash text, plan_ref text, plan_hash text,
  outcome text, criterion_results jsonb, evidence_refs jsonb, blocker_code text,
  result_summary text, observed_minutes integer, interaction_surface text,
  heavy_session_used boolean, manual_context_transfers integer,
  accepted_by_actor_slug text, accepted_at timestamptz, replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  actor_slug text; a public.actor%rowtype; w ops.work_request%rowtype;
  p ops.sourced_work_request_plan%rowtype;
  ar ops.sourced_work_request_plan_acceptance_receipt%rowtype;
  f ops.sourced_work_request_outcome_feedback%rowtype;
  fr ops.sourced_work_request_outcome_feedback_acceptance_receipt%rowtype;
  canonical_preimage jsonb; canonical_hash text;
begin
  if coalesce(btrim(p_work_request),'') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1
     or coalesce(p_feedback_hash,'') !~ '^sha256:[0-9a-f]{64}$'
     or p_idempotency_key is null then
    raise exception 'independent outcome-feedback acceptance requires exact ready Work Request/version, feedback hash, and UUID idempotency key';
  end if;
  actor_slug := ops.authority_actor_slug();
  select x.* into a from public.actor x where x.slug=actor_slug and x.active and x.kind='human' for share;
  if not found then raise exception 'authority session user is not an active human actor'; end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-outcome-feedback-acceptance:' || p_idempotency_key,0));
  select x.* into fr from ops.sourced_work_request_outcome_feedback_acceptance_receipt x
   where x.idempotency_key=p_idempotency_key for share;
  if found then
    select x.* into f from ops.sourced_work_request_outcome_feedback x where x.id=fr.feedback_id for share;
    select x.* into w from ops.work_request x where x.id=fr.work_request_id for share;
    select x.* into p from ops.sourced_work_request_plan x where x.id=f.plan_id for share;
    if not found or w.ref is distinct from p_work_request
       or fr.base_version is distinct from p_base_version
       or fr.result_version is distinct from p_base_version or f.feedback_hash is distinct from p_feedback_hash
       or fr.feedback_hash is distinct from p_feedback_hash or fr.accepted_by_actor_id is distinct from a.id then
      raise exception 'idempotency key already names different sourced outcome feedback acceptance';
    end if;
    return query select w.id,w.ref,'ready'::text,fr.result_version,f.id,f.feedback_ref,f.feedback_hash,
      p.plan_ref,p.plan_hash,f.outcome,f.criterion_results,f.evidence_refs,f.blocker_code,f.result_summary,
      f.observed_minutes,f.interaction_surface,f.heavy_session_used,f.manual_context_transfers,
      a.slug,fr.accepted_at,true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref=p_work_request for update;
  if not found then raise exception 'exact sourced Work Request not found'; end if;
  select x.* into f from ops.sourced_work_request_outcome_feedback x
   where x.work_request_id=w.id and x.feedback_hash=p_feedback_hash for share;
  if not found then raise exception 'exact sourced outcome feedback proposal not found'; end if;
  select x.* into p from ops.sourced_work_request_plan x where x.id=f.plan_id for share;
  select x.* into ar from ops.sourced_work_request_plan_acceptance_receipt x
   where x.id=f.plan_acceptance_receipt_id for share;
  if not found or w.capture_idempotency_key is null
     or w.organization_tenant_id is distinct from 'carr-internal'
     or w.state is distinct from 'ready' or w.version is distinct from p_base_version
     or f.work_request_version is distinct from p_base_version
     or ar.work_request_id is distinct from w.id or ar.plan_id is distinct from p.id
     or ar.result_version is distinct from w.version or ar.plan_hash is distinct from p.plan_hash
     or ar.accepted_by_actor_id is null
     or w.shape_fixed_surface_ref is distinct from ar.shape_fixed_surface_ref then
    raise exception 'only an exact current ready sourced plan may receive human outcome-feedback acceptance';
  end if;
  canonical_preimage := ops.sourced_work_request_outcome_feedback_preimage(
    w.id,p.id,ar.id,f.criterion_results,f.evidence_refs,f.outcome,f.blocker_code,
    f.result_summary,f.observed_minutes,f.interaction_surface,f.heavy_session_used,
    f.manual_context_transfers);
  canonical_hash := ops.sourced_work_request_outcome_feedback_digest(canonical_preimage);
  if canonical_preimage is distinct from f.preimage
     or canonical_hash is distinct from f.feedback_hash
     or canonical_hash is distinct from p_feedback_hash then
    raise exception 'sourced outcome-feedback preimage is stale or does not match its exact hash';
  end if;
  if exists (select 1 from ops.sourced_work_request_outcome_feedback_acceptance_receipt x where x.feedback_id=f.id) then
    raise exception 'the sourced outcome-feedback proposal already has an immutable acceptance receipt';
  end if;
  insert into ops.sourced_work_request_outcome_feedback_acceptance_receipt
    (work_request_id,feedback_id,idempotency_key,base_version,feedback_hash,
     accepted_by_actor_id,result_version)
  values (w.id,f.id,p_idempotency_key,p_base_version,p_feedback_hash,a.id,w.version)
  returning * into fr;
  -- No Work Request UPDATE: this accepts a measurement, not an execution result.
  return query select w.id,w.ref,w.state,w.version,f.id,f.feedback_ref,f.feedback_hash,
    p.plan_ref,p.plan_hash,f.outcome,f.criterion_results,f.evidence_refs,f.blocker_code,f.result_summary,
    f.observed_minutes,f.interaction_surface,f.heavy_session_used,f.manual_context_transfers,
    a.slug,fr.accepted_at,false;
end;
$$;

drop function ops.work_request_card(text,text);
create or replace function ops.work_request_card(
  p_work_request text, p_organization_tenant_id text
)
returns table (
  ref text, title text, state text, version integer, origin_ref text,
  desired_outcome text, acceptance_criteria jsonb, doctrine_section_id uuid,
  doctrine_revision_id uuid, doctrine_source_label text, source_current boolean,
  triage_classification text, triaged_by_actor_slug text, triaged_at timestamptz,
  plan_ref text, plan_hash text, scope_summary text, runbook_ref text, runbook_revision_id uuid,
  runbook_content_hash text, plan_caps jsonb, dependency_refs jsonb,
  recovery_ref text, observability_ref text, accepted_by_actor_slug text,
  accepted_at timestamptz, shape_disposition text, shape_fixed_surface_ref text,
  outcome_feedback jsonb, outcome_feedback_history jsonb, accepted_feedback_count bigint
)
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select w.ref,w.title,w.state,w.version,w.origin_ref,w.desired_outcome,
         w.acceptance_criteria,w.doctrine_section_id,w.doctrine_revision_id,
         coalesce(s.title,s.section_key),
         (s.status='active' and s.current_revision_id=w.doctrine_revision_id),
         w.triage_classification,ta.slug,w.triaged_at,
         p.plan_ref,p.plan_hash,p.scope_summary,p.runbook_ref,p.runbook_revision_id,
         case when p.runbook_content_hash is null then null else 'sha256:' || p.runbook_content_hash end,
         p.caps,p.dependency_refs,p.recovery_ref,p.observability_ref,aa.slug,ar.accepted_at,
         w.shape_disposition,w.shape_fixed_surface_ref,
         latest.feedback,coalesce(history.feedback_history,'[]'::jsonb),coalesce(counted.accepted_count,0)
    from ops.work_request w
    join public.doctrine_section s on s.id=w.doctrine_section_id
    join public.doctrine_document d on d.id=s.document_id
    left join public.actor ta on ta.id=w.triaged_by_actor_id
    left join lateral (
      select x.* from ops.sourced_work_request_plan x
      left join ops.sourced_work_request_plan_acceptance_receipt accepted on accepted.plan_id=x.id
       where x.work_request_id=w.id
       order by (accepted.id is not null) desc,x.plan_version desc limit 1
    ) p on true
    left join ops.sourced_work_request_plan_acceptance_receipt ar on ar.plan_id=p.id
    left join public.actor aa on aa.id=ar.accepted_by_actor_id
    left join lateral (
      select jsonb_build_object(
        'feedback_ref',f.feedback_ref,'feedback_hash',f.feedback_hash,'outcome',f.outcome,
        'criterion_results',f.criterion_results,'evidence_refs',f.evidence_refs,
        'blocker_code',f.blocker_code,'result_summary',f.result_summary,
        'observed_minutes',f.observed_minutes,'interaction_surface',f.interaction_surface,
        'heavy_session_used',f.heavy_session_used,'manual_context_transfers',f.manual_context_transfers,
        'accepted_by_actor_slug',fa.slug,'accepted_at',fr.accepted_at) as feedback
        from ops.sourced_work_request_outcome_feedback f
        join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
        join public.actor fa on fa.id=fr.accepted_by_actor_id
       where f.work_request_id=w.id
       order by fr.accepted_at desc,fr.id desc limit 1
    ) latest on true
    left join lateral (
      select jsonb_agg(h.feedback order by h.accepted_at,h.acceptance_id) as feedback_history
        from (
          select fr.accepted_at,fr.id as acceptance_id,jsonb_build_object(
            'feedback_ref',f.feedback_ref,'feedback_hash',f.feedback_hash,'outcome',f.outcome,
            'criterion_results',f.criterion_results,'evidence_refs',f.evidence_refs,
            'blocker_code',f.blocker_code,'result_summary',f.result_summary,
            'observed_minutes',f.observed_minutes,'interaction_surface',f.interaction_surface,
            'heavy_session_used',f.heavy_session_used,'manual_context_transfers',f.manual_context_transfers,
            'accepted_by_actor_slug',fa.slug,'accepted_at',fr.accepted_at) as feedback
            from ops.sourced_work_request_outcome_feedback f
            join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
            join public.actor fa on fa.id=fr.accepted_by_actor_id
           where f.work_request_id=w.id
           order by fr.accepted_at desc,fr.id desc limit 20
        ) h
    ) history on true
    left join lateral (
      select count(*)::bigint as accepted_count
        from ops.sourced_work_request_outcome_feedback f
        join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
       where f.work_request_id=w.id
    ) counted on true
   where p_organization_tenant_id='carr-internal'
     and w.organization_tenant_id='carr-internal' and w.ref=p_work_request
     and w.state in ('captured','triaged','ready') and d.visibility='shared';
$$;

revoke all on table ops.sourced_work_request_outcome_feedback,
  ops.sourced_work_request_outcome_feedback_acceptance_receipt
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.propose_sourced_work_request_outcome_feedback(text,integer,text,jsonb,jsonb,text,text,integer,text,boolean,integer,uuid)
  from public,carr_reader,carr_jobs,carr_authority;
revoke all on function ops.accept_sourced_work_request_outcome_feedback(text,integer,text,uuid)
  from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.propose_sourced_work_request_outcome_feedback(text,integer,text,jsonb,jsonb,text,text,integer,text,boolean,integer,uuid)
  to carr_writer;
grant execute on function ops.accept_sourced_work_request_outcome_feedback(text,integer,text,uuid)
  to carr_authority;
revoke all on function ops.work_request_card(text,text) from public;
grant execute on function ops.work_request_card(text,text) to carr_reader,carr_writer;

commit;
