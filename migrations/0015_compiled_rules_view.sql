-- 0015: v_compiled_rules — the read side of the taught-rules loop (ORDER 6).
--
-- The rule store has had a write path since day one (`teach` -> `activate-rule`,
-- both human-gated) and no read path: an activated rule sat in a table nothing
-- loaded, so a lesson Joe taught could not reach the next session. This view is
-- what the compiled-context exporters render, closing the loop.
--
-- ACTIVE ONLY, by design (A12): a proposed rule is a draft, and the context
-- compiler must never bind a session to something no human activated.
create view v_compiled_rules as
select r.statement,
       r.human_quote,
       teacher.display_name              as taught_by,
       owner.slug                        as personal_to,   -- null = shared scope
       r.enforcement,
       r.activated_at
  from rule r
  join actor teacher on teacher.id = r.taught_by
  left join actor owner on owner.id = r.personal_to
 where r.status = 'active'
 order by r.activated_at;
grant select on v_compiled_rules to carr_reader;
