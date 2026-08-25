-- 0302: make the release schema-ledger digest independent of database locale.
--
-- 0301 was applied successfully to isolated staging before review found that
-- its ORDER BY inherited the database collation.  Python's manifest builder
-- orders filenames bytewise, so the database side must use the C collation to
-- keep the exact digest portable.  0301 is applied history and is not edited.

begin;

create or replace view ops.v_release_schema_provenance as
with live as (
  select count(*)::integer as applied_count,
         (max(filename collate "C") collate "default")
           as highest_applied_migration,
         'sha256:' || encode(public.digest(
           coalesce(string_agg(
             convert_to(filename, 'UTF8') || decode('00', 'hex') ||
             convert_to(sha256, 'UTF8') || decode('0a', 'hex'),
             ''::bytea order by filename collate "C"), ''::bytea),
           'sha256'), 'hex') as ledger_sha256
    from public.schema_migrations
)
select
  r.id as release_id,
  r.release_key,
  r.environment,
  r.state,
  r.schema_highest_migration as declared_schema_highest_migration,
  r.schema_applied_count as declared_schema_applied_count,
  live.highest_applied_migration as live_schema_highest_migration,
  live.applied_count as live_schema_applied_count,
  live.ledger_sha256 as live_schema_ledger_sha256,
  (
    r.schema_highest_migration is not null
    and r.schema_applied_count is not null
    and r.schema_ledger_sha256 is not null
    and r.schema_highest_migration = live.highest_applied_migration
    and r.schema_applied_count = live.applied_count
    and r.schema_ledger_sha256 = live.ledger_sha256
  ) as schema_declaration_matches_live,
  case
    when r.schema_highest_migration is null
      or r.schema_applied_count is null
      or r.schema_ledger_sha256 is null
      or live.highest_applied_migration is null
      or live.ledger_sha256 is null
      then 'unknown'
    when r.schema_highest_migration = live.highest_applied_migration
      and r.schema_applied_count = live.applied_count
      and r.schema_ledger_sha256 = live.ledger_sha256
      then 'match'
    else 'mismatch'
  end as schema_status,
  jsonb_build_object(
    'source', 'public.schema_migrations',
    'declared', jsonb_build_object(
      'highest_applied_migration', r.schema_highest_migration,
      'applied_count', r.schema_applied_count,
      'ledger_sha256', r.schema_ledger_sha256
    ),
    'live', jsonb_build_object(
      'highest_applied_migration', live.highest_applied_migration,
      'applied_count', live.applied_count,
      'ledger_sha256', live.ledger_sha256
    )
  ) as schema_evidence
from ops.release r
cross join live;

create or replace function ops.release_schema_declaration_matches_live()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, ops, public
as $$
declare
  live_count integer;
  live_highest text;
  live_digest text;
begin
  if new.environment = 'production' and new.state = 'approved' then
    select count(*)::integer, max(filename collate "C"),
           'sha256:' || encode(public.digest(
             coalesce(string_agg(
               convert_to(filename, 'UTF8') || decode('00', 'hex') ||
               convert_to(sha256, 'UTF8') || decode('0a', 'hex'),
               ''::bytea order by filename collate "C"), ''::bytea),
             'sha256'), 'hex')
      into live_count, live_highest, live_digest
      from public.schema_migrations;

    if new.schema_applied_count is null
       or new.schema_highest_migration is distinct from live_highest
       or new.schema_applied_count <> live_count
       or new.schema_ledger_sha256 is distinct from live_digest then
      raise exception
        'Production release % schema declaration does not match live ops schema truth',
        new.release_key
        using detail = jsonb_build_object(
          'release_key', new.release_key,
          'declared_schema_highest_migration', new.schema_highest_migration,
          'declared_schema_applied_count', new.schema_applied_count,
          'declared_schema_ledger_sha256', new.schema_ledger_sha256,
          'live_schema_highest_migration', live_highest,
          'live_schema_applied_count', live_count,
          'live_schema_ledger_sha256', live_digest,
          'evidence_source', 'public.schema_migrations'
        )::text;
    end if;
  end if;
  return new;
end
$$;

comment on view ops.v_release_schema_provenance is
  'Derived release schema status using a locale-stable exact ledger digest. '
  'Declarations remain historical; live evidence reads schema_migrations.';

comment on function ops.release_schema_declaration_matches_live() is
  'Production approval gate: declared highest migration, count, and '
  'locale-stable exact digest must equal live schema_migrations truth.';

do $$
begin
  if position('COLLATE "C"' in
       pg_get_viewdef('ops.v_release_schema_provenance'::regclass, true)) = 0 then
    raise exception '0302 FAILED: provenance view is not C-collated';
  end if;
  if position('collate "C"' in
       pg_get_functiondef('ops.release_schema_declaration_matches_live()'::regprocedure)) = 0 then
    raise exception '0302 FAILED: approval gate is not C-collated';
  end if;
end
$$;

commit;
