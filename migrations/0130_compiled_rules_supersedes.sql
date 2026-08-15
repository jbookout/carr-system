-- 0130_compiled_rules_supersedes.sql — publish rule.supersedes through v_compiled_rules.
--
-- THE DEFECT, measured. The monthly conflict-surfacing job (pipelines/learning_jobs.py,
-- job_conflicts) runs two checks that are deliberately different in kind. One is a
-- CANDIDATE heuristic: pairs of active rules sharing vocabulary while pointing opposite
-- ways, explicitly "never verdicts". The other is the only FACT the job can state — an
-- active rule whose `supersedes` target is ALSO active, meaning two rules bind at once
-- where one was written to replace the other. That second check is the reason the job
-- exists, and it has never once run: it needs `rule.supersedes`, the view does not
-- publish it, and the jobs role is denied the base table by design. Every monthly run
-- since the job shipped has printed "mechanical check UNAVAILABLE (needs
-- `rule.supersedes`)" while printing thousands of guesses beside it. The 2026-08-15
-- playbook review found it reporting UNAVAILABLE against 220 active rules.
--
-- WHY THE VIEW AND NOT A GRANT. Same reasoning 0029 settled and 0067 followed for
-- `id`: the view is how one column reaches a reader role while the base table stays
-- closed. `select count(*) from rule` as carr_jobs returns "permission denied for
-- table rule" today, and that is correct — granting the base table to widen one column
-- would also hand over status, human_quote and every proposed row.
--
-- WHY IT IS SAFE. `create or replace view` with the existing column list in its
-- existing order plus ONE appended column. Postgres permits replace only when the
-- leading columns keep their names, types and order, so a reorder fails loudly here
-- rather than silently reshaping a view several exporters read. Consumers are safe by
-- construction: exporters/targets.py builds dicts with `dict(zip(cols, row))` off
-- `select *` and addresses columns by name, and learning_jobs.py reads named keys, so
-- an appended column is inert to code that does not ask for it. Migration and deploy
-- are order-independent in either direction.
--
-- REVERSAL is the same statement without the final column:
--   create or replace view v_compiled_rules as select r.statement, r.human_quote,
--     teacher.display_name as taught_by, owner.slug as personal_to, r.enforcement,
--     r.activated_at, r.scope, r.id from rule r
--     join actor teacher on teacher.id = r.taught_by
--     left join actor owner on owner.id = r.personal_to
--    where r.status = 'active' order by r.activated_at;
--
-- Written 2026-08-15 by the monthly playbook review, which is the routine the broken
-- check was supposed to feed.

begin;

-- Guard: extend the shape 0067 left behind, not something else that landed since.
do $$
declare
  n_cols int;
begin
  select count(*) into n_cols
    from information_schema.columns
   where table_schema = 'public' and table_name = 'v_compiled_rules';

  if n_cols = 0 then
    raise exception '0130: v_compiled_rules does not exist — expected 0029/0067 to have built it';
  end if;

  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'v_compiled_rules'
                    and column_name = 'id') then
    raise exception '0130: v_compiled_rules does not publish id — expected 0067 to have applied';
  end if;

  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'v_compiled_rules'
                and column_name = 'supersedes') then
    raise notice '0130: v_compiled_rules already publishes supersedes — nothing to do';
  else
    raise notice '0130: v_compiled_rules has % column(s), appending supersedes', n_cols;
  end if;
end $$;

create or replace view v_compiled_rules as
select r.statement,
       r.human_quote,
       teacher.display_name as taught_by,
       owner.slug           as personal_to,
       r.enforcement,
       r.activated_at,
       r.scope,
       r.id,
       r.supersedes
  from rule r
  join actor teacher on teacher.id = r.taught_by
  left join actor owner on owner.id = r.personal_to
 where r.status = 'active'
 order by r.activated_at;

-- `create or replace view` preserves existing grants; these are restated so the
-- migration is correct when replayed against a rebuilt database, which is exactly
-- what the weekly restore rehearsal does.
grant select on v_compiled_rules to carr_reader;
grant select on v_compiled_rules to carr_jobs;

-- Closing guard: the column is published, the row count is unchanged, and the check
-- this migration exists to unblock can now actually be evaluated.
do $$
declare
  has_sup  boolean;
  n_rows   int;
  n_active int;
  n_hard   int;
begin
  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'v_compiled_rules'
                    and column_name = 'supersedes') into has_sup;
  if not has_sup then
    raise exception '0130: supersedes column did not appear on v_compiled_rules';
  end if;

  execute 'select count(*) from v_compiled_rules' into n_rows;
  select count(*) into n_active from rule where status = 'active';
  if n_rows <> n_active then
    raise exception '0130: v_compiled_rules returns % row(s) but % rule(s) are active',
      n_rows, n_active;
  end if;

  -- Report the finding this unblocks. A non-zero count is not a migration failure:
  -- it is the contradiction the job was built to surface, and it belongs to a human.
  execute 'select count(*) from v_compiled_rules a
            where a.supersedes is not null
              and exists (select 1 from v_compiled_rules b where b.id = a.supersedes)'
     into n_hard;

  raise notice '0130: supersedes published · % active rule(s) visible · unchanged row count',
    n_rows;
  raise notice '0130: mechanical contradiction check is now evaluable — % active rule(s) supersede a rule that is also still active',
    n_hard;
end $$;

commit;
