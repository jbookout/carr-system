-- 0580_resource_observation.sql
--
-- DoctorCRE V5-UX-C02 (Resource dashboard and metering read contract) and
-- V5-UX-C06 (Local compute capacity and model-route visibility): the store
-- and the one write door those two UI slices need.
--
-- WHY THIS EXISTS. C02's included_scope asks for a "provider/account/project/
-- product/period/as-of quantity, allowance, policy, estimate and charge
-- projection" that "use[s] absent/ineligible/stale states until actual
-- collector evidence exists" -- no fabricated telemetry, ever. C06 adds one
-- more requirement on top: local-host capacity must carry MEASURED and
-- CONFIGURED as two separate fields, never collapsed into one number, and an
-- absent sensor reads back null with a reason, never a zero.
--
-- SCOPE. One append-only observation table (ops.resource_observation), one
-- SECURITY DEFINER write door (ops.record_resource_observation) for the local,
-- credential-less collector (tools/resource-collector.py, called through
-- ./run.sh call record-resource-observation -- see that script's header for
-- why it carries no credential), and one read projection
-- (ops.read_resource_dashboard) that always returns a row for every provider
-- this contract names -- neon, github, cloudflare, local_compute, model_route
-- -- even when no collector for that provider exists yet. A provider with no
-- observation reads back state='unconfigured' (external providers, C03-C05
-- not yet built) or state='collector_absent' (local providers, no observation
-- received), with an explicit reason. Nothing here fabricates a number for
-- Neon, GitHub or Cloudflare; C03-C05 each add their own collector later and
-- this door does not change when they land.
--
-- No SECURITY DEFINER on the read side: ops.read_resource_dashboard only ever
-- SELECTs ops.resource_observation, and carr_reader is granted SELECT on that
-- table directly below, so the read function needs no elevated privilege.
--
-- No explicit transaction control: from 0339 onward tools/migrate.py runs
-- each migration inside its own single transaction.

create table if not exists ops.resource_observation (
  id uuid primary key default gen_random_uuid(),
  provider text not null check (provider in ('neon','github','cloudflare','local_compute','model_route')),
  account text,
  project text,
  product text,
  period text,
  as_of timestamptz,
  quantity numeric,
  quantity_unit text,
  allowance numeric,
  policy jsonb,
  estimate numeric,
  charge numeric,
  -- C06 checkable_done: "Source contract distinguishes measured capacity from
  -- configured capacity." Two independent, independently-nullable fields --
  -- never one column collapsing both, and never a substituted zero for a
  -- sensor that did not answer.
  measured_capacity jsonb,
  configured_capacity jsonb,
  model_route jsonb,
  state text not null check (state in ('ok','stale','unconfigured','collector_absent','host_offline')),
  reason text,
  source text not null check (btrim(source) <> ''),
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);

create index if not exists resource_observation_provider_observed_at_idx
  on ops.resource_observation (provider, observed_at desc);

comment on table ops.resource_observation is
  'DoctorCRE V5-UX-C02/C06: one row per collector observation. Append-only; the read door always projects the latest row per provider. No delete, no update -- a stale observation is superseded by a newer row, never edited in place.';

revoke all on table ops.resource_observation from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.resource_observation to carr_reader;

-- Private receipt: idempotency_key compare-and-swap for the write door, same
-- shape as every other receipt table in this system (e.g.
-- ops.work_request_joe_answer_receipt, 0575).
create table if not exists ops.resource_observation_receipt (
  id uuid primary key default gen_random_uuid(),
  resource_observation_id uuid not null references ops.resource_observation(id),
  idempotency_key uuid not null unique,
  request_digest text not null check (request_digest ~ '^sha256:[0-9a-f]{64}$'),
  recorded_by_actor_slug text,
  created_at timestamptz not null default now()
);

comment on table ops.resource_observation_receipt is
  'Private idempotency receipt for ops.record_resource_observation. Not a dispatch or execution record.';

revoke all on table ops.resource_observation_receipt from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.resource_observation_receipt to carr_reader;

create or replace function ops.record_resource_observation(
  p_provider text,
  p_account text,
  p_project text,
  p_product text,
  p_period text,
  p_as_of timestamptz,
  p_quantity numeric,
  p_quantity_unit text,
  p_allowance numeric,
  p_policy jsonb,
  p_estimate numeric,
  p_charge numeric,
  p_measured_capacity jsonb,
  p_configured_capacity jsonb,
  p_model_route jsonb,
  p_state text,
  p_reason text,
  p_source text,
  p_observed_at timestamptz,
  p_idempotency_key uuid,
  p_actor_slug text
)
returns table (
  id uuid,
  provider text,
  state text,
  reason text,
  observed_at timestamptz,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_digest text;
  v_existing ops.resource_observation_receipt%rowtype;
  v_row ops.resource_observation%rowtype;
begin
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  if p_provider not in ('neon','github','cloudflare','local_compute','model_route') then
    raise exception 'resource_observation_provider_invalid';
  end if;
  if p_state not in ('ok','stale','unconfigured','collector_absent','host_offline') then
    raise exception 'resource_observation_state_invalid';
  end if;
  if p_source is null or btrim(p_source) = '' then
    raise exception 'resource_observation_source_required';
  end if;
  if p_observed_at is null then
    raise exception 'resource_observation_observed_at_required';
  end if;

  v_digest := 'sha256:' || encode(digest(
    coalesce(p_provider,'') || '|' || coalesce(p_account,'') || '|' || coalesce(p_project,'') || '|' ||
    coalesce(p_product,'') || '|' || coalesce(p_period,'') || '|' || coalesce(p_as_of::text,'') || '|' ||
    coalesce(p_quantity::text,'') || '|' || coalesce(p_quantity_unit,'') || '|' || coalesce(p_allowance::text,'') || '|' ||
    coalesce(p_policy::text,'') || '|' || coalesce(p_estimate::text,'') || '|' || coalesce(p_charge::text,'') || '|' ||
    coalesce(p_measured_capacity::text,'') || '|' || coalesce(p_configured_capacity::text,'') || '|' ||
    coalesce(p_model_route::text,'') || '|' || coalesce(p_state,'') || '|' || coalesce(p_reason,'') || '|' ||
    coalesce(p_source,'') || '|' || coalesce(p_observed_at::text,''), 'sha256'), 'hex');

  select * into v_existing from ops.resource_observation_receipt r where r.idempotency_key = p_idempotency_key;
  if found then
    if v_existing.request_digest <> v_digest then
      raise exception 'resource_observation_key_reuse';
    end if;
    select * into v_row from ops.resource_observation o where o.id = v_existing.resource_observation_id;
    return query select v_row.id, v_row.provider, v_row.state, v_row.reason, v_row.observed_at, true;
    return;
  end if;

  insert into ops.resource_observation (
    provider, account, project, product, period, as_of, quantity, quantity_unit, allowance,
    policy, estimate, charge, measured_capacity, configured_capacity, model_route, state, reason,
    source, observed_at
  ) values (
    p_provider, p_account, p_project, p_product, p_period, p_as_of, p_quantity, p_quantity_unit, p_allowance,
    p_policy, p_estimate, p_charge, p_measured_capacity, p_configured_capacity, p_model_route, p_state, p_reason,
    p_source, p_observed_at
  ) returning * into v_row;

  insert into ops.resource_observation_receipt (
    resource_observation_id, idempotency_key, request_digest, recorded_by_actor_slug
  ) values (v_row.id, p_idempotency_key, v_digest, p_actor_slug);

  return query select v_row.id, v_row.provider, v_row.state, v_row.reason, v_row.observed_at, false;
end;
$$;

comment on function ops.record_resource_observation is
  'DoctorCRE V5-UX-C02/C06 write door: append one collector observation for one provider. Idempotent on p_idempotency_key. No delete, no update -- a superseded observation is a newer row, not an edit.';

revoke all on function ops.record_resource_observation(text,text,text,text,text,timestamp with time zone,numeric,text,numeric,jsonb,numeric,numeric,jsonb,jsonb,jsonb,text,text,text,timestamp with time zone,uuid,text) from public;
grant execute on function ops.record_resource_observation(text,text,text,text,text,timestamp with time zone,numeric,text,numeric,jsonb,numeric,numeric,jsonb,jsonb,jsonb,text,text,text,timestamp with time zone,uuid,text) to carr_writer;

-- Canonical provider list this contract owns. C03-C05 add their own
-- collectors for neon/github/cloudflare without touching this list or this
-- function; a provider not yet backed by any observation always projects
-- here rather than being silently absent from the payload.
create or replace function ops.read_resource_dashboard()
returns jsonb
language sql
stable
set search_path = pg_catalog, ops
as $$
  with providers(provider) as (
    values ('neon'), ('github'), ('cloudflare'), ('local_compute'), ('model_route')
  ),
  latest as (
    select distinct on (o.provider) o.*
    from ops.resource_observation o
    order by o.provider, o.observed_at desc, o.created_at desc
  )
  select jsonb_build_object(
    'schema', 'doctorcre-resource-dashboard.v1',
    'generated_at', now(),
    'providers', coalesce(jsonb_agg(
      jsonb_build_object(
        'provider', p.provider,
        'account', l.account,
        'project', l.project,
        'product', l.product,
        'period', l.period,
        'as_of', l.as_of,
        'quantity', l.quantity,
        'quantity_unit', l.quantity_unit,
        'allowance', l.allowance,
        'policy', l.policy,
        'estimate', l.estimate,
        'charge', l.charge,
        'measured_capacity', l.measured_capacity,
        'configured_capacity', l.configured_capacity,
        'model_route', l.model_route,
        'state', coalesce(l.state,
          case when p.provider in ('neon','github','cloudflare') then 'unconfigured' else 'collector_absent' end),
        'reason', coalesce(l.reason,
          case when p.provider in ('neon','github','cloudflare')
            then 'no collector configured for this provider yet (V5-UX-C03/C04/C05 not built)'
            else 'no collector observation received yet' end),
        'source', l.source,
        'observed_at', l.observed_at
      ) order by p.provider
    ), '[]'::jsonb)
  )
  from providers p
  left join latest l on l.provider = p.provider;
$$;

comment on function ops.read_resource_dashboard is
  'DoctorCRE V5-UX-C02/C06 read door: the full provider matrix (neon, github, cloudflare, local_compute, model_route), one row each, always present. A provider with no observation reads back unconfigured/collector_absent with an explicit reason; nothing is fabricated.';

revoke all on function ops.read_resource_dashboard() from public;
grant execute on function ops.read_resource_dashboard() to carr_reader;
