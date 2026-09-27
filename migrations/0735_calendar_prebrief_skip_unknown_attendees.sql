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
--   exactly one live unmerged ref           -> that ref (unchanged)
--   no canonical ref at all, live or merged -> NULL: the caller skips and
--                                              counts the attendee
--   anything else (two or more live refs, or
--   only merged/tombstoned refs)            -> 22023, unchanged: an ambiguous
--                                              or tombstoned identity still
--                                              refuses rather than choosing
--
-- Same signature, owner, SECURITY DEFINER, search_path and grants (CREATE OR
-- REPLACE keeps the ACL), so no capability or registry surface changes.
-- Nothing is stored: the raw address exists only inside this call, as before.


create or replace function ops.resolve_calendar_prebrief_email_ref(p_email text)
returns text language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare v_live integer; v_any integer; v_ref text;
begin
  perform ops.calendar_prebrief_resolver_sponsor();
  if p_email is null or length(p_email)>320
     or lower(btrim(p_email)) !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$' then
    raise exception using errcode='42501',message='calendar prebrief email resolver requires one bounded exact email';
  end if;
  select count(distinct r.ref) filter (where not r.merged),
         count(distinct r.ref),
         min(r.ref) filter (where not r.merged)
    into v_live,v_any,v_ref
    from party p join v_ref_index r on r.party_id=p.id
   where lower(btrim(p.email))=lower(btrim(p_email));
  if v_any=0 then
    return null;
  end if;
  if v_live<>1 then
    raise exception using errcode='22023',message='calendar prebrief email resolver requires exactly one live unmerged canonical ref';
  end if;
  return v_ref;
end $$;

comment on function ops.resolve_calendar_prebrief_email_ref(text) is
  'One exact attendee email -> its single live canonical ref; NULL when the '
  'record holds no canonical ref for it (caller skips and counts); 22023 when '
  'ambiguous or only tombstoned. Raw email never stored. Migration 0735.';

