-- 0023: prospect_pool — the bulk lead universe (ORDER 25(a); binding design
-- record-layer/wave3-design-2026-07-31.md, Joe's three Wave 3 rulings).
--
-- ─────────────────────────────────────────────────────────────────────────────
-- WHY A SEPARATE TABLE AND NOT MORE `lead` ROWS (Joe's ruling 1, verbatim in the
-- design): "The bulk universe (~9,300 router rows) and the 207 curated working
-- leads are different animals; `lead` keeps meaning 'Joe is working this.'"
-- A pool row PROMOTES into a lead when Joe acts on it. Cadence rules, cold-client
-- rules and every lead query stay crisp because the 9,320 never enter `lead`.
--
-- NEVER-PRE-QUALIFY BINDS THE POOL EXACTLY AS IT BINDS LEADS. Every source row
-- lands. A row that matches an existing lead or client is MARKED and POINTED,
-- never dropped: status 'suppressed_dup' with dup_subject_* filled in. That is a
-- deliberate softening of the renewal-radar suppressor, which DROPS a
-- do-not-contact match outright (renewal-radar-sop.md, Joe 2026-07-14). The
-- design's words win — "marked, pointed, kept — never deleted, never
-- re-presented" — so the drop becomes a flag: `dup_do_not_contact`. The board
-- honours it by not presenting the row; the record honours never-pre-qualify by
-- keeping it. Nothing is thrown away to make a count look tidy.
--
-- NO PARTY ROW PER POOL ENTRY, ON PURPOSE. Identity is denormalized onto the
-- pool row. 9,320 party rows would double the party table with people nobody has
-- ever contacted, and every party-shaped query (dedup candidates, links, the
-- graph) would have to learn to ignore them. A party is minted at PROMOTION,
-- which is the moment the person becomes someone Joe is working.
--
-- SCORE IS A COLUMN, NOT A FILTER. score/score_basis exist so the board can rank
-- and Joe can qualify AT THE BOARD. Nothing in this schema, and nothing in the
-- importer, may use score to decide whether a row lands.
-- ─────────────────────────────────────────────────────────────────────────────

create table prospect_pool (
  id            uuid primary key default gen_random_uuid(),

  -- ── provenance: which lane found it, and the row exactly as it arrived ──────
  source        text not null,        -- lane slug: 'lead-router', 'npi-sweep',
                                      -- 'corp-filings', 'renewal-radar', 'pecos'.
                                      -- Plain text like lead.source_type, NOT a
                                      -- ref table: a lane is a provenance string,
                                      -- and lead_lane's five slugs are a lead
                                      -- vocabulary, not a finder registry.
  source_key    text not null,        -- the row's natural key IN that source.
                                      -- lead-router: the state License number
                                      -- (measured 2026-07-31: present and unique
                                      -- on all 9,320 rows, zero blanks, zero
                                      -- collisions). Idempotency rides on this.
  source_seq    int,                  -- position in the source file (1-based data
                                      -- row). Export target #8 reproduces the
                                      -- sheet in its original order from this;
                                      -- jsonb has no row order to borrow.
  source_row    jsonb not null,       -- VERBATIM. The exporter passes untouched
                                      -- columns straight back through it, the
                                      -- same fidelity rule build_deals uses.

  -- ── identity, as the source states it ──────────────────────────────────────
  name          text not null,
  org_name      text,
  vertical      text,                 -- the router's Profession; a practice type
  address       text,
  city          text,
  county        text,
  state         text,

  -- ── contact, as the source carries it (A9 applies: see the view grants) ─────
  email         text,
  phone         text,

  -- ── routing and presentation ───────────────────────────────────────────────
  segment       text,                 -- the finder's own segment string
  segment_play  text,                 -- the finder's play for that segment
  score         numeric(5,2),         -- PRESENTED, never filtering
  score_basis   text,                 -- how the score was arrived at, or why
                                      -- there is none. Never null when score is
                                      -- null and the row was imported: the
                                      -- absence of a score is itself a fact.
  est_lease_event date,               -- ruling 3: an estimate is a fact about the
  est_basis       text,               -- COLD ENTITY, so it lives here and rides
                                      -- along on promotion. Keeps its est- naming
                                      -- all the way; never a confirmed date.

  -- ── lifecycle ──────────────────────────────────────────────────────────────
  status        text not null default 'pool'
                check (status in ('pool','promoted','suppressed_dup')),
  promoted_lead_id uuid references lead(id),

  -- THE MATCH POINTER HAS TWO STRENGTHS, and the reason is measured, not
  -- theoretical. The renewal-radar suppressor's three rules were built to
  -- compare ~167 CoStar tenant names against ~199 registry rows. Run against
  -- 9,320 router rows on 2026-07-31 they produced 134 matches, of which the
  -- practice-token rule alone contributed 30 and nearly all were wrong: "panama
  -- city" (a city the stoplist does not cover) matched ten unrelated practices
  -- to L-195; "dentistry"+"implant" matched five to L-188. A wrong suppression
  -- is not harmless here — the design says a suppressed row is "never
  -- re-presented", so a false positive silently removes a real prospect from
  -- Joe's board. That is never-pre-qualify failing through the back door.
  --   'suppressed' — a high-precision match (exact email, or a first+last
  --                  contact-name match). status goes to suppressed_dup.
  --   'review'     — a low-precision signal worth SEEING, never worth acting on
  --                  silently. status stays 'pool': the row is presented, with
  --                  its pointer attached, and Joe qualifies at the board.
  -- Both are marked, both are pointed, both are kept. Only "never re-presented"
  -- is withheld from the weak tier. The importer's --strict-suppression flag
  -- reproduces the source's single-tier behaviour verbatim if that is ruled.
  dup_tier         text check (dup_tier in ('suppressed','review')),
  dup_subject_type text check (dup_subject_type in ('lead','client')),
  dup_subject_id   uuid,              -- deliberately NOT an FK: it points at one
                                      -- of two tables, and a merged record must
                                      -- not block a pool row from existing.
  dup_ref          text,              -- 'L-041' / 'C-118' — the human pointer
  dup_basis        text,              -- WHICH rule matched, in words
  dup_do_not_contact boolean not null default false,

  version       int not null default 1,
  created_at    timestamptz not null default now(),
  created_by    uuid not null references actor(id),
  updated_at    timestamptz not null default now(),
  updated_by    uuid not null references actor(id),

  -- idempotency: a rerun of any importer writes 0 new rows.
  unique (source, source_key),

  -- the three statuses mean what they say, enforced rather than trusted.
  constraint pool_promoted_has_lead
    check ((status = 'promoted') = (promoted_lead_id is not null)),
  -- a pointer always names its strength, and a strength always has a pointer.
  constraint pool_dup_tier_pairs_with_pointer
    check ((dup_tier is null) = (dup_subject_type is null)),
  -- suppressed_dup and the 'suppressed' tier are the same fact stated twice;
  -- they may never disagree. A 'review' pointer therefore cannot hide a row.
  -- coalesce, not `dup_tier = 'suppressed'`: a null there makes the comparison
  -- null, a null CHECK passes, and the guard would silently stop guarding.
  constraint pool_suppressed_iff_tier_suppressed
    check ((status = 'suppressed_dup') = (coalesce(dup_tier, '') = 'suppressed'))
);

create trigger prospect_pool_touch before update on prospect_pool
  for each row execute function trg_touch_row();

create index prospect_pool_status_idx  on prospect_pool (status);
create index prospect_pool_segment_idx on prospect_pool (segment);
create index prospect_pool_county_idx  on prospect_pool (county);
create index prospect_pool_event_idx   on prospect_pool (est_lease_event)
  where est_lease_event is not null;
create index prospect_pool_source_idx  on prospect_pool (source, source_seq);

comment on table prospect_pool is
  'The bulk lead universe (Wave 3, Joe ruling 1). NOT leads: a lead means Joe is '
  'working it. Rows promote into lead one-way via the promote-pool verb. Every '
  'source row lands here — a duplicate is marked and pointed, never dropped.';
comment on column prospect_pool.score is
  'PRESENTED, never filtering. Nothing may use this to decide whether a row lands.';
comment on column prospect_pool.dup_do_not_contact is
  'The renewal-radar suppressor DROPS a do-not-contact match. The pool keeps the '
  'row and raises this flag instead — never deleted, never re-presented.';

-- ── grants on the table ──────────────────────────────────────────────────────
-- 0004's `grant ... on all tables in schema public` was a one-time grant, not a
-- standing rule, so a table created in 0023 starts with none. The promote-pool
-- verb runs as carr_writer and must read the pool row and flip its status; every
-- other role stays out. carr_reader gets NOTHING on the base table — it reads
-- v_pool, which is what makes the safe-columns boundary structural (amendment 11:
-- the reader is views-only BY DESIGN).
grant select, insert, update on prospect_pool to carr_writer;

-- ── the reader surface ───────────────────────────────────────────────────────
-- SAFE COLUMNS ONLY, the v_ref_index precedent (0016). No email, no phone, no
-- source_row. The pool is 9,320 third parties who have never been contacted —
-- an order of magnitude more personal data than the 207 worked leads whose
-- contact detail v_export_leads already exposes — so the class-parity argument
-- does not carry it. A reader-scoped session gets existence, routing and score;
-- reaching a person is a write-side act that goes through promotion.
create view v_pool as
select pp.id                as pool_id,
       pp.source,
       pp.source_key,
       pp.name              as display_name,
       pp.org_name,
       pp.vertical,
       pp.city,
       pp.county,
       pp.state,
       pp.segment,
       pp.segment_play,
       pp.score,
       pp.score_basis,
       pp.est_lease_event,
       pp.est_basis,
       pp.status,
       l.registry_ref       as promoted_ref,
       pp.dup_tier,
       pp.dup_subject_type,
       pp.dup_ref,
       pp.dup_basis,
       pp.dup_do_not_contact,
       (pp.email is not null and pp.email <> '') as has_email,
       (pp.phone is not null and pp.phone <> '') as has_phone,
       pp.created_at,
       pp.version
  from prospect_pool pp
  left join lead l on l.id = pp.promoted_lead_id;

grant select on v_pool to carr_reader;

comment on view v_pool is
  'Reader surface for the prospect pool. SAFE COLUMNS ONLY — never add email, '
  'phone, address or source_row here; a reader-scoped session sees everything in '
  'this view, and this view covers thousands of uncontacted third parties.';

-- ── the export surface (target #8) ───────────────────────────────────────────
-- Contact detail lives here because the router xlsx has always carried it and
-- Dell reads that file. Granted to carr_exporter ONLY — deliberately NOT to
-- carr_reader, which is what keeps the safe-columns line above meaningful.
-- Every row exports regardless of status: a suppressed_dup is still a row, and
-- the sheet is the market map, not the call queue.
create view v_export_pool as
select pp.source_seq,
       pp.source_key,
       pp.source_row,
       pp.segment          as "SEGMENT",
       pp.segment_play     as "THE PLAY",
       pp.name             as "Name",
       pp.vertical         as "Profession",
       pp.address          as "Practice Address",
       pp.city             as "City",
       pp.county           as "County",
       pp.email            as "Email",
       pp.phone            as "Phone",
       pp.status           as "_status",
       pp.dup_tier         as "_dup_tier",
       pp.dup_ref          as "_dup_ref"
  from prospect_pool pp
 where pp.source = 'lead-router';

grant select on v_export_pool to carr_exporter;

comment on view v_export_pool is
  'Export target #8 (the lead-router xlsx). DB-owned columns are named; every '
  'other sheet column passes through source_row verbatim, the build_deals '
  'fidelity rule. DEATH SENTENCE: this surface retires at the Wave 4 repoint '
  'once the board view is confirmed the only reader (amendment-5 shim pattern).';

-- ── guards: assert the end state rather than trusting the statements above ───
do $$
declare n int;
begin
  if to_regclass('public.prospect_pool') is null then
    raise exception 'prospect_pool was not created';
  end if;

  -- the status vocabulary is exactly the three the design names. (Several
  -- constraints on this table mention `status`, so the CHECK is identified by
  -- its content, not by a name pattern.)
  if not exists (select 1 from pg_constraint
                  where conrelid = 'prospect_pool'::regclass and contype = 'c'
                    and pg_get_constraintdef(oid) like '%''pool''%'
                    and pg_get_constraintdef(oid) like '%''promoted''%'
                    and pg_get_constraintdef(oid) like '%''suppressed_dup''%') then
    raise exception 'prospect_pool status CHECK is not the designed vocabulary';
  end if;

  -- a 'review' pointer must never be able to hide a row from the board.
  begin
    insert into prospect_pool (source, source_key, source_row, name, status, dup_tier,
                               dup_subject_type, dup_ref, created_by, updated_by)
    select '__guard__', '__guard__', '{}'::jsonb, 'guard', 'suppressed_dup', 'review',
           'lead', 'L-000', a.id, a.id from actor a where a.slug = 'system';
    raise exception 'a review-tier pointer was allowed to set status suppressed_dup';
  exception when check_violation then
    null;                       -- the constraint held, which is the assertion
  end;

  -- idempotency has a structural home, not a convention.
  if not exists (select 1 from pg_constraint
                  where conrelid = 'prospect_pool'::regclass and contype = 'u'
                    and pg_get_constraintdef(oid) like '%source, source_key%') then
    raise exception 'the (source, source_key) uniqueness that makes reruns idempotent is missing';
  end if;

  -- `lead` is untouched. ORDER 25's own stop rule: any change to lead's schema
  -- is Fable territory, so the absence of one is asserted here.
  select count(*) into n from information_schema.columns
   where table_schema='public' and table_name='lead';
  if n <> 32 then
    raise exception 'lead has % columns, expected the 32 it had before 0023 — this migration must not touch lead', n;
  end if;

  -- the reader view cannot leak contact detail, checked rather than remembered.
  if exists (select 1 from information_schema.columns
              where table_schema='public' and table_name='v_pool'
                and column_name in ('email','phone','address','source_row')) then
    raise exception 'v_pool exposes contact detail — the safe-columns boundary is broken';
  end if;
  if has_table_privilege('carr_reader', 'v_export_pool', 'select') then
    raise exception 'carr_reader can read v_export_pool — the export surface must stay exporter-scoped';
  end if;
  if has_table_privilege('carr_reader', 'prospect_pool', 'select') then
    raise exception 'carr_reader has a BASE TABLE grant on prospect_pool — views-only is the leak guard';
  end if;
  if not has_table_privilege('carr_reader', 'v_pool', 'select') then
    raise exception 'carr_reader cannot read v_pool — the reader surface is unreachable';
  end if;

  -- the writer can do exactly the three the verb needs, and no delete exists
  -- anywhere in this schema by design (A9: purge is scrub-in-place).
  select count(*) into n from information_schema.role_table_grants
   where grantee = 'carr_writer' and table_schema = 'public'
     and table_name = 'prospect_pool' and privilege_type in ('SELECT','INSERT','UPDATE');
  if n <> 3 then
    raise exception 'carr_writer grants incomplete on prospect_pool (% of 3)', n;
  end if;
  if has_table_privilege('carr_writer', 'prospect_pool', 'delete') then
    raise exception 'DELETE was granted on prospect_pool — no pool row is ever deleted';
  end if;

  raise notice 'ORDER 25(a) guards: prospect_pool created with the three-status vocabulary, '
               '(source, source_key) unique, lead untouched at 32 columns, v_pool safe-columns '
               'only, v_export_pool exporter-scoped.';
end $$;
