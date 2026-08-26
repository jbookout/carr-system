-- 0301: make the release's declared schema provenance observable and live.
--
-- The checked-in manifest proves the source tree's exact migration ledger.
-- This migration is the database half: a Production approval may only name the
-- schema truth the current database can actually observe. Completed releases
-- remain append-only history; a later schema change is shown as a derived
-- mismatch instead of rewriting what the completed release declared.

begin;

create or replace view ops.v_release_schema_provenance as
with live as (
  select count(*)::integer as applied_count,
         max(filename) as highest_applied_migration,
         'sha256:' || encode(public.digest(
           coalesce(string_agg(
             convert_to(filename, 'UTF8') || decode('00', 'hex') ||
             convert_to(sha256, 'UTF8') || decode('0a', 'hex'),
             ''::bytea order by filename), ''::bytea),
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

comment on view ops.v_release_schema_provenance is
  'Derived release schema status. Declarations remain historical; the live '
  'comparison reads public.schema_migrations and exposes exact evidence.';

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
    select count(*)::integer, max(filename),
           'sha256:' || encode(public.digest(
             coalesce(string_agg(
               convert_to(filename, 'UTF8') || decode('00', 'hex') ||
               convert_to(sha256, 'UTF8') || decode('0a', 'hex'),
               ''::bytea order by filename), ''::bytea),
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

comment on function ops.release_schema_declaration_matches_live() is
  'Production approval gate: declared highest migration, count, and digest '
  'must equal the live public.schema_migrations truth in this database.';

drop trigger if exists release_schema_declaration_matches_live on ops.release;
create trigger release_schema_declaration_matches_live
before insert or update of state, environment, schema_highest_migration,
  schema_applied_count, schema_ledger_sha256 on ops.release
for each row execute function ops.release_schema_declaration_matches_live();

create or replace function ops.completed_release_append_only()
returns trigger
language plpgsql
as $$
begin
  if old.state = 'complete' then
    raise exception 'completed release % is append-only history', old.release_key;
  end if;
  return new;
end
$$;

comment on function ops.completed_release_append_only() is
  'A completed release is immutable history; derived schema mismatch evidence '
  'must not rewrite its declaration.';

drop trigger if exists release_completed_append_only_update on ops.release;
create trigger release_completed_append_only_update
before update on ops.release
for each row execute function ops.completed_release_append_only();

create or replace function ops.completed_release_delete_refused()
returns trigger
language plpgsql
as $$
begin
  if old.state = 'complete' then
    raise exception 'completed release % is append-only history', old.release_key;
  end if;
  return old;
end
$$;

drop trigger if exists release_completed_append_only_delete on ops.release;
create trigger release_completed_append_only_delete
before delete on ops.release
for each row execute function ops.completed_release_delete_refused();

grant select on ops.v_release_schema_provenance to carr_reader;

do $$
begin
  if to_regclass('ops.v_release_schema_provenance') is null then
    raise exception '0301 FAILED: release schema provenance view was not created';
  end if;
  if to_regprocedure('ops.release_schema_declaration_matches_live()') is null then
    raise exception '0301 FAILED: Production schema declaration gate was not created';
  end if;
  if to_regprocedure('ops.completed_release_append_only()') is null
     or to_regprocedure('ops.completed_release_delete_refused()') is null then
    raise exception '0301 FAILED: completed release append-only guards were not created';
  end if;
end
$$;

commit;
