-- cadence-rules-seed.sql — ORDER 14(b). SIX ROWS, and they are DATA, not code.
--
-- This is not a migration and it must never become one. wave2-design §2e:
-- "The engine reads rules; rules are rows, not code." Joe grows, tunes or
-- retires a cadence with a sentence — an UPDATE or an INSERT — never a deploy.
-- It is filed here beside the engine so the starting six are legible and
-- reproducible; running it is a production WRITE and therefore Joe's tap.
--
-- IDEMPOTENT BY CONSTRUCTION. cadence_rule carries no unique constraint (0019
-- shipped it without one, deliberately — nothing had ever written to it), so
-- every insert below guards on (lane, subject_type, trigger, interval_days).
-- Running this file twice inserts nothing the second time. Verify with the
-- final select, which must read 6.
--
-- THE HARD GUARD IS NOT IN THIS FILE, ON PURPOSE. Cold and paused clients are
-- excluded by the ENGINE, unconditionally, "regardless of rules data" (ORDER
-- 14a). A guard expressed as a row is a guard that can be deleted at 2am by
-- somebody tidying a table. Joe's rule, in his own words: "a cold client is
-- usually one where they are probably ghosting us. we dont want to pester
-- them... we have plenty of business."
--
-- THE interval_days CONVENTION, WHICH DIFFERS BY TRIGGER — read this before
-- adding a row:
--   on_complete : days AFTER the completed action. Successor due = completed + N.
--   on_date     : days BEFORE the dated trigger. The T-18/T-12/T-6/T-3
--                 milestones below are 548/365/183/91 days ahead of
--                 lead.est_lease_event.
-- 0019's column comment says "an on_date rule has no interval" — that was
-- written before any on_date rule existed. The countdown milestones ARE on_date
-- rules and they need a number; interval_days is the only numeric column, and
-- reusing it beats inventing schema. Flagged to Fable in the ORDER 14 report.
--
-- LANE IS THE DISPATCH KEY for on_date rules: lane 'lease_event' +
-- subject_type 'lead' is what tells the engine to read lead.est_lease_event.
-- A new dated source needs an engine change as well as a row; on_complete
-- rules are pure data.
--
-- Run:
--   psql "$DATABASE_URL" -f pipelines/cadence-rules-seed.sql

begin;

-- (1) engaged-client nurture, 45 days.
-- NOTE, and it is honest rather than hidden: cadence_rule has no status-filter
-- column, so "engaged-client" cannot be expressed as a row today. The engine
-- applies the hard cold/paused guard and nothing narrower. If the qualifier
-- must bite (nurture ONLY status='engaged'), that is one new column
-- (subject_filter jsonb) and a migration — a Fable call, not this file's.
insert into cadence_rule (lane, subject_type, trigger, interval_days, action_template, active)
select 'nurture45', 'client', 'on_complete', 45,
       'Nurture touch — {subject} ({ref}). 45-day client cadence.', true
 where not exists (select 1 from cadence_rule
                    where lane='nurture45' and subject_type='client'
                      and trigger='on_complete' and interval_days=45);

-- (2) vendor relationship maintenance, 90 days.
insert into cadence_rule (lane, subject_type, trigger, interval_days, action_template, active)
select 'vendor_maintenance', 'vendor', 'on_complete', 90,
       'Relationship touch — {subject} ({ref}). 90-day vendor cadence.', true
 where not exists (select 1 from cadence_rule
                    where lane='vendor_maintenance' and subject_type='vendor'
                      and trigger='on_complete' and interval_days=90);

-- (3)-(6) lease-event countdowns off lead.est_lease_event.
-- Each milestone fires inside its OWN window — from (event - interval) until the
-- next tighter milestone opens — so a lead discovered two months out trips T-3
-- and only T-3. A T-18 action is a T-18 action; discovered late it is stale,
-- not owed.
insert into cadence_rule (lane, subject_type, trigger, interval_days, action_template, active)
select 'lease_event', 'lead', 'on_date', 548,
       'Lease event T-18 months — {subject} ({ref}), est. {date}. Open the conversation '
       'early: this is the window where representation is still a choice, not a scramble.', true
 where not exists (select 1 from cadence_rule
                    where lane='lease_event' and trigger='on_date' and interval_days=548);

insert into cadence_rule (lane, subject_type, trigger, interval_days, action_template, active)
select 'lease_event', 'lead', 'on_date', 365,
       'Lease event T-12 months — {subject} ({ref}), est. {date}. Market check and '
       'options review.', true
 where not exists (select 1 from cadence_rule
                    where lane='lease_event' and trigger='on_date' and interval_days=365);

insert into cadence_rule (lane, subject_type, trigger, interval_days, action_template, active)
select 'lease_event', 'lead', 'on_date', 183,
       'Lease event T-6 months — {subject} ({ref}), est. {date}. Renewal vs relocation '
       'decision window.', true
 where not exists (select 1 from cadence_rule
                    where lane='lease_event' and trigger='on_date' and interval_days=183);

insert into cadence_rule (lane, subject_type, trigger, interval_days, action_template, active)
select 'lease_event', 'lead', 'on_date', 91,
       'Lease event T-3 months — {subject} ({ref}), est. {date}. Last window before '
       'leverage is gone.', true
 where not exists (select 1 from cadence_rule
                    where lane='lease_event' and trigger='on_date' and interval_days=91);

-- Verification, in the same transaction: exactly six active rules, and the four
-- countdown milestones distinct. Anything else rolls the whole file back.
do $$
declare n int;
begin
  select count(*) into n from cadence_rule where active;
  if n <> 6 then
    raise exception 'expected 6 active cadence rules after seeding, found % — stop and report', n;
  end if;
  select count(distinct interval_days) into n from cadence_rule where lane='lease_event';
  if n <> 4 then
    raise exception 'expected 4 distinct lease_event milestones, found % — stop and report', n;
  end if;
  raise notice 'cadence rules seeded: 6 active (nurture45, vendor_maintenance, lease_event x4)';
end $$;

commit;

select lane, subject_type, trigger, interval_days, active from cadence_rule order by lane, interval_days desc;
