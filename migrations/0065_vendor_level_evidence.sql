-- 0065_vendor_level_evidence.sql — the vendor-level suggestion stops speaking where it knows
-- nothing. Closes open loop #134.
--
-- THE DEFECT, and loop #134 states it in Joe's own system's words: "v_vendor_level_suggestion
-- is a dead signal until the relationship history is backfilled... 123 'downgrade to 0'
-- signals with a 100% false-positive rate. Joe's taught rule says a disagreement is worth a
-- look in either direction, but nobody will read a view that flags every established vendor."
--
-- MEASURED READ-ONLY AGAINST PRODUCTION 2026-08-02, and the shape of the 123 matters because
-- it is TWO faults, not one:
--   282 active vendors in the view
--   282 rows where `suggested` = 0            <- every single one
--   123 rows where recorded IS DISTINCT FROM suggested, which splits:
--        64  recorded is 1/2/3 and suggested is 0   a genuine-looking downgrade, all false
--        59  recorded is NULL and suggested is 0    NOT A DISAGREEMENT AT ALL
-- The 59 are the more embarrassing half. 0047 spent a paragraph establishing that a NULL
-- relationship_level means "nobody has judged this vendor yet" and is a DIFFERENT FACT from
-- 0 — "collapsing them into 0 would have hidden that one in five has never been assessed."
-- Then the consumer compares that NULL against a suggestion with `is distinct from`, which
-- treats NULL as a value, and quietly un-does the distinction the column was designed around.
--
-- ── WHY EVERY SUGGESTION IS 0, TRACED TO THE ROW COUNT ──
-- 0053's suggestion reads two evidence sources. Both are empty for vendors:
--   activity      203 rows total, 13 carrying a vendor_id. All 13 are kind 'note' (12) or
--                 'analysis' (1) — neither is a contact of any kind. Exactly ONE activity in
--                 the entire system is a contact event, a single 'call', and it carries no
--                 vendor. So two_way = 0 and attempts_only = 0 for all 282.
--   party_link    31 rows: 24 'knows', 6 'can_introduce', 1 'introduced'. The value-moved
--                 test reads kinds ('introduced','referred') via_party; the one 'introduced'
--                 row is a legacy edge with via_party NULL, so it credits nobody. they_gave
--                 and we_gave are 0 for all 282.
-- Counted directly: 282 of 282 active vendors have ZERO evidence events of any kind.
--
-- ── THE CHOICE: BACKFILL OR SUPPRESS. IT IS NOT CLOSE, AND THE DATA DECIDES IT ──
-- Loop #134 offers both. Backfill is not merely weak here, it is IMPOSSIBLE: the supply is
-- zero, not small. There are no historical contact events to promote — the 13 vendor-linked
-- activities are notes, and a note is not evidence that anyone spoke. Turning notes into
-- contact events would not be a backfill, it would be fabrication, and the standing
-- no-fabrication rule 0053 invoked when it refused to backfill `connected` applies verbatim.
-- The one 'introduced' edge would move exactly ONE vendor and leave 281 unchanged.
-- So: SUPPRESS. The view speaks only where it has evidence, and says plainly when it has none.
--
-- ── WHAT IS AND IS NOT CHANGED, BECAUSE THE TEMPTATION IS TO REWRITE MORE ──
-- The two-way definition is NOT touched. 0053 reasoned it out properly — a call or text
-- counts only when activity.connected is explicitly true, because a logged call is either a
-- conversation or a voicemail — and its kind list is complete against activity_kind as it
-- stands. Re-deriving it from a per-kind flag would be worse: `connected` is per-ROW for
-- exactly the kinds where it matters, and no column on activity_kind can express that.
-- The value-moved kind list is NOT widened either. It was checked against the live
-- vocabulary: of party_link_kind's nine slugs, 'introduced' and 'referred' are the two that
-- mean value was DELIVERED. 'can_introduce' and 'intro_requested' are an offer and an ask,
-- and counting an offer as value moved is the same error as counting an unreturned voicemail
-- as a relationship. The legacy 'intro' / 'intro_received' / 'referral' slugs hold zero rows.
-- The list is right; the evidence is missing. Those are different problems.
--
-- ── WHAT CHANGES ──
--   suggested   becomes NULL when there is no evidence at all. NULL now means "this view has
--               nothing to say", which is a third state distinct from 0 ("we have evidence,
--               and it amounts to outreach nobody answered") and from a real level. Same
--               NULL-is-not-zero discipline 0047 applied one table over.
--   disagrees   a real boolean column, so consumers stop writing `recorded is distinct from
--               suggested` and stop counting unjudged vendors as disagreements. TRUE requires
--               both sides to be non-null and to differ.
--   signal      names what the row IS, in words, so a reader is never left inferring it from
--               two nullable integers.
--   evidence_events  the count the whole thing rests on, exposed. If it is 0, nothing else on
--               the row is a judgement about the vendor — it is a statement about capture.
--
-- ── WHAT THIS COSTS, STATED HONESTLY ──
-- The view goes from 123 signals to ZERO. That is the correct number and it is also a real
-- loss of the illusion of coverage: after this, nothing in the system flags a vendor level as
-- wrong, because nothing in the system knows anything about any vendor relationship. The
-- finding is not "the view is fixed", it is "0 of 282 active vendors have a single recorded
-- relationship event, and that is the actual open loop". The guard prints that number so it
-- lands in the apply log rather than in a document nobody reopens.
--
-- Joe's taught rule — a disagreement is worth a look IN EITHER DIRECTION — is preserved, not
-- weakened: `disagrees` fires on evidence_exceeds_recorded exactly as it does on the reverse.
-- What is removed is only the case where there is no evidence to disagree with.
--
-- REVERSAL: re-run 0053's `create view` block verbatim. This file drops and recreates one
-- view and touches no table, no row and no other object.

begin;

-- DROP first, not CREATE OR REPLACE: this adds columns in the middle of the column list and
-- Postgres refuses that through a replace. Same reason 0053 dropped 0052's version.
drop view if exists v_vendor_level_suggestion;

create view v_vendor_level_suggestion as
with contact as (
  -- Unchanged from 0053, deliberately. A call or text is two-way ONLY when someone recorded
  -- that it connected; meeting/tour/loi/lease_signed are two-way by attendance; email_in and
  -- counter_received prove a reply by existing.
  select v.id as vendor_id,
         count(*) filter (
           where a.kind in ('meeting','tour','loi','lease_signed','email_in','counter_received')
              or (a.kind in ('call','text') and a.connected is true)
         ) as two_way,
         count(*) filter (
           where a.kind = 'email_out'
              or (a.kind in ('call','text') and a.connected is not true)
         ) as attempts_only
    from vendor v
    left join activity a on a.vendor_id = v.id
   group by v.id),
value_moved as (
  -- Unchanged from 0052/0053. 'introduced' and 'referred' are the two party_link kinds that
  -- mean value was DELIVERED; can_introduce and intro_requested are an offer and an ask.
  select v.id as vendor_id,
         count(*) filter (where pl.via_party = v.party_id) as they_gave,
         count(*) filter (where pl.via_party in (select id from party
                                                  where name in ('Joe Bookout','Dell McCraney'))
                            and (pl.from_party = v.party_id or pl.to_party = v.party_id)) as we_gave
    from vendor v
    left join party_link pl on pl.kind in ('introduced','referred')
                           and (pl.via_party = v.party_id
                             or pl.from_party = v.party_id
                             or pl.to_party = v.party_id)
   group by v.id),
scored as (
  select v.vendor_ref,
         p.name,
         v.relationship_level as recorded,
         c.two_way, c.attempts_only, vm.they_gave, vm.we_gave,
         c.two_way + c.attempts_only + vm.they_gave + vm.we_gave as evidence_events,
         case
           -- NOTHING RECORDED IS NOT EVIDENCE OF NOTHING. This single line is the fix.
           when c.two_way + c.attempts_only + vm.they_gave + vm.we_gave = 0 then null
           when vm.they_gave > 1 and vm.we_gave > 1 then 3
           when vm.they_gave > 0 or  vm.we_gave > 0 then 2
           when c.two_way > 0                        then 1
           -- Reached only when attempts exist and nothing came back: the meaningful 0, and
           -- 0052's original point — you can email fifty people and have fifty relationships
           -- that do not exist.
           else 0
         end as suggested
    from vendor v
    join party p        on p.id = v.party_id
    join contact c      on c.vendor_id = v.id
    join value_moved vm on vm.vendor_id = v.id
   where v.disposition = 'active')
select s.vendor_ref,
       s.name,
       s.recorded,
       s.suggested,
       -- A real column, so no consumer ever writes `recorded is distinct from suggested`
       -- again. NULL on either side is not a disagreement: one means nobody has judged the
       -- vendor (0047), the other means this view has no evidence.
       (s.recorded is not null and s.suggested is not null and s.recorded <> s.suggested)
                                                    as disagrees,
       case
         when s.evidence_events = 0        then 'no_evidence'
         when s.recorded is null           then 'unjudged_with_evidence'
         when s.recorded = s.suggested     then 'agrees'
         when s.suggested > s.recorded     then 'evidence_exceeds_recorded'
         else                                   'recorded_exceeds_evidence'
       end                                          as signal,
       s.evidence_events,
       s.two_way, s.attempts_only, s.they_gave, s.we_gave
  from scored s;

comment on view v_vendor_level_suggestion is
  'Recorded vendor relationship level against what the evidence supports (0065, closing loop '
  '#134). THE VIEW ONLY SPEAKS WHERE IT HAS EVIDENCE: suggested is NULL when a vendor has '
  'zero recorded contact events and zero delivered intros, because 0 there would mean '
  '"assessed as Prospective" when the truth is "never observed". Before this fix all 282 '
  'active vendors read as suggested = 0 and 123 read as disagreements, of which 59 were '
  'merely unjudged levels being compared against a suggestion — a signal with a 100% '
  'false-positive rate, which trains everyone to ignore it. Reports only; the level stays '
  'Joe''s judgement. Two-way contact and value-moved are defined exactly as 0053 and 0052 '
  'left them — the definitions were never the fault, the empty evidence was.';

grant select on v_vendor_level_suggestion to carr_reader;

-- ── guards BEFORE commit ─────────────────────────────────────────────────────────────────
do $$
declare
  rows_total int; silent int; speaks int; disagree int; unjudged int;
  bad int; active int;
begin
  select count(*) into active from vendor where disposition = 'active';
  select count(*),
         count(*) filter (where suggested is null),
         count(*) filter (where suggested is not null),
         count(*) filter (where disagrees),
         count(*) filter (where recorded is null)
    into rows_total, silent, speaks, disagree, unjudged
    from v_vendor_level_suggestion;

  -- (1) THE ROW SET DID NOT SHRINK. Suppressing a SIGNAL is the intent; suppressing a VENDOR
  -- would hide the capture gap this view now exists to show.
  if rows_total <> active then
    raise exception 'the view returns % row(s) for % active vendor(s) — 0065 suppresses '
                    'suggestions, never vendors', rows_total, active;
  end if;

  -- (2) NO SUGGESTION WITHOUT EVIDENCE. The fix itself, asserted rather than assumed.
  select count(*) into bad from v_vendor_level_suggestion
   where evidence_events = 0 and suggested is not null;
  if bad > 0 then
    raise exception '% vendor(s) carry a suggestion on zero evidence events — that is the '
                    '100%% false-positive signal loop #134 exists to kill', bad;
  end if;

  -- (3) AN UNJUDGED LEVEL IS NEVER A DISAGREEMENT. 0047''s NULL-is-not-zero rule, enforced at
  -- the consumer this time instead of only at the column.
  select count(*) into bad from v_vendor_level_suggestion
   where disagrees and (recorded is null or suggested is null);
  if bad > 0 then
    raise exception '% row(s) report a disagreement with a NULL on one side — a vendor nobody '
                    'has judged is not in conflict with anything', bad;
  end if;

  -- (4) 0053'S GUARANTEE SURVIVES THE REWRITE: nobody is promoted on attempts alone.
  select count(*) into bad from v_vendor_level_suggestion
   where suggested >= 1 and two_way = 0;
  if bad > 0 then
    raise exception '% vendor(s) suggested at level 1+ with zero two-way contact — an attempt '
                    'is being counted as a relationship (0053''s guard, re-run here)', bad;
  end if;

  -- (5) EVERY ROW IS CLASSIFIED. A signal column with a hole in it is worse than none.
  select count(*) into bad from v_vendor_level_suggestion where signal is null;
  if bad > 0 then raise exception '% row(s) carry a NULL signal', bad; end if;

  raise notice 'vendor level suggestion now speaks only from evidence. % active vendor(s); % '
               'row(s) SILENT for want of any recorded event; % row(s) with something to say; '
               '% real disagreement(s), down from 123. % vendor(s) remain unjudged (NULL '
               'level), which is a prompt to judge and NOT a conflict. THE REMAINING FINDING '
               'IS THE SILENCE: this view will stay quiet until vendor contact is actually '
               'logged — today 13 activities carry a vendor_id and every one of them is a '
               'note, not a contact.',
               active, silent, speaks, disagree, unjudged;
end $$;

commit;
