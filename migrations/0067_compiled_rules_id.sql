-- 0067_compiled_rules_id.sql — publish rule.id through v_compiled_rules.
--
-- THE DEFECT. Every rule in this system is referenced BY ID in conversation, in
-- handoffs, in decision entries and in the curation plan: "activate caa92331",
-- "retire 305df62b", "supersedes 0e3ed876". The compiled-rules exports and the
-- new gist index cannot print those ids, because v_compiled_rules does not
-- select rule.id and the exporter credential is denied the base table by design
-- (verified: `permission denied for table rule` as carr_exporter). So the files
-- a session actually reads name no rule, and a reader who wants to act on one
-- has to go find its id somewhere else. The gist index makes this sharper: it is
-- a list of pointers whose whole job is to be followed, and today none of its
-- lines carries the locator you would follow it with.
--
-- WHY THE VIEW AND NOT A GRANT. 0029's own note settled this: the view is how a
-- column gets published to the reader role, precisely so the base table stays
-- closed. Granting select on `rule` to widen one column would hand the exporter
-- human_quote-adjacent columns, status, and every proposed row, none of which it
-- should see. Appending one already-published-adjacent column to the view is the
-- narrow change; the broad one is a permissions decision nobody asked for.
--
-- WHY IT IS SAFE. `create or replace view` with the existing column list in the
-- existing order plus ONE appended column. Postgres permits replace only when the
-- leading columns keep their names, types and order, so a rename or reorder would
-- fail here rather than silently reshape a view four exporters read. The exporter
-- code already tolerates the column's absence (`_rule_ident()` emits the id only
-- when present), so this migration and the exporter are order-independent: apply
-- first or deploy first, neither breaks the other.
--
-- REVERSAL is the same statement without the final column:
--   create or replace view v_compiled_rules as select r.statement, r.human_quote,
--     teacher.display_name as taught_by, owner.slug as personal_to, r.enforcement,
--     r.activated_at, r.scope from rule r
--     join actor teacher on teacher.id = r.taught_by
--     left join actor owner on owner.id = r.personal_to
--    where r.status = 'active' order by r.activated_at;
--
-- Written 2026-08-02 after the pass-1 exporter work reported the gap and
-- explicitly declined to invent a substitute identifier.

begin;

-- Guard: the view must already exist in the shape we are extending. If some
-- other change landed first, fail loudly rather than replacing something else.
do $$
declare
  n_cols int;
begin
  select count(*) into n_cols
    from information_schema.columns
   where table_schema = 'public' and table_name = 'v_compiled_rules';

  if n_cols = 0 then
    raise exception '0067: v_compiled_rules does not exist — expected 0029 to have created it';
  end if;

  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'v_compiled_rules'
                and column_name = 'id') then
    raise notice '0067: v_compiled_rules already publishes id — nothing to do';
  else
    raise notice '0067: v_compiled_rules has % column(s), appending id', n_cols;
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
       r.id
  from rule r
  join actor teacher on teacher.id = r.taught_by
  left join actor owner on owner.id = r.personal_to
 where r.status = 'active'
 order by r.activated_at;

grant select on v_compiled_rules to carr_reader;

-- Closing guard: the column is published AND the view still returns the same
-- rows it did before. A view that gained a column and lost a row is a worse
-- outcome than one that gained nothing.
do $$
declare
  has_id  boolean;
  n_rows  int;
  n_active int;
begin
  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'v_compiled_rules'
                    and column_name = 'id') into has_id;
  if not has_id then
    raise exception '0067: id column did not appear on v_compiled_rules';
  end if;

  execute 'select count(*) from v_compiled_rules' into n_rows;
  select count(*) into n_active from rule where status = 'active';
  if n_rows <> n_active then
    raise exception '0067: v_compiled_rules returns % row(s) but % rule(s) are active',
      n_rows, n_active;
  end if;

  raise notice '0067: id published · % active rule(s) visible · unchanged row count', n_rows;
end $$;

commit;
