-- 0225_ops_run_fixture_row_guard.sql
-- Refuse fabricated backup-drill rows at the database, so a stale checkout
-- cannot write them.
--
-- WHY THIS LIVES IN THE DATABASE AND NOT IN A SCRIPT. ops/ci.sh globs
-- ops/*-selftest.py, so ops/key-recovery-test-selftest.py runs on an ordinary
-- CI or pre-push sweep. In a worktree whose base predates the PR #340 merge
-- (2026-08-19T00:46Z) that selftest still reaches production, because
-- tools/ops-record.py's own _load_db_env() reads ~/.config/carr/db.env
-- directly and re-supplies the real jobs DSN for any credential name the
-- caller left unset — blinding the environment is not enough. A guard placed
-- in any file on main cannot help, because a stale checkout calls its OWN copy
-- of every file involved. The database is the one participant both versions
-- share. Measured 2026-08-20: 24 fabricated rows landed at 02:04, 04:02,
-- 04:07 and 04:13 UTC, all AFTER the fix merged, and the collector raised four
-- of them as restore.key-recovery / restore.rehearsal incidents — the record
-- reporting a backup drill failing when no drill ran. New worktrees keep being
-- cut from pre-fix bases, so this reproduces rather than draining away.
--
-- WHAT COUNTS AS FABRICATED, drawn deliberately narrower than the one-time
-- purge predicate in out/purge-evidence/purge-fixture-rows-20260819.sql:
--   * a dump dated before this system held any data — carr-20260101,
--     carr-20260201, carr-20260301 are the three the fixtures name, and no
--     real dump can carry those dates;
--   * the two fabricated sizes, written as the fixtures write them —
--     '(999B' and '(123456B'. The open parenthesis is load-bearing: an
--     unanchored '%999B%' would reject a genuine 41203999-byte dump, and a
--     guard that eats one real row in a thousand is worse than no guard.
--
-- THE CLAUSE THIS GUARD REFUSES TO INHERIT. The August purge also matched
-- detail like 'paper-copy key%' whenever detail did not name a carr-202608
-- dump. That pairing was safe for one afternoon in August and is wrong
-- forever: a GENUINE key-recovery run in September writes
-- 'paper-copy key MATCHES ...' naming a carr-202609 dump, which satisfies both
-- halves. A permanent constraint carrying that clause would silently discard
-- real backup-failure history, which is the one record a live restore
-- emergency is read from. Sizes and impossible dates are self-evidencing;
-- prose is not.
--
-- REVERSIBLE: drop the trigger. It adds no column and rewrites no row.

begin;

create or replace function ops.reject_fabricated_drill_row()
returns trigger
language plpgsql
as $$
begin
  if new.source_ref in ('bin/key-recovery-test.sh','bin/restore-rehearse.sh')
     and (   new.detail like '%carr-20260101%'
          or new.detail like '%carr-20260201%'
          or new.detail like '%carr-20260301%'
          or new.detail like '%(999B%'
          or new.detail like '%(123456B%') then
    raise exception
      'ops.run refused a fabricated backup-drill row: detail names a dump or size only a selftest fixture produces (%). A selftest reached the production ledger — the checkout running it predates the PR #340 credential belts; rebase it onto main. See migration 0225.',
      left(new.detail, 120);
  end if;
  return new;
end $$;

drop trigger if exists reject_fabricated_drill_row on ops.run;
create trigger reject_fabricated_drill_row
  before insert on ops.run
  for each row execute function ops.reject_fabricated_drill_row();

-- Proof, in this transaction: a fabricated row is refused and a genuine one is
-- not. The genuine row is deleted before commit, so the migration leaves the
-- ledger exactly as it found it.
do $$
declare
  refused boolean := false;
  minted_service boolean := false;
  svc uuid;
begin
  -- A fresh schema has no services, and this proof must hold there too: the
  -- migration series is replayed end to end on a throwaway branch by
  -- ops/release-abandon-selftest.py, where the ledger is empty.
  select id into svc from ops.service order by id limit 1;
  if svc is null then
    insert into ops.service (key, name, owner_actor, criticality)
    values ('migration-0225-proof','migration 0225 proof service','joe','low')
    returning id into svc;
    minted_service := true;
  end if;

  begin
    insert into ops.run
      (kind, service_id, environment, run_key, state, failure_class,
       started_at, ended_at, source_kind, source_ref, detail)
    values
      ('check', svc, 'local', 'migration-0225-proof', 'failed', 'fixture',
       now(), now(), 'wrapper', 'bin/restore-rehearse.sh',
       'dump=carr-20260101.sql.age (123456B, taken 20260101) restored 99.8% of rows');
    raise exception '0225 FAILED: a fabricated drill row was accepted';
  exception when others then
    if position('fabricated backup-drill row' in sqlerrm) > 0 then
      refused := true;
    else
      raise;
    end if;
  end;
  if not refused then
    raise exception '0225 FAILED: the fabricated row was not refused by this guard';
  end if;

  -- the shapes a genuine run writes, including the two byte counts that would
  -- trip an unanchored size match
  begin
    insert into ops.run
      (kind, service_id, environment, run_key, state,
       started_at, ended_at, source_kind, source_ref, detail)
    values
      ('check', svc, 'local', 'migration-0225-proof', 'succeeded',
       now(), now(), 'wrapper', 'bin/key-recovery-test.sh',
       'paper-copy key MATCHES backups-public-key.txt; restore VERIFIED using dump=carr-20260901.sql.age (41203344B)'),
      ('check', svc, 'local', 'migration-0225-proof', 'succeeded',
       now(), now(), 'wrapper', 'bin/restore-rehearse.sh',
       'dump=carr-20260903.sql.age (41203999B, taken 20260903) restored 100% of rows'),
      ('check', svc, 'local', 'migration-0225-proof', 'succeeded',
       now(), now(), 'wrapper', 'bin/restore-rehearse.sh',
       'dump=carr-20260904.sql.age (8123456B, taken 20260904) restored 100% of rows');
  exception when others then
    raise exception '0225 FAILED: a genuine drill row was refused — %', sqlerrm;
  end;

  delete from ops.run where run_key = 'migration-0225-proof';
  if minted_service then
    delete from ops.service where id = svc;
  end if;
  if exists (select 1 from ops.run where run_key = 'migration-0225-proof') then
    raise exception '0225 FAILED: the proof rows were not cleaned up';
  end if;
end $$;

commit;
