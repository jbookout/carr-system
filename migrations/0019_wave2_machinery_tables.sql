-- 0019: Wave 2 machinery (ORDER 11; binding design wave2-design-2026-07-31.md §2a,
-- as amended by the Fable ruling of 2026-07-31 ~1:10pm CT).
--
-- ─────────────────────────────────────────────────────────────────────────────
-- READ THIS FIRST: SIX OF THE ORDER'S EIGHT ITEMS ALREADY EXIST, APPLIED BY
-- 0001 ON BUILD DAY. This migration creates ONE table and alters NOTHING that
-- exists. That is the order's own stop rule, not a choice:
--   "Any existing table alter beyond (h)'s registration column → stop."
--
-- Measured on a full-data branch copy of production before a line of DDL was
-- written — the order's premise was checked against the database, not against
-- the migration files:
--
--   ORDER 11 item          state in production          rows
--   (a) lease              EXISTS  (0001, line ~727)     0
--   (b) negotiation_round  EXISTS  (0001, line ~493)     0
--   (c) doc_template       EXISTS  (0001, line ~894)     0
--   (d) document           EXISTS  (0001, line ~911)     0
--   (e) placement_metric   EXISTS  (0001, line ~853)     0
--   (f) cadence_rule       MISSING                        —   <- created here
--   (g) job_config         RULED OUT — see below
--   (h) building_ownership EXISTS  (0001, line ~575)     0
--       registration.registered_with
--                          EXISTS as registered_with_party, NOT NULL party FK
--                                  (0001, line ~716)      0
--
-- All seven existing tables are EMPTY, so the order's "all initially empty" end
-- state already holds for them. Their column shapes are NOT identical to the
-- order's field lists, and FABLE HAS RULED THAT THE 0001 SHAPES STAND (ruling
-- of 2026-07-31 ~1:10pm CT; the design doc is being amended to record as-built):
--   · negotiation_round.side checks tenant/landlord/buyer/seller, NOT the
--     order's ours/theirs — which flips meaning between rep sides and is the
--     reason the 0001 vocabulary wins.
--   · lease carries options_note text and escalator text, not options jsonb and
--     escalation numeric.
--   · document references the attachment table (where r2_key, sha256 and bytes
--     live) instead of carrying working_path/pdf_path/r2_key itself. The
--     normalization stands.
--   · placement_metric keys on a placement FK, not (platform, external_id).
--     The FK stands — it cannot orphan.
--   · registration's party FK stands as registered_with_party. A second,
--     nullable `registered_with` beside it would put two columns on one table
--     for one fact.
-- Nothing above is touched here.
--
-- (g) job_config IS DELIBERATELY NOT CREATED. The Fable ruling: ONE CONFIG
-- HOME. system_config (0001/0002) already exists, already holds thresholds as
-- rows under dotted keys, and already carries the learning floor. A job_config
-- table would have been a second home for the same class of fact — the drift
-- generator this ruling exists to prevent. The four job entries ORDER 11(g)
-- names are handled in system_config below, under its own convention.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 0. the premise, asserted rather than assumed ─────────────────────────────
-- If this migration is ever replayed onto a database built from 0001 forward,
-- these seven must be present — 0001 creates them. A failure here means 0001
-- changed under us, and the shape questions above stop being paperwork.
do $$
declare missing text;
begin
  select string_agg(t, ', ' order by t) into missing
    from unnest(array['lease','negotiation_round','doc_template','document',
                      'placement_metric','building_ownership','registration']) as t
   where to_regclass('public.' || t) is null;
  if missing is not null then
    raise exception
      'ORDER 11 premise broken: expected these to exist from 0001 but they do not: %. '
      'Stop and report — their shapes are a Fable design call, not this migration''s.',
      missing;
  end if;
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'registration'
                    and column_name = 'registered_with_party') then
    raise exception 'ORDER 11 (h): registration.registered_with_party is gone — stop and report';
  end if;
  raise notice 'ORDER 11 premise: all seven pre-existing tables present; creating cadence_rule only';
end $$;

-- ── 1. (f) cadence_rule ──────────────────────────────────────────────────────
-- §2e: "The engine reads rules; rules are rows, not code." When a next_action
-- completes (or a dated trigger arrives), matching rows here spawn the next one.
create table cadence_rule (
  id              uuid primary key default gen_random_uuid(),
  lane            text not null,
  subject_type    text not null check (subject_type in ('deal','client','lead','vendor')),
  trigger         text not null check (trigger in ('on_complete','on_date')),
  interval_days   int,
  action_template text,
  active          boolean not null default true
);

comment on table cadence_rule is
  'Cadence engine rules (ORDER 11f, wave2-design §2e). One row = one rule; the '
  'engine spawns next_action rows from them, so subject_type mirrors '
  'next_action''s own vocabulary exactly — a rule naming a subject_type that '
  'next_action cannot hold is dead on arrival. lane is deliberately un-CHECKed: '
  'the design names nurture45 / nurture90 / vendor_maintenance / lease_event / '
  'custom, and 0017''s direction of travel is vocabularies as ROWS, so a lane '
  'ref table is a later row-add, never a redeploy. HARD DEFAULT from Joe''s '
  '2026-07-31 cold/paused ruling (rule store + decision-history): cold-class '
  'clients are EXCLUDED from automatic nurture cadences — never pester a ghost. '
  'A cold or paused client re-enters cadence only by a human act. Engine '
  'thresholds and settings live in system_config, never in a second config table.';

comment on column cadence_rule.trigger is
  'on_complete = fires when the subject''s next_action completes; on_date = '
  'fires on a dated trigger (lease event, critical date).';
comment on column cadence_rule.interval_days is
  'Nullable on purpose: an on_date rule has no interval.';

-- ── 2. (g) the four job entries, in the ONE config home ──────────────────────
-- system_config's convention, read off the live table rather than assumed:
-- key is '<namespace>.<setting>', value is jsonb (bare scalar or object), note
-- is a plain-words sentence saying what the row gates, and INITIAL marks a
-- number that is a starting guess rather than a ruling. Eleven rows today.
--
-- TWO OF THE FOUR JOBS ARE ALREADY HOME, AND ARE NOT RE-SEEDED. Re-seeding
-- them under a second namespace is the exact duplication the ruling forbids,
-- one level down:
--   · weekly_learning   → learning.min_posts_per_feature_cell = 30  (0002)
--                         and learning.exploration_share            (0002)
--     The order's job_config seed for this job carried min_posts_per_cell 30 —
--     the SAME fact under a different name. Skipped; the canonical key above
--     is the one every learning job reads.
--   · promotion_review  → promotion.min_repeat_violations = 3       (0002)
--
-- The other two have no home yet and get one. Their value is an empty object
-- ON PURPOSE — ORDER 11(g)'s own words, "empty jsonb is fine where no
-- threshold is ruled yet". A job reading {} must report the shortfall (§C:
-- "14 posts tagged, threshold is 30, no conclusions yet"), never assume a
-- default. When a real setting is ruled it lands as its own dotted key
-- alongside these, exactly as the learning and promotion namespaces did.
insert into system_config (key, value, note) values
  ('matcher.settings', '{}'::jsonb,
   'ORDER 11(g) / wave2-design §D4. Settings for the availability x space_search '
   'matcher (nightly join; matches land in the digest, never auto-sent). EMPTY '
   'BY DESIGN: no threshold has been ruled yet, and the job must say so rather '
   'than invent one. First real setting lands as its own matcher.<setting> key.'),
  ('notification_budget.settings', '{}'::jsonb,
   'ORDER 11(g) / wave2-design §2g. Notification thresholds and budgets for the '
   'operating-rhythm layer. EMPTY BY DESIGN: unruled, and quiet hours plus the '
   'weekends-off rule already bind at the notification layer regardless. First '
   'real budget lands as its own notification_budget.<setting> key.');

-- ── 3. grants ────────────────────────────────────────────────────────────────
-- Writer full on the new table, matching what 0004 gave every base table
-- (select, insert, update — no delete anywhere in this schema by design).
-- carr_reader gets NOTHING: the order specifies none, no read verb touches it,
-- and amendment 11's views-only posture is what makes the leak guard
-- structural. system_config needs no grant work — it already has 0004's, and
-- carr_exporter's read grant from 0006.
grant select, insert, update on cadence_rule to carr_writer;

-- ── 4. guards ────────────────────────────────────────────────────────────────
do $$
declare n int; leaked text; strays text;
begin
  if to_regclass('public.cadence_rule') is null then
    raise exception 'cadence_rule was not created';
  end if;

  -- The ruling, made mechanical: there is ONE config home. If a later hand
  -- re-adds job_config, this migration is where the intent is written down.
  if to_regclass('public.job_config') is not null then
    raise exception
      'job_config exists — the 2026-07-31 ruling is ONE config home (system_config). '
      'Stop and report rather than maintaining two.';
  end if;

  -- cadence_rule ships empty (the engine is ORDER 14; this order is schema only)
  select count(*) into n from cadence_rule;
  if n <> 0 then
    raise exception 'cadence_rule must ship empty, found % row(s)', n;
  end if;

  -- the two new config rows exist and are honestly empty
  select count(*) into n from system_config
   where key in ('matcher.settings', 'notification_budget.settings')
     and value = '{}'::jsonb;
  if n <> 2 then
    raise exception 'expected 2 empty job-config rows in system_config, found %', n;
  end if;

  -- the two already-home jobs were NOT duplicated under a second namespace
  select string_agg(key, ', ' order by key) into strays
    from system_config
   where key like 'weekly\_learning.%' or key like 'promotion\_review.%'
      or key like 'job\_config%';
  if strays is not null then
    raise exception
      'duplicate job namespace(s) in system_config: %. weekly_learning lives under '
      'learning.*, promotion_review under promotion.* — one home per fact.', strays;
  end if;

  -- and their canonical values are untouched by this migration
  if (select value from system_config where key = 'learning.min_posts_per_feature_cell')
       is distinct from '30'::jsonb then
    raise exception 'learning.min_posts_per_feature_cell moved — this migration must not touch it';
  end if;
  if (select value from system_config where key = 'promotion.min_repeat_violations')
       is distinct from '3'::jsonb then
    raise exception 'promotion.min_repeat_violations moved — this migration must not touch it';
  end if;

  -- the stop rule, made mechanical: ANY carr_reader privilege on the new table
  -- fails this migration rather than shipping a widened reader.
  select string_agg(distinct table_name, ', ') into leaked
    from information_schema.role_table_grants
   where grantee = 'carr_reader'
     and table_schema = 'public'
     and table_name = 'cadence_rule';
  if leaked is not null then
    raise exception
      'ORDER 11 stop rule: carr_reader holds a privilege on % — none is specified', leaked;
  end if;

  -- writer can actually use it (a table the verbs cannot write is furniture)
  select count(*) into n
    from information_schema.role_table_grants
   where grantee = 'carr_writer' and table_schema = 'public'
     and table_name = 'cadence_rule'
     and privilege_type in ('SELECT', 'INSERT', 'UPDATE');
  if n <> 3 then
    raise exception 'carr_writer grants incomplete on cadence_rule (% of 3)', n;
  end if;

  -- the seven pre-existing tables are still empty and still untouched by this
  -- migration: nothing here writes to them, and this proves it rather than
  -- claiming it in a comment.
  select coalesce(sum(c), 0) into n from (
    select count(*) c from lease
    union all select count(*) from negotiation_round
    union all select count(*) from doc_template
    union all select count(*) from document
    union all select count(*) from placement_metric
    union all select count(*) from building_ownership
    union all select count(*) from registration
  ) s;
  if n <> 0 then
    raise exception
      'a pre-existing Wave 2 table is no longer empty (% row(s)) — this migration '
      'writes to none of them; stop and report', n;
  end if;

  raise notice 'ORDER 11 guards: cadence_rule empty, 2 config rows seeded, no duplicate namespaces, reader 0 grants, writer 3, pre-existing 7 untouched';
end $$;
