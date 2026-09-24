-- 0591: V5-J303 client-shared Tours -- one explicit, default-deny client
-- field allowlist.
--
-- Joe's ruling (decision 4ab3933e, 2026-09-24): a client sees exactly what is
-- on today's Tour PDF -- property name, address, suite, space type, size,
-- asking economics, availability and parking. Notes, owner contacts, access
-- notes and every other field stay internal.
--
-- Before this migration the only list was the fact-shape list in
-- ops.tour_public_value_safe and the tour_public_projection_fact check
-- constraint, which also admit access, photos, floor_plan,
-- source_attribution, as_of and caveat. That list says which values are
-- SAFE TO STORE; it was never a statement of what a client may SEE.
--
-- What this adds:
--   1. ops.tour_client_field_keys() -- the allowlist, as one literal array.
--      A field is internal until it is added here (and to
--      CLIENT_TOUR_FIELD_KEYS in mcp-server/src/tour-operations-contract.js,
--      which test/tour-client-share-allowlist.test.mjs binds to this text).
--   2. A BEFORE INSERT trigger on ops.tour_public_projection_fact, so no path
--      -- the seal function or a direct insert -- can put a non-allowlisted
--      fact into a projection. It is named to fire before the existing
--      tour_projection_fact_guard, so the refusal names the real reason.
--   3. ops.read_tour_share_packet and ops.read_tour_packet_for_render select
--      only allowlisted facts and no longer name a per-property caveat
--      column. A projection sealed BEFORE this migration that holds an
--      access, caveat, photo or other now-internal fact therefore stops
--      showing it on the next read, with no data rewrite. The packet-level
--      'caveat' key stays an explicit null exactly as 0586 left it.
--
-- read_tour_packet_for_render no longer joins ops.tour for tour_name: the
-- column was selected but never emitted, and the (tenant, tour_id) foreign
-- key on ops.tour_public_projection already guarantees the tour exists, so
-- dropping the join changes no row.
--
-- The existing check constraint is left alone: internal facts may still be
-- recorded; they just cannot be sealed for a client. Signatures and grants of
-- the replaced functions are unchanged; only their bodies change.
--
-- 4. ops.tour_public_value_safe: one three-valued-logic fix, found by this
--    migration's own proof. For size and asking_economics the 0427 body ends
--    with `and not (<min is number> and <max is number> and min > max)`. When
--    the value has no min/max -- the ordinary {"value":4200,"unit":"SF"} --
--    jsonb_typeof(NULL) is NULL, the inner conjunction is NULL, `not NULL` is
--    NULL, and the whole CASE returns NULL: not safe. So no size or asking
--    economics fact without BOTH a numeric min and max could ever be sealed,
--    which silently kept two of the eight ruled client fields off every
--    share. The inner test is wrapped in coalesce(..., false); every other
--    branch is the 0427 body unchanged.

create or replace function ops.tour_public_value_safe(p_field_key text, p_value jsonb)
returns boolean language sql immutable as $$
  select case
    when p_field_key in ('display.name','display.address') then
      jsonb_typeof(p_value) = 'string' and btrim(p_value #>> '{}') <> '' and char_length(btrim(p_value #>> '{}')) <= 360
    when p_field_key in ('suite','property_type','availability','parking','access','source_attribution','as_of','caveat') then jsonb_typeof(p_value) = 'string'
    when p_field_key in ('size','asking_economics') then
      jsonb_typeof(p_value) = 'object'
      and (p_value ? 'value' or p_value ? 'min' or p_value ? 'max')
      and not exists (
        select 1 from jsonb_each(p_value) e
         where e.key not in ('value','unit','min','max','currency','period','label')
            or (e.key in ('value','min','max') and (
                 jsonb_typeof(e.value) not in ('string','number')
                 or (jsonb_typeof(e.value)='string' and (btrim(e.value #>> '{}')='' or char_length(btrim(e.value #>> '{}'))>120))))
            or (e.key in ('unit','currency','period','label') and (
                 jsonb_typeof(e.value)<>'string'
                 or btrim(e.value #>> '{}')='' or char_length(btrim(e.value #>> '{}'))>120))
      )
      and not coalesce(
        jsonb_typeof(p_value->'min')='number' and jsonb_typeof(p_value->'max')='number'
        and (p_value->>'min')::numeric > (p_value->>'max')::numeric,
        false
      )
    when p_field_key in ('photos','floor_plan') then jsonb_typeof(p_value) = 'array' and not exists (
      select 1 from jsonb_array_elements(p_value) item
       where jsonb_typeof(item) <> 'object'
          or not (item ? 'asset_ref')
          or (item->>'asset_ref') !~ '^asset:public:[A-Za-z0-9_-]+$'
          or char_length(item->>'asset_ref') not between 29 and 269
          or exists (select 1 from jsonb_each(item) e where e.key not in ('asset_ref','alt','caption','source') or jsonb_typeof(e.value) <> 'string')
    )
    else false end;
$$;

create or replace function ops.tour_client_field_keys()
returns text[] language sql immutable parallel safe as $$
  select array['display.name','display.address','suite','property_type','size','asking_economics','availability','parking']::text[]
$$;

create or replace function ops.tour_client_field_allowed(p_field_key text)
returns boolean language sql immutable parallel safe as $$
  select coalesce(p_field_key = any(ops.tour_client_field_keys()), false)
$$;

create or replace function ops.tour_projection_fact_client_allowlist_guard() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  if not ops.tour_client_field_allowed(new.display_field_key) then
    raise exception 'projection fact field is not client-allowlisted';
  end if;
  return new;
end $$;

revoke all on function ops.tour_projection_fact_client_allowlist_guard() from public;

drop trigger if exists tour_projection_fact_client_allowlist on ops.tour_public_projection_fact;
create trigger tour_projection_fact_client_allowlist
  before insert on ops.tour_public_projection_fact
  for each row execute function ops.tour_projection_fact_client_allowlist_guard();

create or replace function ops.read_tour_share_packet(p_session_digest text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with grant_row as (select (ops.tour_share_session_grant(p_session_digest,'view_packet')).*), projection as (
    select p.* from grant_row g join ops.tour_public_projection p on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id
    where p.status='approved' and exists(select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id and s.canonical_projection_digest=p.projection_digest)
      and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null
  ), stops as (
    select m.route_sequence,m.route_label,'property:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||m.property_id::text,'sha256'),'hex'),1,32) property_ref,
      max(a.value#>>'{}') filter(where f.display_field_key='display.name') name,
      max(a.value#>>'{}') filter(where f.display_field_key='display.address') address,
      max(a.value#>>'{}') filter(where f.display_field_key='suite') suite,
      max(a.value#>>'{}') filter(where f.display_field_key='property_type') property_type,
      (jsonb_agg(a.value order by f.id) filter(where f.display_field_key='size'))->0 size,
      (jsonb_agg(a.value order by f.id) filter(where f.display_field_key='asking_economics'))->0 asking_economics,
      max(a.value#>>'{}') filter(where f.display_field_key='availability') availability,
      max(a.value#>>'{}') filter(where f.display_field_key='parking') parking
    from projection p join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version
    join ops.tour_public_projection_fact f on f.organization_tenant_id=p.organization_tenant_id and f.projection_id=p.id and f.property_id=m.property_id
      and ops.tour_client_field_allowed(f.display_field_key)
    join ops.tour_field_assertion a on a.organization_tenant_id=f.organization_tenant_id and a.id=f.field_assertion_id and ops.tour_public_value_safe(a.field_key,a.value)
    group by p.organization_tenant_id,p.id,m.property_id,m.route_sequence,m.route_label
  ) select jsonb_build_object('as_of',p.as_of,'caveat',null,'stops',coalesce((select jsonb_agg(to_jsonb(stops) order by route_sequence) from stops),'[]'::jsonb)) from projection p;
$$;

create or replace function ops.read_tour_packet_for_render(p_tenant text,p_projection_id uuid,p_actor_id text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with projection as (
    select p.* from ops.tour_public_projection p
    where p.organization_tenant_id=p_tenant and p.id=p_projection_id and nullif(btrim(p_actor_id),'') is not null and p.status='approved'
      and exists(select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id and s.projection_id=p.id and s.canonical_projection_digest=p.projection_digest)
      and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null
  ), properties as (
    select m.route_sequence,m.route_label,'property:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||m.property_id::text,'sha256'),'hex'),1,32) property_ref,
      max(a.value#>>'{}') filter(where f.display_field_key='display.name') name,
      max(a.value#>>'{}') filter(where f.display_field_key='display.address') address,
      max(a.value#>>'{}') filter(where f.display_field_key='suite') suite,
      max(a.value#>>'{}') filter(where f.display_field_key='property_type') property_type,
      (jsonb_agg(a.value order by f.id) filter(where f.display_field_key='size'))->0 size,
      (jsonb_agg(a.value order by f.id) filter(where f.display_field_key='asking_economics'))->0 asking_economics,
      max(a.value#>>'{}') filter(where f.display_field_key='availability') availability,
      max(a.value#>>'{}') filter(where f.display_field_key='parking') parking
    from projection p join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version
    join ops.tour_public_projection_fact f on f.organization_tenant_id=p.organization_tenant_id and f.projection_id=p.id and f.property_id=m.property_id
      and ops.tour_client_field_allowed(f.display_field_key)
    join ops.tour_field_assertion a on a.organization_tenant_id=f.organization_tenant_id and a.id=f.field_assertion_id and ops.tour_public_value_safe(a.field_key,a.value)
    group by p.organization_tenant_id,p.id,m.property_id,m.route_sequence,m.route_label
  ) select jsonb_build_object(
    'projection_digest',p.projection_digest,
    'packet',jsonb_build_object('as_of',p.as_of,'caveat',null,'properties',coalesce((select jsonb_agg(to_jsonb(properties) order by route_sequence) from properties),'[]'::jsonb))
  ) from projection p;
$$;
