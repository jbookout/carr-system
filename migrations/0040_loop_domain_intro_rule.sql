-- 0040_loop_domain_intro_rule.sql — the vendor-introduction rule, corrected.
--
-- Comment-only. No table, no column, no row of business data changes. The doctrine lives
-- on the column so it is read wherever the schema is read, and 0039's version of it was
-- wrong in a way that would have mis-filed the most valuable loops in the system.
--
-- WHAT 0039 GOT WRONG. It stated: "vendor introduces a PROSPECT to Joe -> PROSPECTING
-- (the prospect takes over)". Joe, 2026-08-02:
--
--   "no actually a vendor introducing a prospect will hopefully become a deal. its
--    typically going to skip prospect and go straight to deal if we are being connected
--    but in the case of renalus, there is no deal yet. its just an opportuinty for a
--    relationship that we hope will result in deals in the future"
--
-- So prospecting is the EXCEPTION on that path, not the rule. A vendor connecting Joe to
-- a prospect normally signals real intent, and the loop belongs in DEALS from the start.
-- It sits in prospecting only while it is still conversion work with no deal formed.
--
-- Getting this backwards would have buried live transactions in the prospecting section —
-- the highest-volume, most deliberately-narrow domain — which is precisely the burial
-- this whole column was added to end.
--
-- THE RULE, complete:
--   vendor introduces a PROSPECT, intent real, a deal forms  -> DEALS (the normal path;
--                                                               it skips prospecting)
--   vendor introduces a PROSPECT, no deal yet, converting    -> PROSPECTING
--   vendor introduces ANOTHER VENDOR                         -> NETWORKING
--   Joe connects a PROSPECT to a vendor                      -> NETWORKING (reciprocity)
--   Joe connects a CLIENT to a vendor, deal live             -> DEALS
--   vendor-to-vendor connections Joe brokers                 -> NETWORKING
--   setting vendor meetings, sourcing new vendors            -> NETWORKING
--
-- THE WORKED CASE, kept because it is the one that will recur. Renalus (C-125) is a 10+
-- location nephrology group that OWNS its real estate and self-leases — an owner-occupier
-- at portfolio scale. Justin Dansby, its former CEO, made a warm three-way intro to CEO
-- Jennifer Thomas on 2026-07-13. There is no deal: the file's own instruction is
-- "Relationship play first; pace SLOW and low-pressure", and the opportunities are future
-- acquisition, build-to-suit, sale-leaseback and disposition. Joe's ruling: PROSPECTING,
-- not networking (Jennifer is a potential client, not a vendor) and not business (it is
-- still lead conversion, merely slow). Every multi-site group will arrive this way.
--
-- The underlying principle is unchanged: classify by WHAT THE WORK IS, not by who appears
-- in it. Lead generation and conversion are prospecting. Growing and servicing the vendor
-- network is networking, even when the person being connected is a prospect, because the
-- work is reciprocity rather than conversion. Executing a live transaction is deals.

begin;

comment on column loop_item.domain is
  'deals | prospecting | networking | marketing | business | system (loop_domain, Joe''s '
  'vocabulary 2026-08-02). NULL = not yet classified, and that renders as its own '
  'unsorted section rather than defaulting into a domain — a guessed classification '
  'would bury exactly what this column exists to surface. '
  'BOUNDARY RULE: classify by WHAT THE WORK IS, not who appears in it. '
  'A vendor introducing a PROSPECT normally means real intent and goes straight to '
  'DEALS; it is PROSPECTING only while no deal has formed and it is still conversion '
  'work (the Renalus C-125 case). A vendor introducing a VENDOR is networking. '
  'Connecting a prospect to a vendor is networking (reciprocity). Connecting a client '
  'to a vendor on a LIVE deal is deals. Prospecting is drawn narrowly on purpose: it '
  'carries the most volume, so anything adjacent that lands there drowns the lead work '
  'it exists to hold.';

commit;

-- guard: comment-only. The vocabulary and every classification must be untouched.
do $$
declare doms int; classified int; first_slug text; last_slug text;
begin
  select count(*) into doms from loop_domain;
  if doms <> 6 then raise exception 'loop_domain should still hold 6, found %', doms; end if;

  select slug into first_slug from loop_domain order by sort asc  limit 1;
  select slug into last_slug  from loop_domain order by sort desc limit 1;
  if first_slug <> 'deals' or last_slug <> 'system' then
    raise exception 'domain ordering disturbed by a comment-only migration: first=%, last=%',
                    first_slug, last_slug;
  end if;

  select count(*) into classified from loop_item where domain is not null;
  raise notice 'intro rule corrected. domains: %, loops classified: % (unchanged by this '
               'migration by design)', doms, classified;
end $$;
