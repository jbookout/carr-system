-- 0019: Wave 2 machinery tables (ORDER 11; binding design wave2-design-2026-07-31.md §2a).
--
-- ─────────────────────────────────────────────────────────────────────────────
-- READ THIS FIRST: SIX OF THE ORDER'S EIGHT ITEMS ALREADY EXIST, APPLIED BY
-- 0001 ON BUILD DAY. This migration creates ONLY the two that do not, and it
-- alters NOTHING that does. That is the order's own stop rule, not a choice:
--   "Any existing table alter beyond (h)'s registration column → stop."
--
-- Measured on branch rehearse-0019 (a full-data copy of production taken
-- 2026-07-31 17:37Z, schema_migrations through 0018) before a line of DDL was
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
--   (g) job_config         MISSING                        —   <- created here
--   (h) building_ownership EXISTS  (0001, line ~575)     0
--       registration.registered_with
--                          EXISTS as registered_with_party, NOT NULL party FK
--                                  (0001, line ~716)      0
--
-- All seven existing tables are EMPTY, so the order's "all initially empty" end
-- state already holds for them. Their column shapes are NOT identical to the
-- order's field lists (e.g. negotiation_round.side checks tenant/landlord/
-- buyer/seller rather than ours/theirs; lease carries options_note text and
-- escalator text rather than options jsonb and escalation numeric; document
-- references the attachment table, which is where r2_key lives, instead of
-- carrying working_path/pdf_path/r2_key itself; placement_metric keys on a
-- placement FK rather than (platform, external_id)). Reconciling those shapes
-- means ALTERing existing tables, which this order forbids outright, so every
-- one of them is reported to Fable and left exactly as 0001 built it.
--
-- The same applies to (h): registration already carries a party FK for the
-- registered-with party. Adding a second, nullable `registered_with` column
-- beside `registered_with_party` would put two columns on one table for one
-- fact — the shape the D5 design asks for is already there under a longer name.
-- Reported, not improvised.
-- ─────────────────────────────────────────────────────────────────────────────
--
-- What this migration therefore is: cadence_rule (§2e's engine reads rules;
-- rules are rows, not code) and job_config (§C's threshold table; tuning is an
-- UPDATE, not a deploy), created exactly as ORDER 11 (f) and (g) specify, plus
-- the writer grants. Nothing is granted to carr_reader — no read verb touches
-- either table, and amendment 11's views-only posture decides per-view later.
--
-- Schema-only, no data outside the four job_config seed rows the order names,
-- no view touched: every export must be byte-for-byte unchanged either side of
-- this migration, and that is the done-test.

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
  raise notice 'ORDER 11 premise: all seven pre-existing tables present; creating only cadence_rule + job_config';
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
  'A cold or paused client re-enters cadence only by a human act.';

comment on column cadence_rule.trigger is
  'on_complete = fires when the subject''s next_action completes; on_date = '
  'fires on a dated trigger (lease event, critical date).';
comment on column cadence_rule.interval_days is
  'Nullable on purpose: an on_date rule has no interval.';

-- ── 2. (g) job_config ────────────────────────────────────────────────────────
-- §C's threshold table. Learning-job evidence floors, notification budgets and
-- matcher settings live here, so tuning is an UPDATE and not a deploy, and a
-- job below its floor reports the shortfall instead of concluding.
create table job_config (
  job_slug   text primary key,
  config     jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

comment on table job_config is
  'Per-job thresholds and settings (ORDER 11g, wave2-design §2a/§C). Empty '
  'config = no threshold ruled yet, which a job must report honestly rather '
  'than treat as zero. NOTE FOR FABLE: system_config (0001/0002) already holds '
  'thresholds as rows under dotted keys — learning.min_posts_per_feature_cell '
  'is 30 there today, and this table''s weekly_learning seed states the same '
  'number under min_posts_per_cell. Two homes for one fact is a drift '
  'generator; which one is authoritative is a design ruling, not this '
  'migration''s to take. Both are seeded consistently until it lands.';

-- The four seed rows ORDER 11(g) names. Only weekly_learning has a threshold
-- ruled; the other three are empty by design, not by omission.
insert into job_config (job_slug, config) values
  ('weekly_learning',     '{"min_posts_per_cell": 30}'::jsonb),
  ('promotion_review',    '{}'::jsonb),
  ('matcher',             '{}'::jsonb),
  ('notification_budget', '{}'::jsonb);

-- ── 3. grants ────────────────────────────────────────────────────────────────
-- Writer full, matching what 0004 gave every base table (select, insert,
-- update — no delete anywhere in this schema by design). carr_reader gets
-- NOTHING: the order specifies none, no read verb touches these tables, and
-- amendment 11's views-only posture is what makes the leak guard structural.
grant select, insert, update on cadence_rule to carr_writer;
grant select, insert, update on job_config  to carr_writer;

-- ── 4. guards ────────────────────────────────────────────────────────────────
do $$
declare n int; leaked text;
begin
  -- (f)+(g) exist and only they were created
  if to_regclass('public.cadence_rule') is null then
    raise exception 'cadence_rule was not created';
  end if;
  if to_regclass('public.job_config') is null then
    raise exception 'job_config was not created';
  end if;

  -- cadence_rule ships empty (the engine is ORDER 14; this order is schema only)
  select count(*) into n from cadence_rule;
  if n <> 0 then
    raise exception 'cadence_rule must ship empty, found % row(s)', n;
  end if;

  -- job_config carries exactly the four seeded jobs
  select count(*) into n from job_config;
  if n <> 4 then
    raise exception 'job_config seed: expected 4 rows, found %', n;
  end if;

  -- the stop rule, made mechanical: ANY carr_reader privilege on either new
  -- table fails this migration rather than shipping a widened reader.
  select string_agg(distinct table_name, ', ') into leaked
    from information_schema.role_table_grants
   where grantee = 'carr_reader'
     and table_schema = 'public'
     and table_name in ('cadence_rule', 'job_config');
  if leaked is not null then
    raise exception
      'ORDER 11 stop rule: carr_reader holds a privilege on % — none is specified', leaked;
  end if;

  -- writer can actually use them (a table the verbs cannot write is furniture)
  select count(*) into n
    from information_schema.role_table_grants
   where grantee = 'carr_writer' and table_schema = 'public'
     and table_name in ('cadence_rule', 'job_config')
     and privilege_type in ('SELECT', 'INSERT', 'UPDATE');
  if n <> 6 then
    raise exception 'carr_writer grants incomplete on the new tables (% of 6)', n;
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

  raise notice 'ORDER 11 guards: cadence_rule empty, job_config 4 seeds, reader 0 grants, writer 6, pre-existing 7 untouched';
end $$;
