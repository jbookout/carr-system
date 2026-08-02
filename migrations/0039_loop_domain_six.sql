-- 0039_loop_domain_six.sql — 0038's three domains become Joe's six.
--
-- WHY THIS IS A SECOND MIGRATION AND NOT AN EDIT TO 0038. It should have been one from
-- the start. 0038 was applied while it still carried three domains; the six-domain
-- vocabulary was then written straight over that file — twice — and migrate.py refused
-- the next run with "0038_loop_domain.sql was EDITED after being applied (sha mismatch)".
-- The guard was right and had already been quoted back to Joe earlier the same day, on
-- 0036, as the reason an applied migration cannot be corrected in place. 0038 has been
-- restored byte-for-byte to its applied content (sha a813c2da68e02b37, verified) and
-- every change lives here, forward-only, which is the rule the guard exists to enforce.
--
-- Nothing was classified under the three-domain vocabulary (verified: 0 rows with a
-- non-null domain), so no loop changes meaning and no data is rewritten. This widens an
-- empty vocabulary.
--
-- JOE'S SIX, in his words: "Deals | Prospecting | System | Marketing | Networking |
-- Business — networking would be related to vendor networking - Business would be
-- anything business related that doesn't filter to one of the other categories"
--
--   deals       — active transactions. INCLUDES connecting a client to a vendor when the
--                 deal is live: on an active deal the vendor introduction IS deal work.
--   prospecting — lead GENERATION and lead CONVERSION, and deliberately nothing else.
--                 Radar lanes, lead sources, outreach, a pursuit working toward client.
--   networking  — the VENDOR network: setting vendor meetings, identifying new vendors,
--                 connecting vendors to other vendors, and connecting a PROSPECT to a
--                 vendor (no active deal yet).
--   marketing   — social, newsletter, content, video, profile. Time-boxed and PUBLIC,
--                 so it cannot sit behind infrastructure; a queued batch fires whether
--                 or not anyone read it.
--   business    — Joe's catch-all: business work fitting none of the above. Deliberate,
--                 so nothing gets mis-filed into a specific lane just to have a home.
--   system      — record layer, repo, automation, hosting. Real work that must never
--                 outrank a live deal in the render.
--
-- WHY SIX AND NOT THREE. The three-domain draft had to fudge the radar lanes (#81
-- entity-formation, #74 relocation, #82 PECOS): build tasks, so "system", but they exist
-- to produce leads. Joe's split resolves it — they are PROSPECTING. A vocabulary that
-- forces a fudge is the wrong vocabulary.
--
-- THE DEALS / PROSPECTING / NETWORKING BOUNDARY IS DIRECTIONAL, and WHO was introduced
-- decides it. Joe, correcting a first draft of this comment that said a vendor introduces
-- "someone": "and of course, if a vendor introduces me to another vendor its networking.
-- so 'Someone' is too vague. a prospect vs a vendor needs to be distinguished"
--
--   vendor introduces a PROSPECT to Joe           -> PROSPECTING (the prospect takes over;
--                                                    the intro is how it arrived, not what
--                                                    the loop is now about)
--   vendor introduces ANOTHER VENDOR to Joe       -> NETWORKING (the vendor network just
--                                                    grew; that is the work)
--   Joe connects a PROSPECT to a vendor           -> NETWORKING (reciprocity; no deal yet)
--   Joe connects a CLIENT to a vendor, deal live  -> DEALS
--   vendor-to-vendor connections Joe brokers      -> NETWORKING
--   setting vendor meetings, sourcing new vendors -> NETWORKING
--
-- THE UNDERLYING PRINCIPLE, when a case is not on the list: classify by WHAT THE WORK IS,
-- not by who happens to appear in it. Lead generation and conversion are prospecting.
-- Growing and servicing the vendor network is networking — even when a prospect is the
-- person being connected, because the work is reciprocity, not conversion. Executing a
-- live transaction is deals. That is why a prospect can appear in a networking loop and a
-- vendor can appear in a deals loop without either being mis-filed.
--
-- AND WHY PROSPECTING IS DRAWN NARROWLY (Joe): "prospecting will mostly be lead gen and
-- lead conversion activities. i dont want that category to get too noisy since it has the
-- most volume". It carries the most volume, so anything adjacent that lands there drowns
-- the lead work it exists to hold. Keeping vendor-side activity out is what stops
-- prospecting becoming the new dumping ground — the same failure this column exists to
-- fix, one level down.
--
-- SORT ORDER IS THE POINT, not decoration. Revenue-proximate first, infrastructure last,
-- matching the standing instruction to prioritise prospecting and pipeline building at
-- this stage of the practice.

begin;

-- Re-sort the three that exist, so the six interleave in Joe's priority order.
update loop_domain set sort = 40, label = 'Marketing — social, newsletter, content, profile'
 where slug = 'marketing';
update loop_domain set sort = 50, label = 'Business — everything else business-side'
 where slug = 'business';
update loop_domain set sort = 60, label = 'System — record layer, repo, automation, hosting'
 where slug = 'system';

insert into loop_domain (slug, label, sort) values
  ('deals',       'Deals — active transactions (incl. vendor intros on a live deal)', 10),
  ('prospecting', 'Prospecting — lead generation and lead conversion',                20),
  ('networking',  'Networking — vendor meetings, new vendors, vendor-to-vendor, prospect-to-vendor', 30)
on conflict (slug) do nothing;

comment on column loop_item.domain is
  'deals | prospecting | networking | marketing | business | system (loop_domain, Joe''s '
  'vocabulary 2026-08-02). NULL = not yet classified, and that renders as its own '
  'unsorted section rather than defaulting into a domain — a guessed classification '
  'would bury exactly what this column exists to surface. Boundary rule: classify by '
  'WHAT THE WORK IS, not who appears in it. A vendor introducing a PROSPECT is '
  'prospecting; a vendor introducing a VENDOR is networking; connecting a prospect to a '
  'vendor is networking; connecting a client to a vendor on a LIVE deal is deals.';

commit;

-- guard: six domains, Joe's order, still nothing classified, no orphaned references.
do $$
declare doms int; first_slug text; last_slug text; dupes int; orphans int;
begin
  select count(*) into doms from loop_domain;
  if doms <> 6 then raise exception 'expected 6 loop domains, found %', doms; end if;

  select slug into first_slug from loop_domain order by sort asc  limit 1;
  select slug into last_slug  from loop_domain order by sort desc limit 1;
  if first_slug <> 'deals' then
    raise exception 'deals must sort first, got % — the ordering IS the fix', first_slug;
  end if;
  if last_slug <> 'system' then
    raise exception 'system must sort last, got % — system outranking revenue work is '
                    'the defect being repaired', last_slug;
  end if;

  select count(*) into dupes from (select sort from loop_domain group by sort having count(*)>1) x;
  if dupes > 0 then raise exception 'loop_domain.sort collision on % value(s)', dupes; end if;

  select count(*) into orphans from loop_item li
   where li.domain is not null
     and not exists (select 1 from loop_domain d where d.slug = li.domain);
  if orphans > 0 then
    raise exception '% loop(s) reference a domain that no longer exists', orphans;
  end if;

  raise notice 'loop_domain now 6 (deals first / system last). classified so far: % '
               '(classification is its own reviewed pass)',
               (select count(*) from loop_item where domain is not null);
end $$;
