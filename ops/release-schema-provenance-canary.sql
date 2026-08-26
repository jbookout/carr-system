-- Transactional live canary for migration 0301.  It leaves no rows or objects.
-- Run through the scoped database tap after migration apply.

begin;

create temp table release_schema_provenance_probe (
  release_key text,
  environment text,
  state text,
  schema_highest_migration text,
  schema_applied_count integer,
  schema_ledger_sha256 text
) on commit drop;

create trigger release_schema_provenance_probe_gate
before insert or update on release_schema_provenance_probe
for each row execute function ops.release_schema_declaration_matches_live();

do $$
declare
  live_count integer;
  live_highest text;
  live_digest text;
begin
  select count(*)::integer, max(filename collate "C"),
         'sha256:' || encode(public.digest(
           coalesce(string_agg(
             convert_to(filename, 'UTF8') || decode('00', 'hex') ||
             convert_to(sha256, 'UTF8') || decode('0a', 'hex'),
             ''::bytea order by filename collate "C"), ''::bytea),
           'sha256'), 'hex')
    into live_count, live_highest, live_digest
    from public.schema_migrations;

  insert into release_schema_provenance_probe
    (release_key, environment, state, schema_highest_migration,
     schema_applied_count, schema_ledger_sha256)
  values
    ('ca06-match', 'production', 'approved', live_highest,
     live_count, live_digest);

  begin
    insert into release_schema_provenance_probe
      (release_key, environment, state, schema_highest_migration,
       schema_applied_count, schema_ledger_sha256)
    values
      ('ca06-mismatch', 'production', 'approved', live_highest,
       live_count, 'sha256:' || repeat('0', 64));
    raise exception 'CA-06 canary FAILED: digest mismatch was accepted';
  exception when others then
    if sqlerrm = 'CA-06 canary FAILED: digest mismatch was accepted' then
      raise;
    end if;
    if sqlerrm not like
       'Production release ca06-mismatch schema declaration does not match live ops schema truth' then
      raise;
    end if;
  end;

  begin
    insert into release_schema_provenance_probe
      (release_key, environment, state, schema_highest_migration,
       schema_applied_count, schema_ledger_sha256)
    values
      ('ca06-highest-mismatch', 'production', 'approved',
       live_highest || '.wrong', live_count, live_digest);
    raise exception 'CA-06 canary FAILED: highest-migration mismatch was accepted';
  exception when others then
    if sqlerrm = 'CA-06 canary FAILED: highest-migration mismatch was accepted' then
      raise;
    end if;
    if sqlerrm not like 'Production release ca06-highest-mismatch %' then
      raise;
    end if;
  end;

  begin
    insert into release_schema_provenance_probe
      (release_key, environment, state, schema_highest_migration,
       schema_applied_count, schema_ledger_sha256)
    values
      ('ca06-count-mismatch', 'production', 'approved', live_highest,
       live_count + 1, live_digest);
    raise exception 'CA-06 canary FAILED: applied-count mismatch was accepted';
  exception when others then
    if sqlerrm = 'CA-06 canary FAILED: applied-count mismatch was accepted' then
      raise;
    end if;
    if sqlerrm not like 'Production release ca06-count-mismatch %' then
      raise;
    end if;
  end;

  insert into release_schema_provenance_probe
    (release_key, environment, state, schema_highest_migration,
     schema_applied_count, schema_ledger_sha256)
  values
    ('ca06-update-mismatch', 'production', 'candidate', live_highest,
     live_count, 'sha256:' || repeat('0', 64));
  begin
    update release_schema_provenance_probe
       set state = 'approved'
     where release_key = 'ca06-update-mismatch';
    raise exception 'CA-06 canary FAILED: UPDATE approval mismatch was accepted';
  exception when others then
    if sqlerrm = 'CA-06 canary FAILED: UPDATE approval mismatch was accepted' then
      raise;
    end if;
    if sqlerrm not like 'Production release ca06-update-mismatch %' then
      raise;
    end if;
  end;

  if exists (select 1 from ops.release)
     and not exists (
       select 1
         from ops.v_release_schema_provenance
        where live_schema_highest_migration = live_highest
          and live_schema_applied_count = live_count
          and live_schema_ledger_sha256 = live_digest
          and schema_evidence->>'source' = 'public.schema_migrations'
     ) then
    raise exception 'CA-06 canary FAILED: provenance view does not expose live ledger evidence';
  end if;

  if exists (select 1 from ops.release where state = 'complete') then
    begin
      update ops.release
         set updated_at = updated_at
       where id = (select id from ops.release where state = 'complete' limit 1);
      raise exception 'CA-06 canary FAILED: completed release UPDATE was accepted';
    exception when others then
      if sqlerrm = 'CA-06 canary FAILED: completed release UPDATE was accepted' then
        raise;
      end if;
      if sqlerrm not like 'completed release % is append-only history' then
        raise;
      end if;
    end;

    begin
      delete from ops.release
       where id = (select id from ops.release where state = 'complete' limit 1);
      raise exception 'CA-06 canary FAILED: completed release DELETE was accepted';
    exception when others then
      if sqlerrm = 'CA-06 canary FAILED: completed release DELETE was accepted' then
        raise;
      end if;
      if sqlerrm not like 'completed release % is append-only history' then
        raise;
      end if;
    end;
  end if;
end
$$;

rollback;
