-- 0066_marketing_campaign_and_measurement.sql — the marketing lane gets an intent, a
-- non-party subject for a finding, and a way to say "not measured" that is not a zero.
--
-- FULL REASONING AND THE SHAPE COMPARISONS: 0066_marketing_campaign_and_measurement_spec.md,
-- beside this file. This header carries the part a reader needs in order to judge the SQL.
--
-- ── THE THREE DEFECTS, all verified read-only against production on 2026-08-02 before a
-- line of this was written. Every number below came off a query that was actually run.
--
-- (1) NOTHING SAYS WHAT ANY OF IT WAS FOR. `campaign` holds 0 rows. `content_piece` holds
--     89 and every single one has campaign_id = null (`select count(*), count(campaign_id)
--     from content_piece` -> 89, 0). 259 placement_metric rows therefore cannot answer the
--     only question the marketing seat exists to answer, which is whether a thing worked,
--     because nothing in the database states what any of it was trying to do. The table
--     that would carry the intent is four columns wide — id, name, goal, status — with no
--     window, no channel, no success criterion, no outcome and no version, so even if a
--     verb existed it would have nowhere to put an objective that can later be checked.
--
-- (2) A MARKETING FINDING HAS NO SUBJECT. record_flag is polymorphic on
--     (subject_type, subject_id), and record-finding resolves `subject` only to a client,
--     lead, vendor or party. A finding about a PLATFORM ("X has never returned analytics
--     for any of these 42 posts"), a PILLAR, a FORMAT or a CAMPAIGN cannot be written at
--     all. Joe's standing rule is that findings go to the database and never to a markdown
--     report, so today the marketing seat's core output has literally nowhere to go. The
--     marketing-coo agent file says so in its own words and instructs the seat to state the
--     gap on every run rather than force a finding onto an unrelated subject.
--
-- (3) SILENCE READS AS ZERO. 89 placements; 16 are measured and 73 are not
--     (`select count(*) filter (where m.placement_id is not null), count(*) from placement
--     p left join (select distinct placement_id from placement_metric) m on ...` -> 16, 89).
--     By platform: instagram 16 of 16 measured, facebook 0 of 16, linkedin 0 of 15,
--     twitter 0 of 42. Every metric row in the system carries source 'blotato_api'. So a
--     reader who sums placement_metric by platform is handed 0 for X — the lane Joe
--     prioritises — and 0 is a lie: nobody has ever measured those 42 posts. This is the
--     placeholder-contact trap one domain over. An unmeasured thing must stay VISIBLY
--     unmeasured, exactly the way record-finding's found:false makes "searched and came up
--     dry" a real row rather than an absence.
--
-- ── A FOURTH DEFECT FOUND WHILE MEASURING THE THIRD, and it is already producing wrong
-- numbers. placement_metric is keyed (placement_id, kind, observed_at): every analytics
-- pull lands its own snapshot row, by design, so reruns are free. Ten of the sixteen
-- measured placements carry two snapshots and six carry one. That means the obvious query
-- — `sum(value) where kind='views_count'` — DOUBLE-COUNTS. Measured, not asserted:
-- the naive sum returns 621 Instagram views; the correct latest-snapshot total is 490.
-- A 27% overstatement sitting in the one lane that has any data at all. v_placement_metric_
-- latest below is the fix, and every rollup here reads it rather than the base table.
--
-- ── WHAT THIS MIGRATION DOES NOT DO, on purpose.
--   * It does not create a `pillar` vocabulary. Zero pillars are evidenced anywhere in the
--     record layer today (content_piece.features carries mechanical facts only — the
--     pipeline's own header explains that hook family, voice and pillar are judgments and
--     are deliberately excluded from a shared surface). Seeding a pillar list would be
--     inventing the taxonomy this migration is supposed to give a home to. The table
--     accepts subject_type='pillar' and holds none; the first one is a human act.
--   * It does not add campaign_id to `placement`. Measured: all 89 placements sit
--     1:1 under 89 pieces (`select cnt, count(*) from (select piece_id, count(*) cnt from
--     placement group by 1) x group by 1` -> exactly one bucket, 1 -> 89), because Joe
--     writes per-platform copy so each platform post is its own piece. A second campaign
--     pointer would therefore add no expressive power today and would add a way for two
--     rows to disagree about which campaign a post belongs to. Reopen when one piece is
--     ever placed twice: the FK goes on placement then, and content_piece.campaign_id
--     becomes derived.
--   * It does not touch pipelines/pull_placement_metrics.py, which is the ONLY thing that
--     writes pieces and placements today and is owned by another file scope. Consequence,
--     stated plainly because it is load-bearing: until that job also records a
--     placement_measurement row per pull, every one of the 89 existing placements reports
--     unmeasured_reason = 'no measurement attempt recorded'. That is TRUE and it is the
--     honest state — nobody has attempted to measure the 73 — but the 16 Instagram ones
--     deserve 'recorded' attempt rows and will not have them until that job is taught.
--     Follow-up work, not a defect in this migration.
--
-- ── FORWARD-ONLY, and reversible in effect. No row is deleted and no existing column
-- changes type. The reversal is: drop the two new tables, drop the four new views, drop the
-- new campaign columns and the record_flag check. `campaign` is EMPTY (0 rows, asserted in
-- the precondition guard below), which is the one fact that makes the NOT NULL columns
-- added here safe — a NOT NULL column with no default is impossible on a populated table,
-- and getting the requirement into the schema rather than only into the verb is worth
-- spending that emptiness on. If production is ever found non-empty this migration REFUSES
-- rather than degrading to nullable columns behind the reader's back.
--
-- ── THE WORKER AND THIS FILE DEPLOY SEPARATELY, either order. The four new verbs
-- (open-campaign, score-campaign, attach-to-campaign, measure-placement) each call
-- require0066() in mcp-server/src/tools.js, which checks for its own schema and names what
-- is missing instead of surfacing an undefined_column from inside a rolled-back
-- transaction. Same discipline 0063 established. Nothing here breaks a verb that already
-- ships: the only constraint added to an existing table is the record_flag subject_type
-- CHECK, whose accepted list is a strict superset of every value record-finding can
-- currently produce.

begin;

-- ── 0. PRECONDITIONS. Fail loudly and roll back rather than half-apply ───────────────────
-- Each of these is a fact this migration's shape depends on. If one has drifted, the right
-- outcome is a refusal a human reads, not a silent adaptation.
do $$
declare
  n_campaign int; n_flag int; n_place int; n_measured int; n_pieces int; bad_types text;
begin
  select count(*) into n_campaign from campaign;
  if n_campaign <> 0 then
    raise exception '0066 precondition: campaign holds % row(s). This migration adds NOT NULL '
                    'columns (goal, success_criterion, starts_on, created_by) with no default, '
                    'which is only safe on an empty table. Someone has written a campaign since '
                    '2026-08-02 — decide what those rows should say and backfill them in a '
                    'preceding migration rather than weakening the constraints here.', n_campaign;
  end if;

  -- The record_flag CHECK below must accept every row that already exists, or the ALTER
  -- fails mid-migration with a constraint violation that names a row and not a cause.
  select string_agg(distinct subject_type, ', ') into bad_types
    from record_flag
   where subject_type not in ('lead','client','vendor','party','deal',
                              'campaign','platform','pillar','format');
  if bad_types is not null then
    raise exception '0066 precondition: record_flag carries subject_type(s) [%] that the new '
                    'CHECK would reject. Widen the list in this file deliberately — do not '
                    'delete the rows.', bad_types;
  end if;
  select count(*) into n_flag from record_flag;

  -- The measurement numbers this migration exists to make visible. Captured rather than
  -- hardcoded so the closing guard's assertions are RELATIVE and stay true if production
  -- has moved since 2026-08-02 (89 placements / 16 measured / 89 pieces).
  select count(*) into n_place from placement;
  select count(*) into n_measured from placement p
   where exists (select 1 from placement_metric m where m.placement_id = p.id);
  select count(*) into n_pieces from content_piece;
  create temp table _0066_before on commit drop as
    select n_place as placements, n_measured as measured, n_pieces as pieces, n_flag as flags;

  raise notice '0066 preconditions ok — campaign empty, % record_flag rows all in vocabulary, '
               '% placement(s) of which % measured, % content_piece row(s).',
               n_flag, n_place, n_measured, n_pieces;
end $$;

-- ── 1. NON-PARTY SUBJECTS: the thing a marketing finding can be ABOUT ────────────────────
-- WHY A REGISTRY AND NOT A NEW POINTER COLUMN ON record_flag. record_flag is already
-- polymorphic — (subject_type text, subject_id uuid), no FK, five branches in use. The
-- cheap-looking alternative is a nullable `subject_ref text` for non-party subjects, and it
-- is the wrong shape for one specific reason: it creates a SECOND way to say what a flag is
-- about, so every reader has to know which column to look at and a flag can carry both. The
-- 0045 fault, one table over. Minting a uuid for a platform costs one small table and keeps
-- the existing pointer as the only pointer. `campaign` needs no entry here at all — it
-- already has a uuid primary key, so subject_type='campaign' works through the same pointer
-- with nothing added.
create table marketing_subject (
  id           uuid primary key default gen_random_uuid(),
  subject_type text not null check (subject_type in ('platform','pillar','format')),
  slug         text not null,
  label        text not null,
  note         text,
  created_at   timestamptz not null default now(),
  created_by   uuid references actor(id),
  retired_at   timestamptz,
  unique (subject_type, slug),
  -- A slug with a capital or a space is the same slug typed twice. The unique index above
  -- cannot see that; this can.
  check (slug = lower(btrim(slug)) and slug <> '' and slug !~ '\s')
);

comment on table marketing_subject is
  'The non-party things a marketing finding can be about (0066): a PLATFORM, a content '
  'PILLAR, a FORMAT. One stable uuid each, so record_flag''s existing (subject_type, '
  'subject_id) pointer reaches them unchanged — this table exists to supply the uuid, not to '
  'introduce a parallel addressing scheme. A campaign is NOT in here: it already has a uuid. '
  'Rows are seeded ONLY from values the record already contains; the pillar branch is '
  'deliberately empty because zero pillars are evidenced anywhere in the record layer and '
  'seeding a taxonomy would invent the very judgment this table is meant to hold. Retire by '
  'setting retired_at, never by deleting: a finding filed against a platform must stay '
  'readable after the platform stops being used.';

-- SEEDED FROM MEASURED VALUES ONLY. platforms = `select distinct platform from placement`
-- (facebook, instagram, linkedin, twitter — exactly four, all four are Joe's connected
-- Blotato accounts). formats = `select distinct kind from content_piece` (post, reel).
-- Nothing aspirational: 'newsletter' and 'youtube' appear in 0001's comments as examples and
-- in no row anywhere, so they are not seeded. Add one the day something is placed there.
insert into marketing_subject (subject_type, slug, label, note, created_by)
select 'platform', v.slug, v.label, v.note, (select id from actor where slug = 'system')
  from (values
    ('facebook',  'Facebook',  'Joe''s Facebook Page. 16 placements, 0 measured as of 2026-08-02.'),
    ('instagram', 'Instagram', 'The only measured platform: 16 of 16 placements carry metrics.'),
    ('linkedin',  'LinkedIn',  '15 placements, 0 measured as of 2026-08-02.'),
    ('twitter',   'X (Twitter)', '42 placements and 0 measured — the largest and least '
                                 'measured surface, and the lane Joe prioritises.')
  ) as v(slug, label, note);

insert into marketing_subject (subject_type, slug, label, note, created_by)
select 'format', v.slug, v.label, v.note, (select id from actor where slug = 'system')
  from (values
    ('post', 'Static / text post', '88 of the 89 pieces.'),
    ('reel', 'Reel / short video',  'Exactly one piece, and it is measured.')
  ) as v(slug, label, note);

grant select on marketing_subject to carr_reader, carr_writer, carr_exporter;
grant insert, update on marketing_subject to carr_writer;

-- ── 2. record_flag LEARNS THE WIDER VOCABULARY ───────────────────────────────────────────
-- The column had no CHECK at all, so a typo'd subject_type would have landed a flag pointing
-- at nothing, findable by nobody, and looking exactly like a real record. The list is a
-- strict superset of what record-finding can produce today (resolveSubject returns lead /
-- client / vendor / deal; subject_kind:'party' returns party) plus the four new branches.
-- TO EXTEND IT: drop and recreate this constraint in a new migration and teach
-- v_record_flag_subject the new branch in the same file — a subject_type the resolution view
-- does not know renders as an unlabelled uuid, which is how blocker (2) looked to begin with.
alter table record_flag
  add constraint record_flag_subject_type_check
  check (subject_type in ('lead','client','vendor','party','deal',
                          'campaign','platform','pillar','format'));

-- THE READ SIDE OF THE SAME BLOCKER. Storing a finding about X is only half a fix: without a
-- surface that resolves the pointer to a name, a platform finding is an opaque uuid and the
-- seat that wrote it cannot read it back. carr_reader holds no grant on record_flag (or on
-- any base table — views only, 0004/0016), so this view is the ONLY way a read session sees
-- a finding at all.
create or replace view v_record_flag_subject as
select f.id            as flag_id,
       f.subject_type,
       f.subject_id,
       case f.subject_type
         when 'campaign' then (select c.name from campaign c where c.id = f.subject_id)
         when 'platform' then (select m.label from marketing_subject m where m.id = f.subject_id)
         when 'pillar'   then (select m.label from marketing_subject m where m.id = f.subject_id)
         when 'format'   then (select m.label from marketing_subject m where m.id = f.subject_id)
         else (select r.display_name from v_ref_index r
                where r.subject_type = f.subject_type and r.subject_id = f.subject_id limit 1)
       end             as subject_label,
       case when f.subject_type in ('campaign','platform','pillar','format') then null
            else (select r.ref from v_ref_index r
                   where r.subject_type = f.subject_type and r.subject_id = f.subject_id limit 1)
       end             as subject_ref,
       f.kind,
       -- record-finding stores found:false for a searched-and-empty result. Lifting it out
       -- of the jsonb is what keeps "we looked and there was nothing" distinguishable from
       -- "nobody looked" at the read surface, which is the same rail as unmeasured vs zero.
       coalesce((f.value ->> 'found')::boolean, true)        as found,
       (f.value ? 'proposes_correction')                     as proposes_correction,
       f.value, f.source, f.observed_at, f.expires_on,
       (f.expires_on is not null and f.expires_on < current_date) as expired,
       a.slug          as recorded_by
  from record_flag f
  left join actor a on a.id = f.created_by;

comment on view v_record_flag_subject is
  'Every record_flag with its subject resolved to a NAME, across all nine branches (0066). '
  'The read side of the finding store: without it a platform or campaign finding is an '
  'opaque uuid, and carr_reader cannot see record_flag at all. `found` is lifted out of the '
  'jsonb on purpose — a searched-and-empty finding must not read like an absent one.';

grant select on v_record_flag_subject to carr_reader, carr_writer, carr_exporter;

-- ── 3. THE CAMPAIGN BECOMES AN OBJECT WITH AN INTENT AND AN OUTCOME ──────────────────────
-- Only safe because the table is empty (asserted above). Each NOT NULL is a refusal encoded
-- in the schema rather than only in the verb: a campaign with no objective, no success
-- criterion, no start and no author is a name in a table, and a name in a table is what the
-- lane already has 89 of.
alter table campaign
  add column starts_on          date,
  add column ends_on            date,
  add column success_criterion  text,
  add column channels           text[] not null default '{}',
  add column outcome_verdict    text,
  add column outcome_note       text,
  add column coverage_at_scoring jsonb,
  add column scored_at          timestamptz,
  add column scored_by          uuid references actor(id),
  add column version            int not null default 1,
  add column created_at         timestamptz not null default now(),
  add column created_by         uuid references actor(id),
  add column updated_at         timestamptz not null default now(),
  add column updated_by         uuid references actor(id);

-- `goal` already existed and is nullable. It is REUSED as the objective rather than joined by
-- a second `objective` column — two homes for one fact is the 0045 fault, and the seat's own
-- CAMPAIGN PROPOSAL block already calls this field `goal`.
alter table campaign
  alter column goal              set not null,
  alter column success_criterion set not null,
  alter column starts_on         set not null,
  alter column created_by        set not null;

alter table campaign
  add constraint campaign_status_check
    check (status in ('active','paused','closed')),
  -- A campaign is a WINDOW. An end before its start is a typo that would silently exclude
  -- every piece from every date filter.
  add constraint campaign_window_check
    check (ends_on is null or ends_on >= starts_on),
  add constraint campaign_verdict_check
    check (outcome_verdict is null or outcome_verdict in ('worked','did_not_work','inconclusive')),
  -- Scoring and the verdict arrive together or not at all. A scored_at with no verdict is a
  -- campaign somebody closed without saying what happened, which is the state this whole
  -- migration exists to make impossible.
  add constraint campaign_scored_pair_check
    check ((scored_at is null) = (outcome_verdict is null)),
  add constraint campaign_closed_is_scored_check
    check (status <> 'closed' or scored_at is not null),
  add constraint campaign_channels_nonempty_check
    check (cardinality(channels) > 0);

-- ONE CAMPAIGN PER NAME, ENFORCED. This is 0059's lesson applied before the damage instead
-- of after it: 415 org rows collapsed to 306 because nothing looked first and every writer
-- did a blind insert. campaign has zero rows today, so the index is free now and impossible
-- to add later once the same name has been minted three times.
create unique index campaign_name_uniq on campaign (lower(btrim(name)));

create trigger campaign_touch before update on campaign
  for each row execute function trg_touch_row();

-- CHANNELS ARE A CONTROLLED VOCABULARY, AND POSTGRES CANNOT SAY SO IN A CHECK. There is no
-- foreign key from an array element to another table, and a CHECK may not contain a
-- subquery. The options were: (a) a campaign_channel child table, (b) validate only in the
-- verb, (c) a trigger. (a) was rejected because channels are a 1-4 element set that is
-- always read whole and never joined — a child table would add a join to every read to buy
-- nothing. (b) was rejected on this system's own history: the verb layer is deployed
-- separately from the schema and was for twelve hours out of step in July, and a rule that
-- only one of the two halves knows is a rule that stops applying on the wrong afternoon.
create function campaign_channels_valid() returns trigger language plpgsql as $$
declare bad text;
begin
  select string_agg(ch, ', ') into bad
    from unnest(new.channels) ch
   where ch not in (select slug from marketing_subject
                     where subject_type = 'platform' and retired_at is null);
  if bad is not null then
    raise exception 'campaign.channels holds unknown platform slug(s): %. Known live '
                    'platforms: %. Register a new platform in marketing_subject before '
                    'naming it here.', bad,
                    (select string_agg(slug, ', ' order by slug) from marketing_subject
                      where subject_type = 'platform' and retired_at is null);
  end if;
  return new;
end $$;

create trigger campaign_channels_check before insert or update of channels on campaign
  for each row execute function campaign_channels_valid();

comment on column campaign.goal is
  'The objective, in one sentence — what this campaign is FOR. NOT NULL since 0066. Reused '
  'as the objective rather than joined by a second `objective` column: two homes for one '
  'fact is the 0045 fault.';
comment on column campaign.success_criterion is
  'What would have to be observably true for this to have worked, written so it can be '
  'CHECKED rather than admired. NOT NULL since 0066, and score-campaign quotes it back '
  'before accepting a verdict — a criterion invented after the results are in is not a '
  'criterion. The single most important column in this table: 259 metric rows exist today '
  'and none of them can answer "did it work" because nothing ever stated what working meant.';
comment on column campaign.channels is
  'Where this campaign runs. Validated against marketing_subject platform slugs by trigger, '
  'not by CHECK — Postgres has no array-element foreign key. Never empty.';
comment on column campaign.coverage_at_scoring is
  'The measurement coverage SNAPSHOT taken at the moment of scoring: how many of the '
  'campaign''s placements actually carried metrics when the verdict was formed. Stored so a '
  'verdict can never be re-read as better-evidenced than it was — a "worked" over 3 measured '
  'placements out of 40 is a different claim from a "worked" over 40 of 40, and six months '
  'later nothing else in the record would tell them apart.';

-- ── 4. MEASUREMENT ATTEMPTS: the row that makes silence legible ──────────────────────────
-- WHY placement_metric CANNOT CARRY THIS. Its primary key is (placement_id, kind,
-- observed_at) and value is `numeric not null`. There is no shape in that table for "we
-- asked and the platform returned nothing" — the only way to express it would be a row with
-- value 0, which is precisely the false zero this migration exists to prevent. So the
-- attempt is its own record, and it is the exact analogue of record-finding's found:false:
-- a searched-and-empty result is a fact, and a fact needs a row.
--
-- THE DISTINCTION THIS BUYS, which derivation from placement_metric cannot: a left join
-- tells you a placement has no metrics. It cannot tell you whether nobody ever pulled, or
-- whether the pull ran and the platform has no analytics to give. For X's 42 placements
-- that difference decides the next action — chase the integration, or stop expecting
-- numbers that are never coming.
create table placement_measurement (
  id           uuid primary key default gen_random_uuid(),
  placement_id uuid not null references placement(id),
  attempted_at timestamptz not null default now(),
  source       text not null,
  outcome      text not null check (outcome in ('recorded','unavailable')),
  reason       text,
  metric_kinds text[] not null default '{}',
  note         text,
  recorded_by  uuid not null references actor(id),
  unique (placement_id, source, attempted_at),
  -- 'recorded' must name what landed; 'unavailable' must say why and may name nothing.
  -- Without this an attempt row could claim success and list no metrics, which is a
  -- measured-looking placement with no measurement — the failure mode, wearing the fix.
  check ((outcome = 'recorded'    and cardinality(metric_kinds) > 0)
      or (outcome = 'unavailable' and cardinality(metric_kinds) = 0
          and btrim(coalesce(reason, '')) <> ''))
);

create index placement_measurement_placement_idx on placement_measurement (placement_id, attempted_at desc);

comment on table placement_measurement is
  'One row per attempt to measure one placement (0066). The analogue of record-finding''s '
  'found:false, in the measurement domain: it makes "we pulled and the platform gave '
  'nothing" a RECORD rather than an absence, so it stops being indistinguishable from "nobody '
  'has pulled". As of 2026-08-02 that distinction covers 73 of 89 placements, including all '
  '42 on X. Written by the measure-placement verb; pipelines/pull_placement_metrics.py '
  'should write one per pull too and does not yet.';

grant select on placement_measurement to carr_reader, carr_writer, carr_exporter;
grant insert on placement_measurement to carr_writer, carr_jobs;

-- ── 5. THE READ SURFACES ─────────────────────────────────────────────────────────────────

-- 5a. THE DOUBLE-COUNT FIX, and it is not cosmetic. Every snapshot is its own row by design.
-- Ten of the sixteen measured placements carry two snapshots, six carry one, so a naive
-- `sum(value)` over placement_metric reports 621 Instagram views where the truth is 490 —
-- measured on 2026-08-02, a 27% overstatement in the only lane that has any data. Every
-- rollup below reads THIS view and never the base table.
create or replace view v_placement_metric_latest as
select distinct on (placement_id, kind)
       placement_id, kind, value, observed_at, source
  from placement_metric
 order by placement_id, kind, observed_at desc;

comment on view v_placement_metric_latest is
  'The newest snapshot per (placement, kind) (0066). placement_metric keeps every pull as its '
  'own row, so summing it directly double-counts any placement pulled more than once — 621 '
  'vs the true 490 Instagram views on 2026-08-02. Roll up through here, always.';

-- 5b. ONE ROW PER PLACEMENT, CARRYING ITS MEASUREMENT STATE EXPLICITLY. `measured` is the
-- field a caller must read FIRST; metric_kind_count is 0 when measured is false and that 0
-- means "no metrics", not "zero views". unmeasured_reason says which kind of nothing it is.
create or replace view v_placement_measurement as
with m as (
  select placement_id,
         count(*)               as kind_count,
         min(observed_at)       as first_observed,
         max(observed_at)       as last_observed,
         array_agg(distinct source order by source) as sources
    from v_placement_metric_latest
   group by placement_id
), a as (
  select distinct on (placement_id)
         placement_id, attempted_at, outcome, reason, source
    from placement_measurement
   order by placement_id, attempted_at desc
)
select p.id                    as placement_id,
       p.platform,
       p.external_id,
       p.url,
       p.live_at,
       cp.id                   as piece_id,
       cp.kind                 as piece_kind,
       cp.status               as piece_status,
       cp.campaign_id,
       c.name                  as campaign_name,
       (m.placement_id is not null)         as measured,
       coalesce(m.kind_count, 0)            as metric_kind_count,
       m.first_observed,
       m.last_observed,
       m.sources,
       a.attempted_at          as last_attempt_at,
       a.outcome               as last_attempt_outcome,
       a.source                as last_attempt_source,
       -- The whole point of the view, in one column. Null when measured; otherwise it names
       -- which flavour of nothing this is, so no reader can mistake it for a zero.
       case
         when m.placement_id is not null                then null
         when a.outcome = 'unavailable'                 then a.reason
         when a.placement_id is null                    then 'no measurement attempt recorded'
         else 'an attempt was recorded but no metric rows exist — investigate'
       end                     as unmeasured_reason
  from placement p
  join content_piece cp on cp.id = p.piece_id
  left join campaign c   on c.id = cp.campaign_id
  left join m on m.placement_id = p.id
  left join a on a.placement_id = p.id;

comment on view v_placement_measurement is
  'Every placement with its measurement state stated rather than implied (0066). Read '
  '`measured` BEFORE any metric number: metric_kind_count is 0 for an unmeasured placement '
  'and that 0 means "no data", never "zero views". unmeasured_reason distinguishes "nobody '
  'pulled" from "the platform returned nothing". As of 2026-08-02: 89 rows, 16 measured, 73 '
  'not — including every one of the 42 X placements.';

-- 5c. COVERAGE BY PLATFORM, and the null-not-zero rule made structural. views_total is NULL
-- where nothing was measured. That is the single most important line in this file: a 0 there
-- would tell Joe that X earned no views, when the truth is that X was never measured.
create or replace view v_marketing_measurement_coverage as
select v.platform,
       count(*)                                       as placements,
       count(*) filter (where v.measured)             as measured_placements,
       count(*) filter (where not v.measured)         as unmeasured_placements,
       round(100.0 * count(*) filter (where v.measured) / nullif(count(*), 0), 1)
                                                      as coverage_pct,
       min(v.live_at)                                 as first_live_at,
       max(v.live_at)                                 as last_live_at,
       case when count(*) filter (where v.measured) = 0 then null
            else sum(l.views) end                     as views_total,
       case when count(*) filter (where v.measured) = 0 then null
            else sum(l.interactions) end              as interactions_total
  from v_placement_measurement v
  left join lateral (
    select sum(value) filter (where kind = 'views_count')       as views,
           sum(value) filter (where kind = 'interactions_sum')  as interactions
      from v_placement_metric_latest lm where lm.placement_id = v.placement_id
  ) l on true
 group by v.platform;

comment on view v_marketing_measurement_coverage is
  'Measurement coverage per platform (0066). views_total and interactions_total are NULL — '
  'never 0 — on a platform where nothing was measured, because a 0 there reads as "earned '
  'nothing" when the truth is "was never measured", and 73 of 89 placements are in exactly '
  'that state. coverage_pct IS 0.0 rather than null for such a platform: 0% coverage is a '
  'real, known measurement about the measuring, not a missing value.';

-- 5d. THE CAMPAIGN SCORECARD — what score-campaign reads before it accepts a verdict, and
-- the answer to "did it work" that 259 metric rows could not previously give.
create or replace view v_campaign_scorecard as
select c.id                                           as campaign_id,
       c.name, c.status, c.goal, c.success_criterion,
       c.starts_on, c.ends_on, c.channels,
       c.outcome_verdict, c.outcome_note, c.scored_at,
       count(v.placement_id)                          as placements,
       count(distinct v.piece_id)                     as pieces,
       count(*) filter (where v.measured)             as measured_placements,
       count(*) filter (where not v.measured)         as unmeasured_placements,
       case when count(v.placement_id) = 0 then null
            else round(100.0 * count(*) filter (where v.measured)
                       / count(v.placement_id), 1) end as coverage_pct,
       case when count(*) filter (where v.measured) = 0 then null
            else sum(l.views) end                     as views_total,
       case when count(*) filter (where v.measured) = 0 then null
            else sum(l.interactions) end              as interactions_total
  from campaign c
  left join v_placement_measurement v on v.campaign_id = c.id
  left join lateral (
    select sum(value) filter (where kind = 'views_count')      as views,
           sum(value) filter (where kind = 'interactions_sum') as interactions
      from v_placement_metric_latest lm where lm.placement_id = v.placement_id
  ) l on true
 group by c.id, c.name, c.status, c.goal, c.success_criterion, c.starts_on, c.ends_on,
          c.channels, c.outcome_verdict, c.outcome_note, c.scored_at;

comment on view v_campaign_scorecard is
  'One row per campaign: its stated criterion beside what was actually measured (0066). '
  'Totals are NULL when measured_placements is 0, so an unmeasured campaign can never be '
  'read as a campaign that earned nothing. score-campaign reads coverage_pct here and '
  'refuses an unconfirmed "worked" verdict beneath the confidence floor.';

grant select on v_placement_metric_latest, v_placement_measurement,
                v_marketing_measurement_coverage, v_campaign_scorecard
  to carr_reader, carr_writer, carr_exporter;

-- ── 6. the confidence floor score-campaign enforces ──────────────────────────────────────
-- Config rather than a constant so Joe can move it without a deploy, same pattern as the
-- rate plausibility bands. 50 is a judgment, not a measurement, and it is deliberately not
-- 100: a campaign is often partly measurable and a verb that demanded perfection would just
-- be routed around with confirm:true every time, which teaches the caller to ignore the gate.
insert into system_config (key, value, note)
values ('marketing.scoring_min_coverage_pct', '50'::jsonb,
        'score-campaign needs_confirm below this measurement coverage (0066). A verdict '
        'formed over mostly-unmeasured placements is a guess wearing a number.')
on conflict (key) do nothing;

insert into system_config (key, value, note)
values ('marketing.metric_value_band',
        '{"max": 1000000}'::jsonb,
        'measure-placement needs_confirm above this single metric value (0066). The largest '
        'real value in placement_metric on 2026-08-02 was 845,877 (view_time_ms_sum), so the '
        'band sits above real data and catches a units error or a pasted-wrong figure.')
on conflict (key) do nothing;

-- ── GUARDS, before commit, so a failure rolls the whole thing back ───────────────────────
-- (0043's lesson: a guard after `commit;` is a report, because migrate.py has already ended
-- the transaction. Every assertion below is relative to the numbers captured in step 0.)
do $$
declare
  b record;
  n int; ig_views numeric; tw_views numeric; tw_cov numeric; tw_meas int;
  n_meas_view int; n_unmeas_view int; naive numeric; accepted boolean;
begin
  select * into b from _0066_before;

  -- (1) NOTHING WAS DESTROYED. This migration adds; it must not have moved a single row of
  -- the data it describes.
  select count(*) into n from placement;
  if n <> b.placements then
    raise exception 'placement went % -> % — 0066 writes no placement rows', b.placements, n; end if;
  select count(*) into n from content_piece;
  if n <> b.pieces then
    raise exception 'content_piece went % -> % — 0066 writes no piece rows', b.pieces, n; end if;
  select count(*) into n from record_flag;
  if n <> b.flags then
    raise exception 'record_flag went % -> % — 0066 adds a CHECK, never a row', b.flags, n; end if;
  select count(*) into n from campaign;
  if n <> 0 then
    raise exception 'campaign holds % row(s) after 0066 — this migration creates no campaign; '
                    'a campaign is a human act through open-campaign', n; end if;

  -- (2) THE VOCABULARY SEEDED FROM MEASURED VALUES, and matches the data exactly. A platform
  -- in the registry that no placement uses, or a platform in use that the registry lacks,
  -- both mean the seed drifted from the reason it exists.
  if exists (select 1 from (select distinct platform from placement) p
              where p.platform not in (select slug from marketing_subject
                                        where subject_type = 'platform')) then
    raise exception 'a platform appears in placement but not in marketing_subject — the seed '
                    'no longer covers the data it was taken from';
  end if;
  select count(*) into n from marketing_subject where subject_type = 'platform';
  if n <> (select count(distinct platform) from placement) then
    raise exception 'marketing_subject holds % platform(s) for % distinct placement '
                    'platform(s) — the seed invented or dropped one', n,
                    (select count(distinct platform) from placement); end if;
  -- The inverse assertion, and it is the honest half: this migration is FORBIDDEN from
  -- inventing a pillar taxonomy. Zero pillars are evidenced, so zero pillars are seeded.
  select count(*) into n from marketing_subject where subject_type = 'pillar';
  if n <> 0 then
    raise exception '0066 seeded % pillar(s). No pillar is evidenced anywhere in the record '
                    'layer; seeding one would invent the judgment this table exists to hold', n; end if;

  -- (3) A NON-PARTY FINDING IS NOW ACTUALLY WRITABLE. Asserted by doing it and rolling it
  -- back inside the migration's own transaction, rather than by trusting the CHECK list.
  begin
    insert into record_flag (subject_type, subject_id, kind, value, source, created_by)
    select 'platform', m.id, '_0066_selftest',
           '{"found": true, "note": "guard probe, deleted in the same transaction"}'::jsonb,
           'migration 0066 guard', (select id from actor where slug = 'system')
      from marketing_subject m where m.subject_type = 'platform' and m.slug = 'twitter';
    if not found then raise exception '0066 self-test: no twitter platform row to file against'; end if;
    delete from record_flag where kind = '_0066_selftest';
  exception when check_violation then
    raise exception '0066 self-test: record_flag still REFUSES a platform finding — blocker 2 '
                    'is not closed and the CHECK list is wrong';
  end;
  if exists (select 1 from record_flag where kind = '_0066_selftest') then
    raise exception '0066 self-test left its probe row behind'; end if;

  -- (4) THE MEASUREMENT VIEW COVERS EVERY PLACEMENT, EXACTLY ONCE, and its measured count
  -- reconciles with the base data captured before any of this ran.
  select count(*), count(*) filter (where measured), count(*) filter (where not measured)
    into n, n_meas_view, n_unmeas_view from v_placement_measurement;
  if n <> b.placements then
    raise exception 'v_placement_measurement returns % row(s) for % placement(s) — a join '
                    'dropped or duplicated one', n, b.placements; end if;
  if n_meas_view <> b.measured then
    raise exception 'v_placement_measurement calls % placement(s) measured; the base data says '
                    '%', n_meas_view, b.measured; end if;
  if n_unmeas_view <> b.placements - b.measured then
    raise exception 'unmeasured count % does not reconcile (% - %)',
                    n_unmeas_view, b.placements, b.measured; end if;

  -- (5) EVERY UNMEASURED PLACEMENT SAYS WHY. An unmeasured row with a null reason is the
  -- silent absence this migration exists to abolish, and it would be invisible without this.
  select count(*) into n from v_placement_measurement
   where not measured and btrim(coalesce(unmeasured_reason, '')) = '';
  if n <> 0 then
    raise exception '% unmeasured placement(s) carry no unmeasured_reason — silence is back', n; end if;
  -- …and the pair, which is what stops the column being a constant: a MEASURED row must
  -- carry no reason at all.
  select count(*) into n from v_placement_measurement where measured and unmeasured_reason is not null;
  if n <> 0 then
    raise exception '% measured placement(s) carry an unmeasured_reason', n; end if;

  -- (6) NULL IS NOT ZERO, ASSERTED ON LIVE DATA AND IN BOTH DIRECTIONS. This is the defect in
  -- one line. X: 42 placements, 0 measured -> views_total must be NULL while coverage_pct is
  -- a real 0.0. Instagram: 16 of 16 measured -> a number, and specifically the
  -- latest-snapshot number (490 on 2026-08-02), never the double-counted naive sum (621).
  select views_total, coverage_pct, measured_placements
    into tw_views, tw_cov, tw_meas
    from v_marketing_measurement_coverage where platform = 'twitter';
  if not found then
    raise notice 'no twitter placements exist any more — the null-vs-zero fixture has moved. '
                 'Point guard (6) at whichever platform is now unmeasured; do NOT delete it.';
  elsif tw_meas > 0 then
    raise notice 'twitter now has % measured placement(s) — the null-vs-zero guard below no '
                 'longer applies to this platform. That is good news; move the fixture.', tw_meas;
  else
    if tw_views is not null then
      raise exception 'twitter has 0 measured placements but views_total reads % — an '
                      'unmeasured platform is reporting a number', tw_views; end if;
    if tw_cov is distinct from 0.0 then
      raise exception 'twitter coverage_pct is % , expected 0.0 — 0%% coverage is a real '
                      'measurement about the measuring and must not be null', tw_cov; end if;
  end if;

  select views_total into ig_views from v_marketing_measurement_coverage where platform = 'instagram';
  select sum(value) into naive from placement_metric pm
    join placement p on p.id = pm.placement_id
   where pm.kind = 'views_count' and p.platform = 'instagram';
  if naive is null then
    raise notice 'instagram carries no views_count rows any more — the measured half of the '
                 'null-vs-zero pair has moved. Repoint it; do NOT delete it.';
  else
    if ig_views is null then
      raise exception 'instagram is measured but views_total is null — the null-not-zero rule '
                      'has become a null-always rule, which reports nothing at all'; end if;
    -- THE DOUBLE-COUNT ASSERTION. 10 of the 16 measured placements carry two snapshots, so
    -- while that is true the correct total (490 on 2026-08-02) must NOT equal the naive sum
    -- over every snapshot row (621).
    if ig_views = naive and
       exists (select 1 from (select placement_id from placement_metric
                               group by placement_id, kind having count(*) > 1) x) then
      raise exception 'instagram views_total (%) equals the naive sum (%) while multi-snapshot '
                      'placements exist — the rollup is double-counting again', ig_views, naive;
    end if;
  end if;

  -- (7) THE CONSTRAINTS ACTUALLY BITE. A campaign with no channel, or a closed one with no
  -- verdict, must be impossible rather than discouraged. Both probes roll back.
  -- The accepted/refused flag is a VARIABLE rather than a `raise` inside the block, because
  -- a `raise exception` in the body would be swallowed by this block's own exception
  -- handler and the probe would report success on a broken constraint.
  accepted := true;
  begin
    insert into campaign (name, goal, success_criterion, starts_on, channels, created_by)
    values ('_0066 guard probe', 'g', 's', current_date, '{}', (select id from actor where slug='system'));
  exception when others then accepted := false; end;
  if accepted then raise exception '0066: a campaign with zero channels was ACCEPTED'; end if;

  accepted := true;
  begin
    insert into campaign (name, goal, success_criterion, starts_on, channels, created_by)
    values ('_0066 guard probe', 'g', 's', current_date, '{not_a_platform}',
            (select id from actor where slug='system'));
  exception when others then accepted := false; end;
  if accepted then raise exception '0066: a campaign naming an unregistered platform was ACCEPTED'; end if;

  accepted := true;
  begin
    insert into campaign (name, goal, success_criterion, starts_on, channels, status,
                          created_by)
    values ('_0066 guard probe', 'g', 's', current_date, '{twitter}', 'closed',
            (select id from actor where slug='system'));
  exception when others then accepted := false; end;
  if accepted then raise exception '0066: a CLOSED campaign with no verdict was ACCEPTED — a '
                                   'campaign could be closed without saying what happened'; end if;
  if exists (select 1 from campaign where name like '\_0066%') then
    raise exception '0066 guard probe left a campaign row behind'; end if;

  -- (8) THE READER CAN SEE ALL OF IT. carr_reader holds no base-table grant by design, so a
  -- missing view grant is the exact twelve-hour outage shape from 2026-07-31.
  if not has_table_privilege('carr_reader', 'v_placement_measurement', 'select')
     or not has_table_privilege('carr_reader', 'v_campaign_scorecard', 'select')
     or not has_table_privilege('carr_reader', 'v_marketing_measurement_coverage', 'select')
     or not has_table_privilege('carr_reader', 'v_record_flag_subject', 'select') then
    raise exception '0066: carr_reader cannot read one of the new views'; end if;
  if not has_table_privilege('carr_writer', 'placement_measurement', 'insert')
     or not has_table_privilege('carr_writer', 'marketing_subject', 'insert') then
    raise exception '0066: carr_writer cannot write the new tables — every verb would 500'; end if;

  raise notice '0066 live. campaign now carries a window, channels, a success criterion and a '
               'scored verdict (still 0 rows — opening one is a human act). % non-party '
               'subject(s) registered. record_flag accepts campaign/platform/pillar/format, '
               'self-tested. % placement(s): % measured, % NOT, and every one of those says '
               'why. twitter reports NULL views over % placements rather than 0. THE VERBS '
               'ARE NOT LIVE UNTIL THE WORKER IS DEPLOYED — open-campaign, score-campaign, '
               'attach-to-campaign and measure-placement all return migration_not_applied or '
               'unknown_tool until then.',
               (select count(*) from marketing_subject),
               b.placements, b.measured, b.placements - b.measured,
               (select placements from v_marketing_measurement_coverage where platform='twitter');
end $$;

commit;
