-- 0102_lead_client_link.sql
-- THE LEAD ↔ CLIENT LINK BECOMES READABLE (loop #127).
--
-- THE BLOCKER. The link between a lead and the client it became already exists in the
-- data and NO READ VERB FOLLOWS IT. `find` matched deals by NAME only, so a search that
-- landed on a lead returned deals:[] even when that lead's own client carried a live
-- deal — the deal is filed under the practice's name, the search was for the doctor's.
-- The record knew; the read surface did not ask.
--
-- MEASURED BEFORE BUILDING, against production 2026-08-13:
--   * lead.client_id set ........................ 10 leads
--   * lead.party_id = client.party_id ........... 21 pairs
--   * union of both ............................. 23 leads
-- So the pointer alone would have found fewer than half of them. Two exact keys, not one.
--
-- NEVER MATCH ON NAME. This row carries that instruction in its own body and it is not
-- decoration: this system already welded Jenna Beasley to Jeff Beasley DMD once — two
-- different people — through an import that matched on a surname. Every basis below is
-- an equality on a uuid.
--
-- THE THREE BASES, ranked, each labelled in the output so a reader can tell them apart:
--   1. conversion  — lead.client_id. The explicit pointer, set when a lead converts.
--                    This is the only basis that means "this lead BECAME this client".
--   2. same_party  — lead and client hang off the same party, merge-survivors resolved.
--                    Same person wearing both records. Rule 2e8b4840 already settles
--                    that this is not a duplicate to be cleaned up; it is a link to be
--                    followed.
--   3. same_org    — the lead's person sits under the same ORG party as the client, or
--                    directly under the client's own party. This is NOT a conversion and
--                    is labelled so it can never be read as one; it answers the different
--                    and useful question "is this lead's practice already a client".
--
-- WHAT THIS STILL MISSES, stated so nobody reads the view as complete. The row's own
-- example — lead L-004 (Dr. Kaydee Zimmern) and client C-112, which carries the live deal
-- "Gulf Coast Pelvic Health" — does NOT link here, and the cause is not this view. That
-- practice exists as FOUR separate party rows (P-0137, P-0312, P-0585, P-0621), so the
-- org keys are unequal and no exact-key traversal can join them. Welding them by name is
-- precisely what this row forbids. It resolves when the party dedup lands (loop #125),
-- and this view starts returning it that day with no further change. Stated in the view
-- comment too, because a reader who sees an empty result deserves to know which of
-- "no link" and "duplicate parties" they are looking at.
--
-- READ-ONLY. No column added, no row written, no existing view changed.

begin;

create or replace view v_lead_client_link as
-- 1. THE EXPLICIT POINTER.
select l.id                as lead_id,
       l.registry_ref      as lead_ref,
       lp.name             as lead_name,
       c.id                as client_id,
       c.roster_ref        as client_ref,
       cp.name             as client_name,
       'conversion'::text  as link_basis,
       1                   as basis_rank,
       (lp.merged_into is not null
        or coalesce(c.merged_into, cp.merged_into) is not null) as either_merged
  from lead l
  join party lp   on lp.id = l.party_id
  join client c   on c.id  = l.client_id
  join party cp   on cp.id = c.party_id
union
-- 2. SAME PARTY, merge-survivors resolved on BOTH sides. A record whose party was merged
--    away still points at the tombstone; resolving through merged_into is what keeps the
--    link alive across a merge instead of silently dropping it.
select l.id, l.registry_ref, lp.name, c.id, c.roster_ref, cp.name,
       'same_party', 2,
       (lp.merged_into is not null
        or coalesce(c.merged_into, cp.merged_into) is not null)
  from lead l
  join party lp  on lp.id = l.party_id
  join party cp  on coalesce(cp.merged_into, cp.id) = coalesce(lp.merged_into, lp.id)
  join client c  on c.party_id = cp.id
 where lp.deleted_at is null and cp.deleted_at is null
union
-- 3. SAME ORG, or the lead's person sitting directly under the client's party. Labelled
--    distinctly on purpose: an org neighbour is not a conversion and must never be read
--    as one.
select l.id, l.registry_ref, lp.name, c.id, c.roster_ref, cp.name,
       'same_org', 3,
       (lp.merged_into is not null
        or coalesce(c.merged_into, cp.merged_into) is not null)
  from lead l
  join party lp  on lp.id = l.party_id
  join party cp  on cp.id = lp.org_id or cp.org_id = lp.org_id
  join client c  on c.party_id = cp.id
 where lp.org_id is not null
   and lp.deleted_at is null and cp.deleted_at is null
   and coalesce(lp.merged_into, lp.id) <> coalesce(cp.merged_into, cp.id);

comment on view v_lead_client_link is
  'Every lead that is linked to a client, by EXACT KEY only — never by name (0102, loop '
  '#127). link_basis says which: conversion = lead.client_id, the explicit pointer set at '
  'conversion; same_party = both records hang off one party with merges resolved; same_org '
  '= the lead sits under the client''s org or under the client''s own party, which answers '
  '"is this practice already a client" and is NOT a conversion. An empty result for a lead '
  'whose practice is obviously a client usually means that practice exists as several '
  'party rows, which is the party dedup (loop #125), not a missing link.';

grant select on v_lead_client_link to carr_reader, carr_writer, carr_exporter;

-- THE ONE-ROW ANSWER a read verb actually wants: for a lead, its best link; for a client,
-- its originating lead. Ranked so an explicit conversion always beats a party match and a
-- party match always beats an org neighbour, and no caller has to re-derive that order.
create or replace view v_lead_client_best as
select distinct on (lead_id)
       lead_id, lead_ref, lead_name, client_id, client_ref, client_name,
       link_basis, either_merged
  from v_lead_client_link
 order by lead_id, basis_rank, client_ref;

comment on view v_lead_client_best is
  'One row per linked lead: its strongest link only, conversion beating same_party beating '
  'same_org (0102). The read verbs traverse THIS so the ranking lives in one place.';

grant select on v_lead_client_best to carr_reader, carr_writer, carr_exporter;

-- DONE-TEST. Asserted against production reality as measured on 2026-08-13, expressed as
-- floors rather than exact counts so ordinary data growth never turns a passing migration
-- into a failing one — but tight enough that a broken join fails loudly.
do $$
declare n_conv int; n_party int; n_best int; n_bad int;
begin
  select count(*) into n_conv  from v_lead_client_link where link_basis = 'conversion';
  select count(*) into n_party from v_lead_client_link where link_basis = 'same_party';
  select count(*) into n_best  from v_lead_client_best;
  if n_conv < 10 then
    raise exception '0102 done-test: conversion links = %, expected at least the 10 measured', n_conv;
  end if;
  if n_party < 21 then
    raise exception '0102 done-test: same_party links = %, expected at least the 21 measured', n_party;
  end if;
  if n_best < 23 then
    raise exception '0102 done-test: best-link rows = %, expected at least the 23 measured union', n_best;
  end if;
  -- A lead must never appear twice in the ranked view; that is the whole point of it.
  select count(*) into n_bad from (
    select lead_id from v_lead_client_best group by lead_id having count(*) > 1) x;
  if n_bad <> 0 then
    raise exception '0102 done-test: % lead(s) carry more than one best link', n_bad;
  end if;
  raise notice '0102 done-test ok — % conversion, % same_party, % best-link rows',
               n_conv, n_party, n_best;
end $$;

commit;
