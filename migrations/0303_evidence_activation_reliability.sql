-- 0303: plan-bound knowledge activation and reliability facts.
--
-- These are additive projections of the existing Work Request -> accepted plan
-- -> ExecutionEnvelope -> AttemptReceipt path.  They are not a second receipt,
-- workflow, memory, or authority store.  Bodies are intentionally absent: the
-- canonical ContextBundle contains only refs/revisions/digests.
--
-- Rollback posture: migrations are forward-only.  If this slice must be
-- withdrawn, feature mode disables the MCP/browser consumers and authority
-- admission door; append-only metadata stays intact for audit and automatic
-- telemetry clear/remedy verification.  No destructive down migration is
-- permitted for already admitted Work Request evidence.

-- Numbered after the release provenance migrations already on origin/main.
begin;

-- One pure, stable bundle-body compiler is shared by plan proposal,
-- acceptance recomputation, post-accept rendering, and activation.  The
-- hashed body contains no clock or final-plan value; issued_at is derived
-- from the durable Work Request timestamp.
create or replace function ops.context_activation_bundle_body(
  p_tenant text, p_work_request_ref text, p_plan_revision_ref text,
  p_plan_revision integer, p_base_plan_digest text, p_issued_at timestamptz,
  p_item_ref text, p_revision_ref text, p_item_digest text
) returns jsonb language sql immutable strict security definer
set search_path=pg_catalog,ops,public as $$
  select jsonb_build_object(
    'schema_version','context-bundle.v1',
    'header',jsonb_build_object(
      'tenant_id',p_tenant,
      'work_request_id',p_work_request_ref,
      'accepted_plan_revision_id',p_plan_revision_ref,
      'accepted_plan_revision',p_plan_revision,
      'accepted_plan_digest',p_base_plan_digest,
      'issued_at',to_char(p_issued_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
      'mode','shadow',
      'retrieval_policy','policy:bounded-doctrine-v1',
      'retrieval_policy_version','v1','compiler_id','compiler:context-activation-v1',
      'compiler_version','v1','compiler_digest','sha256:'||encode(digest('compiler:context-activation-v1','sha256'),'hex'),
      'query_basis_digest','sha256:'||encode(digest(p_tenant||':'||p_work_request_ref||':'||p_plan_revision_ref,'sha256'),'hex'),
      'grounding_plan',jsonb_build_object('inline_budget',64,'retrieval_policy','bounded-doctrine','cache_segment','plan-bound','modalities',jsonb_build_array('metadata_only'),'freshness_sla','accepted-plan-bound')
    ),
    'items',jsonb_build_array(jsonb_build_object(
      'kind','doctrine',
      'canonical_ref',p_item_ref,
      'revision',p_revision_ref,
      'digest',p_item_digest,
      'required',true,
      'trigger','work-request-admission',
      'consumer','hermes-profile-brief',
      'enforcement','must-apply',
      'redaction_class','metadata_only','artifact_kind','doctrine','scope_redaction','metadata_only',
      'trigger_ref','work-request-admission','consumer_ref','hermes-profile-brief','delivery_mode','inline','representation_kind','doctrine','freshness_sla','accepted-plan-bound','selection_reason','canonical-doctrine-binding','selection_rank',0,'requirement_class','required',
      'freshness','fresh'
    ))
  );
$$;
revoke all on function ops.context_activation_bundle_body(text,text,text,integer,text,timestamptz,text,text,text) from public;

-- The server may attach issuance/expiry/binding metadata after compilation,
-- but it must never change the frozen compiler digest.  Reuse the existing
-- portable compact lexical JSON compiler so Postgres, Python, and browser
-- render exactly the same digest preimage.
create or replace function ops.context_activation_bundle_digest(p_bundle jsonb)
returns text language sql immutable strict security definer
set search_path=pg_catalog,public as $$
  select 'sha256:' || encode(digest(ops.guidance_import_canonical_json(
    jsonb_build_object(
      'schema_version', p_bundle->'schema_version',
      'header', coalesce(p_bundle->'header','{}'::jsonb)
                  - 'issued_at' - 'expires_at' - 'binding_id',
      'items', p_bundle->'items'
    )), 'sha256'), 'hex')
$$;
revoke all on function ops.context_activation_bundle_digest(jsonb) from public;

-- This is the sole retrieval compiler.  It only emits canonical metadata:
-- never bodies, prompts, transcript text, or caller-selected flags.  Required
-- standing rules come from the existing delivery compiler (Layer 0); promoted
-- shared memory comes from the existing memory kernel and remains advisory.
create or replace function ops.context_activation_compiler_items(
  p_work_request_id uuid, p_tenant text, p_dependency_refs jsonb
) returns jsonb language plpgsql stable security definer
set search_path=pg_catalog,public,ops as $$
declare items jsonb;
begin
  select jsonb_agg(item order by ordinal) into items from (
    select 0 as ordinal, jsonb_build_object(
      'kind','doctrine', 'canonical_ref','doctrine:section:'||w.doctrine_section_id::text,
      'revision','revision:'||w.doctrine_revision_id::text,
      'digest','sha256:'||dr.content_hash, 'required',true,
      'trigger','work-request-admission', 'consumer','hermes-profile-brief',
      'enforcement','must-apply', 'redaction_class','metadata_only', 'freshness','fresh'
    ) as item
      from ops.work_request w
      join public.doctrine_revision dr on dr.id=w.doctrine_revision_id and dr.section_id=w.doctrine_section_id
     where w.id=p_work_request_id and w.organization_tenant_id=p_tenant
    union all
    select 100000 + row_number() over (order by r.id), jsonb_build_object(
      'kind','rule', 'canonical_ref','rule:'||r.id::text,
      'revision','revision:'||r.id::text,
      'digest','sha256:'||encode(public.digest(r.statement,'sha256'),'hex'), 'required',true,
      'trigger','standing-rule-delivery', 'consumer','execution-envelope',
      'enforcement',r.enforcement, 'redaction_class','metadata_only', 'freshness','fresh'
    )
      from ops.rule_delivery_plan(null, '{}'::text[]) delivery
      join public.rule r on r.id=delivery.rule_id
     where delivery.selected and delivery.scope='shared'
    union all
    select 150000 + row_number() over (order by d.decision_id), jsonb_build_object(
      'kind','decision', 'canonical_ref','decision:'||d.decision_id::text,
      'revision','revision:'||d.event_id::text,
      'digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(jsonb_build_object(
        'title',d.title, 'human_quote',d.human_quote, 'agent_rationale',d.agent_rationale,
        'provenance',d.provenance, 'entry_date',d.entry_date
      )),'sha256'),'hex'), 'required',true,
      'trigger','accepted-plan-dependency', 'consumer','execution-envelope',
      'enforcement','must-apply', 'redaction_class','metadata_only', 'freshness','fresh'
    )
      from public.v_decision_entry d
     where coalesce(p_dependency_refs,'[]'::jsonb) ? ('safe:decision:'||d.decision_id::text)
    union all
    select 200000 + row_number() over (order by m.id), jsonb_build_object(
      'kind','memory', 'canonical_ref','memory:'||m.id::text,
      'revision','revision:'||m.id::text||':v'||m.version::text,
      'digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(jsonb_build_object(
        'id',m.id,'version',m.version,'kind',m.kind,'statement',m.statement,'context',m.context,
        'scope',m.scope,'organization_tenant_id',m.organization_tenant_id,'work_request_id',m.work_request_id,
        'work_request_version',m.work_request_version,'plan_id',m.plan_id,'status',m.status,'confidence',m.confidence
      )),'sha256'),'hex'), 'required',false,
      'trigger','promoted-memory-retrieval', 'consumer','execution-envelope',
      'enforcement','advisory-only', 'redaction_class','metadata_only', 'freshness','fresh',
      'selection_reason',case when m.work_request_id=p_work_request_id then 'work-request-anchor'
        when coalesce(p_dependency_refs,'[]'::jsonb) ? ('safe:memory:'||m.id::text) then 'accepted-plan-dependency'
        else 'accepted-work-request-fts' end,
      'selection_rank',row_number() over (order by (m.work_request_id=p_work_request_id) desc,
        (coalesce(p_dependency_refs,'[]'::jsonb) ? ('safe:memory:'||m.id::text)) desc,
        m.confidence desc,m.id)
    )
      from (
        select m.* from public.memory_item m
        join ops.work_request candidate_work on candidate_work.id=p_work_request_id
         where m.organization_tenant_id=p_tenant and m.status='promoted' and m.scope='shared'
           and (
             m.work_request_id=candidate_work.id
             or coalesce(p_dependency_refs,'[]'::jsonb) ? ('safe:memory:'||m.id::text)
             or m.search_vector @@ websearch_to_tsquery('english', candidate_work.title || ' ' || candidate_work.desired_outcome)
           )
         order by (m.work_request_id=candidate_work.id) desc,
                  (coalesce(p_dependency_refs,'[]'::jsonb) ? ('safe:memory:'||m.id::text)) desc,
                  ts_rank(m.search_vector,websearch_to_tsquery('english', candidate_work.title || ' ' || candidate_work.desired_outcome)) desc,
                  m.confidence desc,m.id
         limit 16
      ) m
    union all
    select 250000 + row_number() over (order by d.occurred_on desc,d.id), jsonb_build_object(
      'kind','prior_failure','canonical_ref','defect:'||d.id::text,
      'revision','occurred:'||d.occurred_on::text,
      'digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(jsonb_build_object(
        'id',d.id,'occurred_on',d.occurred_on,'defect_class',d.defect_class,'claimed',d.claimed,
        'actual',d.actual,'source_unread',d.source_unread,'rule_violated',d.rule_violated,
        'detected_by',d.detected_by,'session_key',d.session_key,'cost_note',d.cost_note,'created_at',d.created_at,'created_by',d.created_by
      )),'sha256'),'hex'),
      'required',false,'trigger','accepted-plan-prior-failure','consumer','execution-envelope',
      'enforcement','advisory-only','redaction_class','metadata_only','freshness','fresh'
    ) from (
      select * from public.defect
       where coalesce(p_dependency_refs,'[]'::jsonb) ? ('safe:defect:'||id::text)
       order by occurred_on desc,id limit 16
    ) d
  ) compiled;
  if items is null or jsonb_array_length(items)=0 then
    raise exception 'context compiler has no canonical doctrine item';
  end if;
  if jsonb_array_length(items) > 64 then
    raise exception 'context compiler requires % items, exceeding the contract maximum; narrow the active delivery set', jsonb_array_length(items);
  end if;
  return items;
end $$;
revoke all on function ops.context_activation_compiler_items(uuid,text,jsonb) from public;

create or replace function ops.context_activation_bundle_from_items(
  p_tenant text, p_work_request_ref text, p_plan_revision_ref text,
  p_plan_revision integer, p_base_plan_digest text, p_issued_at timestamptz,
  p_items jsonb
) returns jsonb language sql immutable strict security definer
set search_path=pg_catalog,ops,public as $$
  select jsonb_build_object(
    'schema_version','context-bundle.v1',
    'header',jsonb_build_object(
      'tenant_id',p_tenant, 'work_request_id',p_work_request_ref,
      'accepted_plan_revision_id',p_plan_revision_ref,
      'accepted_plan_revision',p_plan_revision,
      'accepted_plan_digest',p_base_plan_digest,
      'issued_at',to_char(p_issued_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
      'mode','shadow', 'retrieval_policy','policy:bounded-doctrine-v1',
      'retrieval_policy_version','v1','compiler_id','compiler:context-activation-v1','compiler_version','v1',
      'compiler_digest','sha256:'||encode(digest('compiler:context-activation-v1','sha256'),'hex'),
      'query_basis_digest','sha256:'||encode(digest(p_tenant||':'||p_work_request_ref||':'||p_plan_revision_ref,'sha256'),'hex'),
      'grounding_plan',jsonb_build_object('inline_budget',64,'retrieval_policy','bounded-doctrine','cache_segment','plan-bound','modalities',jsonb_build_array('metadata_only'),'freshness_sla','accepted-plan-bound')
    ), 'items',(select jsonb_agg(item || jsonb_build_object('artifact_kind',item->>'kind','scope_redaction',item->>'redaction_class','trigger_ref',item->>'trigger','consumer_ref',item->>'consumer','delivery_mode',case when (item->>'required')::boolean and item->>'kind' in ('doctrine','rule','decision') then 'inline' when item->>'kind'='memory' then 'on_demand_tool' else 'reference_only' end,'representation_kind',item->>'kind','freshness_sla','accepted-plan-bound','selection_reason',coalesce(item->>'selection_reason','canonical-compiler'),'selection_rank',coalesce((item->>'selection_rank')::int,ordinal),'requirement_class',case when (item->>'required')::boolean then 'required' else 'advisory' end) order by ordinal) from jsonb_array_elements(p_items) with ordinality x(item,ordinal))
  )
$$;
revoke all on function ops.context_activation_bundle_from_items(text,text,text,integer,text,timestamptz,jsonb) from public;

-- Replace the canonical preimage producer used by BOTH proposal and
-- acceptance. The context digest is computed from stable WR/plan inputs and
-- the base preimage digest, so acceptance reproduces the same bytes without a
-- circular reference to the final plan hash.
create or replace function ops.sourced_work_request_plan_preimage(
  p_work_request_id uuid, p_scope_summary text, p_runbook_ref text,
  p_runbook_section_id uuid, p_runbook_revision_id uuid, p_runbook_content_hash text,
  p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb
) returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public as $$
with base as (
  select jsonb_build_object(
    'contract','carr-sourced-ready-plan/v1',
    'work_request',jsonb_build_object('id',w.id,'ref',w.ref,'state',w.state,'version',w.version,'title',w.title,'desired_outcome',w.desired_outcome,'acceptance_criteria',w.acceptance_criteria,'origin_ref',w.origin_ref,'doctrine_section_id',w.doctrine_section_id,'doctrine_revision_id',w.doctrine_revision_id,'doctrine_content_hash',(select 'sha256:'||r.content_hash from public.doctrine_revision r where r.id=w.doctrine_revision_id and r.section_id=w.doctrine_section_id),'triage_classification',w.triage_classification,'triaged_by_actor_id',w.triaged_by_actor_id,'triaged_at',w.triaged_at,'shape_disposition',w.shape_disposition,'shape_fixed_surface_ref',w.shape_fixed_surface_ref,'shape_rationale',w.shape_rationale,'shape_decided_by_actor_id',w.shape_decided_by_actor_id,'shape_decided_at',w.shape_decided_at),
    'runbook',jsonb_build_object('ref',p_runbook_ref,'section_id',p_runbook_section_id,'revision_id',p_runbook_revision_id,'content_hash','sha256:'||p_runbook_content_hash),
    'plan',jsonb_build_object('scope_summary',p_scope_summary,'dependency_refs',p_dependency_refs,'recovery_ref',p_recovery_ref,'observability_ref',p_observability_ref,'caps',p_caps)
  ) preimage, w as work, p_runbook_revision_id
  from ops.work_request w where w.id=p_work_request_id
), item as (
  select ('plan:runbook:'||p_runbook_revision_id::text) plan_revision_ref,
         (work).triaged_at as issued_at, (work).organization_tenant_id, (work).ref, base.preimage,
         ops.context_activation_compiler_items((work).id,(work).organization_tenant_id,p_dependency_refs) compiler_items
  from base
)
  select preimage || jsonb_build_object('context_activation', jsonb_build_object(
    'bundle_digest',ops.context_activation_bundle_digest(ops.context_activation_bundle_from_items(
      organization_tenant_id, ref, plan_revision_ref, 1,
      'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(preimage),'sha256'),'hex'),
      issued_at, compiler_items
    )),
    'item_refs',(select jsonb_agg(value->'canonical_ref' order by ordinal)
      from jsonb_array_elements(compiler_items) with ordinality as x(value,ordinal)),
    'base_plan_digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(preimage),'sha256'),'hex')
  ), 'context_activation_items',compiler_items) from item;
$$;

revoke all on function ops.sourced_work_request_plan_preimage(uuid,text,text,uuid,uuid,text,jsonb,text,text,jsonb) from public;

create table if not exists ops.context_activation_binding (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null,
  organization_tenant_id text not null,
  work_request_id uuid not null references ops.work_request(id),
  work_request_version integer not null check (work_request_version > 0),
  plan_id uuid not null references ops.sourced_work_request_plan(id),
  plan_hash text not null check (plan_hash ~ '^sha256:[0-9a-f]{64}$'),
  binding_id text not null,
  bundle_digest text not null check (bundle_digest ~ '^sha256:[0-9a-f]{64}$'),
  retrieval_policy jsonb not null check (jsonb_typeof(retrieval_policy) = 'object'),
  mode text not null check (mode in ('shadow','canary','live','enforced')),
  issued_at timestamptz not null default now(),
  expires_at timestamptz not null,
  compiler_ref text not null,
  query_basis_digest text not null check (query_basis_digest ~ '^sha256:[0-9a-f]{64}$'),
  grounding_plan jsonb not null check (jsonb_typeof(grounding_plan) = 'object'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id, binding_id),
  unique (organization_tenant_id, idempotency_key)
);

create table if not exists ops.context_activation_item (
  id uuid primary key default gen_random_uuid(),
  binding_id uuid not null references ops.context_activation_binding(id),
  ordinal integer not null check (ordinal > 0),
  artifact_kind text not null,
  canonical_ref text not null,
  revision text not null,
  content_digest text not null check (content_digest ~ '^sha256:[0-9a-f]{64}$'),
  scope_redaction text not null,
  required boolean not null,
  trigger_ref text not null,
  consumer_ref text not null,
  delivery_mode text not null check (delivery_mode in ('inline','on_demand_tool','tool_affordance','reference_only')),
  representation_kind text not null,
  freshness jsonb not null check (jsonb_typeof(freshness) = 'object'),
  selection_reason text not null,
  selection_rank integer not null check (selection_rank >= 0),
  unique (binding_id, ordinal),
  unique (binding_id, canonical_ref, revision)
);

-- A profile lane is a server-owned, append-only execution assignment.  The
-- envelope issuer never picks an alphabetic/default profile: a policy/gateway
-- must first create this exact Work Request assignment under human authority.
create table if not exists ops.work_request_execution_assignment (
  id uuid primary key default gen_random_uuid(),
  work_request_id uuid not null unique references ops.work_request(id),
  profile_id uuid not null references agent_profile(id),
  sponsoring_human_id uuid not null references actor(id),
  environment text not null check (environment in ('local','rehearsal','staging','production')),
  policy_ref text not null,
  policy_digest text not null check (policy_digest ~ '^sha256:[0-9a-f]{64}$'),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default now()
);

-- The existing ExecutionEnvelope v1 is persisted as an immutable, redacted
-- projection.  This is not a replacement envelope type: it is the same v1
-- contract's server-issued binding record, allowing receipts to resolve an
-- exact envelope instead of trusting a caller-provided digest.
create table if not exists ops.execution_envelope_v1 (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null,
  organization_tenant_id text not null,
  work_request_id uuid not null references ops.work_request(id),
  plan_hash text not null check (plan_hash ~ '^sha256:[0-9a-f]{64}$'),
  activation_binding_id uuid not null references ops.context_activation_binding(id),
  envelope_digest text not null check (envelope_digest ~ '^sha256:[0-9a-f]{64}$'),
  envelope jsonb not null check (jsonb_typeof(envelope)='object' and envelope->>'schema_version'='execution-envelope.v1'),
  runtime_profile jsonb not null check (jsonb_typeof(runtime_profile)='object'),
  execution_topology jsonb not null check (jsonb_typeof(execution_topology)='object'),
  evaluation_plan jsonb not null check (jsonb_typeof(evaluation_plan)='object'),
  configuration_digest text not null check (configuration_digest ~ '^sha256:[0-9a-f]{64}$'),
  issued_at timestamptz not null default now(),
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (organization_tenant_id, idempotency_key),
  unique (organization_tenant_id, envelope_digest),
  unique (organization_tenant_id, activation_binding_id)
);

-- Persist the existing AttemptReceipt v1 verbatim as a redacted, append-only
-- projection.  This is deliberately not an opaque "reliability fact" type:
-- activation/reliability are extensions within the receipt contract.
create table if not exists ops.attempt_receipt (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null,
  organization_tenant_id text not null,
  work_request_id uuid not null references ops.work_request(id),
  plan_hash text not null check (plan_hash ~ '^sha256:[0-9a-f]{64}$'),
  envelope_digest text not null check (envelope_digest ~ '^sha256:[0-9a-f]{64}$'),
  attempt_id text not null,
  activation_binding_id uuid not null references ops.context_activation_binding(id),
  execution_envelope_id uuid not null references ops.execution_envelope_v1(id),
  receipt jsonb not null check (jsonb_typeof(receipt) = 'object' and receipt->>'schema_version' = 'attempt-receipt.v1'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id, attempt_id),
  unique (organization_tenant_id, idempotency_key)
);

create table if not exists ops.activation_reliability_telemetry (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  attempt_receipt_id uuid not null references ops.attempt_receipt(id),
  signal_id text not null,
  trigger_ref text not null,
  consumer_ref text not null,
  enforcement text not null,
  owner_ref text not null,
  remedy_ref text not null,
  verification_ref text not null,
  auto_clear boolean not null,
  state text not null check (state in ('open','verified','cleared','unknown')),
  created_at timestamptz not null default now(),
  unique (attempt_receipt_id, signal_id)
);

-- Canonical evaluation facts remain attached to the existing AttemptReceipt.
-- They are not a second receipt or promotion ledger: authority records an
-- independently observed evaluator result against the frozen envelope plan.
create table if not exists ops.attempt_receipt_evaluation_attestation (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  attempt_receipt_id uuid not null references ops.attempt_receipt(id),
  evaluator_kind text not null check (evaluator_kind in ('deterministic','judge','human_acceptance','outcome_horizon')),
  check_ref text not null default '',
  dimension_refs jsonb not null check (jsonb_typeof(dimension_refs)='array'),
  evaluator_policy_digest text not null check (evaluator_policy_digest ~ '^sha256:[0-9a-f]{64}$'),
  -- The authority fact carries reproducible evaluator configuration rather
  -- than trusting a similarly named executor result in AttemptReceipt.
  evaluator_ref text not null default 'evaluator:unspecified' check (evaluator_ref ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'),
  rubric_ref text not null default 'rubric:unspecified' check (rubric_ref ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'),
  evaluator_version text not null default 'version:unspecified' check (evaluator_version ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'),
  evaluator_digest text not null default 'sha256:0000000000000000000000000000000000000000000000000000000000000000' check (evaluator_digest ~ '^sha256:[0-9a-f]{64}$'),
  confidence text not null default 'unknown' check (confidence in ('high','medium','low','unknown')),
  held_out_case_count integer not null default 0 check (held_out_case_count >= 0),
  calibration_refs jsonb not null default '[]'::jsonb check (jsonb_typeof(calibration_refs)='array'),
  lower_bound_ref text null check (lower_bound_ref is null or lower_bound_ref ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'),
  outcome_feedback_ref text null check (outcome_feedback_ref is null or outcome_feedback_ref ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'),
  outcome_feedback_hash text null check (outcome_feedback_hash is null or outcome_feedback_hash ~ '^sha256:[0-9a-f]{64}$'),
  status text not null check (status in ('passed','failed','blocked','unknown','not_run','mature','immature')),
  independent boolean not null default false,
  evidence_refs jsonb not null check (jsonb_typeof(evidence_refs)='array' and jsonb_array_length(evidence_refs)>0),
  attested_by_actor_id uuid not null references actor(id),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default clock_timestamp(),
  unique (attempt_receipt_id,evaluator_kind,check_ref)
);

create table if not exists ops.proposed_eval_candidate (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  attempt_receipt_id uuid not null references ops.attempt_receipt(id),
  candidate_ref text not null,
  case_ref text not null,
  target_golden_set_ref text not null,
  normalized_root_cause_key text not null,
  source_digest text not null check (source_digest ~ '^sha256:[0-9a-f]{64}$'),
  lane_ref text not null,
  risk_class text not null check (risk_class ~ '^R[0-6]$'),
  provenance text not null check (provenance in ('correction','defect','incident','evaluation','human_observation')),
  split_target text not null check (split_target in ('development','held_out','canary')),
  context_binding jsonb not null check (jsonb_typeof(context_binding) = 'object'),
  basis jsonb not null check (jsonb_typeof(basis) = 'array'),
  -- Lifecycle is event-derived. This compatibility field is deliberately
  -- fixed at proposal and must never be read as current state.
  lifecycle text not null default 'proposed' check (lifecycle = 'proposed'),
  promotion_state text not null default 'not_promoted' check (promotion_state = 'not_promoted'),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id, candidate_ref),
  unique (organization_tenant_id, normalized_root_cause_key, source_digest)
);

-- A proposal names a possible target set but is not golden membership. Human
-- authority advances it through append-only events; acceptance alone creates
-- a membership projection.
create table if not exists ops.proposed_eval_candidate_event (
  id uuid primary key default gen_random_uuid(),
  candidate_id uuid not null references ops.proposed_eval_candidate(id),
  event_kind text not null check (event_kind in ('proposed','triaged','accepted','retired')),
  decided_by_actor_id uuid references actor(id),
  decision_basis jsonb not null check (jsonb_typeof(decision_basis)='object'),
  idempotency_key uuid not null unique,
  -- Lifecycle order is a causal fact. `now()` is transaction-stable and
  -- cannot distinguish triage then acceptance in the same canary transaction.
  created_at timestamptz not null default clock_timestamp()
);
create table if not exists ops.accepted_eval_golden_membership (
  id uuid primary key default gen_random_uuid(),
  candidate_id uuid not null unique references ops.proposed_eval_candidate(id),
  target_golden_set_ref text not null,
  accepted_by_actor_id uuid not null references actor(id),
  accepted_at timestamptz not null default now()
);

-- Defence in depth for the direct database door.  The MCP adapter rejects
-- raw fields too, but table admission must remain safe if another sanctioned
-- writer calls record_attempt_receipt directly.  Do not substitute a label
-- such as `redacted` for this structural check: transcript/prompt/tool bodies
-- must never enter this projection at any nesting depth.
create or replace function ops.attempt_receipt_contains_raw_content(p_value jsonb)
returns boolean language plpgsql immutable security definer
set search_path=pg_catalog,ops as $$
declare entry record;
begin
  if jsonb_typeof(p_value) = 'object' then
    -- Held-out expected outputs are evaluator material, not executor or
    -- Passport material.  Refuse them at the same recursive persistence
    -- boundary as prompts and tool bodies.
    if p_value ?| array['raw_prompt','raw_transcript','tool_payload','raw_output','prompt','transcript','expected_output','expected_answer','held_out_expected_output','held_out_answer'] then
      return true;
    end if;
    for entry in select value from jsonb_each(p_value) loop
      if ops.attempt_receipt_contains_raw_content(entry.value) then return true; end if;
    end loop;
  elsif jsonb_typeof(p_value) = 'array' then
    for entry in select value from jsonb_array_elements(p_value) loop
      if ops.attempt_receipt_contains_raw_content(entry.value) then return true; end if;
    end loop;
  elsif jsonb_typeof(p_value) = 'string' then
    -- This projection is an identifier/enumeration/timestamp/digest contract,
    -- never a prose transport.  A sentence cannot be made safe merely by
    -- changing its field name.
    if char_length(p_value #>> '{}') > 127 or (p_value #>> '{}') ~ '[[:space:]]' then return true; end if;
  end if;
  return false;
end $$;
revoke all on function ops.attempt_receipt_contains_raw_content(jsonb) from public;

create or replace function ops.attempt_receipt_binding_valid()
returns trigger language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare binding ops.context_activation_binding%rowtype; envelope_row ops.execution_envelope_v1%rowtype; expected_unresolved jsonb; expected_closure_state text;
  disposition jsonb; reliability jsonb; expected_reliability_state text; expected_reliability_reasons jsonb; required_evaluator_kinds text[]; held_out_count integer;
begin
  select * into binding from ops.context_activation_binding where id=new.activation_binding_id;
  select * into envelope_row from ops.execution_envelope_v1 where id=new.execution_envelope_id;
  if not found or binding.organization_tenant_id is distinct from new.organization_tenant_id
     or binding.work_request_id is distinct from new.work_request_id
     or binding.plan_hash is distinct from new.plan_hash
     or binding.expires_at < now()
     or exists (select 1 from ops.work_request current_work where current_work.id=binding.work_request_id and (current_work.version<>binding.work_request_version or current_work.organization_tenant_id<>binding.organization_tenant_id))
     or envelope_row.organization_tenant_id is distinct from new.organization_tenant_id
     or envelope_row.work_request_id is distinct from new.work_request_id
     or envelope_row.plan_hash is distinct from new.plan_hash
     or envelope_row.activation_binding_id is distinct from binding.id
     or envelope_row.envelope_digest is distinct from new.envelope_digest
     or envelope_row.expires_at < now()
     or jsonb_typeof(new.receipt) is distinct from 'object'::text
     or new.receipt->>'schema_version' <> 'attempt-receipt.v1'
     or new.receipt->>'envelope_digest' is distinct from new.envelope_digest
     or new.receipt->>'attempt_id' is distinct from new.attempt_id
     or not (new.receipt ?& array['schema_version','attempt_id','envelope_digest','attempt_ordinal','adapter','lifecycle','result','attestation','negative_knowledge','telemetry','tool_event_summaries','observation','interventions','handoff_proposal','visual_artifacts','evaluation_binding'])
     or exists (select 1 from jsonb_object_keys(new.receipt) key where key <> all(array['schema_version','attempt_id','envelope_digest','attempt_ordinal','adapter','lifecycle','result','attestation','negative_knowledge','telemetry','tool_event_summaries','observation','interventions','handoff_proposal','visual_artifacts','evaluation_binding','knowledge_activation','reliability']))
     or ops.attempt_receipt_contains_raw_content(new.receipt)
     or not (new.receipt ?& array['knowledge_activation','reliability'])
     or coalesce(new.receipt->'knowledge_activation'->'closure'->>'derived_by','') <> 'server' then
    raise exception 'attempt receipt is cross-bound, malformed, or contains raw content';
  end if;
  -- A `derived_by: server` label is not proof.  Recompute the required-item
  -- closure from the immutable activation rows and reject any forged or stale
  -- nested receipt before it is admitted.
  if jsonb_typeof(new.receipt->'knowledge_activation') <> 'object'
     or not (new.receipt->'knowledge_activation' ?& array['bundle_digest','item_dispositions','closure','mode','canonical_binding'])
     or exists (select 1 from jsonb_object_keys(new.receipt->'knowledge_activation') key where key <> all(array['bundle_digest','item_dispositions','closure','mode','canonical_binding']))
     or jsonb_typeof(new.receipt->'knowledge_activation'->'canonical_binding') <> 'object'
     or not (new.receipt->'knowledge_activation'->'canonical_binding' ?& array['work_request_id','work_request_version','accepted_plan_digest','envelope_digest','activation_binding_ref'])
     or exists (select 1 from jsonb_object_keys(new.receipt->'knowledge_activation'->'canonical_binding') key where key <> all(array['work_request_id','work_request_version','accepted_plan_digest','envelope_digest','activation_binding_ref']))
     or new.receipt->'knowledge_activation'->'canonical_binding'->>'work_request_id' <> (select ref from ops.work_request where id=binding.work_request_id)
     or new.receipt->'knowledge_activation'->'canonical_binding'->>'work_request_version' <> binding.work_request_version::text
     or new.receipt->'knowledge_activation'->'canonical_binding'->>'accepted_plan_digest' <> binding.plan_hash
     or new.receipt->'knowledge_activation'->'canonical_binding'->>'envelope_digest' <> new.envelope_digest
     or new.receipt->'knowledge_activation'->'canonical_binding'->>'activation_binding_ref' <> binding.binding_id
     or new.receipt->'knowledge_activation'->>'bundle_digest' <> binding.bundle_digest
     or jsonb_typeof(new.receipt->'knowledge_activation'->'item_dispositions') <> 'array'
     or (select count(*) from jsonb_array_elements(new.receipt->'knowledge_activation'->'item_dispositions'))
          <> (select count(*) from ops.context_activation_item where binding_id=binding.id)
     or exists (
       select 1 from jsonb_array_elements(new.receipt->'knowledge_activation'->'item_dispositions') d
        where jsonb_typeof(d) <> 'object'
           or not (d ?& array['item_ref','disposition','evidence_refs','reason_ref'])
           or exists (select 1 from jsonb_object_keys(d) key where key <> all(array['item_ref','disposition','evidence_refs','reason_ref','stage_ref','tool_ref']))
           or d->>'disposition' not in ('applied','not_applicable','conflicted','stale','missing')
           or jsonb_typeof(d->'evidence_refs') <> 'array'
           or coalesce(d->>'reason_ref','') !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
           or (d->>'disposition'='applied' and (jsonb_array_length(d->'evidence_refs')=0 or not (d ?| array['stage_ref','tool_ref'])))
     )
     or exists (
       select 1 from ops.context_activation_item i
       left join lateral (
         select d from jsonb_array_elements(new.receipt->'knowledge_activation'->'item_dispositions') d
          where d->>'item_ref'=i.canonical_ref limit 1
       ) supplied on true
       where i.binding_id=binding.id and supplied.d is null
          or (i.binding_id=binding.id and i.required and coalesce(supplied.d->>'disposition','missing') <> 'applied'
              and (jsonb_typeof(supplied.d->'evidence_refs') <> 'array' or jsonb_array_length(supplied.d->'evidence_refs')=0))
     ) then
    raise exception 'attempt receipt knowledge activation is not exactly bound';
  end if;
  select coalesce(jsonb_agg(i.canonical_ref order by i.canonical_ref),'[]'::jsonb)
    into expected_unresolved
    from ops.context_activation_item i
    left join lateral (
      select d from jsonb_array_elements(new.receipt->'knowledge_activation'->'item_dispositions') d
       where d->>'item_ref'=i.canonical_ref limit 1
    ) disposition on true
   where i.binding_id=binding.id and i.required
     and coalesce(disposition.d->>'disposition','missing') <> 'applied';
  expected_closure_state := case when binding.mode='shadow' then 'not_activated'
    when expected_unresolved='[]'::jsonb then 'closed' else 'blocked' end;
  if new.receipt->'knowledge_activation'->'closure'->'unresolved_required_item_refs' is distinct from expected_unresolved
     or new.receipt->'knowledge_activation'->'closure'->>'state' is distinct from expected_closure_state then
    raise exception 'attempt receipt knowledge closure was not server-derived';
  end if;
  reliability := new.receipt->'reliability';
  if jsonb_typeof(reliability) <> 'object'
     or not (reliability ?& array['route_digest','topology_digest','evaluation_plan_digest','grounding_sufficiency','deterministic_checks','model_judgement','human_acceptance','trajectory','evaluator_results','corrections','defects','incidents','downstream_outcome','outcome_horizon','process_metrics','eval_candidates','shadow_comparisons','learning_disposition','telemetry','closure'])
     or exists (select 1 from jsonb_object_keys(reliability) key where key <> all(array['route_digest','topology_digest','evaluation_plan_digest','grounding_sufficiency','deterministic_checks','model_judgement','human_acceptance','trajectory','evaluator_results','corrections','defects','incidents','downstream_outcome','outcome_horizon','process_metrics','eval_candidates','shadow_comparisons','learning_disposition','telemetry','closure']))
     or jsonb_typeof(reliability->'deterministic_checks') <> 'array'
     or exists (select 1 from jsonb_array_elements(reliability->'deterministic_checks') check_row where jsonb_typeof(check_row) <> 'object' or not (check_row ?& array['check_id','state','critical','evidence_refs']) or check_row->>'check_id' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or check_row->>'state' not in ('passed','failed','unknown','not_run') or jsonb_typeof(check_row->'critical')<>'boolean' or jsonb_typeof(check_row->'evidence_refs')<>'array')
     or jsonb_typeof(reliability->'trajectory') <> 'array'
     or exists (select 1 from jsonb_array_elements(reliability->'trajectory') trace_row where jsonb_typeof(trace_row) <> 'object' or not (trace_row ?& array['sequence','stage_ref','parent_event_ref','decision_class','tool_class','result_state','fallback_state','guardrail_state','latency_ms','evidence_refs']) or exists (select 1 from jsonb_object_keys(trace_row) key where key <> all(array['sequence','stage_ref','parent_event_ref','decision_class','tool_class','result_state','fallback_state','guardrail_state','latency_ms','evidence_refs'])))
     or jsonb_typeof(reliability->'evaluator_results') <> 'array' or jsonb_array_length(reliability->'evaluator_results')=0
     or exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where jsonb_typeof(evaluator) <> 'object' or not (evaluator ?& array['kind','evaluator_ref','rubric_ref','evaluator_version','evaluator_digest','status','confidence','critical','independence_state','held_out_case_count','check_refs','dimension_refs','evidence_refs','judge_provenance','calibration_evidence_refs']) or exists (select 1 from jsonb_object_keys(evaluator) key where key <> all(array['kind','evaluator_ref','rubric_ref','evaluator_version','evaluator_digest','status','confidence','critical','independence_state','held_out_case_count','check_refs','dimension_refs','evidence_refs','judge_provenance','calibration_evidence_refs'])) or evaluator->>'kind' not in ('deterministic','judge','human_acceptance') or evaluator->>'status' not in ('passed','failed','blocked','unknown','not_run') or evaluator->>'independence_state' not in ('not_independent','unknown') or jsonb_typeof(evaluator->'held_out_case_count')<>'number' or (evaluator->>'held_out_case_count')::integer<0 or jsonb_typeof(evaluator->'check_refs')<>'array' or jsonb_typeof(evaluator->'dimension_refs')<>'array')
     or jsonb_typeof(reliability->'grounding_sufficiency') <> 'object' or not (reliability->'grounding_sufficiency' ?& array['state','evidence_refs','required_supplied','required_used','required_missing','advisory_supplied','advisory_used','freshness_failures','retrieval_failures'])
     -- Learning facts are canonical, redacted metadata.  The executor cannot
     -- smuggle a candidate, free-text case body, or a fact into the wrong lane.
     or jsonb_typeof(reliability->'eval_candidates') <> 'array' or jsonb_array_length(reliability->'eval_candidates') <> 0
     -- Executor receipts cannot carry a shadow route or its side-effect
     -- claims. Governed shadows are admitted only by Policy Learning.
     or jsonb_typeof(reliability->'shadow_comparisons') <> 'array' or jsonb_array_length(reliability->'shadow_comparisons') <> 0
     or exists (select 1 from jsonb_array_elements(reliability->'corrections') event_row where jsonb_typeof(event_row)<>'object' or not (event_row ?& array['event_ref','kind','evidence_refs','summary']) or exists(select 1 from jsonb_object_keys(event_row) key where key<>all(array['event_ref','kind','evidence_refs','summary'])) or event_row->>'kind'<>'correction' or event_row->>'event_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or event_row->>'summary' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or jsonb_typeof(event_row->'evidence_refs')<>'array' or jsonb_array_length(event_row->'evidence_refs')=0)
     or exists (select 1 from jsonb_array_elements(reliability->'defects') event_row where jsonb_typeof(event_row)<>'object' or not (event_row ?& array['event_ref','kind','evidence_refs','summary']) or exists(select 1 from jsonb_object_keys(event_row) key where key<>all(array['event_ref','kind','evidence_refs','summary'])) or event_row->>'kind'<>'defect' or event_row->>'event_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or event_row->>'summary' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or jsonb_typeof(event_row->'evidence_refs')<>'array' or jsonb_array_length(event_row->'evidence_refs')=0)
     or exists (select 1 from jsonb_array_elements(reliability->'incidents') event_row where jsonb_typeof(event_row)<>'object' or not (event_row ?& array['event_ref','kind','evidence_refs','summary']) or exists(select 1 from jsonb_object_keys(event_row) key where key<>all(array['event_ref','kind','evidence_refs','summary'])) or event_row->>'kind'<>'incident' or event_row->>'event_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or event_row->>'summary' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or jsonb_typeof(event_row->'evidence_refs')<>'array' or jsonb_array_length(event_row->'evidence_refs')=0)
     or jsonb_typeof(reliability->'human_acceptance')<>'object' or not (reliability->'human_acceptance' ?& array['state','actor_ref','evidence_refs','outcome_feedback_ref','outcome_feedback_hash'])
     or reliability->'human_acceptance'->>'state' not in ('accepted','rejected','absent','unknown')
     or (reliability->'human_acceptance'->>'state'='accepted' and (reliability->'human_acceptance'->>'outcome_feedback_ref' is null or reliability->'human_acceptance'->>'outcome_feedback_hash' !~ '^sha256:[0-9a-f]{64}$'))
     or (reliability->'human_acceptance'->>'state'<>'accepted' and ((reliability->'human_acceptance'->'outcome_feedback_ref')<>'null'::jsonb or (reliability->'human_acceptance'->'outcome_feedback_hash')<>'null'::jsonb))
     or jsonb_typeof(reliability->'downstream_outcome')<>'object' or not (reliability->'downstream_outcome' ?& array['state','brokerage_ref','evidence_refs','outcome_feedback_ref','outcome_feedback_hash'])
     or reliability->'downstream_outcome'->>'state' not in ('observed','not_observed','unknown')
     or (reliability->'downstream_outcome'->>'state'='observed' and (reliability->'downstream_outcome'->>'outcome_feedback_ref' is null or reliability->'downstream_outcome'->>'outcome_feedback_hash' !~ '^sha256:[0-9a-f]{64}$'))
     or (reliability->'downstream_outcome'->>'state'<>'observed' and ((reliability->'downstream_outcome'->'outcome_feedback_ref')<>'null'::jsonb or (reliability->'downstream_outcome'->'outcome_feedback_hash')<>'null'::jsonb))
     or jsonb_typeof(reliability->'outcome_horizon') <> 'object' or not (reliability->'outcome_horizon' ?& array['state','ends_at','as_of','evidence_refs'])
     or jsonb_typeof(reliability->'process_metrics') <> 'object' or not (reliability->'process_metrics' ?& array['latency_ms','cost_usd','input_tokens','output_tokens','cached_input_tokens','retry_count','recovery_count','context_reconstruction_ms','human_intervention_count','security_event_refs'])
     or jsonb_typeof(reliability->'closure') <> 'object' or not (reliability->'closure' ?& array['state','reasons','derived_by']) or reliability->'closure'->>'derived_by'<>'server'
     or jsonb_typeof(reliability->'telemetry') <> 'array' or jsonb_array_length(reliability->'telemetry') <> 0 then
    raise exception 'attempt receipt reliability extension is malformed';
  end if;
  if reliability ? 'route_digest' and reliability->>'route_digest' <> envelope_row.runtime_profile->>'digest'
     or reliability ? 'topology_digest' and reliability->>'topology_digest' <> envelope_row.execution_topology->>'digest'
     or reliability ? 'evaluation_plan_digest' and reliability->>'evaluation_plan_digest' <> envelope_row.evaluation_plan->>'digest' then
    raise exception 'attempt receipt route/topology/evaluation digests do not bind the server-issued envelope';
  end if;
  if (select coalesce(jsonb_agg(check_row->>'check_id' order by check_row->>'check_id'),'[]'::jsonb) from jsonb_array_elements(reliability->'deterministic_checks') check_row)
       is distinct from (select coalesce(jsonb_agg(value order by value),'[]'::jsonb) from jsonb_array_elements_text(envelope_row.evaluation_plan->'required_deterministic_check_refs') value)
     or exists (select 1 from jsonb_array_elements(reliability->'deterministic_checks') check_row where (check_row->>'critical')::boolean is not true)
     or exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where evaluator->>'kind'='deterministic' and (jsonb_array_length(evaluator->'check_refs')<>1 or evaluator->'dimension_refs' is distinct from envelope_row.evaluation_plan->'critical_dimensions'))
     or exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where evaluator->>'kind'<>'deterministic' and (jsonb_array_length(evaluator->'check_refs')<>0 or evaluator->'dimension_refs' is distinct from envelope_row.evaluation_plan->'critical_dimensions')) then
    raise exception 'evaluation evidence does not exactly bind server-required checks and critical dimensions';
  end if;
  -- Executor-submitted actor/status labels are never acceptance evidence.  A
  -- claimed human acceptance/outcome must resolve to the existing immutable,
  -- human-authority Program 6 feedback receipt for this exact Work Request and
  -- accepted plan; absent/unknown feedback remains visibly unavailable.
  if reliability->'human_acceptance'->>'state'='accepted' and not exists (
    select 1 from ops.sourced_work_request_outcome_feedback f
      join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
      join public.actor a on a.id=fr.accepted_by_actor_id
     where f.work_request_id=new.work_request_id and f.plan_id=binding.plan_id
       and f.feedback_ref=reliability->'human_acceptance'->>'outcome_feedback_ref'
       and f.feedback_hash=reliability->'human_acceptance'->>'outcome_feedback_hash'
       and ('actor:'||a.slug)=reliability->'human_acceptance'->>'actor_ref'
  ) then raise exception 'attempt receipt human acceptance is not bound to accepted Work Request outcome feedback'; end if;
  if reliability->'downstream_outcome'->>'state'='observed' and (
    reliability->'downstream_outcome'->>'outcome_feedback_ref' is distinct from reliability->'human_acceptance'->>'outcome_feedback_ref'
    or reliability->'downstream_outcome'->>'outcome_feedback_hash' is distinct from reliability->'human_acceptance'->>'outcome_feedback_hash'
    or not exists (
      select 1 from ops.sourced_work_request_outcome_feedback f
        join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
       where f.work_request_id=new.work_request_id and f.plan_id=binding.plan_id
         and f.feedback_ref=reliability->'downstream_outcome'->>'outcome_feedback_ref'
         and f.feedback_hash=reliability->'downstream_outcome'->>'outcome_feedback_hash'
    )
  ) then raise exception 'attempt receipt observed outcome is not bound to accepted Work Request feedback'; end if;
  select coalesce(array_agg(value),array[]::text[]) into required_evaluator_kinds from jsonb_array_elements_text(envelope_row.evaluation_plan->'requirements'->'required_evaluator_kinds');
  select coalesce(sum((evaluator->>'held_out_case_count')::integer),0) into held_out_count from jsonb_array_elements(reliability->'evaluator_results') evaluator;
  if exists (select 1 from jsonb_array_elements(reliability->'deterministic_checks') check_row where (check_row->>'critical')::boolean and check_row->>'state'='failed')
     or exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where (evaluator->>'critical')::boolean and evaluator->>'status' in ('failed','blocked')) then
    expected_reliability_state := 'blocked';
  elsif reliability->'grounding_sufficiency'->>'state'<>'sufficient'
     or coalesce((envelope_row.evaluation_plan->'requirements'->>'outcome_horizon_required')::boolean,false) and reliability->'outcome_horizon'->>'state'<>'mature'
     or reliability->'model_judgement'->>'state'<>'pass'
     or exists (select 1 from jsonb_array_elements(reliability->'deterministic_checks') check_row where (check_row->>'critical')::boolean and check_row->>'state' in ('unknown','not_run'))
     or exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where (evaluator->>'critical')::boolean and evaluator->>'status' in ('unknown','not_run'))
     or exists (select 1 from unnest(required_evaluator_kinds) kind where not exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where evaluator->>'kind'=kind))
     or held_out_count < coalesce((envelope_row.evaluation_plan->'requirements'->>'minimum_held_out_case_count')::integer,0)
     or coalesce((envelope_row.evaluation_plan->'requirements'->>'independent_review_required')::boolean,false) and not exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where evaluator->>'independence_state'='independent')
     or coalesce((envelope_row.evaluation_plan->'requirements'->>'human_acceptance_required')::boolean,false) and reliability->'human_acceptance'->>'state'<>'accepted' then
    expected_reliability_state := 'insufficient_evidence';
  -- AttemptReceipt is executor evidence, never the authoritative evaluator
  -- event.  It cannot promote its own judge/held-out/calibration claims.
  else expected_reliability_state := 'insufficient_evidence';
  end if;
  if reliability->'closure'->>'state' is distinct from expected_reliability_state then
    raise exception 'attempt receipt reliability closure was not server-derived for attempt %, expected %, got %',new.attempt_id,expected_reliability_state,reliability->'closure'->>'state';
  end if;
  expected_reliability_reasons := case when expected_reliability_state='blocked' then
    jsonb_build_array('reason:authority_evaluation_evidence_missing','reason:critical_deterministic_or_evaluator_failure')
  else jsonb_build_array('reason:authority_evaluation_evidence_missing') end;
  if reliability->'closure'->'reasons' is distinct from expected_reliability_reasons then
    raise exception 'attempt receipt reliability closure reasons were not server-derived';
  end if;
  return new;
end $$;
drop trigger if exists attempt_receipt_binding_valid on ops.attempt_receipt;
create trigger attempt_receipt_binding_valid before insert on ops.attempt_receipt for each row execute function ops.attempt_receipt_binding_valid();

drop trigger if exists attempt_receipt_telemetry_projection on ops.attempt_receipt;
drop function if exists ops.project_attempt_receipt_telemetry();

create or replace function ops.evidence_activation_append_only()
returns trigger language plpgsql as $$
begin
  raise exception 'evidence activation projections are append-only';
end $$;

drop trigger if exists context_activation_binding_append_only on ops.context_activation_binding;
create trigger context_activation_binding_append_only before update or delete on ops.context_activation_binding for each row execute function ops.evidence_activation_append_only();
drop trigger if exists context_activation_item_append_only on ops.context_activation_item;
create trigger context_activation_item_append_only before update or delete on ops.context_activation_item for each row execute function ops.evidence_activation_append_only();
drop trigger if exists execution_envelope_v1_append_only on ops.execution_envelope_v1;
create trigger execution_envelope_v1_append_only before update or delete on ops.execution_envelope_v1 for each row execute function ops.evidence_activation_append_only();
drop trigger if exists attempt_receipt_append_only on ops.attempt_receipt;
create trigger attempt_receipt_append_only before update or delete on ops.attempt_receipt for each row execute function ops.evidence_activation_append_only();
drop trigger if exists activation_reliability_telemetry_append_only on ops.activation_reliability_telemetry;
create trigger activation_reliability_telemetry_append_only before update or delete on ops.activation_reliability_telemetry for each row execute function ops.evidence_activation_append_only();
drop trigger if exists attempt_receipt_evaluation_attestation_append_only on ops.attempt_receipt_evaluation_attestation;
create trigger attempt_receipt_evaluation_attestation_append_only before update or delete on ops.attempt_receipt_evaluation_attestation for each row execute function ops.evidence_activation_append_only();
drop trigger if exists proposed_eval_candidate_append_only on ops.proposed_eval_candidate;
create trigger proposed_eval_candidate_append_only before update or delete on ops.proposed_eval_candidate for each row execute function ops.evidence_activation_append_only();
drop trigger if exists proposed_eval_candidate_event_append_only on ops.proposed_eval_candidate_event;
create trigger proposed_eval_candidate_event_append_only before update or delete on ops.proposed_eval_candidate_event for each row execute function ops.evidence_activation_append_only();
drop trigger if exists accepted_eval_golden_membership_append_only on ops.accepted_eval_golden_membership;
create trigger accepted_eval_golden_membership_append_only before update or delete on ops.accepted_eval_golden_membership for each row execute function ops.evidence_activation_append_only();

-- No direct table INSERT is granted.  Tenant, sponsor, accepted-plan, bundle
-- digest, timestamps, and idempotency are derived/checked by these doors.
revoke all on ops.context_activation_binding, ops.context_activation_item, ops.work_request_execution_assignment, ops.execution_envelope_v1, ops.attempt_receipt, ops.activation_reliability_telemetry, ops.attempt_receipt_evaluation_attestation, ops.proposed_eval_candidate, ops.proposed_eval_candidate_event, ops.accepted_eval_golden_membership from public,carr_reader,carr_writer,carr_jobs;
revoke insert, update, delete on ops.context_activation_binding, ops.context_activation_item, ops.work_request_execution_assignment, ops.execution_envelope_v1, ops.attempt_receipt, ops.activation_reliability_telemetry, ops.attempt_receipt_evaluation_attestation, ops.proposed_eval_candidate, ops.proposed_eval_candidate_event, ops.accepted_eval_golden_membership from carr_reader, carr_writer, carr_jobs;

create or replace function ops.activate_context_bundle(
  p_work_request text, p_plan_ref text, p_bundle jsonb, p_idempotency_key uuid
) returns table(binding_id text, bundle_digest text, replayed boolean)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare
  tenant text := current_setting('carr.organization_tenant_id', true);
  work ops.work_request%rowtype;
  plan ops.sourced_work_request_plan%rowtype;
  existing ops.context_activation_binding%rowtype;
  new_id text;
  digest_text text;
  item jsonb;
  ordinal integer := 0;
begin
  if coalesce(tenant,'') = '' then raise exception 'activation requires authenticated tenant context'; end if;
  select * into work from ops.work_request where ref=p_work_request and organization_tenant_id=tenant for share;
  if not found then raise exception 'activation work request is not visible to tenant'; end if;
  select p.* into plan from ops.sourced_work_request_plan p
   join ops.sourced_work_request_plan_acceptance_receipt a on a.plan_id=p.id and a.plan_hash=p.plan_hash
   where p.work_request_id=work.id and p.plan_ref=p_plan_ref and a.result_version=work.version for share;
  if not found then raise exception 'activation requires the exact accepted current plan'; end if;
  select * into existing from ops.context_activation_binding where organization_tenant_id=tenant and idempotency_key=p_idempotency_key for share;
  if found then
    if existing.work_request_id<>work.id or existing.plan_hash<>plan.plan_hash or existing.bundle_digest<>coalesce(p_bundle->>'bundle_digest','') then raise exception 'activation idempotency key conflicts with prior binding'; end if;
    return query select existing.binding_id, existing.bundle_digest, true; return;
  end if;
  if jsonb_typeof(p_bundle)<>'object' or p_bundle->>'schema_version' <> 'context-bundle.v1' or jsonb_typeof(p_bundle->'header')<>'object' or jsonb_typeof(p_bundle->'items')<>'array' then raise exception 'activation bundle shape is invalid'; end if;
  if jsonb_array_length(p_bundle->'items') < 1 or jsonb_array_length(p_bundle->'items') > 64 then raise exception 'activation bundle exceeds bounded item count'; end if;
  if p_bundle->'header'->>'work_request_id' <> work.ref or p_bundle->'header'->>'accepted_plan_digest' <> coalesce(plan.preimage->'context_activation'->>'base_plan_digest',plan.plan_hash) then raise exception 'activation bundle is not bound to accepted Work Request and plan'; end if;
  if p_bundle->'header'->>'tenant_id' <> tenant then raise exception 'activation bundle tenant mismatch'; end if;
  if plan.preimage->'context_activation'->>'bundle_digest' is null
     or plan.preimage->'context_activation'->>'bundle_digest' <> p_bundle->>'bundle_digest' then
    raise exception 'activation bundle digest is not in the accepted plan preimage';
  end if;
  if plan.preimage->'context_activation'->'item_refs' is null
     or (select count(*) from jsonb_array_elements_text(plan.preimage->'context_activation'->'item_refs')) <> jsonb_array_length(p_bundle->'items') then
    raise exception 'activation bundle item set is not in the accepted plan preimage';
  end if;
  digest_text := ops.context_activation_bundle_digest(p_bundle);
  if p_bundle->>'bundle_digest' <> digest_text then raise exception 'activation bundle digest does not reproduce canonical body'; end if;
  new_id := 'ctx-' || encode(gen_random_bytes(8),'hex');
  insert into ops.context_activation_binding(idempotency_key,organization_tenant_id,work_request_id,work_request_version,plan_id,plan_hash,binding_id,bundle_digest,retrieval_policy,mode,issued_at,expires_at,compiler_ref,query_basis_digest,grounding_plan)
  values(p_idempotency_key,tenant,work.id,work.version,plan.id,plan.plan_hash,new_id,p_bundle->>'bundle_digest',jsonb_build_object('ref',coalesce(p_bundle->'header'->>'retrieval_policy','policy:unknown')),coalesce(p_bundle->'header'->>'mode','shadow'),now(),coalesce((p_bundle->'header'->>'expires_at')::timestamptz,now()+interval '1 hour'),coalesce(p_bundle->'header'->>'compiler_id','compiler:unknown'),coalesce(p_bundle->'header'->>'query_basis_digest','sha256:'||repeat('0',64)),jsonb_build_object('source_coverage',jsonb_build_object('doctrine','retrieved','standing_rules','retrieved','accepted_decisions','dependency_selected','promoted_memory','dependency_selected','skills',jsonb_build_object('state','not_available','reason','no canonical skills store'), 'architecture_constraints',jsonb_build_object('state','covered_by_doctrine_and_active_rules','reason','canonical architecture constraints are represented by the selected doctrine/rule revisions'), 'prior_failures',jsonb_build_object('state','dependency_selected','reason','selected canonical defect refs are frozen body-free in the bundle'))));
  for item in select * from jsonb_array_elements(p_bundle->'items') loop
    ordinal := ordinal + 1;
    insert into ops.context_activation_item(binding_id,ordinal,artifact_kind,canonical_ref,revision,content_digest,scope_redaction,required,trigger_ref,consumer_ref,delivery_mode,representation_kind,freshness,selection_reason,selection_rank)
    values((select b.id from ops.context_activation_binding b where b.binding_id=new_id),ordinal,coalesce(item->>'artifact_kind',item->>'kind'),item->>'canonical_ref',item->>'revision',item->>'digest',coalesce(item->>'scope_redaction',item->>'redaction_class'),'true' = lower(coalesce(item->>'required','false')),coalesce(item->>'trigger_ref',item->>'trigger'),coalesce(item->>'consumer_ref',item->>'consumer'),coalesce(item->>'delivery_mode','reference_only'),coalesce(item->>'representation_kind',item->>'kind'),jsonb_build_object('state',coalesce(item->>'freshness','unknown')),coalesce(item->>'selection_reason','bounded-retrieval'),coalesce((item->>'selection_rank')::integer,ordinal));
  end loop;
  return query select new_id, p_bundle->>'bundle_digest', false;
end $$;

create or replace function ops.compile_context_bundle(p_work_request text, p_plan_ref text, p_tenant text)
returns jsonb language plpgsql stable security definer set search_path=ops,public,pg_temp as $$
declare work ops.work_request%rowtype; plan ops.sourced_work_request_plan%rowtype; body jsonb;
begin
  if coalesce(current_setting('carr.organization_tenant_id',true),'') <> p_tenant then
    raise exception 'context compilation tenant must match authenticated tenant context';
  end if;
  if p_tenant is null or p_tenant='' then raise exception 'context compiler requires authenticated tenant'; end if;
  select * into work from ops.work_request where ref=p_work_request and organization_tenant_id=p_tenant;
  select * into plan from ops.sourced_work_request_plan where work_request_id=work.id and plan_ref=p_plan_ref order by plan_version desc limit 1;
  if not found then raise exception 'context compiler plan not found'; end if;
  if work.id is null then raise exception 'context compiler Work Request not found'; end if;
  if jsonb_typeof(plan.preimage->'context_activation_items') <> 'array'
     or jsonb_array_length(plan.preimage->'context_activation_items') < 1 then
    raise exception 'context compiler accepted plan has no frozen canonical items';
  end if;
  body := ops.context_activation_bundle_from_items(
    p_tenant, work.ref, 'plan:runbook:' || plan.runbook_revision_id::text, 1,
    coalesce(plan.preimage->'context_activation'->>'base_plan_digest',plan.plan_hash),
    work.triaged_at, plan.preimage->'context_activation_items'
  );
  return body || jsonb_build_object('bundle_digest',ops.context_activation_bundle_digest(body));
end $$;

create or replace function ops.read_context_activation(p_work_request text, p_binding_id text)
returns jsonb language sql stable security definer set search_path=ops,public,pg_temp as $$
  -- This is the source-linked read spine consumed by Job Passport, Model
  -- Room, Observatory, and Hermes.  It deliberately reports canary evidence
  -- as unavailable until a receipt/evaluation is recorded; it does not
  -- manufacture a production posture from the feature mode alone.
  select jsonb_build_object(
    'binding',to_jsonb(b),
    'items',coalesce(jsonb_agg(to_jsonb(i) order by i.ordinal),'[]'::jsonb),
    'execution_envelopes',(select coalesce(jsonb_agg(jsonb_build_object(
      'envelope_digest',e.envelope_digest,'runtime_profile',e.runtime_profile,
      'execution_topology',e.execution_topology,'evaluation_plan',e.evaluation_plan,
      'configuration_digest',e.configuration_digest,'issued_at',e.issued_at,'expires_at',e.expires_at
    ) order by e.issued_at),'[]'::jsonb) from ops.execution_envelope_v1 e where e.activation_binding_id=b.id),
    'attempt_receipts',(select coalesce(jsonb_agg(jsonb_build_object(
      'attempt_id',r.attempt_id,'envelope_digest',r.envelope_digest,
      'knowledge_activation',r.receipt->'knowledge_activation','reliability',r.receipt->'reliability','created_at',r.created_at
    ) order by r.created_at),'[]'::jsonb) from ops.attempt_receipt r where r.activation_binding_id=b.id),
    'learning',(select coalesce(jsonb_agg(jsonb_build_object(
      'candidate_ref',p.candidate_ref,'case_ref',p.case_ref,
      'lifecycle',coalesce((select event_kind from ops.proposed_eval_candidate_event pe where pe.candidate_id=p.id order by pe.created_at desc,pe.id desc limit 1),p.lifecycle),
      'promotion_state',p.promotion_state,'provenance',p.provenance,
      'lane_ref',p.lane_ref,'risk_class',p.risk_class,'split_target',p.split_target,
      'source_digest',p.source_digest,'source_ref','attempt:'||r.attempt_id,
      'basis',p.basis,'created_at',p.created_at,
      'golden_membership',jsonb_build_object(
        'target_golden_set_ref',p.target_golden_set_ref,
        'ever_accepted',exists(select 1 from ops.accepted_eval_golden_membership gm where gm.candidate_id=p.id),
        'active',coalesce((select event_kind from ops.proposed_eval_candidate_event pe where pe.candidate_id=p.id order by pe.created_at desc,pe.id desc limit 1),'proposed')='accepted'
      )
    ) order by p.created_at,p.id),'[]'::jsonb) from ops.proposed_eval_candidate p join ops.attempt_receipt r on r.id=p.attempt_receipt_id where r.activation_binding_id=b.id),
    'evidence_register',jsonb_build_object(
      'work_request_ref',p_work_request,
      'source_ref','plan:'||b.plan_id::text,
      'source_digest',b.plan_hash,
      'admission_ref','activation:'||b.binding_id,
      'retrieval_evidence_ref','context-bundle:'||b.bundle_digest,
      'operator_surface','job-passport:context-activation',
      'telemetry_ref','observatory:activation-reliability:'||b.binding_id,
      'canary',jsonb_build_object('posture',b.mode,
        'evidence_availability',case when exists(select 1 from ops.attempt_receipt r where r.activation_binding_id=b.id) then 'recorded_redacted' else 'not_recorded' end,
        'evidence_ref',case when exists(select 1 from ops.attempt_receipt r where r.activation_binding_id=b.id) then 'attempt-receipt:'||b.binding_id else null end),
      'rollback_ref',coalesce((select recovery_ref from ops.sourced_work_request_plan where id=b.plan_id), 'not_configured'),
      'freshness',jsonb_build_object('state',case when b.expires_at < now() then 'stale' else 'fresh' end,'expires_at',b.expires_at),
      'items',coalesce(jsonb_agg(jsonb_build_object(
        'source_ref',i.canonical_ref,'source_digest',i.content_digest,
        'consumer',i.consumer_ref,'trigger',i.trigger_ref,
        'admission_ref','activation:'||b.binding_id,
        'retrieval_evidence_ref','context-bundle:'||b.bundle_digest,
        'enforcement',case when i.required then 'must-apply' else 'advisory-only' end,
        'operator_surface','job-passport:context-activation',
        'telemetry_ref','observatory:activation-reliability:'||b.binding_id,
        'canary_posture',b.mode,
        'rollback_ref',coalesce((select recovery_ref from ops.sourced_work_request_plan where id=b.plan_id), 'not_configured'),
        'freshness',i.freshness
      ) order by i.ordinal),'[]'::jsonb)
    )
  )
    from ops.context_activation_binding b left join ops.context_activation_item i on i.binding_id=b.id
   where b.organization_tenant_id=current_setting('carr.organization_tenant_id', true)
     and b.binding_id=p_binding_id and b.work_request_id=(select id from ops.work_request where ref=p_work_request and organization_tenant_id=current_setting('carr.organization_tenant_id', true))
   group by b.id;
$$;

-- Fresh-session retrieval resolves the frozen revision, never the current
-- mutable row.  This narrow first renderer supports canonical doctrine/rules;
-- other representations remain reference-only until their canonical renderer
-- is registered. A required unresolved item refuses the whole brief.
create or replace function ops.render_context_activation_for_brief(p_work_request text,p_binding_id text)
returns jsonb language plpgsql stable security definer set search_path=ops,public,pg_temp as $$
declare tenant text:=current_setting('carr.organization_tenant_id',true); item record; rendered jsonb:='[]'::jsonb; body text; actual_digest text;
begin
  if not exists (
    select 1
      from ops.context_activation_binding b
      join ops.work_request w on w.id=b.work_request_id
      join ops.sourced_work_request_plan p on p.id=b.plan_id
      join ops.sourced_work_request_plan_acceptance_receipt ar on ar.work_request_id=w.id and ar.plan_id=p.id
     where b.binding_id=p_binding_id and w.ref=p_work_request and b.organization_tenant_id=tenant
       and b.expires_at>=now() and w.version=b.work_request_version
       and p.work_request_id=w.id and p.plan_hash=b.plan_hash
       and ar.plan_hash=b.plan_hash and ar.result_version=w.version
  ) then raise exception 'context activation binding is expired, stale, or no longer the exact accepted plan'; end if;
  for item in select i.* from ops.context_activation_item i join ops.context_activation_binding b on b.id=i.binding_id join ops.work_request w on w.id=b.work_request_id where b.binding_id=p_binding_id and w.ref=p_work_request and b.organization_tenant_id=tenant order by i.ordinal loop
    body:=null; actual_digest:=null;
    if item.artifact_kind='doctrine' then
      select r.plain_text,'sha256:'||r.content_hash into body,actual_digest from public.doctrine_revision r where ('revision:'||r.id::text)=item.revision;
    elsif item.artifact_kind='rule' then
      select r.statement,'sha256:'||encode(public.digest(r.statement,'sha256'),'hex') into body,actual_digest from public.rule r where ('rule:'||r.id::text)=item.canonical_ref;
    elsif item.artifact_kind='decision' then
      select jsonb_build_object('title',d.title,'human_quote',d.human_quote,'agent_rationale',d.agent_rationale,'provenance',d.provenance,'entry_date',d.entry_date)::text,
        'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(jsonb_build_object('title',d.title,'human_quote',d.human_quote,'agent_rationale',d.agent_rationale,'provenance',d.provenance,'entry_date',d.entry_date)),'sha256'),'hex')
        into body,actual_digest from public.v_decision_entry d where ('decision:'||d.decision_id::text)=item.canonical_ref and ('revision:'||d.event_id::text)=item.revision;
    elsif item.artifact_kind='memory' then
      select m.statement,'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(jsonb_build_object('id',m.id,'version',m.version,'kind',m.kind,'statement',m.statement,'context',m.context,'scope',m.scope,'organization_tenant_id',m.organization_tenant_id,'work_request_id',m.work_request_id,'work_request_version',m.work_request_version,'plan_id',m.plan_id,'status',m.status,'confidence',m.confidence)),'sha256'),'hex') into body,actual_digest
        from public.memory_item m where ('memory:'||m.id::text)=item.canonical_ref and ('revision:'||m.id::text||':v'||m.version::text)=item.revision;
    elsif item.artifact_kind='prior_failure' then
      select '[redacted prior-failure metadata]','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(jsonb_build_object('id',d.id,'occurred_on',d.occurred_on,'defect_class',d.defect_class,'claimed',d.claimed,'actual',d.actual,'source_unread',d.source_unread,'rule_violated',d.rule_violated,'detected_by',d.detected_by,'session_key',d.session_key,'cost_note',d.cost_note,'created_at',d.created_at,'created_by',d.created_by)),'sha256'),'hex') into body,actual_digest
        from public.defect d where ('defect:'||d.id::text)=item.canonical_ref and ('occurred:'||d.occurred_on::text)=item.revision;
    end if;
    if item.freshness->>'state' <> 'fresh' or body is null or actual_digest is distinct from item.content_digest then
      if item.required then raise exception 'required frozen context revision cannot render or is stale'; end if;
      rendered:=rendered||jsonb_build_array(jsonb_build_object('canonical_ref',item.canonical_ref,'delivery_mode','reference_only','state','unavailable'));
    elsif item.delivery_mode='reference_only' then
      rendered:=rendered||jsonb_build_array(jsonb_build_object('canonical_ref',item.canonical_ref,'revision',item.revision,'content_digest',item.content_digest,'delivery_mode','reference_only','state','reference_only'));
    else
      rendered:=rendered||jsonb_build_array(jsonb_build_object('canonical_ref',item.canonical_ref,'revision',item.revision,'content_digest',item.content_digest,'delivery_mode',item.delivery_mode,'state','rendered','content',body));
    end if;
  end loop;
  return rendered;
end $$;

create or replace function ops.context_activation_brief_assignment(p_work_request text,p_binding_id text)
returns text language plpgsql stable security definer set search_path=ops,public,pg_temp as $$
declare profile_key text;
begin
 select p.profile_key into profile_key from ops.context_activation_binding b join ops.work_request w on w.id=b.work_request_id
 join ops.sourced_work_request_plan plan on plan.id=b.plan_id
 join ops.sourced_work_request_plan_acceptance_receipt ar on ar.work_request_id=w.id and ar.plan_id=plan.id
 join ops.work_request_execution_assignment a on a.work_request_id=w.id join public.agent_profile p on p.id=a.profile_id
 join ops.execution_envelope_v1 e on e.activation_binding_id=b.id and e.expires_at>=now()
 where b.binding_id=p_binding_id and b.expires_at>=now() and w.ref=p_work_request and b.organization_tenant_id=current_setting('carr.organization_tenant_id',true)
   and w.version=b.work_request_version and plan.work_request_id=w.id and plan.plan_hash=b.plan_hash
   and ar.plan_hash=b.plan_hash and ar.result_version=w.version;
 if profile_key is null then raise exception 'context activation brief assignment is expired, stale, or no longer the exact accepted plan'; end if;
 return profile_key;
end $$;

create or replace function ops.assign_execution_profile(
  p_work_request text, p_profile_key text, p_environment text, p_policy_ref text, p_policy_digest text, p_idempotency_key uuid
) returns table(assignment_id uuid, replayed boolean)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare tenant text := current_setting('carr.organization_tenant_id', true); work ops.work_request%rowtype;
  profile agent_profile%rowtype; sponsor actor%rowtype; existing ops.work_request_execution_assignment%rowtype;
begin
  if session_user !~ '^carr_authority_' then raise exception 'execution profile assignment requires the authoritative policy gateway'; end if;
  select * into work from ops.work_request where ref=p_work_request and organization_tenant_id=tenant for share;
  if not found then raise exception 'execution profile assignment work request is not visible'; end if;
  select * into profile from agent_profile where profile_key=p_profile_key and status='active' and current_model is not null and current_desk is not null for share;
  if not found or p_environment not in ('local','rehearsal','staging','production') or p_policy_ref !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or p_policy_digest !~ '^sha256:[0-9a-f]{64}$' then
    raise exception 'execution profile assignment requires active profile and exact policy binding';
  end if;
  select * into sponsor from actor where slug=regexp_replace(session_user,'^carr_authority_','') and kind='human' and active;
  if not found then raise exception 'execution profile assignment cannot derive sponsoring human'; end if;
  select * into existing from ops.work_request_execution_assignment where work_request_id=work.id;
  if found then
    if existing.profile_id<>profile.id or existing.sponsoring_human_id<>sponsor.id or existing.environment<>p_environment or existing.policy_ref<>p_policy_ref or existing.policy_digest<>p_policy_digest then raise exception 'execution profile assignment conflicts with immutable existing lane'; end if;
    return query select existing.id,true; return;
  end if;
  insert into ops.work_request_execution_assignment(work_request_id,profile_id,sponsoring_human_id,environment,policy_ref,policy_digest,idempotency_key)
  values(work.id,profile.id,sponsor.id,p_environment,p_policy_ref,p_policy_digest,p_idempotency_key) returning id into assignment_id;
  return query select assignment_id,false;
end $$;

-- Narrow tenant-derived lookup for the receipt adapter.  Application roles
-- never regain raw SELECT on the immutable binding table merely to discover
-- a primary key and plan hash.
create or replace function ops.context_activation_receipt_binding(p_work_request text,p_binding_id text)
returns table(binding_pk uuid,plan_hash text) language sql stable security definer set search_path=ops,pg_temp as $$
  select b.id,b.plan_hash
    from ops.context_activation_binding b join ops.work_request w on w.id=b.work_request_id
    join ops.sourced_work_request_plan p on p.id=b.plan_id
    join ops.sourced_work_request_plan_acceptance_receipt ar on ar.work_request_id=w.id and ar.plan_id=p.id
   where b.binding_id=p_binding_id and w.ref=p_work_request
     and b.organization_tenant_id=current_setting('carr.organization_tenant_id',true)
     and w.organization_tenant_id=current_setting('carr.organization_tenant_id',true)
     and b.expires_at>=now() and w.version=b.work_request_version
     and p.work_request_id=w.id and p.plan_hash=b.plan_hash
     and ar.plan_hash=b.plan_hash and ar.result_version=w.version
$$;

create or replace function ops.issue_execution_envelope_v1(
  p_work_request text, p_binding_id text, p_idempotency_key uuid
) returns table(envelope_id uuid, envelope_digest text, envelope jsonb, replayed boolean)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare tenant text := current_setting('carr.organization_tenant_id', true); binding ops.context_activation_binding%rowtype;
  work ops.work_request%rowtype; plan ops.sourced_work_request_plan%rowtype; assignment ops.work_request_execution_assignment%rowtype; profile agent_profile%rowtype; sponsor actor%rowtype; existing ops.execution_envelope_v1%rowtype; issued timestamptz := now(); expires timestamptz;
  runtime jsonb; topology jsonb; evaluation jsonb; configuration text; body jsonb; digest_text text;
begin
  if coalesce(tenant,'')='' then raise exception 'execution envelope requires authenticated tenant context'; end if;
  select b.* into binding from ops.context_activation_binding b join ops.work_request w on w.id=b.work_request_id
   where b.binding_id=p_binding_id and b.organization_tenant_id=tenant and w.ref=p_work_request
     and b.expires_at>=now() and w.version=b.work_request_version for share;
  if not found then raise exception 'execution envelope binding is not visible to tenant'; end if;
  select * into work from ops.work_request where id=binding.work_request_id for share;
  select * into plan from ops.sourced_work_request_plan where id=binding.plan_id for share;
  if plan.id is null or plan.work_request_id<>work.id or plan.plan_hash<>binding.plan_hash
     or not exists (select 1 from ops.sourced_work_request_plan_acceptance_receipt ar where ar.work_request_id=work.id and ar.plan_id=plan.id and ar.plan_hash=binding.plan_hash and ar.result_version=work.version) then
    raise exception 'execution envelope binding is stale or no longer the exact accepted plan';
  end if;
  select * into assignment from ops.work_request_execution_assignment where work_request_id=work.id for share;
  if not found then raise exception 'execution envelope requires a preassigned server-owned profile lane'; end if;
  select * into profile from agent_profile where id=assignment.profile_id and status='active' and current_model is not null and current_desk is not null for share;
  select * into sponsor from actor where id=assignment.sponsoring_human_id and kind='human' and active for share;
  if profile.id is null or sponsor.id is null then raise exception 'execution envelope assignment has no active durable runtime profile/sponsor'; end if;
  select * into existing from ops.execution_envelope_v1 where organization_tenant_id=tenant and idempotency_key=p_idempotency_key for share;
  if found then
    if existing.work_request_id<>work.id or existing.activation_binding_id<>binding.id then raise exception 'execution envelope idempotency conflict'; end if;
    return query select existing.id,existing.envelope_digest,existing.envelope,true; return;
  end if;
  -- A binding has one server-issued envelope.  A later request cannot cause a
  -- timestamp/profile snapshot to fork the same governed attempt.
  select * into existing from ops.execution_envelope_v1
   where organization_tenant_id=tenant and activation_binding_id=binding.id for share;
  if found then
    return query select existing.id,existing.envelope_digest,existing.envelope,true; return;
  end if;
  -- These bounded versioned metadata refs are server-issued configuration,
  -- never caller authority/provider/model selections. They contain no secret
  -- values and are retained with the immutable envelope for audit.
  runtime := jsonb_build_object('ref','runtime-profile:'||profile.profile_key||':v'||profile.version::text,'profile_key',profile.profile_key,'profile_version',profile.version,'provider_id','provider:'||split_part(profile.current_model,'/',1),'model_id','model:'||profile.current_model,'desk',profile.current_desk,'policy_ref',assignment.policy_ref,'policy_digest',assignment.policy_digest,'modality','modality:text','reasoning_effort_ref','reasoning-effort:governed-default','sampling_profile_ref','sampling:governed-default','context_budget',8192,'cache_policy_ref','cache:governed-default','knowledge_cutoff_posture','knowledge-cutoff:provider-declared','tool_calling_mode','tool-calling:metadata-only');
  runtime := runtime || jsonb_build_object('digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(runtime),'sha256'),'hex'));
  topology := jsonb_build_object('ref','execution-topology:single-governed-attempt-v1','kind','single_agent_loop','harness_digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(jsonb_build_object('harness','postgres-governed-attempt-v1')),'sha256'),'hex'),'parallelism','sequential','code_model_step_refs',jsonb_build_array('step:model-governed'),'fallback_policy_ref','fallback:stop-and-escalate','stop_condition_refs',jsonb_build_array('stop:capability-expired','stop:critical-failure'),'context_refresh_policy_ref','context-refresh:bound-revisions-only','memory_policy_ref','memory:context-never-authority','sandbox_ref','sandbox:metadata-only','guardrail_ref','guardrail:governed-default','threat_model_ref','threat-model:governed-default');
  topology := topology || jsonb_build_object('digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(topology),'sha256'),'hex'));
  evaluation := jsonb_build_object('ref','evaluation-plan:independent-risk-v1','lane_ref','lane:governed-work','risk_class','R2','rubric_digest','sha256:'||encode(public.digest('rubric:independent-risk-v1','sha256'),'hex'),'case_set_digest',binding.bundle_digest,'evaluator_policy_digest','sha256:'||encode(public.digest('evaluator-policy:r2-v1','sha256'),'hex'),'evaluator_ref','evaluator:authority-independent-v1','rubric_ref','rubric:independent-risk-v1','evaluator_version','version:v1','evaluator_digest','sha256:'||encode(public.digest('evaluator:authority-independent-v1:version:v1','sha256'),'hex'),'required_rungs',jsonb_build_array('rung:smoke','rung:regression'),'required_deterministic_check_refs',jsonb_build_array('check:activation-binding','check:critical-security'),'critical_dimensions',jsonb_build_array('dimension:correctness','dimension:security'),'human_acceptance_required',true,'outcome_horizon_ref',case when assignment.environment='rehearsal' then 'outcome-horizon:synthetic-fixture-zero' else 'outcome-horizon:r2-seven-day' end,'outcome_horizon_not_before',to_char((issued + case when assignment.environment='rehearsal' then interval '0' else interval '7 days' end) at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),'requirements',jsonb_build_object('required_evaluator_kinds',jsonb_build_array('deterministic','judge','human_acceptance'),'minimum_held_out_case_count',1,'minimum_calibration_ref_count',1,'maximum_critical_failure_count',0,'maximum_critical_failure_rate',0,'confidence_posture','lower_bound_required','drift_tolerance','no_critical_regression','independent_review_required',true,'human_acceptance_required',true,'outcome_horizon_required',true));
  evaluation := evaluation || jsonb_build_object('digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(evaluation),'sha256'),'hex'));
  configuration := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(runtime||topology||evaluation),'sha256'),'hex');
  expires := least(binding.expires_at, issued + interval '1 hour');
  body := jsonb_build_object(
    'schema_version','execution-envelope.v1','envelope_id','env:'||binding.binding_id,
    'work_request_id',work.ref,
    'plan_revision',jsonb_build_object('id','plan:'||binding.plan_id::text,'revision',plan.plan_version,'digest',binding.plan_hash),
    'agent_session',jsonb_build_object('id','session:'||binding.binding_id,'lease_expires_at',to_char(expires at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')),
    'issued_at',to_char(issued at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),'expires_at',to_char(expires at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'state_binding',jsonb_build_object('state_version',work.version,'canonical_record_digest',binding.plan_hash,'accepted_resource_revisions','[]'::jsonb,'compare_and_swap_required',true),
    'phase_binding',jsonb_build_object('phase_id','phase:governed-execution','session_affinity','same_native_session_preferred','switch_conditions',jsonb_build_array('verified_checkpoint','phase_boundary'),'native_session_transfer','semantic_state_only'),
    'evaluation_context',jsonb_build_object('experiment_arm','same_pair_audited_state','auditor_mode','diverse_read_only_auditor','evaluation_kernel_ref',evaluation->>'ref','workflow_rubric_digest',evaluation->>'rubric_digest','case_set_digest',binding.bundle_digest),
    'request',jsonb_build_object('job_ref','job:'||work.ref,'input_digest',binding.bundle_digest,'data_class','metadata_only','allowed_actions','[]'::jsonb,'declared_expectations',jsonb_build_object('plan_step_refs','[]'::jsonb,'component_refs','[]'::jsonb,'component_dependencies','[]'::jsonb,'resource_refs','[]'::jsonb)),
    'server_binding',jsonb_build_object('identity',jsonb_build_object('organization_tenant_id',tenant,'sponsoring_human_id','human:'||sponsor.slug,'agent_principal_id','agent:'||profile.profile_key,'runtime_principal','runtime:'||profile.profile_key,'personal_brain_scope','none','personal_brain_version','none','personal_rule_count',0,'derived_by','server_identity_resolution','client_mutable',false),'authority',jsonb_build_object('environment',assignment.environment,'risk_class','R2','capability_profile','capability:metadata-only','capability_grant_ref','grant:'||assignment.id::text,'read_only',true,'derived_by','server_capability_resolution','client_mutable',false),'adapter',jsonb_build_object('surface','hermes_desktop','adapter_id','adapter:hermes-desktop','adapter_version','v1','harness_id','harness:postgres','harness_version','v1','provider_id','provider:'||split_part(profile.current_model,'/',1),'model_id','model:'||profile.current_model,'native_session_ref','native:profile-'||profile.profile_key,'configuration_fingerprint',configuration)),
    'handoff',jsonb_build_object('mode','original','replaces_agent_session_id',null,'capability_inherited',false,'checkpoint_ref',null,'native_session_transfer','semantic_state_only'),
    'activation_binding',jsonb_build_object('bundle_digest',binding.bundle_digest,'item_refs',(select coalesce(jsonb_agg(canonical_ref order by ordinal),'[]'::jsonb) from ops.context_activation_item where binding_id=binding.id),'mode',binding.mode,'retrieval_policy_version','v1'),
    'reliability_policy_binding',jsonb_build_object('policy_ref',assignment.policy_ref,'policy_digest',assignment.policy_digest,'risk_class','R2','mode',binding.mode),
    'context_activation_ref',binding.binding_id,'runtime_profile',runtime,'execution_topology',topology,'evaluation_plan',evaluation
  );
  digest_text := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(body),'sha256'),'hex');
  insert into ops.execution_envelope_v1(idempotency_key,organization_tenant_id,work_request_id,plan_hash,activation_binding_id,envelope_digest,envelope,runtime_profile,execution_topology,evaluation_plan,configuration_digest,issued_at,expires_at)
  values(p_idempotency_key,tenant,work.id,binding.plan_hash,binding.id,digest_text,body,runtime,topology,evaluation,configuration,issued,expires)
  returning id,ops.execution_envelope_v1.envelope_digest,ops.execution_envelope_v1.envelope into envelope_id,envelope_digest,envelope;
  return query select envelope_id,envelope_digest,envelope,false;
end $$;

create or replace function ops.record_attempt_receipt(
  p_work_request text, p_plan_hash text, p_envelope_digest text,
  p_activation_binding_id uuid, p_receipt jsonb, p_idempotency_key uuid
) returns table(attempt_receipt_id uuid, attempt_id text, replayed boolean)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare tenant text := current_setting('carr.organization_tenant_id', true); existing ops.attempt_receipt%rowtype; work_id uuid; envelope_id uuid;
begin
  if coalesce(tenant,'')='' then raise exception 'reliability record requires authenticated tenant context'; end if;
  select w.id into work_id from ops.work_request w
   join ops.context_activation_binding b on b.id=p_activation_binding_id and b.work_request_id=w.id
   join ops.sourced_work_request_plan p on p.id=b.plan_id
   join ops.sourced_work_request_plan_acceptance_receipt ar on ar.work_request_id=w.id and ar.plan_id=p.id
   where w.ref=p_work_request and w.organization_tenant_id=tenant and b.organization_tenant_id=tenant
     and b.expires_at>=now() and w.version=b.work_request_version and p.work_request_id=w.id
     and p.plan_hash=p_plan_hash and b.plan_hash=p_plan_hash
     and ar.plan_hash=p_plan_hash and ar.result_version=w.version;
  if work_id is null then raise exception 'reliability Work Request is not visible to tenant'; end if;
  if jsonb_typeof(p_receipt) <> 'object' or p_receipt->>'schema_version' <> 'attempt-receipt.v1'
     or p_receipt->>'envelope_digest' <> p_envelope_digest or coalesce(p_receipt->>'attempt_id','') = '' then
    raise exception 'exact attempt receipt v1/envelope binding is required';
  end if;
  select id into envelope_id from ops.execution_envelope_v1
   where organization_tenant_id=tenant and work_request_id=work_id and plan_hash=p_plan_hash
     and activation_binding_id=p_activation_binding_id and envelope_digest=p_envelope_digest;
  if envelope_id is null then raise exception 'attempt receipt requires exact server-issued execution envelope'; end if;
  select * into existing from ops.attempt_receipt where organization_tenant_id=tenant and idempotency_key=p_idempotency_key;
  if found then
    if existing.work_request_id<>work_id or existing.attempt_id<>p_receipt->>'attempt_id' or existing.envelope_digest<>p_envelope_digest or existing.receipt is distinct from p_receipt then raise exception 'attempt receipt idempotency conflict'; end if;
    return query select existing.id, existing.attempt_id, true; return;
  end if;
  insert into ops.attempt_receipt(idempotency_key,organization_tenant_id,work_request_id,plan_hash,envelope_digest,attempt_id,activation_binding_id,execution_envelope_id,receipt)
  values(p_idempotency_key,tenant,work_id,p_plan_hash,p_envelope_digest,p_receipt->>'attempt_id',p_activation_binding_id,envelope_id,p_receipt)
  returning id, ops.attempt_receipt.attempt_id into attempt_receipt_id, attempt_id;
  -- Observatory telemetry is generated from the admitted canonical receipt,
  -- never accepted from executor-supplied telemetry JSON.
  insert into ops.activation_reliability_telemetry(organization_tenant_id,attempt_receipt_id,signal_id,trigger_ref,consumer_ref,enforcement,owner_ref,remedy_ref,verification_ref,auto_clear,state)
  values(tenant,attempt_receipt_id,'telemetry:knowledge-activation','trigger:required-knowledge-closure','consumer:observatory','enforcement:block-reliability-closure','owner:execution-governance','remedy:resolve-required-activation','verification:canonical-receipt-binding',true,
    case when p_receipt->'knowledge_activation'->'closure'->>'state'='closed' then 'verified' else 'open' end);
  return query select attempt_receipt_id, attempt_id, false;
end $$;

-- The caller selects an already-admitted canonical fact; it never supplies a
-- candidate, root-cause, target set, split, or basis.  Those are derived from
-- the frozen receipt + its issued evaluation plan, so one correction/defect/
-- incident creates exactly one tenant-scoped, replay-safe proposal.
-- Authority-only evaluation admission.  The evaluator plan and its required
-- check/dimension binding come from the exact issued envelope, never an
-- executor label in AttemptReceipt.
create or replace function ops.attest_attempt_receipt_evaluation(
  p_attempt_id text, p_evaluator_kind text, p_check_ref text, p_dimension_refs jsonb,
  p_status text, p_independent boolean, p_evidence_refs jsonb, p_evaluation_metadata jsonb,
  p_outcome_feedback_ref text, p_outcome_feedback_hash text, p_idempotency_key uuid
) returns table(attestation_id uuid, replayed boolean)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare tenant text := current_setting('carr.organization_tenant_id',true); receipt_row ops.attempt_receipt%rowtype;
  envelope_row ops.execution_envelope_v1%rowtype; authority_actor actor%rowtype; existing ops.attempt_receipt_evaluation_attestation%rowtype;
  derived_horizon text; accepted_feedback boolean;
begin
  if session_user !~ '^carr_authority_' then raise exception 'evaluation attestation requires human authority'; end if;
  select * into authority_actor from actor where slug=regexp_replace(session_user,'^carr_authority_','') and kind='human' and active;
  select * into receipt_row from ops.attempt_receipt where organization_tenant_id=tenant and attempt_id=p_attempt_id for share;
  select * into envelope_row from ops.execution_envelope_v1 where id=receipt_row.execution_envelope_id and organization_tenant_id=tenant for share;
  if authority_actor.id is null or receipt_row.id is null or envelope_row.id is null
     or jsonb_typeof(p_dimension_refs)<>'array' or jsonb_array_length(p_dimension_refs)=0
     or jsonb_typeof(p_evidence_refs)<>'array' or jsonb_array_length(p_evidence_refs)=0
     or jsonb_typeof(p_evaluation_metadata)<>'object'
     or not (p_evaluation_metadata ?& array['evaluator_ref','rubric_ref','evaluator_version','evaluator_digest','confidence','held_out_case_count','calibration_refs','lower_bound_ref'])
     or exists (select 1 from jsonb_object_keys(p_evaluation_metadata) k where k <> all(array['evaluator_ref','rubric_ref','evaluator_version','evaluator_digest','confidence','held_out_case_count','calibration_refs','lower_bound_ref']))
     or p_evaluation_metadata->>'evaluator_ref' <> envelope_row.evaluation_plan->>'evaluator_ref'
     or p_evaluation_metadata->>'rubric_ref' <> envelope_row.evaluation_plan->>'rubric_ref'
     or p_evaluation_metadata->>'evaluator_version' <> envelope_row.evaluation_plan->>'evaluator_version'
     or p_evaluation_metadata->>'evaluator_digest' <> envelope_row.evaluation_plan->>'evaluator_digest'
     or p_evaluation_metadata->>'confidence' not in ('high','medium','low','unknown')
     or jsonb_typeof(p_evaluation_metadata->'held_out_case_count') <> 'number' or (p_evaluation_metadata->>'held_out_case_count')::integer < 0
     or jsonb_typeof(p_evaluation_metadata->'calibration_refs') <> 'array'
     or ops.attempt_receipt_contains_raw_content(p_evidence_refs) or ops.attempt_receipt_contains_raw_content(p_evaluation_metadata)
     or p_evaluator_kind not in ('deterministic','judge','human_acceptance','outcome_horizon')
     or p_status not in ('passed','failed','blocked','unknown','not_run','mature','immature')
     or p_dimension_refs is distinct from envelope_row.evaluation_plan->'critical_dimensions' then
    raise exception 'evaluation attestation lacks exact canonical binding';
  end if;
  if (p_evaluator_kind='deterministic' and (p_check_ref='' or not ((envelope_row.evaluation_plan->'required_deterministic_check_refs') ? p_check_ref) or p_status not in ('passed','failed','blocked','unknown','not_run')))
     or (p_evaluator_kind<>'deterministic' and p_check_ref<>'')
     or (p_evaluator_kind='judge' and p_status not in ('passed','failed','blocked','unknown','not_run'))
     or (p_evaluator_kind='human_acceptance' and p_status not in ('passed','failed','blocked','unknown','not_run'))
     or (p_evaluator_kind='outcome_horizon' and p_status not in ('mature','immature'))
     or (p_evaluator_kind='judge' and not p_independent)
     or (p_evaluator_kind<>'judge' and p_independent) then
    raise exception 'evaluation attestation kind/check/status/independence is invalid';
  end if;
  select exists (
    select 1 from ops.sourced_work_request_outcome_feedback f join ops.sourced_work_request_outcome_feedback_acceptance_receipt accepted on accepted.feedback_id=f.id
      where f.work_request_id=receipt_row.work_request_id and f.plan_id=(select plan_id from ops.context_activation_binding where id=receipt_row.activation_binding_id)
        and f.feedback_ref=p_outcome_feedback_ref and f.feedback_hash=p_outcome_feedback_hash
  ) into accepted_feedback;
  if p_evaluator_kind in ('human_acceptance','outcome_horizon') and (not accepted_feedback or p_outcome_feedback_ref !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or p_outcome_feedback_hash !~ '^sha256:[0-9a-f]{64}$') then
    raise exception 'authority attestation requires exact accepted Program 6 outcome feedback';
  elsif p_evaluator_kind not in ('human_acceptance','outcome_horizon') and (p_outcome_feedback_ref is not null or p_outcome_feedback_hash is not null) then
    raise exception 'only outcome-bound authority facts may name Program 6 feedback';
  end if;
  derived_horizon := case when clock_timestamp() >= (envelope_row.evaluation_plan->>'outcome_horizon_not_before')::timestamptz then 'mature' else 'immature' end;
  if p_evaluator_kind='outcome_horizon' and p_status <> derived_horizon then
    raise exception 'outcome horizon is derived from the issued policy and database clock';
  end if;
  select * into existing from ops.attempt_receipt_evaluation_attestation where idempotency_key=p_idempotency_key for share;
  if found then
    if existing.attempt_receipt_id<>receipt_row.id or existing.evaluator_kind<>p_evaluator_kind or existing.check_ref<>p_check_ref or existing.dimension_refs is distinct from p_dimension_refs or existing.status<>p_status or existing.independent<>p_independent or existing.evidence_refs is distinct from p_evidence_refs or existing.evaluator_ref<>p_evaluation_metadata->>'evaluator_ref' or existing.rubric_ref<>p_evaluation_metadata->>'rubric_ref' or existing.evaluator_version<>p_evaluation_metadata->>'evaluator_version' or existing.evaluator_digest<>p_evaluation_metadata->>'evaluator_digest' or existing.confidence<>p_evaluation_metadata->>'confidence' or existing.held_out_case_count<>(p_evaluation_metadata->>'held_out_case_count')::integer or existing.calibration_refs is distinct from p_evaluation_metadata->'calibration_refs' or existing.lower_bound_ref is distinct from nullif(p_evaluation_metadata->>'lower_bound_ref','') or existing.outcome_feedback_ref is distinct from p_outcome_feedback_ref or existing.outcome_feedback_hash is distinct from p_outcome_feedback_hash then raise exception 'evaluation attestation idempotency conflict'; end if;
    return query select existing.id,true; return;
  end if;
  insert into ops.attempt_receipt_evaluation_attestation(organization_tenant_id,attempt_receipt_id,evaluator_kind,check_ref,dimension_refs,evaluator_policy_digest,evaluator_ref,rubric_ref,evaluator_version,evaluator_digest,confidence,held_out_case_count,calibration_refs,lower_bound_ref,outcome_feedback_ref,outcome_feedback_hash,status,independent,evidence_refs,attested_by_actor_id,idempotency_key)
  values(tenant,receipt_row.id,p_evaluator_kind,p_check_ref,p_dimension_refs,envelope_row.evaluation_plan->>'evaluator_policy_digest',p_evaluation_metadata->>'evaluator_ref',p_evaluation_metadata->>'rubric_ref',p_evaluation_metadata->>'evaluator_version',p_evaluation_metadata->>'evaluator_digest',p_evaluation_metadata->>'confidence',(p_evaluation_metadata->>'held_out_case_count')::integer,p_evaluation_metadata->'calibration_refs',nullif(p_evaluation_metadata->>'lower_bound_ref',''),p_outcome_feedback_ref,p_outcome_feedback_hash,p_status,p_independent,p_evidence_refs,authority_actor.id,p_idempotency_key)
  returning id into attestation_id;
  return query select attestation_id,false;
end $$;

create or replace function ops.read_attempt_receipt_reliability(p_attempt_id text)
returns jsonb language plpgsql stable security definer set search_path=ops,public,pg_temp as $$
declare tenant text := current_setting('carr.organization_tenant_id',true); receipt_row ops.attempt_receipt%rowtype; envelope_row ops.execution_envelope_v1%rowtype;
  expected jsonb; have jsonb; final_state text; reasons jsonb; binding ops.context_activation_binding%rowtype; candidate_refs jsonb; lifecycle_value text; telemetry jsonb; authority_fact_count integer; learning_event_count integer; outcome_horizon_mature boolean;
begin
  select * into receipt_row from ops.attempt_receipt where organization_tenant_id=tenant and attempt_id=p_attempt_id;
  select * into envelope_row from ops.execution_envelope_v1 where id=receipt_row.execution_envelope_id and organization_tenant_id=tenant;
  select * into binding from ops.context_activation_binding where id=receipt_row.activation_binding_id and organization_tenant_id=tenant;
  if receipt_row.id is null or envelope_row.id is null then raise exception 'attempt reliability is not visible to tenant'; end if;
  select coalesce(jsonb_agg(value order by value),'[]'::jsonb) into expected from jsonb_array_elements_text(envelope_row.evaluation_plan->'required_deterministic_check_refs') value;
  -- Projection ordering is server-derived, not room arrival order.  Each
  -- append-only authority fact/event can only raise its count; the horizon
  -- bit can only advance from immature to mature for the frozen issued plan.
  select count(*)::integer into authority_fact_count from ops.attempt_receipt_evaluation_attestation where attempt_receipt_id=receipt_row.id;
  select count(*)::integer into learning_event_count from ops.proposed_eval_candidate_event e join ops.proposed_eval_candidate p on p.id=e.candidate_id where p.attempt_receipt_id=receipt_row.id;
  outcome_horizon_mature := clock_timestamp() >= (envelope_row.evaluation_plan->>'outcome_horizon_not_before')::timestamptz;
  select coalesce(jsonb_agg(a.check_ref order by a.check_ref),'[]'::jsonb) into have from ops.attempt_receipt_evaluation_attestation a where a.attempt_receipt_id=receipt_row.id and a.evaluator_kind='deterministic' and a.status='passed';
  if exists (select 1 from ops.attempt_receipt_evaluation_attestation a where a.attempt_receipt_id=receipt_row.id and a.evaluator_kind='deterministic' and a.status in ('failed','blocked')) then
    final_state := 'blocked'; reasons := jsonb_build_array('reason:critical_deterministic_or_evaluator_failure');
  elsif exists (select 1 from ops.attempt_receipt_evaluation_attestation a where a.attempt_receipt_id=receipt_row.id and a.evaluator_kind='judge' and a.independent and a.status in ('failed','blocked'))
     or exists (select 1 from ops.attempt_receipt_evaluation_attestation a where a.attempt_receipt_id=receipt_row.id and a.evaluator_kind='human_acceptance' and a.status in ('failed','blocked')) then
    final_state := 'blocked'; reasons := jsonb_build_array('reason:critical_authority_evaluator_or_human_rejection');
  elsif have is distinct from expected
     or not exists (select 1 from ops.attempt_receipt_evaluation_attestation a where a.attempt_receipt_id=receipt_row.id and a.evaluator_kind='judge' and a.status='passed' and a.independent and a.confidence in ('high','medium') and a.lower_bound_ref is not null and jsonb_array_length(a.calibration_refs) >= coalesce((envelope_row.evaluation_plan->'requirements'->>'minimum_calibration_ref_count')::integer,1) and a.held_out_case_count >= coalesce((envelope_row.evaluation_plan->'requirements'->>'minimum_held_out_case_count')::integer,1))
     or not exists (select 1 from ops.attempt_receipt_evaluation_attestation a where a.attempt_receipt_id=receipt_row.id and a.evaluator_kind='human_acceptance' and a.status='passed' and a.outcome_feedback_ref is not null and a.outcome_feedback_hash is not null)
     or not exists (select 1 from ops.attempt_receipt_evaluation_attestation a where a.attempt_receipt_id=receipt_row.id and a.evaluator_kind='outcome_horizon' and a.status='mature' and clock_timestamp() >= (envelope_row.evaluation_plan->>'outcome_horizon_not_before')::timestamptz) then
    final_state := 'insufficient_evidence'; reasons := jsonb_build_array('reason:canonical_evaluation_coverage_incomplete');
  else final_state := 'eligible_for_human_review'; reasons := '[]'::jsonb;
  end if;
  select coalesce(jsonb_agg('candidate:'||p.id::text order by p.created_at,p.id),'[]'::jsonb), coalesce((array_agg(e.event_kind order by e.created_at desc,e.id desc))[1],'none') into candidate_refs,lifecycle_value from ops.proposed_eval_candidate p left join lateral (select id,event_kind,created_at from ops.proposed_eval_candidate_event where candidate_id=p.id order by created_at desc,id desc limit 1) e on true where p.attempt_receipt_id=receipt_row.id;
  select coalesce(jsonb_agg(jsonb_build_object('signal_id',signal_id,'state',state,'trigger_ref',trigger_ref,'consumer_ref',consumer_ref,'enforcement',enforcement,'owner_ref',owner_ref,'remedy_ref',remedy_ref,'verification_ref',verification_ref,'auto_clear',auto_clear) order by signal_id),'[]'::jsonb) into telemetry from ops.activation_reliability_telemetry where attempt_receipt_id=receipt_row.id;
  return jsonb_build_object('canonical_binding',jsonb_build_object('work_request_id',(select ref from ops.work_request where id=receipt_row.work_request_id),'work_request_version',binding.work_request_version,'accepted_plan_digest',receipt_row.plan_hash,'envelope_digest',receipt_row.envelope_digest,'attempt_id',receipt_row.attempt_id,'activation_binding_ref',binding.binding_id),'canonical_revision',jsonb_build_object('authority_fact_count',authority_fact_count,'learning_event_count',learning_event_count,'outcome_horizon_mature',outcome_horizon_mature),'learning',jsonb_build_object('lifecycle',lifecycle_value,'candidate_refs',candidate_refs),'telemetry',telemetry,'reliability',jsonb_build_object('state',final_state,'reasons',reasons,'derived_by','canonical_authority_evaluation','outcome_horizon_state',case when outcome_horizon_mature then 'mature' else 'immature' end,'outcome_horizon_not_before',envelope_row.evaluation_plan->>'outcome_horizon_not_before'));
end $$;

create or replace function ops.propose_eval_candidate(
  p_attempt_id text, p_fact_event_ref text, p_idempotency_key uuid
) returns table(candidate_id uuid, candidate_ref text, lifecycle text, promotion_state text, replayed boolean)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare tenant text := current_setting('carr.organization_tenant_id', true); fact ops.attempt_receipt%rowtype;
  envelope_row ops.execution_envelope_v1%rowtype; fact_event jsonb; fact_count integer; fact_kind text;
  source_digest_value text; root_key text; candidate_ref_value text; case_ref_value text; target_set_ref text;
  lane_value text; risk_value text; split_value text; context_value jsonb; basis_value jsonb;
  row_out ops.proposed_eval_candidate%rowtype; previous_event ops.proposed_eval_candidate_event%rowtype;
begin
  if coalesce(tenant,'')='' or p_attempt_id !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_fact_event_ref !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' then
    raise exception 'eval proposal requires tenant-scoped canonical attempt and fact references';
  end if;
  select * into fact from ops.attempt_receipt where organization_tenant_id=tenant and attempt_id=p_attempt_id for share;
  if not found then raise exception 'eval candidate attempt receipt is not visible to tenant'; end if;
  select * into envelope_row from ops.execution_envelope_v1 where id=fact.execution_envelope_id and organization_tenant_id=tenant for share;
  if not found then raise exception 'eval candidate requires its exact issued evaluation plan'; end if;
  select count(*), (array_agg(item))[1], min(kind) into fact_count,fact_event,fact_kind from (
    select e.value as item, 'correction'::text as kind from jsonb_array_elements(fact.receipt->'reliability'->'corrections') e where e.value->>'event_ref'=p_fact_event_ref
    union all select e.value, 'defect'::text from jsonb_array_elements(fact.receipt->'reliability'->'defects') e where e.value->>'event_ref'=p_fact_event_ref
    union all select e.value, 'incident'::text from jsonb_array_elements(fact.receipt->'reliability'->'incidents') e where e.value->>'event_ref'=p_fact_event_ref
  ) canonical_fact;
  if fact_count<>1 or fact_event->>'kind'<>fact_kind
     or jsonb_typeof(fact_event->'evidence_refs')<>'array'
     or jsonb_array_length(fact_event->'evidence_refs')=0
     or ops.attempt_receipt_contains_raw_content(fact_event) then
    raise exception 'eval proposal fact must be one redacted canonical correction, defect, or incident';
  end if;
  -- `summary` is metadata classification, not a copied client/executor body.
  if coalesce(fact_event->>'summary','') !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' then
    raise exception 'eval proposal fact summary must be a bounded classification ref';
  end if;
  source_digest_value := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(fact_event),'sha256'),'hex');
  -- The event ref identifies this occurrence; summary is the bounded canonical
  -- taxonomy/root-cause classification.  Dedupe therefore means same root
  -- cause *and* same exact source fact, never merely the same event label.
  root_key := 'root:'||fact_kind||':'||lower(regexp_replace(fact_event->>'summary','[^A-Za-z0-9]+','-','g'));
  lane_value := coalesce(envelope_row.evaluation_plan->>'lane_ref','lane:governed-work');
  risk_value := coalesce(envelope_row.evaluation_plan->>'risk_class','R2');
  -- Issued policy currently has a single safe default.  It is deliberately
  -- server-owned and never passed by the executor or human proposer.
  split_value := coalesce(envelope_row.evaluation_plan->>'learning_split_target','development');
  if lane_value !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or risk_value !~ '^R[0-6]$'
     or split_value not in ('development','held_out','canary') then
    raise exception 'issued evaluation plan has no valid learning lane/risk/split';
  end if;
  candidate_ref_value := 'eval-candidate:'||substr(replace(source_digest_value,'sha256:',''),1,24);
  case_ref_value := 'case:proposal:'||substr(replace(source_digest_value,'sha256:',''),1,24);
  target_set_ref := 'golden:'||lower(regexp_replace(lane_value,'[^A-Za-z0-9]+','-','g'))||':'||lower(risk_value)||':'||split_value;
  context_value := jsonb_build_object('work_request_id','wr:'||fact.work_request_id::text,'case_digest',source_digest_value);
  basis_value := fact_event->'evidence_refs';
  select * into previous_event from ops.proposed_eval_candidate_event where idempotency_key=p_idempotency_key for share;
  if found then
    select * into row_out from ops.proposed_eval_candidate where id=previous_event.candidate_id and organization_tenant_id=tenant;
    if row_out.id is null or previous_event.event_kind<>'proposed' or row_out.attempt_receipt_id<>fact.id
       or row_out.source_digest<>source_digest_value or row_out.normalized_root_cause_key<>root_key then
      raise exception 'eval candidate proposal idempotency conflict';
    end if;
    return query select row_out.id,row_out.candidate_ref,
      coalesce((select pe.event_kind from ops.proposed_eval_candidate_event pe where pe.candidate_id=row_out.id order by pe.created_at desc,pe.id desc limit 1),'proposed'),
      row_out.promotion_state,true;
    return;
  end if;
  select * into row_out from ops.proposed_eval_candidate
    where organization_tenant_id=tenant and normalized_root_cause_key=root_key and source_digest=source_digest_value for share;
  if found then
    if row_out.attempt_receipt_id<>fact.id or row_out.case_ref<>case_ref_value or row_out.target_golden_set_ref<>target_set_ref
       or row_out.lane_ref<>lane_value or row_out.risk_class<>risk_value or row_out.split_target<>split_value
       or row_out.provenance<>fact_kind or row_out.context_binding is distinct from context_value or row_out.basis is distinct from basis_value then
      raise exception 'eval candidate source dedupe conflicts with immutable proposal';
    end if;
    return query select row_out.id,row_out.candidate_ref,
      coalesce((select pe.event_kind from ops.proposed_eval_candidate_event pe where pe.candidate_id=row_out.id order by pe.created_at desc,pe.id desc limit 1),'proposed'),
      row_out.promotion_state,true;
    return;
  end if;
  insert into ops.proposed_eval_candidate(organization_tenant_id,attempt_receipt_id,candidate_ref,case_ref,target_golden_set_ref,normalized_root_cause_key,source_digest,lane_ref,risk_class,provenance,split_target,context_binding,basis)
  values(tenant,fact.id,candidate_ref_value,case_ref_value,target_set_ref,root_key,source_digest_value,lane_value,risk_value,fact_kind,split_value,context_value,basis_value)
  returning * into row_out;
  insert into ops.proposed_eval_candidate_event(candidate_id,event_kind,decision_basis,idempotency_key)
  values(row_out.id,'proposed',jsonb_build_object('source','attempt-receipt','fact_event_ref',p_fact_event_ref,'source_digest',source_digest_value),p_idempotency_key);
  return query select row_out.id,row_out.candidate_ref,'proposed',row_out.promotion_state,false;
end $$;

create or replace function ops.transition_proposed_eval_candidate(
  p_work_request text, p_candidate_ref text, p_next_state text, p_decision_basis jsonb, p_idempotency_key uuid
) returns table(candidate_id uuid, lifecycle text, golden_member boolean)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare tenant text := current_setting('carr.organization_tenant_id', true); candidate ops.proposed_eval_candidate%rowtype; actor_row actor%rowtype;
  current_state text; replay_event ops.proposed_eval_candidate_event%rowtype;
begin
  if session_user !~ '^carr_authority_' then raise exception 'eval candidate transition requires human authority'; end if;
  select * into actor_row from actor where slug=regexp_replace(session_user,'^carr_authority_','') and kind='human' and active;
  select p.* into candidate from ops.proposed_eval_candidate p join ops.attempt_receipt r on r.id=p.attempt_receipt_id join ops.work_request w on w.id=r.work_request_id
   where p.organization_tenant_id=tenant and p.candidate_ref=p_candidate_ref and w.ref=p_work_request and w.organization_tenant_id=tenant for update of p;
  if actor_row.id is null or candidate.id is null or jsonb_typeof(p_decision_basis)<>'object' then raise exception 'eval candidate transition lacks visible authority/candidate/basis'; end if;
  if ops.attempt_receipt_contains_raw_content(p_decision_basis) then raise exception 'eval candidate decision basis must be metadata-only'; end if;
  select * into replay_event from ops.proposed_eval_candidate_event where idempotency_key=p_idempotency_key for share;
  if found then
    if replay_event.candidate_id<>candidate.id or replay_event.event_kind<>p_next_state or replay_event.decision_basis is distinct from p_decision_basis then raise exception 'eval candidate transition idempotency conflict'; end if;
    select pe.event_kind into current_state from ops.proposed_eval_candidate_event pe where pe.candidate_id=candidate.id order by pe.created_at desc,pe.id desc limit 1;
    return query select candidate.id,current_state,current_state='accepted'; return;
  end if;
  select pe.event_kind into current_state from ops.proposed_eval_candidate_event pe where pe.candidate_id=candidate.id order by pe.created_at desc,pe.id desc limit 1;
  if p_next_state not in ('triaged','accepted','retired')
     or (current_state='proposed' and p_next_state<>'triaged')
     or (current_state='triaged' and p_next_state<>'accepted')
     or (current_state='accepted' and p_next_state<>'retired')
     or current_state not in ('proposed','triaged','accepted') then
    raise exception 'invalid append-only eval candidate lifecycle transition';
  end if;
  insert into ops.proposed_eval_candidate_event(candidate_id,event_kind,decided_by_actor_id,decision_basis,idempotency_key)
  values(candidate.id,p_next_state,actor_row.id,p_decision_basis,p_idempotency_key);
  if p_next_state='accepted' then
    insert into ops.accepted_eval_golden_membership(candidate_id,target_golden_set_ref,accepted_by_actor_id)
    values(candidate.id,candidate.target_golden_set_ref,actor_row.id);
  end if;
  return query select candidate.id,p_next_state,(p_next_state='accepted');
end $$;

revoke all on function ops.activate_context_bundle(text,text,jsonb,uuid) from public;
grant execute on function ops.activate_context_bundle(text,text,jsonb,uuid) to carr_writer;
revoke all on function ops.compile_context_bundle(text,text,text) from public;
grant execute on function ops.compile_context_bundle(text,text,text) to carr_writer;
revoke all on function ops.read_context_activation(text,text) from public;
grant execute on function ops.read_context_activation(text,text) to carr_reader, carr_writer;
revoke all on function ops.render_context_activation_for_brief(text,text) from public;
grant execute on function ops.render_context_activation_for_brief(text,text) to carr_reader, carr_writer;
revoke all on function ops.context_activation_brief_assignment(text,text) from public;
grant execute on function ops.context_activation_brief_assignment(text,text) to carr_reader, carr_writer;
revoke all on function ops.context_activation_receipt_binding(text,text) from public;
grant execute on function ops.context_activation_receipt_binding(text,text) to carr_writer;
revoke all on function ops.assign_execution_profile(text,text,text,text,text,uuid) from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.assign_execution_profile(text,text,text,text,text,uuid) to carr_authority;
revoke all on function ops.issue_execution_envelope_v1(text,text,uuid) from public;
grant execute on function ops.issue_execution_envelope_v1(text,text,uuid) to carr_writer;
revoke all on function ops.record_attempt_receipt(text,text,text,uuid,jsonb,uuid) from public;
grant execute on function ops.record_attempt_receipt(text,text,text,uuid,jsonb,uuid) to carr_writer;
revoke all on function ops.attest_attempt_receipt_evaluation(text,text,text,jsonb,text,boolean,jsonb,jsonb,text,text,uuid) from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.attest_attempt_receipt_evaluation(text,text,text,jsonb,text,boolean,jsonb,jsonb,text,text,uuid) to carr_authority;
revoke all on function ops.read_attempt_receipt_reliability(text) from public;
grant execute on function ops.read_attempt_receipt_reliability(text) to carr_reader,carr_writer,carr_authority;
revoke all on function ops.propose_eval_candidate(text,text,uuid) from public;
grant execute on function ops.propose_eval_candidate(text,text,uuid) to carr_writer;
revoke all on function ops.transition_proposed_eval_candidate(text,text,text,jsonb,uuid) from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.transition_proposed_eval_candidate(text,text,text,jsonb,uuid) to carr_authority;

commit;
