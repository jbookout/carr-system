-- 0035_phase_order_legal_before_dd.sql — legal comes BEFORE due_diligence, not after.
--
-- 0032 repair B resolved the legal/due_diligence sort collision (both were 100) but
-- encoded the sequence BACKWARDS:
--     0032 applied:  negotiation 40 · due_diligence 50 · legal 60 · closing 70 · closed 80
--
-- Joe's ruling, stated twice and settled before 0032 was written:
--   "negotiation -> legal -> execution -> due diligence -> closing. DD is POST-execution."
--   legal          = attorneys reviewing the agreement itself, PRE-execution, non-binding,
--                    either side walks free.
--   (execution)    = an event, not a phase. Signed. The DD clock starts.
--   due_diligence  = inspections, build-out estimates, financing final approval. Binding,
--                    with an out that EXPIRES. Last chance to terminate without friction.
--
-- The audit's own decision record carries the same order (DECISIONS.md, "Deal phases —
-- settled": pending 10 / research 20 / site_selection 30 / negotiation 40 / legal 44 /
-- due_diligence 47 / closing 50 / closed 60). The prior session had recorded Joe's
-- correction and then shipped the pre-correction assumption anyway.
--
-- WHY THIS IS NOT COSMETIC. phase_sort is how the deal board orders the pipeline, so today
-- five binding due_diligence deals (an expiring out, the highest-urgency state on the board)
-- render as EARLIER-stage than five non-binding legal deals where either side can still walk.
-- The board inverts exactly the risk ranking it exists to show.
--
-- Numbering: this keeps 0032's spacing and swaps the two slugs, rather than adopting
-- DECISIONS.md's 44/47 literals. The settled thing is the ORDER; renumbering closing/closed
-- a second time would churn rows for nothing.

begin;

update deal_phase set sort = 50 where slug = 'legal';
update deal_phase set sort = 60 where slug = 'due_diligence';
-- closing 70 / closed 80 unchanged from 0032.

commit;

-- guard: the pipeline must read in Joe's stated order, with no collisions.
do $$
declare bad int; legal_s int; dd_s int;
begin
  select sort into legal_s from deal_phase where slug = 'legal';
  select sort into dd_s    from deal_phase where slug = 'due_diligence';

  if legal_s is null or dd_s is null then
    raise exception 'legal/due_diligence slug missing — 0032 B did not run';
  end if;
  if legal_s >= dd_s then
    raise exception 'legal (%) must sort before due_diligence (%)', legal_s, dd_s;
  end if;

  -- no two phases may share a sort again (the 100/100 collision 0032 was fixing)
  select count(*) into bad from (
    select sort from deal_phase group by sort having count(*) > 1
  ) x;
  if bad > 0 then raise exception 'deal_phase.sort collision: % duplicated value(s)', bad; end if;

  raise notice 'phase order: legal=% then due_diligence=%', legal_s, dd_s;
end $$;
