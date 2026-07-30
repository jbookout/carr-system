-- ============================================================================
-- CARR RECORD LAYER — SEED DATA (migration 0002)
-- 2026-07-30 · build-session scaffold. Actors, reference vocabularies, and
-- system_config thresholds. Values marked INITIAL are tunable by a sentence
-- (they are rows, not code — addendum §C).
--
-- Vocab honesty rule: lead_stage and lead_lane are seeded here ONLY with the
-- values documented in the blueprint/schema. The Wave 1 import script MUST
-- reconcile against the live lead-registry.xlsx Config tab + distinct column
-- values, propose any missing slugs, and a human approves them. No slug is
-- invented; no phone-dictated variant silently mints one later (A6).
-- ============================================================================

-- actors -----------------------------------------------------------------
insert into actor (slug, kind, display_name) values
  ('joe',        'human',      'Joe Bookout'),
  ('dell',       'human',      'Dell McCraney'),
  ('automation', 'automation', 'Scheduled jobs'),
  ('system',     'system',     'System (migrations, exporters)');

-- deal phases (blueprint vocabulary) ---------------------------------------
insert into deal_phase (slug, label, sort) values
  ('pending',        'Pending',        10),
  ('research',       'Research',       20),
  ('site_selection', 'Site selection', 30),
  ('negotiation',    'Negotiation',    40),
  ('closing',        'Closing',        50),
  ('closed',         'Closed',         60);

-- client statuses -----------------------------------------------------------
insert into client_status (slug, label, sort) values
  ('prospect', 'Prospect', 10),
  ('research', 'Research', 20),
  ('active',   'Active',   30),
  ('won',      'Won',      40),
  ('lost',     'Lost',     50),
  ('paused',   'Paused',   60);

-- client types ----------------------------------------------------------
insert into client_type (slug, label) values
  ('independent',      'Independent'),
  ('group',            'Group practice'),
  ('dso',              'DSO'),
  ('franchise',        'Franchise'),
  ('regional_system',  'Regional system'),
  ('national_account', 'National account');

-- lead lanes (documented set; import reconciles against the registry) -------
insert into lead_lane (slug, label) values
  ('renewal',    'Renewal radar'),
  ('new_entity', 'New entity (corp filings)'),
  ('relocation', 'Relocation'),
  ('upstream',   'Upstream radar (PECOS/NPPES)'),
  ('associate',  'Associate lane');

-- lead stages: DELIBERATELY EMPTY here. The registry's live stage vocabulary
-- is the source of truth; the Wave 1 import seeds it from the Config tab and
-- distinct Stage values, human-reviewed. Seeding a guessed set would be
-- fabrication.

-- system_config: thresholds as rows (addendum §C) --------------------------
insert into system_config (key, value, note) values
  ('learning.min_posts_per_feature_cell', '30',
   'INITIAL (blueprint example). Weekly learning job needs this many tagged posts in a feature cell before proposing a playbook delta; below it, report the shortfall honestly.'),
  ('learning.exploration_share', '{"min": 0.20, "max": 0.30}',
   'Share of each content batch reserved for tagged experiments (settled decision).'),
  ('promotion.min_repeat_violations', '3',
   'INITIAL. Monthly promotion review promotes a rule to gate/constraint after this many post-activation corrections visible in events.'),
  ('rate.asking_confirm_band_sf_yr', '{"min": 5, "max": 120}',
   'Tool-level plausibility CONFIRM band for asking rates (blueprint). Outside it, the tool asks "this is Nx the market band, proceed?" — DB keeps only the wide 2-250 sanity band (A5).'),
  ('rate.comp_confirm_band_sf_yr', '{"min": 3, "max": 150}',
   'Tool-level confirm band for executed comps (wider than asking; distressed and premium executions are real).'),
  ('export.row_tolerance_pct', '5',
   'INITIAL. Exporter validation: row count within this % of the last ok run, else validation_failed and the previous good file stands (A8).'),
  ('export.keep_generations', '7',
   'Exporters keep this many prior generations (A8).'),
  ('export.deadman_hours', '26',
   'Digest alarms on any export target with no ok run in this many hours (A8).'),
  ('ingest.max_payload_bytes', '1048576',
   'INITIAL 1 MiB. Ingest socket rejects larger payloads (A11); attachments go to R2, not the socket.');
