-- 0735_calendar_prebrief_skip_unknown_attendees.sql
--
-- RELEASE ORDER: this must be applied AFTER 0733 and 0734 (RW02, held on an
-- unmerged branch). tools/migrate.py refuses a reordered ledger, so the
-- orchestrator sequences the merge.
--
-- The ruling (orchestrator, 2026-09-27, off Joe's calendar-read decision
-- eca7030a): an outside attendee who matches NO contact in the record is
-- skipped and counted, not a reason to refuse Joe's whole prebrief. An
-- attendee who matches MORE THAN ONE live contact still refuses, because
-- misattribution is the real risk and an unknown person is not.
--
-- Before this, ops.resolve_calendar_prebrief_email_ref raised 22023 on any
-- count other than one. With a 52-day window over Joe's calendar that meant
-- one unknown outside attendee refused the entire snapshot: on 2026-09-27, 9
-- of 15 outside addresses in the window were unknown and the prebrief could
-- never complete.
--
-- Three outcomes, decided here and nowhere else:
--   no party row at all carries the address -> NULL: the caller skips and
--   (deleted rows included)                    counts the attendee
--   exactly one live unmerged ref             -> that ref (unchanged)
--   anything else: two or more live refs,     -> 22023, unchanged: the record
--   only merged refs, a soft-deleted party,       knows this person but not
--   or a party with no canonical ref yet          unambiguously, so it still
--                                                 refuses rather than choosing
--
-- "Unknown" is decided at the PARTY level, not the ref level: a person the
-- record already holds (pending, tombstoned, merged) is never reported as a
-- stranger for intake to create a second time.
--
-- Same signature, owner, SECURITY DEFINER, search_path and grants (CREATE OR
-- REPLACE keeps the ACL), so no capability or registry surface changes.
-- Nothing is stored: the raw address exists only inside this call, as before.


create or replace function ops.resolve_calendar_prebrief_email_ref(p_email text)
returns text language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare v_parties integer; v_live integer; v_ref text;
begin
  perform ops.calendar_prebrief_resolver_sponsor();
  if p_email is null or length(p_email)>320
     or lower(btrim(p_email)) !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$' then
    raise exception using errcode='42501',message='calendar prebrief email resolver requires one bounded exact email';
  end if;
  select count(*) into v_parties
    from party p
   where lower(btrim(p.email))=lower(btrim(p_email));
  if v_parties=0 then
    return null;
  end if;
  select count(distinct r.ref),min(r.ref) into v_live,v_ref
    from party p join v_ref_index r on r.party_id=p.id and not r.merged
   where lower(btrim(p.email))=lower(btrim(p_email));
  if v_live<>1 then
    raise exception using errcode='22023',message='calendar prebrief email resolver requires exactly one live unmerged canonical ref';
  end if;
  return v_ref;
end $$;

comment on function ops.resolve_calendar_prebrief_email_ref(text) is
  'One exact attendee email -> its single live canonical ref; NULL when no '
  'party row carries it (caller skips and counts); 22023 for any other '
  'known-but-not-unique identity. Raw email never stored. Migration 0735.';

