-- 0041_classify_loops.sql — all 79 open loops get a domain, and the bell cap goes
-- per-domain at 3.
--
-- Joe reviewed every one of these before they were written. That is the point: 0038 left
-- the column nullable and classified nothing precisely so this pass could be read rather
-- than guessed, and three of his rulings overturned what a session had proposed:
--
--   #76 space-search coverage -> BUSINESS, not deals. "space search for a certain deal is
--       deals. space search for market knowledge in general is business" — this loop is
--       platform coverage (which MLS covers which county), not the Hughes search.
--   #37 broker enrichment    -> BUSINESS, not networking. "thats more about going deep in
--       our market"
--   #46 referral/review      -> DEALS, not prospecting. "referral / review ask is deal. it
--       needs to be part of the deal close out. Once its captured it becomes marketing
--       where we need to deploy it as marketing material" — the ask belongs to closeout;
--       the deployment is a separate marketing act. NOTE: this loop spans that handoff and
--       should probably become TWO loops, because one loop covering both is how the
--       deploy half quietly never happens.
--
-- KEYED ON id, NOT number. Loop numbers are NOT unique and two collisions are live right
-- now: #111 is both the OAuth connector and the week-of-8/3 social batch, and #103 is both
-- the repo-side space-search repoint (system) and Life Dental Group's Stokes markup
-- (deals, with an Aug 20 client meeting). Classifying by number would have mis-filed four
-- loops, two of them the most time-critical in the set.
--
-- WHAT THE CLASSIFICATION EXPOSES, and it is the whole argument for the column:
--   deals 6 · prospecting 11 · networking 11 · marketing 24 · business 3 · system 24
--   * MARKETING is the largest domain in the system, larger than system. Most of it is
--     tabled or awaiting Joe's go, so it reads as accumulated decisions rather than active
--     work — but it is where the volume actually sits.
--   * NETWORKING is 11 loops and every single one was in the BACKLOG; not one was hot.
--     Read that beside #70 ("the vendor sheet has ZERO Last Touch dates — all 284 rows"),
--     #72 ("VENDOR COVERAGE GAP — two categories at ZERO, both are LEAD SOURCES") and the
--     0.7% vendor capture coverage from v_capture_coverage: the network that produces most
--     of the lead flow has had no hot loop at all.
--   * DEALS is 6 of 79. #103 (Life Dental Group, Aug 20 meeting) sat in the backlog under
--     infrastructure chores. That single row is the burial Joe described, with a date on it.

begin;

update loop_item li set domain = v.dom
  from (values
    -- deals (6)
    ('8f6b2fa7-854d-4a00-86c0-01e1ee9f099f','deals'),       -- 20  7 active deals promoted
    ('68d3c06b-e35d-456a-bb22-274995d9a3e2','deals'),       -- 46  referral/review flywheel
    ('2451be62-2104-4e7f-8d40-f1e56d38c814','deals'),       -- 50  deal-type tags
    ('bb41a1af-f165-4e99-b6a7-8e85b226ff1b','deals'),       -- 103 Life Dental Group, Aug 20
    ('087f1223-3499-43d3-ac39-2a96724bfaea','deals'),       -- 83  Gulf Coast Pelvic Floor
    ('f1bd62bf-6dbd-43de-ab74-c2c7db599694','deals'),       -- 107 Petersen / Club30A
    -- prospecting (11)
    ('d6584379-f988-40bf-be1d-aa62b8aedf73','prospecting'), -- 22  sparse Dell prospect rows
    ('8556962c-1231-407c-9433-2c93f040fb5b','prospecting'), -- 44  FL license lead-gen
    ('3af51c4f-646a-4300-9131-20ebfa150e19','prospecting'), -- 45  lease-expiry radar
    ('cc04fdf5-0f38-46ec-95e8-066b3b01ceba','prospecting'), -- 63  Weiler follow-up Oct 2026
    ('ee970e14-f482-4371-b21b-10034a891ffe','prospecting'), -- 69  Sunbiz-first sweep
    ('b1226d6d-79e7-485d-a5ef-5e4f46ff42f4','prospecting'), -- 77  Tallahassee ramp-up
    ('a9e96b57-920f-47a7-b4fd-bca07803965a','prospecting'), -- 78  NPI wiring, item 4 gated
    ('47da364d-82aa-47c8-84e6-8c8dff8ff4cc','prospecting'), -- 74  relocation lane
    ('d527f4af-0486-4503-845f-35ad255131a0','prospecting'), -- 81  entity-formation lane
    ('7623381c-2f99-4f87-88a9-bad4f84d47fd','prospecting'), -- 82  PECOS lane
    ('de7c4b92-11b3-4842-9f8b-a1b6dce85bfb','prospecting'), -- 93  Renalus (no deal yet)
    -- networking (11) — every one of these was in the backlog
    ('abbe31df-07d6-46b9-8d83-c8fa935c9b5e','networking'),  -- 18  vendor ownership tags
    ('0f707acf-9d59-4a1f-b155-17d690615c90','networking'),  -- 19  vendor Seeking fields
    ('8c8d4a00-85ed-49b9-adaa-518cb4ea720f','networking'),  -- 42  Dental & Medical Counsel
    ('a205b6ce-980d-4ff5-8cfb-3c19566227db','networking'),  -- 43  SharePoint vendor recon
    ('ea81d4ff-69fc-4c15-8d3a-1711deb65843','networking'),  -- 70  ZERO vendor last-touch
    ('f8c31071-09e5-440a-998c-db39baaddb79','networking'),  -- 72  vendor coverage gap
    ('9270006d-a09f-4ef5-a0b0-5223ee120864','networking'),  -- 98  vendor data conflicts
    ('33c89740-6aac-4f00-af36-ac11b2885c5d','networking'),  -- 117 backfill Nilesh Patel
    ('db55a923-3b82-4f90-bc6c-c09fad2310eb','networking'),  -- 118 backfill Jon Shaw
    ('59f745e7-1f56-4e8a-9c50-bf3df6cdbf51','networking'),  -- 119 backfill Nate Woulston
    ('9a408e23-cdb1-4d26-a699-b502df043f9c','networking'),  -- 120 backfill Trey Crowley
    -- marketing (24)
    ('4d99ccdb-820c-4fd4-8d37-6ddd2bcb1ea3','marketing'),   -- 1   content bank inputs
    ('6e571bdd-8748-490d-8ceb-61796e1f155d','marketing'),   -- 4   LinkedIn dev app
    ('174c0bf4-91e5-403c-ac5e-b1fd85530b2b','marketing'),   -- 5   Meta dev app
    ('1088372b-ed87-42ac-bf49-afa72f62f5d5','marketing'),   -- 6   X dev account
    ('f3efb6fe-fb40-43b3-8f5b-2c561a39278f','marketing'),   -- 7   brand assets
    ('9e9925d7-f181-4c53-bd78-234aa5f73b02','marketing'),   -- 47  newsletter drip
    ('ebf1d31a-7c77-4db7-9c53-107437e0656b','marketing'),   -- 51  7/13-7/19 social batch
    ('5a751d45-e779-4dc7-b715-3f83903df3a4','marketing'),   -- 52  Firefly scene library
    ('89e77cef-e4af-4135-92dc-6dfbde442560','marketing'),   -- 55  GBP + web identity
    ('8feda309-1999-43db-b0c0-7070d6e1b0e5','marketing'),   -- 64  video lane pilot
    ('c8f04628-f763-4677-afb2-d395dc1e6356','marketing'),   -- 87  DIY email nurture
    ('12c402ba-85b6-4996-b5d4-292fe855faa9','marketing'),   -- 88  card visual system
    ('0ff90a63-146f-4003-9292-f3c5992f2b44','marketing'),   -- 89  lead magnet
    ('cc35ee35-24ac-40a7-8fe6-acc5c79926cb','marketing'),   -- 90  Facebook Ads
    ('28ee798f-eeb8-4c6a-a6a2-248828a516a9','marketing'),   -- 91  cross-platform batches
    ('c14867ce-5007-4160-9f5e-73c5fe13ef67','marketing'),   -- 92  doctor community group
    ('f7b6b750-0e2c-4124-b7a8-38a97da1eceb','marketing'),   -- 95  Awareness Play proposal
    ('f3f5386d-0e95-4153-b2cc-7ad0ddf84355','marketing'),   -- 108 market aerials provenance
    ('8a921043-939a-492b-824f-bf9cac84e9bc','marketing'),   -- 114 X/IG in Search Console
    ('b372b445-dbb0-4b68-a62f-60c8cbc70bd6','marketing'),   -- 86  five-numbers video series
    ('b1b45f3e-2828-43be-8d2b-ca8cf98f9d14','marketing'),   -- 88  add header to bio
    ('41fe88f3-7255-41c9-8158-1385ba05a651','marketing'),   -- 94  week-of-7/27 batch
    ('1904e222-cd18-47f3-9b45-7e17147a9283','marketing'),   -- 95  CARR newsletter proposal
    ('ec69fc84-c733-423f-808c-49552c17d14e','marketing'),   -- 111 week-of-8/3 batch (FIRES)
    -- business (3)
    ('85e9a986-9618-43ab-b7cd-1c220a3b96ef','business'),    -- 37  broker enrichment
    ('a111bff5-ab41-4988-9b27-8e0e395176e5','business'),    -- 48  lease abstraction service
    ('333a2012-a1d2-409a-88b6-cc03d61b3e4a','business'),    -- 76  space-search coverage
    -- system (24)
    ('35e03c01-d93d-4eda-b16a-906cd8567872','system'),      -- 3   BLOTATO_API_KEY
    ('9142330d-beaf-42fc-a50b-c7820f37712d','system'),      -- 10  pending deletions
    ('8ffb2458-8f4f-4b22-a2ca-e694442af8ba','system'),      -- 28  Make.com closed?
    ('14b77ca3-0cd0-4aa6-aa67-8736d6628d5f','system'),      -- 36  ID-bridge remainders
    ('7d092a45-a300-4ebf-ac67-9a0a037373b1','system'),      -- 39  17 duplicate names
    ('b487bb53-302b-4813-b75a-ec8a9551ead4','system'),      -- 49  Dell twin-brain onboarding
    ('61ec6d75-dfd3-4f6e-9164-55e3278ca03e','system'),      -- 66  gallery hygiene (repaired)
    ('2fee467d-521a-4163-aa8c-3e73bca3c9ed','system'),      -- 68  housekeeping gaps
    ('94c52407-279f-40aa-aa29-83bc63753ce0','system'),      -- 113 corrections-sweep workflow
    ('a22687c7-7703-4a69-a747-c1bc4113f97d','system'),      -- 115 system map rebuild
    ('15d177b0-2a2f-4c0e-bb7f-1ba9d1892516','system'),      -- 116 Source Material ledger
    ('a1c0dc3c-e898-4ad7-81b5-be0eaa432f22','system'),      -- 121 WAVE 5 Doc gate
    ('1253d40c-e166-404b-8212-c0f3555808ad','system'),      -- 122 curriculum dashboard
    ('774d0fe8-1eb8-4ebe-8d08-60a27c6e78a8','system'),      -- 123 verification backfill
    ('b09f5a11-45fd-45a3-a9d2-b01365b2e494','system'),      -- 125 party dedup gate
    ('234e34c3-b290-498c-a80e-7a81fd527076','system'),      -- 126 critical-date gate
    ('4a5df6fe-2391-4c4b-8afd-d30aefdfd2fc','system'),      -- 127 read-path traversal
    ('94e7911b-d0ad-4cbe-8306-2e3673878f80','system'),      -- 84  Salesforce import
    ('11a51941-cf56-4aa5-ba19-efa4eeb86364','system'),      -- 103 repo-side repoint
    ('b38ab262-7962-4424-8090-ccde2720c5c3','system'),      -- 105 client-link host
    ('96fefc9d-9f41-4b76-a791-46e451a6b2b0','system'),      -- 110 nightly chain
    ('fb951af7-93f8-4408-849a-fb30b42c711e','system'),      -- 111 OAuth connector
    ('526092cc-b76b-45b4-b145-6d3b4ce09959','system'),      -- 124 write attribution
    ('9e2dba35-fbbc-4cb3-bbf0-d9cb325bf9f0','system')       -- 128 capture coverage
  ) as v(id, dom)
 where li.id = v.id::uuid;

-- ---------- the bell cap, now per-domain at 3 ----------
-- The old rule was 5 across the WHOLE hot list, written when there was one
-- undifferentiated list. With six domains that is under one bell per lane, so marketing
-- or networking could never flag anything without starving deals — and the observed
-- result was everything drifting to marker 'none' until the hot list held 21 items
-- against its own cap of 5. Joe, 2026-08-02: "yes make the cap per domain and lower to 3".
--
-- A VIEW, NOT A CONSTRAINT, deliberately. A hard refusal would block a real write at the
-- worst moment; an undetected cap is the prose-that-nothing-obeys failure this audit spent
-- the day unpicking. A view is the third thing: the breach is visible, countable, and
-- surfaces on the heartbeat, without ever stopping Joe from flagging something urgent.
create or replace view v_loop_bell_cap as
select coalesce(li.domain, '(unclassified)') as domain,
       count(*)                              as bells,
       3                                     as cap,
       count(*) > 3                          as over_cap
  from loop_item li
 where li.kind = 'open_loop' and li.status = 'open' and li.marker = 'bell'
 group by coalesce(li.domain, '(unclassified)');

comment on view v_loop_bell_cap is
  'Bells per domain against the cap of 3 (Joe, 2026-08-02, replacing the old global cap '
  'of 5 that predated domains). over_cap = re-tier, do not stack. Reported, never '
  'enforced: a constraint would refuse a write at the worst possible moment.';

commit;

-- guard: everything classified, nothing invented, no loop left behind.
do $$
declare unclassified int; total int; orphan int; r record;
begin
  select count(*) into total from loop_item li join loop_block lb on lb.id = li.block_id
   where lb.kind = 'open_loop' and li.status = 'open';
  select count(*) into unclassified from loop_item li join loop_block lb on lb.id = li.block_id
   where lb.kind = 'open_loop' and li.status = 'open' and li.domain is null;
  if unclassified <> 0 then
    raise exception '% of % open loops still unclassified — every one was reviewed, so a '
                    'gap means an id in this migration did not match a row', unclassified, total;
  end if;

  select count(*) into orphan from loop_item li
   where li.domain is not null
     and not exists (select 1 from loop_domain d where d.slug = li.domain);
  if orphan > 0 then raise exception '% loop(s) point at a non-existent domain', orphan; end if;

  raise notice 'classified % open loops', total;
  for r in select domain, count(*) c from loop_item li
            join loop_block lb on lb.id = li.block_id
           where lb.kind='open_loop' and li.status='open' group by domain order by domain loop
    raise notice '  % : %', rpad(r.domain, 12), r.c;
  end loop;
  for r in select * from v_loop_bell_cap where over_cap loop
    raise notice '  OVER CAP: % has % bells (cap 3) — re-tier, do not stack', r.domain, r.bells;
  end loop;
end $$;
