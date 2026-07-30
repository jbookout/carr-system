-- ============================================================================
-- CARR RECORD LAYER — SCHEMA v2
-- 2026-07-30 · REVISED IN PLACE by the build session (local Claude Code,
-- 11:30pm CT Wed 7/29) applying stress-test-addendum-2026-07-30.md §A and §D.
-- Still DRAFT pending Joe's grain review (blueprint-2026-07-30.md §11).
-- v1 → v2 changes, each tagged [A#]/[D#]/[C] inline:
--   A1 idempotency via tool_call replay table; event key non-unique;
--      ingest dedup on (source, external_id)
--   A2 version column + base_version conflict contract; next_action becomes
--      one-open-per-(subject, owner)
--   A3 record_source identity map + merged_into tombstones (party, building)
--   A4 per-prefix reference sequences (C-/L-/V-), seeded above imported max
--   A5 rate-norm as generated-or-owed; wide DB sanity bands (plausibility
--      moves to tool-level confirm); comp/availability bands aligned
--   A6 closed vocabularies: deal_phase, client_status, lead_stage, lead_lane
--      as reference tables with FKs
--   A9 PII: sensitive_blob side table; events/source_row store pointers for
--      designated sensitive fields; scrub-in-place purge
--   A12 rule activation human-gated (tool layer; activated_by recorded)
--   C  system_config: evidence thresholds as rows, not code
--   C  consult attribution on client from day one
--   D1 lease table (created now, Wave 2 fill)
--   D2 negotiation_round (Wave 1 schema; counter tools land Wave 1)
--   D5 building_ownership + registration.registered_with as party FK
--      (leak guard becomes structural)
--   D6 document factory: doc_template registry + document record
-- Target: Postgres 16+ (Neon). Applied via versioned migrations in
-- carr-system (migrations/0001_init.sql mirrors this file).
--
-- CONVENTIONS (unchanged from v1 except as tagged)
--  * ids: uuid (gen_random_uuid()). Business IDs (C-###, L-###, V-###) kept as
--    unique columns so every existing reference keeps resolving.
--  * every mutating table: created_at/by, updated_at/by (trigger-maintained),
--    and version int [A2] — write tools require base_version; on mismatch the
--    tool returns a structured conflict listing intervening events; the
--    session surfaces it and NEVER auto-retries.
--  * occurred_at = when it happened in the world; recorded_at = when we learned.
--  * money: numeric(14,2) + explicit basis column. NEVER a bare number.
--  * enums as reference TABLES (open taxonomy, insert a row not a migration),
--    except where the business meaning is closed (event cause).
--  * no soft-delete except where a purge path exists (party, attachment);
--    purge = scrub-in-place with tombstone [A9].
--  * ALL writes go through MCP tools; tools enforce idempotency + event rows.
--  * actor on every write comes from the verified auth token, never the payload.
-- ============================================================================

create extension if not exists pg_trgm;      -- fuzzy search day one
-- pgvector deliberately NOT installed yet; later: create extension vector;

-- ============================================================
-- 0a. HOUSEKEEPING: shared trigger for updated_at/by + version [A2]
-- ============================================================

create or replace function trg_touch_row() returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  new.version    := old.version + 1;
  return new;
end $$;
-- attached per-table below as: create trigger <t>_touch before update ...

-- ============================================================
-- 0b. SYSTEM CONFIG — thresholds are rows, not code [C]
-- ============================================================

create table system_config (
  key        text primary key,               -- 'learning.min_posts_per_cell',
                                             -- 'learning.exploration_share',
                                             -- 'promotion.min_repeat_violations',
                                             -- 'export.row_tolerance_pct', ...
  value      jsonb not null,
  note       text,                           -- what this gates, in plain words
  updated_at timestamptz not null default now(),
  updated_by uuid                            -- fk added after actor defined
);
-- Every learning/teaching job reads its evidence threshold from here and
-- reports honestly below it ("14 posts tagged, threshold 30, no conclusions").
-- Tuning a threshold is a sentence, not a deploy.

-- ============================================================
-- 0c. ACTORS, USERS, RULES (the teaching layer)
-- ============================================================

create table actor (
  id           uuid primary key default gen_random_uuid(),
  slug         text not null unique,            -- 'joe', 'dell', 'automation', 'system'
  kind         text not null check (kind in ('human','automation','system')),
  display_name text not null,
  email        text,
  active       boolean not null default true
);

alter table system_config
  add constraint system_config_updated_by_fk foreign key (updated_by) references actor(id);

-- per-user profile layer (voice, accounts) lives as keyed docs, not columns:
create table actor_profile (
  actor_id   uuid not null references actor(id),
  key        text not null,                     -- 'voice-profile','x-account','review-prefs'
  value      jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (actor_id, key)
);

-- Rules taught by either partner. Compiled into session context by a build step.
create table rule (
  id            uuid primary key default gen_random_uuid(),
  statement     text not null,                  -- the rule, in enforceable words
  human_quote   text,                           -- verbatim words of the teacher
  taught_by     uuid not null references actor(id),
  scope         jsonb not null default '{}',    -- {surfaces:[],workflows:[],tiers:[]}
  personal_to   uuid references actor(id),      -- null = shared scope
  status        text not null default 'proposed'
                check (status in ('proposed','active','retired','superseded')),
  activated_by  uuid references actor(id),      -- [A12] set ONLY by the human-gated
  activated_at  timestamptz,                    -- activation route; the context
                                                -- compiler reads active rules only
  enforcement   text not null default 'prose'
                check (enforcement in ('prose','checklist','gate','constraint','code')),
  supersedes    uuid references rule(id),
  created_at    timestamptz not null default now(),
  check (status <> 'active' or activated_by is not null)   -- [A12] no silent activation
);
-- [A12] `teach` accepts only interactive human actors (joe/dell) — never
-- automation, never a session whose input includes ingest payloads. Enforced
-- in the tool layer; the DB records who activated.
-- shared-scope conflicts (two active rules, same scope, contradicting) are
-- surfaced by the Monday job, never resolved last-write-wins.

-- ============================================================
-- 1. TOOL CALLS [A1], EVENTS (audit spine), ACTIVITIES (business facts)
-- ============================================================

-- [A1] Idempotency lives HERE, not on event. Tools check this table first;
-- a hit replays the stored response (same result, no second write). A hit
-- whose request_hash differs = key reuse with a different payload = error.
create table tool_call (
  idempotency_key text primary key,
  verb            text not null,
  actor_id        uuid not null references actor(id),
  request_hash    text not null,
  response        jsonb not null,
  created_at      timestamptz not null default now()
);

create table event (
  id              uuid primary key default gen_random_uuid(),
  occurred_at     timestamptz not null,
  recorded_at     timestamptz not null default now(),
  actor_id        uuid not null references actor(id),
  verb            text not null,                -- tool name: 'update-deal','set-lead',...
  subject_type    text not null,                -- 'deal','party','lead','rule',...
  subject_id      uuid not null,
  field           text,                         -- null for creates
  old_value       jsonb,                        -- [A9] designated sensitive fields store
  new_value       jsonb,                        --      {"__sensitive_ref": "<uuid>"} instead
  cause           text not null check (cause in
                    ('human_stated','human_correction','ingest_email','ingest_calendar',
                     'ingest_webhook','import_migration','import_salesforce',
                     'automation_job','learning_job','system')),
  human_quote     text,                         -- what the human actually said (verbatim)
  agent_rationale text,                         -- Claude's reasoning, SEPARATE by design
  idempotency_key text                          -- [A1] NON-unique: one tool call may write
                                                -- several event rows; replay is tool_call's job
);
create index event_subject_idx on event (subject_type, subject_id, occurred_at);
create index event_actor_idx   on event (actor_id, occurred_at);
create index event_idem_idx    on event (idempotency_key);
-- human_correction events are written automatically on every draft edit
-- (capture never depends on remembering to call teach) [C].

-- [A9] PII side store: events and source_row keep pointers for designated
-- sensitive fields; purging a person scrubs HERE (and the base row) in place.
create table sensitive_blob (
  id          uuid primary key default gen_random_uuid(),
  value       jsonb,                            -- null after scrub
  scrubbed_at timestamptz,
  created_at  timestamptz not null default now()
);

-- Business activities: the touch log. Touch stamps DERIVE from this table.
create table activity (
  id           uuid primary key default gen_random_uuid(),
  occurred_at  timestamptz not null,
  recorded_at  timestamptz not null default now(),
  actor_id     uuid not null references actor(id),   -- who did/relayed it
  kind         text not null check (kind in
                 ('call','email_out','email_in','meeting','tour','text','note',
                  'counter_sent','counter_received','loi','lease_signed','task')),
  summary      text not null,
  detail       text,
  owed         text,                             -- what's missing, never invented
  deal_id      uuid,                             -- fk added after deal defined
  client_id    uuid,
  lead_id      uuid,
  vendor_id    uuid,
  source       text not null default 'stated',   -- 'stated','mail_ingest','calendar',
                                                 -- 'call_recording','import'
  version      int not null default 1,           -- [A2] corrections are updates
  updated_at   timestamptz not null default now(),
  updated_by   uuid references actor(id)
);
create index activity_deal_idx on activity (deal_id, occurred_at desc);
create trigger activity_touch before update on activity
  for each row execute function trg_touch_row();

-- ============================================================
-- 2a. IDENTITY MAP + REFERENCE ALLOCATORS [A3][A4]
-- ============================================================

-- [A3] Every imported/ingested record remembers where it came from; the same
-- external key can never create a second record. Import-time candidate
-- matching (normalized email/phone/parcel/address) proposes merges; a HUMAN
-- confirms every merge (Garabadian rule: never auto-merge).
create table record_source (
  entity_type   text not null,                  -- 'party','client','lead','building',...
  entity_id     uuid not null,
  source_system text not null,                  -- 'lead-registry','client-roster',
                                                -- 'salesforce','npi-sweep','costar',...
  external_key  text not null,
  imported_at   timestamptz not null default now(),
  unique (source_system, external_key)
);
create index record_source_entity_idx on record_source (entity_type, entity_id);

-- [A4] Refs mint atomically; no max+1 races. Seeded at import via
-- setval(<seq>, <max imported number>) so new refs continue the sequence.
create sequence ref_client_seq;                 -- C-###
create sequence ref_lead_seq;                   -- L-###
create sequence ref_vendor_seq;                 -- V-### (per-category prefix kept in the
                                                -- ref string; one number space, no collisions)

-- ============================================================
-- 2b. PARTIES (people and orgs; clients/leads/vendors are lifecycle records)
-- ============================================================

create table party (
  id          uuid primary key default gen_random_uuid(),
  kind        text not null check (kind in ('person','org')),
  name        text not null,
  org_id      uuid references party(id),        -- a person's org, or org's parent
  phone       text,                             -- placeholder-phone rule enforced in tools:
                                                -- 205-643-6555 is never stored as a contact
  email       text,
  city        text, state text,
  npi         text,
  notes_path  text,                             -- markdown narrative lives in the vault/repo
  merged_into uuid references party(id),        -- [A3] merge = pointer write; reads follow it
  deleted_at  timestamptz,                      -- purge path exists for parties
  version     int not null default 1,           -- [A2]
  created_at  timestamptz not null default now(),
  created_by  uuid not null references actor(id),
  updated_at  timestamptz not null default now(),
  updated_by  uuid not null references actor(id)
);
create index party_name_trgm on party using gin (name gin_trgm_ops);
create index party_merged_idx on party (merged_into) where merged_into is not null;
create trigger party_touch before update on party
  for each row execute function trg_touch_row();

create table client_type (                       -- open taxonomy (national accounts etc.)
  slug text primary key,                         -- 'independent','group','dso','franchise',
  label text not null                            -- 'regional_system','national_account'
);

create table client_status (                     -- [A6] closed vocabulary, open taxonomy
  slug text primary key,                         -- 'prospect','research','active','won',
  label text not null,                           -- 'lost','paused'
  sort int not null default 100
);

create table client (
  id           uuid primary key default gen_random_uuid(),
  roster_ref   text unique,                      -- 'C-127' (minted from ref_client_seq [A4])
  party_id     uuid not null references party(id),
  client_type  text references client_type(slug),
  vertical     text,                             -- 'dental','medical','vet',...
  subtype      text,                             -- 'GP','ortho',... null = unknown, never guessed
  status       text not null references client_status(slug),   -- [A6] FK, phone-dictated
                                                 -- variants cannot mint phantom statuses
  etl_status   text,                             -- 'none_deliberate','pending','signed' + note
  acquisition_source text,                       -- [C] consult attribution from day one:
  acquisition_detail text,                       -- 'referral','social','newsletter','vendor',
                                                 -- ... + free-text detail ("Dr. Smith sent them")
  notes_path   text,
  version      int not null default 1,           -- [A2]
  created_at   timestamptz not null default now(),
  created_by   uuid not null references actor(id),
  updated_at   timestamptz not null default now(),
  updated_by   uuid not null references actor(id)
);
create trigger client_touch before update on client
  for each row execute function trg_touch_row();

create table lead_stage (                        -- [A6]
  slug text primary key, label text not null, sort int not null default 100
);
create table lead_lane (                         -- [A6]
  slug text primary key, label text not null     -- 'renewal','new_entity','relocation',
);                                               -- 'upstream','associate',...

create table lead (
  id            uuid primary key default gen_random_uuid(),
  registry_ref  text unique,                     -- 'L-204' (minted from ref_lead_seq [A4])
  party_id      uuid not null references party(id),
  lane          text references lead_lane(slug), -- [A6]
  stage         text not null references lead_stage(slug),   -- [A6]
  score         numeric(5,2),
  source        text,                            -- provenance, always
  suppressed    boolean not null default false,  -- registry-suppression carries over
  est_lease_event date,
  last_touch    date,                            -- DERIVED from activity by job; kept for export
  next_action_date date,
  client_id     uuid references client(id),      -- set on conversion, history preserved
  version       int not null default 1,          -- [A2]
  created_at    timestamptz not null default now(),
  created_by    uuid not null references actor(id),
  updated_at    timestamptz not null default now(),
  updated_by    uuid not null references actor(id)
);
create trigger lead_touch before update on lead
  for each row execute function trg_touch_row();

create table vendor (
  id          uuid primary key default gen_random_uuid(),
  vendor_ref  text unique,                       -- 'V-CPA-006' (number from ref_vendor_seq [A4])
  party_id    uuid not null references party(id),
  category    text not null,                     -- 'lender','gc','architect','equipment',...
  verticals   text[],
  intro_notes text,
  version     int not null default 1,            -- [A2]
  created_at  timestamptz not null default now(),
  created_by  uuid not null references actor(id),
  updated_at  timestamptz not null default now(),
  updated_by  uuid not null references actor(id)
);
create trigger vendor_touch before update on vendor
  for each row execute function trg_touch_row();

-- ============================================================
-- 3. DEALS and PARTICIPANTS (interchangeability lives here)
-- ============================================================

create table deal_phase (                        -- [A6]
  slug text primary key,                         -- 'pending','research','site_selection',
  label text not null,                           -- 'negotiation','closing','closed'
  sort int not null default 100
);

create table deal (
  id             uuid primary key default gen_random_uuid(),
  client_id      uuid not null references client(id),
  name           text not null,
  salesforce_id  text unique,
  deal_type      text not null check (deal_type in
                   ('lease','purchase','sale_leaseback','build_to_suit','renewal','other')),
  phase          text not null references deal_phase(slug),   -- [A6]
  segment        text,                           -- 'panhandle_healthcare','musicologie',...
  outcome        text check (outcome in ('won','lost','paused')),
  closed_on      date,
  won_value      numeric(14,2),
  sf_commission_placeholder numeric(14,2),       -- NEVER summed, NEVER shown as pipeline value
  sf_close_date_placeholder date,                -- NEVER treated as a forecast
  notes_path     text,
  source_row     jsonb,                          -- original imported row, preserved forever;
                                                 -- [A9] sensitive fields pointer-swapped, same
                                                 -- policy as event old/new_value
  version        int not null default 1,         -- [A2]
  created_at     timestamptz not null default now(),
  created_by     uuid not null references actor(id),
  updated_at     timestamptz not null default now(),
  updated_by     uuid not null references actor(id)
);
create trigger deal_touch before update on deal
  for each row execute function trg_touch_row();
-- Placeholder rules enforced at the VIEW layer: no view exposes
-- sf_commission_placeholder as a summable column; boards group by phase/segment
-- only. [A8] EXCEPTION, deliberate: v_export_deals carries both placeholder
-- fields as LABELED PASSTHROUGH so the deals export can regenerate its own
-- file — the rule is no aggregation/ordering, not no exposure.

create table deal_participant (
  id         uuid primary key default gen_random_uuid(),
  deal_id    uuid not null references deal(id),
  actor_id   uuid references actor(id),          -- joe/dell when internal
  party_id   uuid references party(id),          -- referring agents, client contacts
  role       text not null check (role in
               ('lead','support','referring_agent','client_contact','listing_side')),
  from_at    timestamptz not null default now(),
  to_at      timestamptz,                        -- null = current
  set_by     uuid not null references actor(id),
  check (actor_id is not null or party_id is not null)
);
-- exactly one CURRENT lead per deal (independently confirmed correct in the
-- stress test):
create unique index deal_one_current_lead
  on deal_participant (deal_id) where role = 'lead' and to_at is null;
-- handoff = tool 'set-lead': closes the old row, opens the new one, writes the event.

create table next_action (
  id          uuid primary key default gen_random_uuid(),
  subject_type text not null check (subject_type in ('deal','client','lead','vendor')),
  subject_id  uuid not null,
  owner_id    uuid not null references actor(id),   -- WHOSE ball it is, always answered
  due_on      date,
  description text not null,
  status      text not null default 'open' check (status in ('open','done','dropped')),
  version     int not null default 1,              -- [A2]
  created_at  timestamptz not null default now(),
  created_by  uuid not null references actor(id),
  updated_at  timestamptz not null default now(),
  updated_by  uuid references actor(id)
);
-- [A2] DELIBERATE change from v1: one open ball PER OWNER per subject, so Joe
-- and Dell can each hold a next action on the same deal without colliding.
create unique index one_open_next_action_per_owner
  on next_action (subject_type, subject_id, owner_id) where status = 'open';
create trigger next_action_touch before update on next_action
  for each row execute function trg_touch_row();

create table critical_date (
  id         uuid primary key default gen_random_uuid(),
  deal_id    uuid not null references deal(id),
  kind       text not null,                      -- 'loi_expiry','lease_expiration',
                                                 -- 'option_window','tail_end','earnout',...
  due_on     date not null,
  note       text,
  source     text not null,                      -- where this date came from
  status     text not null default 'open' check (status in ('open','passed','cleared')),
  version    int not null default 1,             -- [A2]
  created_at timestamptz not null default now(),
  created_by uuid not null references actor(id),
  updated_at timestamptz not null default now(),
  updated_by uuid references actor(id)
);
create trigger critical_date_touch before update on critical_date
  for each row execute function trg_touch_row();

-- [D2] Per-round, per-side negotiation economics. Wave 1 SCHEMA + counter
-- tools; the compare-to-comps view arrives Wave 2. Kills prose-drift on the
-- highest-stakes data.
create table negotiation_round (
  id              uuid primary key default gen_random_uuid(),
  deal_id         uuid not null references deal(id),
  round_no        int not null check (round_no between 1 and 99),
  side            text not null check (side in ('tenant','landlord','buyer','seller')),
  proposed_on     date not null,
  rate_amount     numeric(12,2) check (rate_amount > 0),        -- [A5]
  rate_basis      text check (rate_basis in
                    ('usd_sf_yr','usd_sf_mo','usd_mo_gross','usd_yr_gross')),
  rate_norm_sf_yr numeric(12,2) generated always as (           -- [A5] per-SF bases
                    case rate_basis
                      when 'usd_sf_yr' then rate_amount
                      when 'usd_sf_mo' then rate_amount * 12
                    end) stored,
  ti_amount       numeric(12,2),
  ti_basis        text check (ti_basis in ('usd_total','usd_sf')),
  free_rent_months numeric(4,1) check (free_rent_months between 0 and 36),
  term_months     int check (term_months between 1 and 480),
  options_note    text,                          -- renewal options, purchase options
  escalator       text,
  opex_note       text,
  expires_on      date,
  note            text,
  source          text not null default 'stated',
  version         int not null default 1,        -- [A2]
  created_at      timestamptz not null default now(),
  created_by      uuid not null references actor(id),
  updated_at      timestamptz not null default now(),
  updated_by      uuid references actor(id),
  check (rate_amount is null or rate_basis is not null),        -- no bare numbers
  unique (deal_id, round_no, side)
);
create trigger negotiation_round_touch before update on negotiation_round
  for each row execute function trg_touch_row();

alter table activity add constraint activity_deal_fk   foreign key (deal_id)   references deal(id);
alter table activity add constraint activity_client_fk foreign key (client_id) references client(id);
alter table activity add constraint activity_lead_fk   foreign key (lead_id)   references lead(id);
alter table activity add constraint activity_vendor_fk foreign key (vendor_id) references vendor(id);

-- ============================================================
-- 4. PROPERTY GRAIN: parcel → building → space → premises
--    (tenant reps transact SPACES; Hughes = 3 spaces, 1 premises)
-- ============================================================

create table parcel (
  id         uuid primary key default gen_random_uuid(),
  county     text not null,
  state      text not null,
  parcel_no  text not null,                      -- '29-2S-21-42625-000-0203'
  source     text not null,
  unique (state, county, parcel_no)
);
-- Only parcels we have actually touched. No bulk county loads (settled).

create table building (
  id         uuid primary key default gen_random_uuid(),
  parcel_id  uuid references parcel(id),
  address    text not null,
  city       text, state text, zip text,
  name       text,                               -- 'The Summit'
  class      text,                               -- 'A','B','C'
  sub_type   text,                               -- 'medical_dental','office','retail','flex'
  year_built int check (year_built between 1800 and 2100),
  stories    int check (stories between 1 and 60),
  status     text,                               -- 'existing','under_construction','proposed'
  status_source text,                            -- sources disagree; record which one won
  merged_into uuid references building(id),      -- [A3] source-spelling merges = pointer write
  version    int not null default 1,             -- [A2]
  created_at timestamptz not null default now(),
  created_by uuid not null references actor(id),
  updated_at timestamptz not null default now(),
  updated_by uuid not null references actor(id)
);
create index building_addr_trgm on building using gin (address gin_trgm_ops);
create index building_merged_idx on building (merged_into) where merged_into is not null;
create trigger building_touch before update on building
  for each row execute function trg_touch_row();

-- [D5] Listing-side/ownership as modeled parties: procuring-cause protection
-- becomes queryable and the leak guard becomes STRUCTURAL — client-facing
-- views exclude listing-side parties by construction.
create table building_ownership (
  id          uuid primary key default gen_random_uuid(),
  building_id uuid not null references building(id),
  party_id    uuid not null references party(id),
  kind        text not null check (kind in ('owner','landlord_rep','property_manager',
                                            'listing_agent')),
  from_on     date,
  to_on       date,                              -- null = current
  source      text not null,
  created_at  timestamptz not null default now(),
  created_by  uuid not null references actor(id)
);
create index building_ownership_bldg_idx on building_ownership (building_id)
  where to_on is null;

create table space (
  id          uuid primary key default gen_random_uuid(),
  building_id uuid not null references building(id),
  suite       text,                              -- 'Suite 200', '203'
  floor       int,                               -- the second-floor question is a COLUMN
  area_amount numeric(10,1) check (area_amount between 50 and 500000),
  area_basis  text check (area_basis in ('rentable','usable','county_heated','listed_unverified')),
  condition   text,                              -- 'finished','second_gen','cold_shell','vanilla_shell'
  version     int not null default 1,            -- [A2]
  created_at  timestamptz not null default now(),
  created_by  uuid not null references actor(id),
  updated_at  timestamptz not null default now(),
  updated_by  uuid not null references actor(id)
);
create trigger space_touch before update on space
  for each row execute function trg_touch_row();

-- Asking-rate/availability history: APPEND-ONLY. Re-pulls append, never overwrite.
-- [A5] Norm is GENERATED for per-SF bases; gross bases are normalized by the
-- tool (needs the space's area) into rate_norm_gross_sf_yr, and a trigger
-- makes the gap VISIBLE: unnormalizable rows carry norm_owed = true, never
-- silently unchecked. Plausibility (asking 5–120) is a tool-level CONFIRM
-- ("this is 12x the market band, proceed?"); the DB keeps a wide sanity band.
create table availability (
  id            uuid primary key default gen_random_uuid(),
  space_id      uuid not null references space(id),
  observed_at   timestamptz not null default now(),
  source        text not null,                   -- 'ecar','costar','gccmls','crexi','call'
  status        text not null check (status in ('available','pending','leased','off_market')),
  rate_amount   numeric(12,2) check (rate_amount > 0),          -- [A5]
  rate_basis    text check (rate_basis in
                  ('usd_sf_yr','usd_sf_mo','usd_mo_gross','usd_yr_gross')),
  rate_norm_sf_yr numeric(12,2) generated always as (           -- [A5]
                  case rate_basis
                    when 'usd_sf_yr' then rate_amount
                    when 'usd_sf_mo' then rate_amount * 12
                  end) stored,
  rate_norm_gross_sf_yr numeric(12,2),           -- [A5] tool-computed for gross bases
  norm_owed     boolean not null default false,  -- [A5] trigger-set; owed, never invisible
  opex_sf_yr    numeric(8,2) check (opex_sf_yr between 0 and 60),
  available_on  date,
  note          text,
  check (rate_amount is null or rate_basis is not null),        -- no bare numbers
  check (rate_norm_sf_yr is null or rate_norm_sf_yr between 2 and 250),         -- [A5] wide
  check (rate_norm_gross_sf_yr is null or rate_norm_gross_sf_yr between 2 and 250)
    -- sanity only; the 12x CoStar bug ($218.40 vs $18.27) is caught by the
    -- tool-level confirm against the 5–120 band in system_config
);

create or replace function trg_availability_norm() returns trigger
language plpgsql as $$
begin
  if new.rate_amount is not null
     and new.rate_basis in ('usd_mo_gross','usd_yr_gross')
     and new.rate_norm_gross_sf_yr is null then
    new.norm_owed := true;                       -- [A5] normalized-or-owed, enforced
  end if;
  return new;
end $$;
create trigger availability_norm before insert or update on availability
  for each row execute function trg_availability_norm();

-- A premises is the pursued unit on a deal/search: one or more spaces together.
create table premises (
  id         uuid primary key default gen_random_uuid(),
  deal_id    uuid references deal(id),
  label      text not null,                      -- '42 Business Centre, Suites 203-206 combined'
  created_at timestamptz not null default now(),
  created_by uuid not null references actor(id)
);
create table premises_space (
  premises_id uuid not null references premises(id),
  space_id    uuid not null references space(id),
  primary key (premises_id, space_id)
);

-- Space searches (the Hughes workflow) as records:
create table space_search (
  id          uuid primary key default gen_random_uuid(),
  client_id   uuid not null references client(id),
  deal_id     uuid references deal(id),
  spec        jsonb not null,                    -- polygon description, size band, filters
  status      text not null default 'open' check (status in ('open','delivered','closed')),
  report_path text,                              -- vault/publish location of the rendered report
  created_at  timestamptz not null default now(),
  created_by  uuid not null references actor(id)
);
create table search_candidate (
  id          uuid primary key default gen_random_uuid(),
  search_id   uuid not null references space_search(id),
  premises_id uuid not null references premises(id),
  tier        text not null check (tier in ('tour','look','ruled_out')),
  rank        int,
  reason      text not null,                     -- stated reason, required for ruled_out too
  confirmed_by_joe boolean not null default false   -- nothing reaches a client before this
);
-- prose tier counts are DERIVED from this table; the 5/10/18 vs 5/4/24 drift cannot recur.

-- ============================================================
-- 5. FEE MACHINERY + LEASES (Wave 2 fill, tables created day one)
-- ============================================================

create table agreement (                          -- representation agreements (ETLs etc.)
  id          uuid primary key default gen_random_uuid(),
  client_id   uuid not null references client(id),
  kind        text not null check (kind in ('etl','buyer_rep','listing_referral','other')),
  signed_on   date,
  expires_on  date,
  tail_months int check (tail_months between 0 and 36),
  status      text not null check (status in ('none_deliberate','draft','sent','signed','expired')),
  doc_attachment uuid,                            -- fk to attachment below
  note        text,
  version     int not null default 1,             -- [A2]
  created_at  timestamptz not null default now(),
  created_by  uuid not null references actor(id),
  updated_at  timestamptz not null default now(),
  updated_by  uuid references actor(id)
);
create trigger agreement_touch before update on agreement
  for each row execute function trg_touch_row();
-- 'draft'→'sent' transitions only via the human-gated web route, never an MCP tool.

create table registration (                       -- procuring-cause protection, in writing
  id           uuid primary key default gen_random_uuid(),
  deal_id      uuid not null references deal(id),
  premises_id  uuid references premises(id),
  registered_with_party uuid not null references party(id),   -- [D5] party FK: queryable,
                                                 -- and structurally excludable client-facing
  registered_on   date not null,
  method       text,                              -- 'email','form','portal'
  doc_attachment uuid,
  created_at   timestamptz not null default now(),
  created_by   uuid not null references actor(id)
);

-- [D1] Executed lease terms: the single highest-value structured record in a
-- tenant-rep business; the renewal lane runs on them. Wave 2 fill.
create table lease (
  id              uuid primary key default gen_random_uuid(),
  deal_id         uuid references deal(id),
  client_id       uuid references client(id),
  premises_id     uuid references premises(id),
  executed_on     date,
  commencement_on date,
  expiration_on   date,
  term_months     int check (term_months between 1 and 480),
  rate_amount     numeric(12,2) check (rate_amount > 0),
  rate_basis      text check (rate_basis in
                    ('usd_sf_yr','usd_sf_mo','usd_mo_gross','usd_yr_gross')),
  rate_norm_sf_yr numeric(12,2) generated always as (
                    case rate_basis
                      when 'usd_sf_yr' then rate_amount
                      when 'usd_sf_mo' then rate_amount * 12
                    end) stored,
  escalator       text,
  ti_amount       numeric(12,2),
  free_rent_months numeric(4,1),
  options_note    text,                           -- renewal/purchase options with windows
  opex_structure  text,                           -- 'nnn','gross','modified_gross'
  doc_attachment  uuid,
  source          text not null default 'stated',
  version         int not null default 1,         -- [A2]
  created_at      timestamptz not null default now(),
  created_by      uuid not null references actor(id),
  updated_at      timestamptz not null default now(),
  updated_by      uuid references actor(id),
  check (rate_amount is null or rate_basis is not null)
);
create trigger lease_touch before update on lease
  for each row execute function trg_touch_row();

create table commission (
  id          uuid primary key default gen_random_uuid(),
  deal_id     uuid not null references deal(id),
  gross_amount numeric(14,2) not null check (gross_amount >= 0),
  status      text not null check (status in ('expected','invoiced','received')),
  invoiced_on date, received_on date,
  version     int not null default 1,             -- [A2]
  created_at  timestamptz not null default now(),
  created_by  uuid not null references actor(id),
  updated_at  timestamptz not null default now(),
  updated_by  uuid references actor(id)
);
create trigger commission_touch before update on commission
  for each row execute function trg_touch_row();

create table commission_allocation (              -- nested: intent, not baked percentages
  id          uuid primary key default gen_random_uuid(),
  commission_id uuid not null references commission(id),
  parent_id   uuid references commission_allocation(id),  -- referral fee → then split
  actor_id    uuid references actor(id),
  party_id    uuid references party(id),
  kind        text not null check (kind in ('referral_fee','partner_split','house','other')),
  fraction    numeric(6,5) not null check (fraction > 0 and fraction <= 1),
  computed_amount numeric(14,2),                  -- job-computed from the tree
  check (actor_id is not null or party_id is not null)
);
-- change the internal split once; 21/9-style baked numbers never exist.

create table comp (                               -- executed lease comps (GCCMLS etc.)
  id          uuid primary key default gen_random_uuid(),
  space_id    uuid references space(id),
  building_id uuid references building(id),
  executed_on date,
  term_months int check (term_months between 1 and 480),
  rate_amount numeric(12,2) not null check (rate_amount > 0),   -- [A5]
  rate_basis  text not null check (rate_basis in
                ('usd_sf_yr','usd_sf_mo','usd_mo_gross','usd_yr_gross')),
  rate_norm_sf_yr numeric(12,2)
                check (rate_norm_sf_yr is null or rate_norm_sf_yr between 2 and 250),
                                                  -- [A5] band ALIGNED with availability;
                                                  -- plausibility confirm at tool level
  ti_amount   numeric(12,2),
  escalator   text,
  is_estimate boolean not null default false,     -- GCCMLS property-level estimates flagged
  source      text not null,
  source_row  jsonb,                              -- provenance forever ([A9] policy applies)
  created_at  timestamptz not null default now(),
  created_by  uuid not null references actor(id)
);

-- ============================================================
-- 6. MARKETING (Wave 2): the learning loop's substrate
-- ============================================================

create table campaign (
  id     uuid primary key default gen_random_uuid(),
  name   text not null,
  goal   text,
  status text not null default 'active'
);

create table content_piece (
  id          uuid primary key default gen_random_uuid(),
  campaign_id uuid references campaign(id),
  author_id   uuid not null references actor(id),  -- whose voice/account
  kind        text not null,                       -- 'post','article','reel','animated_card',
                                                    -- 'newsletter','video_script'
  topic       text,
  features    jsonb not null default '{}',         -- {hook_family, format, length, cta,
                                                    --  visual, is_experiment, hypothesis}
  body_path   text,                                 -- the copy itself lives in the repo/vault
  status      text not null default 'idea' check (status in
                ('idea','drafted','in_review','approved','edited_approved','rejected',
                 'scheduled','live','measured','retired')),
  lint_passed boolean,                              -- writing-lint gate result
  version     int not null default 1,               -- [A2]
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  updated_by  uuid references actor(id)
);
create trigger content_piece_touch before update on content_piece
  for each row execute function trg_touch_row();

create table placement (
  id          uuid primary key default gen_random_uuid(),
  piece_id    uuid not null references content_piece(id),
  platform    text not null,                        -- 'x','linkedin','facebook','instagram','newsletter'
  external_id text,                                  -- Blotato/post id
  url         text,
  scheduled_at timestamptz,
  live_at      timestamptz
);
create table placement_metric (
  placement_id uuid not null references placement(id),
  observed_at  timestamptz not null default now(),
  kind         text not null,                       -- 'impressions','engagements','profile_clicks',
                                                    -- 'follows','dms','link_clicks','consults'
  value        numeric(14,2) not null,
  source       text not null default 'blotato_api',
  primary key (placement_id, kind, observed_at)
);

create table ammo_item (                            -- hooks, stats, proof points
  id          uuid primary key default gen_random_uuid(),
  kind        text not null check (kind in ('hook','stat','proof_point','concept','angle')),
  body        text not null,                        -- paraphrase-only per knowledge policy
  provenance  text not null,                        -- where it came from (generic attribution)
  expires_on  date,                                 -- stale stats physically cannot ship:
                                                    -- the draft tool refuses expired ammo
  status      text not null default 'untested'
              check (status in ('untested','testing','proven','archived','failed')),
  evidence    jsonb,                                -- results that moved its status
  created_at  timestamptz not null default now()
);

create table experiment (
  id          uuid primary key default gen_random_uuid(),
  hypothesis  text not null,
  piece_ids   uuid[],
  started_on  date not null,
  verdict     text check (verdict in ('win','loss','inconclusive')),
  verdict_note text,
  decided_on  date
);
-- [C] Learning/teaching jobs all run from day one with thresholds from
-- system_config; below-threshold runs REPORT the shortfall, never conclude.

-- ============================================================
-- 7. DOCUMENT FACTORY [D6], INGEST SOCKET, ATTACHMENTS, EXPORTS
-- ============================================================

-- [D6] Every CARR-branded deliverable generated from records, on demand.
-- Generalizes DNA/Deal Management/fill-engine/ (built Jul 7).
create table doc_template (
  id          uuid primary key default gen_random_uuid(),
  slug        text not null unique,                 -- 'loi-letter','lease-comparison',
                                                    -- 'purchase-vs-lease','tour-book',
                                                    -- 'benefit-summary','proposal'
  name        text not null,
  source_path text not null,                        -- the REAL CARR file in Templates/
  template_version text not null,
  field_map   jsonb not null,                       -- template slots → record fields
                                                    -- (deal, negotiation_round, premises,
                                                    --  comps, client)
  output_kinds text[] not null default '{working,pdf}',
  active      boolean not null default true,
  created_at  timestamptz not null default now(),
  created_by  uuid not null references actor(id)
);

create table document (                             -- [D6] what prepare-document produced
  id           uuid primary key default gen_random_uuid(),
  template_id  uuid not null references doc_template(id),
  deal_id      uuid references deal(id),
  client_id    uuid references client(id),
  prepared_at  timestamptz not null default now(),
  prepared_by  uuid not null references actor(id),
  working_attachment uuid,                          -- the working file (xlsx/docx)
  pdf_attachment     uuid,                          -- the client-facing PDF (ALWAYS produced;
                                                    -- clients get PDFs, never working docs,
                                                    -- unless Joe says otherwise — structural)
  lint_passed  boolean,                             -- writing-lint gate
  leak_check_passed boolean,                        -- no listing-side data client-facing
  sent_status  text not null default 'draft'
               check (sent_status in ('draft','handed_to_joe','sent')),
                                                    -- 'sent' is flipped by a HUMAN statement;
                                                    -- the factory produces, Joe sends
  note         text
);

create table ingest_inbox (
  id          uuid primary key default gen_random_uuid(),
  received_at timestamptz not null default now(),
  source      text not null,                        -- 'make','mail','calendar','mailerlite',
                                                    -- 'notes_call_recording','share_sheet',
                                                    -- 'transcript_drop','webform'
  external_id text,                                 -- [A1] sender's id for the item
  payload     jsonb not null,                       -- UNTRUSTED DATA, never instructions
                                                    -- (triage prompts hard-frame it [A12])
  status      text not null default 'new'
              check (status in ('new','triaged','filed','rejected','duplicate')),
  filed_refs  jsonb,                                -- what records it became
  triage_note text,
  unique (source, external_id)                      -- [A1] webhook retries dedup here;
                                                    -- the socket always returns 2xx on dup
);

create table attachment (
  id          uuid primary key default gen_random_uuid(),
  subject_type text not null,
  subject_id  uuid not null,
  r2_key      text not null unique,                 -- object storage (Cloudflare R2)
  filename    text not null,
  mime        text not null,
  sha256      text not null,
  bytes       bigint not null,
  deleted_at  timestamptz,                          -- real purge path (confidentiality)
  created_at  timestamptz not null default now(),
  created_by  uuid not null references actor(id)
);
alter table agreement  add constraint agreement_doc_fk  foreign key (doc_attachment) references attachment(id);
alter table registration add constraint registration_doc_fk foreign key (doc_attachment) references attachment(id);
alter table lease      add constraint lease_doc_fk      foreign key (doc_attachment) references attachment(id);
alter table document   add constraint document_working_fk foreign key (working_attachment) references attachment(id);
alter table document   add constraint document_pdf_fk   foreign key (pdf_attachment) references attachment(id);

create table export_run (                           -- the legacy-file exporters, audited
  id         uuid primary key default gen_random_uuid(),
  target     text not null,                         -- 'lead-registry.xlsx','panhandle-team-deals.json',
                                                    -- 'clients-active.md','client-roster.xlsx','graph-nodes'
  ran_at     timestamptz not null default now(),
  row_count  int not null,
  checksum   text not null,                         -- [A8] checksum of the canonical EXTRACTED
                                                    -- data (cell-level), never file bytes
  status     text not null check (status in ('ok','failed','validation_failed'))
);
-- [A8] exporters: write temp → validate (row count within system_config
-- tolerance of last ok run, header check) → atomic rename; keep last 7
-- generations; a failed validation leaves the previous good file and alarms.
-- the nightly digest alarms on: a target with no ok run in >26h (dead-man).

-- ============================================================
-- 8. ROLES AND VIEWS (enforcement by construction)
-- ============================================================
-- role carr_writer: used ONLY by the MCP server's write path.
-- role carr_reader: SELECT on views only, zero base-table grants; every read
--   session and every board/exporter job connects as carr_reader.
-- Views to define with the build (v_deal_board, v_today_triage, v_catch_me_up,
--   v_client_index, v_lead_hot, v_stale_records, v_integrity_digest,
--   v_export_deals [A8], v_rate_normalized — coalesce(rate_norm_sf_yr,
--   rate_norm_gross_sf_yr) with norm_owed surfaced):
--   * never expose sf_commission_placeholder as summable, never group by
--     sf_close_date_placeholder (the placeholder rules, enforced structurally)
--   * [A8] v_export_deals carries both placeholder fields as LABELED
--     passthrough so the deals export regenerates its own file
--   * [D5] client-facing views exclude listing_side participants and
--     building_ownership listing parties BY CONSTRUCTION (the leak guard)
--   * v_stale_records replaces the hand-run staleness sweep
--   * v_integrity_digest feeds the heartbeat's ten lines
--   * reads follow merged_into pointers [A3]
-- No regex SQL guards anywhere; the reader ROLE is the guard.
-- No send tool exists in the MCP server. Agreement draft→sent transitions and
--   any future external send live on a human-gated web route only.
-- [A9] pg_dump is ENCRYPTED (age keypair) before any git commit, from the
--   FIRST dump. [A14] build sessions get Neon-branch credentials only.
