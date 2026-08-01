-- 0028: expose `scope` on v_compiled_rules — the read side ORDER 37 needs.
--
-- ORDER 37 turns DNA/Network/introduction-rules.md into a compile target of the
-- rule store (scope kind `intro_politics`), on ORDER 6's mechanism rather than a
-- parallel one. That needs exactly one thing the store did not already publish:
-- the compiler must be able to tell an intro-politics rule from a general one.
--
-- WHY THE VIEW AND NOT THE TABLE: exporters hold the carr_exporter bundle —
-- export views, export_run, system_config, nothing else (0006). Granting the
-- rule table would widen that bundle for one column. The view already joins the
-- actor rows the render needs; scope rides along.
--
-- WHY A SCOPE KEY AND NOT A COLUMN: `rule.scope` is jsonb and free-form by
-- design (0001: "{surfaces:[],workflows:[],tiers:[]}"), and the one non-empty
-- scope in the store already uses a `kind` key. Reusing `kind` invents no schema
-- and costs no migration on the write side — `teach` already accepts scope.
--
-- CREATE OR REPLACE, appending the column at the END: existing column names,
-- types and order are untouched, so every current reader keeps working, and the
-- grant survives the replace.

create or replace view v_compiled_rules as
select r.statement,
       r.human_quote,
       teacher.display_name              as taught_by,
       owner.slug                        as personal_to,   -- null = shared scope
       r.enforcement,
       r.activated_at,
       r.scope                                             -- 0028: appended
  from rule r
  join actor teacher on teacher.id = r.taught_by
  left join actor owner on owner.id = r.personal_to
 where r.status = 'active'
 order by r.activated_at;
grant select on v_compiled_rules to carr_reader;
