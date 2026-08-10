-- 0088_triage_names_and_age.sql — put a NAME and an AGE on the triage surface,
-- and count the age in business days.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- WHAT IS WRONG TODAY, read off the live output on 2026-08-09. v_today_triage
-- returns 36 rows and not one of them says who it is about:
--
--   {"item_kind":"next_action","subject_type":"lead",
--    "subject_id":"5a1eee7a-83d2-40d8-ad9b-2ffd58077d81",
--    "what":"JOE DECIDE (loop #25): send status unknown, re-touch or drop",
--    "due_on":"2026-07-07"}
--
-- Twenty-two of the thirty-six are the byte-identical string
-- "calendar item awaiting triage", because the ingest branch renders
-- `source || ' item awaiting triage'` and throws the payload away. Rule 3a9dbafd
-- says never make a partner decode an id, and law 5 of the UX doctrine says the
-- same. Three of Joe's fourteen oldest asks are literally unactionable as
-- presented, which is a large part of why the oldest is 33 days old.
--
-- AND THERE IS NO AGE. The view orders by nothing and carries no days_overdue,
-- so a 33-day-old decision arrives in the same voice as one due today. Every
-- escalation the council proposed has to be built on an age this view does not
-- expose, so it is added here rather than recomputed by each caller (the brief
-- pack already computes its own, which is exactly the drift this prevents).
--
-- BUSINESS DAYS, NOT CALENDAR DAYS. Rule 236ca227 says weekends are not workdays
-- for either partner and every staleness and escalation clock counts business
-- days only. Codex's dissent measured the gap: a repo-wide grep for business-day
-- arithmetic returns one comment and zero code, while every clock in the record
-- layer is raw `current_date` subtraction. So a Friday obligation ages two days
-- over a weekend the doctrine says does not exist, and every escalation band
-- built on the old number would fire early. The function is defined here, once,
-- so the next surface that needs it does not invent a second one.
--
-- WHAT THIS DELIBERATELY DOES NOT DO. It does not change WHICH rows appear. The
-- due_on IS NOT NULL filter stays exactly as 0032 set it, including its known
-- consequence that ~194 undated commitments remain invisible. That is a real
-- defect with a real fix (require a date at the door, in set-next-action) and
-- widening the view instead would re-create the 145-row flood 0032 was written
-- to stop. One change at a time, and this one is about legibility.

begin;

-- ── business-day arithmetic, defined once ────────────────────────────────────
-- Counts whole business days from a to b, negative when a is in the future.
-- Holidays are NOT modelled: CARR has no holiday calendar in the record, and
-- inventing one here would be a second unmaintained source of truth. Weekends
-- are the part the doctrine actually names.
create or replace function carr_business_days(a date, b date)
returns integer language sql immutable as $$
  select case when a is null or b is null then null
         else (case when b >= a then 1 else -1 end) * (
           select count(*)::int
             from generate_series(least(a,b), greatest(a,b) - 1, interval '1 day') d
            where extract(isodow from d) < 6)
         end;
$$;

comment on function carr_business_days(date, date) is
  'Whole business days from a to b, negative when a is later than b. Weekends '
  'excluded per rule 236ca227; holidays deliberately not modelled, because CARR '
  'holds no holiday calendar and a guessed one would be a second source of truth. '
  'THE one business-day primitive: route human obligation aging, staleness and '
  'overdue display through this rather than raw date subtraction, and leave '
  'machine liveness cadences in elapsed time where a weekend is still a weekend.';

-- ── the triage surface, now legible ──────────────────────────────────────────
create or replace view v_today_triage as
  select 'next_action'::text as item_kind, na.id, na.subject_type, na.subject_id,
         owner.slug as owner, na.description as what, na.due_on,
         coalesce(r.display_name, r.org_name)              as subject_name,
         r.ref                                             as subject_ref,
         carr_business_days(na.due_on, current_date)       as business_days_overdue
    from next_action na
    join actor owner on owner.id = na.owner_id
    left join v_ref_index r
      on r.subject_id = na.subject_id and r.subject_type = na.subject_type
   where na.status = 'open'
     and na.due_on is not null and na.due_on <= current_date
     and (na.hold_until is null or na.hold_until <= current_date)
union all
  select 'critical_date'::text, cd.id, 'deal'::text, cd.deal_id, null::text,
         cd.kind || coalesce(': ' || cd.note, ''), cd.due_on,
         coalesce(r.display_name, r.org_name), r.ref,
         carr_business_days(cd.due_on, current_date)
    from critical_date cd
    left join v_ref_index r on r.subject_id = cd.deal_id and r.subject_type = 'deal'
   where cd.status = 'open' and cd.due_on <= (current_date + 14)
union all
  -- THE INGEST BRANCH NOW SAYS WHAT THE ITEM IS. It rendered
  -- `source || ' item awaiting triage'` and discarded the payload, which is how
  -- twenty-two rows became one identical sentence. The payload is stored and
  -- UNTRUSTED — triage-item's own contract says so — so this reads a title out
  -- of it for DISPLAY only and never acts on it. Several shapes are tried
  -- because the payload comes from more than one capture lane; coalesce falls
  -- back to the old sentence rather than showing an empty row.
  select 'ingest'::text, i.id, 'inbox'::text, i.id, null::text,
         coalesce(
           nullif(trim(i.payload->>'summary'), ''),
           nullif(trim(i.payload->>'title'), ''),
           nullif(trim(i.payload->>'subject'), ''),
           i.source || ' item awaiting triage'),
         i.received_at::date,
         nullif(trim(i.payload->>'organizer'), ''),
         null::text,
         carr_business_days(i.received_at::date, current_date)
    from ingest_inbox i
   where i.status = 'new';

comment on view v_today_triage is
  'What needs attention now, and legible enough to act on: every row carries the '
  'subject NAME and its ref, not only a uuid (rule 3a9dbafd), and an age in '
  'BUSINESS days (rule 236ca227). The ingest branch reads a title out of the '
  'stored payload for DISPLAY only — the payload stays untrusted and nothing '
  'here acts on what it says. Which rows appear is unchanged from 0032: dated, '
  'due, not held. The ~194 undated open commitments are still invisible, which '
  'is a separate defect whose fix is requiring a date at the door.';

-- guards, before commit
do $$
declare miss int; named int; total int;
begin
  if carr_business_days(date '2026-08-07', date '2026-08-10') <> 1 then
    raise exception '0088: Friday to Monday must be 1 business day, got %',
      carr_business_days(date '2026-08-07', date '2026-08-10');
  end if;
  if carr_business_days(date '2026-08-08', date '2026-08-09') <> 0 then
    raise exception '0088: a weekend must add zero business days';
  end if;
  if carr_business_days(date '2026-08-10', date '2026-08-07') <> -1 then
    raise exception '0088: a future date must age negative';
  end if;

  select count(*), count(subject_name) into total, named from v_today_triage;
  select count(*) into miss from v_today_triage where business_days_overdue is null;
  if miss > 0 then
    raise exception '0088: % row(s) have no age', miss;
  end if;
  raise notice '0088: % triage row(s), % carry a name, ages in business days', total, named;
end $$;

commit;
