-- 0086_candidate_claim_card.sql — give the candidate reservoir a DECLINE path,
-- and a bounded claimable slice for the card that presents it.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- WHY THIS EXISTS. Measured 2026-08-09 by the whole-system council: six lanes
-- have accumulated 9,870 candidates and promoted ZERO, ever. Not one. Every one
-- of the 208 leads in the book was entered by a human, and `promote-pool` has
-- been invoked 0 times across 609 local sessions.
--
-- The gate is not the defect. Never-pre-qualify (rule 72e06bdf) is correct and
-- stays: the machine presents, Joe judges. THE BATCH SIZE is the defect. Nobody
-- claims from a list 9,783 rows long, so nobody ever has, and six weekly sweeps
-- plus ~30 pipelines plus a nightly promotion job all run downstream of a gate
-- that has never opened once.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- THE MISSING HALF IS NO, NOT YES. `promote-pool` is one-way BY DESIGN and that
-- is right. But it has no counterpart, so a candidate Joe looks at and rejects
-- stays exactly as claimable as it was, and returns on every future card
-- forever. A card that cannot shrink is a card that gets skimmed, then ignored
-- — the same failure the 33-day-overdue triage list already demonstrates, where
-- 229 open asks accumulated because nothing in this system could ever be
-- honestly declined (only completed, which would be a lie, or dropped, which is
-- also a lie).
--
-- So: a fourth status. NOT a delete, and not a suppression.
--   'pool'           unjudged, presentable
--   'promoted'       became a lead — one-way, has promoted_lead_id
--   'suppressed_dup' a high-precision duplicate of a record we already hold
--   'declined'       A HUMAN LOOKED AND SAID NO. New here.
--
-- `declined` is deliberately distinct from `suppressed_dup`. Suppression is a
-- machine's assertion about identity and can be wrong; a decline is a human's
-- judgment about fit and is not re-litigated by a sweep. Keeping them apart is
-- what lets `run.sh health` ever answer "how many did Joe actually look at?",
-- which today is unanswerable — the reason the pipeline category scores 62 with
-- no instrument behind the number.
--
-- WHY A REASON IS REQUIRED. Rule 590b11e1: no metric without a bound action. The
-- decline reason is the bound action's input — "no contact channel" is a fixable
-- lane defect, "out of territory" is a lane-scoping defect, and "not a fit" is
-- the lane working correctly. Without the reason, a lane producing 100% garbage
-- and a lane producing 100% good-but-busy candidates are indistinguishable, and
-- the retirement decision in the council's finding (five of six lanes are
-- candidates for deletion) has nothing to stand on.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- WHY THE VIEW CARRIES A CONTACT REQUIREMENT. The six gate-PASSED Florida Sunbiz
-- candidates from 2026-08-09 all carry `"e": ""` and `"ph": ""` in source_row —
-- names, NPIs, cities, entity dates, and no way to reach any of them. A prospect
-- Joe cannot contact is not a passed prospect, it is a research task. They are
-- not hidden (never-pre-qualify forbids that): they surface in their own bucket
-- with `needs_contact = true`, so the card can show them as work of a different
-- kind rather than mixing them into a call list where they waste the one slot
-- that mattered.
--
-- WHY NO LIMIT IN THE VIEW. The view ranks; the CALLER bounds. A limit baked in
-- here would be a second place the batch size lives, and the whole finding is
-- that batch size is the thing that has to stay adjustable.

begin;

-- ── the fourth status ────────────────────────────────────────────────────────
-- THE CONSTRAINT NAME IS prospect_pool_status_check, NOT candidate_pool_*.
-- 0048 renamed the table and Postgres does not rename a table's constraints with
-- it. Verified live against pg_constraint before this was written, because a
-- `drop constraint if exists` on a guessed name SILENTLY DOES NOTHING, the old
-- three-value check survives beside the new four-value one, both are ANDed, and
-- 'declined' is refused by a constraint the migration believes it removed. That
-- failure mode passes every test that only checks the migration ran.
alter table candidate_pool
  drop constraint if exists prospect_pool_status_check;

alter table candidate_pool
  add constraint candidate_pool_status_check
  check (status in ('pool','promoted','suppressed_dup','declined'));

alter table candidate_pool
  add column if not exists declined_at     timestamptz,
  add column if not exists declined_by     uuid references actor(id),
  add column if not exists decline_reason  text;

-- The status and its stamps are the same fact stated twice and may never
-- disagree — same posture as pool_promoted_has_lead above it. Written as an
-- equality rather than a one-way implication so that clearing the stamps
-- without clearing the status is refused too.
alter table candidate_pool
  add constraint candidate_pool_declined_has_stamp
  check ((status = 'declined') = (declined_at is not null));

alter table candidate_pool
  add constraint candidate_pool_declined_has_reason
  check ((status = 'declined') = (decline_reason is not null and decline_reason <> ''));

create index if not exists candidate_pool_declined_idx
  on candidate_pool (declined_at) where status = 'declined';

comment on column candidate_pool.decline_reason is
  'WHY a human said no, in his own words. Required when status is declined. This '
  'is the input to the lane-retirement decision: a lane whose declines are all '
  '"no contact channel" has a fixable defect, a lane whose declines are all "out '
  'of territory" is mis-scoped, and a lane whose declines are all "not a fit" is '
  'working correctly and simply has a low hit rate. Without it every lane looks '
  'identical from the outside.';

-- ── the claimable slice ──────────────────────────────────────────────────────
-- SAFE COLUMNS ONLY, same rule as v_pool (0023): no email, no phone, no address,
-- no source_row. A reader-scoped session sees everything in this view and this
-- view covers third parties who have never been contacted. The card needs to
-- know a channel EXISTS; it does not need the channel itself, and the human
-- reads the actual number off the lead record after claiming.
create or replace view v_claim_card as
select cp.id                          as pool_id,
       cp.version                     as base_version,
       cp.source                      as lane,
       cp.name                        as display_name,
       cp.org_name,
       cp.vertical,
       cp.city,
       cp.county,
       cp.state,
       cp.segment,
       cp.segment_play,
       cp.score,
       cp.score_basis,
       cp.est_lease_event,
       cp.est_basis,
       cp.dup_tier,
       cp.dup_ref,
       cp.dup_basis,
       (cp.email is not null and cp.email <> '')
         or (cp.phone is not null and cp.phone <> '')      as has_channel,
       not ((cp.email is not null and cp.email <> '')
         or (cp.phone is not null and cp.phone <> ''))     as needs_contact,
       -- Window proximity in days: negative means the estimated lease event has
       -- ALREADY PASSED. Those are not dropped — three renewal windows expired
       -- unread while the shortlist sat in a gitignored folder, and a passed
       -- window is still a live conversation, just a different one. The card
       -- shows them with their age so the opener can say the true thing.
       (cp.est_lease_event - current_date)                 as days_to_window,
       cp.created_at
  from candidate_pool cp
 where cp.status = 'pool'
   and not cp.dup_do_not_contact;

comment on view v_claim_card is
  'The claimable slice of the candidate reservoir: status pool, not do-not-contact. '
  'Promoted, suppressed and DECLINED rows are gone by construction, which is what '
  'lets the card shrink as Joe works it — the property the 9,783-row list never '
  'had. Deliberately UNBOUNDED and unfiltered on channel: the view ranks nothing '
  'away, the caller decides how many to present and whether to split the '
  'needs_contact bucket. Never add email, phone, address or source_row here.';

grant select on v_claim_card to carr_reader, carr_writer, carr_jobs, carr_exporter;

-- guards, before commit
do $$
declare claimable int; declined_ok boolean; bad_insert boolean;
begin
  select count(*) into claimable from v_claim_card;
  raise notice '0086: v_claim_card exposes % claimable candidate(s)', claimable;

  -- the new status must be accepted
  begin
    perform 1 from candidate_pool where status = 'declined';
    declined_ok := true;
  exception when others then declined_ok := false;
  end;
  if not declined_ok then
    raise exception '0086: declined status not queryable';
  end if;

  -- a declined row with no reason must be REFUSED. A constraint nobody proved
  -- is a constraint that may not exist: this is the same posture as 0048's
  -- own guards, and it costs one savepoint.
  bad_insert := false;
  begin
    update candidate_pool
       set status = 'declined', declined_at = now(), decline_reason = null
     where id = (select id from candidate_pool where status = 'pool' limit 1);
    bad_insert := true;   -- reaching here means the guard did NOT fire
  exception when check_violation then
    bad_insert := false;  -- correct: refused
  end;
  if bad_insert then
    raise exception '0086: a declined row without a reason was ACCEPTED — the '
                    'reason constraint is not enforcing';
  end if;

  -- The old three-value check must be GONE, not merely joined by a new one.
  -- Two check constraints are ANDed, so a surviving prospect_pool_status_check
  -- would refuse 'declined' while this migration reports success.
  if exists (select 1 from pg_constraint
              where conrelid = 'candidate_pool'::regclass
                and conname  = 'prospect_pool_status_check') then
    raise exception '0086: the old three-value status check survived the drop — '
                    '''declined'' would be refused by a constraint this migration '
                    'believes it removed';
  end if;
end $$;

commit;
