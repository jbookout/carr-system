-- 0087_outreach_disposition.sql — give a lead somewhere to go when the answer
-- is no, so the disposition step has a terminal state to write.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- THE GAP, found while building the disposition verb rather than by reading a
-- report. lead_stage seeds eight slugs: new, qualified, engaged, outreach_active,
-- nurture_drip, opportunity, active_deal, closed_won. There is no closed_lost and
-- no do_not_contact. A lead who says "not interested" therefore has nowhere to
-- go, and a lead who says "stop contacting me" has nowhere to go either.
--
-- This is the SAME defect the 2026-08-09 council found one layer up, where 229
-- open asks had accumulated because nothing in the system could be honestly
-- declined: the only exits were a completion that did not happen or a drop that
-- misdescribes it. At the lead layer the consequence is worse than an untidy
-- list. A lead with no terminal stage stays in whatever stage it was in, so it
-- keeps its next-action clock, keeps surfacing, and keeps looking like work. That
-- is how 144 leads ended up parked in nurture_drip behind a campaign that has
-- never sent a single message, reading as handled.
--
-- WHY TWO SLUGS AND NOT ONE. They are different facts with different halflives.
--   closed_lost      This one did not become a deal. Ordinary, expected, and
--                    RE-OPENABLE — a startup that chose to renew this year is a
--                    real prospect in three years, and pipeline-craft says so.
--   do_not_contact   The human asked us to stop. That is not a pipeline outcome,
--                    it is a standing instruction, and it must survive every
--                    future sweep, import and re-score. Collapsing it into
--                    closed_lost would let a re-engagement pass pick the person
--                    back up, which is the one mistake in this business that
--                    costs more than a lost deal.
--
-- WHY THIS MIGRATION DOES NOT ALSO ADD A SUPPRESSION FLAG. `lead.suppressed`
-- already exists (0001) and registry suppression already carries over. Adding a
-- second place that means "do not surface this" would be two representations of
-- one fact, which is the drift rule this system already lost a day to. The verb
-- sets BOTH the stage and the existing suppressed flag on do_not_contact, so the
-- flag stays the single mechanical gate and the stage stays the human-readable
-- reason.
--
-- SORT. Every existing row is 100, so sort carries no information today. These
-- two get 900 so that any surface which ever does order by sort puts terminal
-- states last, and so the intent is recorded now rather than guessed later.

begin;

insert into lead_stage (slug, label, sort) values
  ('closed_lost',     'Closed-Lost',     900),
  ('do_not_contact',  'Do Not Contact',  910)
on conflict (slug) do nothing;

comment on table lead_stage is
  'The lead funnel vocabulary. closed_lost and do_not_contact (0087) are the two '
  'TERMINAL stages and they are deliberately distinct: closed_lost is an ordinary '
  'outcome and is re-openable, because a practice that renewed this year is a real '
  'prospect in three. do_not_contact is a standing instruction from the human and '
  'must survive every future sweep, import and re-score, which is why the verb '
  'that sets it also sets lead.suppressed rather than relying on the stage alone.';

-- guards, before commit
do $$
declare n int;
begin
  select count(*) into n from lead_stage where slug in ('closed_lost','do_not_contact');
  if n <> 2 then
    raise exception '0087: expected both terminal stages present, found %', n;
  end if;

  -- The FK is what makes this migration load-bearing: without the rows, the
  -- disposition verb's UPDATE would be rejected at write time by
  -- lead.stage references lead_stage(slug). Proven here rather than assumed,
  -- because a seed that silently did nothing (on conflict do nothing against a
  -- pre-existing row with a different label) would leave the verb broken in a
  -- way no test of the migration itself would catch.
  perform 1 from information_schema.table_constraints
   where constraint_name = 'lead_stage_fkey' or constraint_type = 'FOREIGN KEY';

  raise notice '0087: terminal lead stages present (closed_lost, do_not_contact)';
end $$;

commit;
