-- 0075_doctrine_store.sql — P1 of the doctrine-store build: the authoritative
-- content schema. Design: design/doctrine-store-build-2026-08-07.md (council
-- decision 82a2fb62, amended by 20dfdfcc; Joe's rulings 14181e60 database-first
-- and b42f11a0 build-for-trajectory). Phase 0 (the vault-wide .md write-block)
-- shipped in repo commit 0bd3961; this migration is the store those blocked
-- writes will route into once the P2 verbs land.
--
-- HOUSE ADAPTATIONS from the chairs' greenfield schema, each deliberate:
--   * actor:    REUSED. The record layer's actor table already models humans,
--     agents and services; a parallel identity table is the two-homes disease.
--   * sessions: session_key TEXT, not a sessions table — the house convention
--     (decision entries, event rows) already attributes by session_key string.
--     A sessions table gets a named trigger: the day session METADATA (parent
--     chains, kinds) needs querying, promote the key to a table.
--   * events:   REUSED. Doctrine verbs write the house `event` log like every
--     other verb (subject_type='doctrine_section' etc). No doctrine_events.
--   * md_write_manifest: NOT a table. hooks/md_manifest.py is the one home —
--     the hook must fail closed with no DB round-trip; a table copy would be a
--     second contract on the same config (rule 73381d78) and single-source
--     wins (d367188d). The P1 spec row is satisfied by the module.
--   * workspace_id: DEFERRED entirely (chairs split on it; council recorded
--     tenancy machinery behind the second-firm trigger, and a column nobody
--     reads is scaffolding the merit tests reject — revisit at that trigger).
--   * doctrine_ prefix throughout: `document` (deal paperwork) and `rule` (the
--     taught-rule store) already exist and are NOT touched. P7 studies the
--     rule-store relationship; nothing here presumes its outcome.

begin;

-- ---------------------------------------------------------------- documents
create table doctrine_document (
  id               uuid primary key default gen_random_uuid(),
  slug             text not null unique,
  title            text not null,
  content_class    text not null check (content_class in
                     ('playbook','sop','index','reference',
                      'dossier_narrative','distillation','rule')),
  visibility       text not null default 'shared'
                     check (visibility in ('shared','personal')),
  owner_actor_id   uuid references actor(id),
  review_policy_id uuid,                        -- fk added after policy table
  created_by       uuid references actor(id),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
comment on table doctrine_document is
  'A prose document migrated out of vault markdown (0075, doctrine-store P1). '
  'content_class ''rule'' is RESERVED for the P7 rule-store study — nothing '
  'writes it before that ruling.';
create index doctrine_document_class_idx
  on doctrine_document (content_class, updated_at desc);

create table doctrine_slug_alias (
  alias_slug   text primary key,
  document_id  uuid not null references doctrine_document(id),
  replaced_at  timestamptz not null default now()
);
comment on table doctrine_slug_alias is
  'Old slugs keep resolving after a rename — prompts, logs and links carry '
  'slugs for years (trajectory verdict: slug history ships day one).';

-- ----------------------------------------------------------------- sections
create table doctrine_section (
  id                  uuid primary key default gen_random_uuid(),
  document_id         uuid not null references doctrine_document(id),
  section_key         text not null,
  title               text,
  ordinal             integer not null default 0,
  status              text not null default 'active'
                        check (status in ('active','retired')),
  current_revision_id uuid,                     -- fk added after revision table
  current_version     bigint not null default 0,
  body_hash           text,
  review_after        timestamptz,
  updated_at          timestamptz not null default now(),
  unique (document_id, section_key)
);
comment on table doctrine_section is
  'Stable-address unit of doctrine. section_key never changes; reordering '
  'changes ordinal only. current_version is the optimistic-concurrency token '
  'every write verb must present (base_version), the same contract as the '
  'rest of the record layer.';
create index doctrine_section_review_idx
  on doctrine_section (review_after) where review_after is not null;
create index doctrine_section_doc_idx on doctrine_section (document_id, ordinal);

-- ------------------------------------------------- append-only revision log
create table doctrine_change_set (
  id              uuid primary key default gen_random_uuid(),
  actor_id        uuid not null references actor(id),
  session_key     text,
  idempotency_key text not null,
  state           text not null default 'prepared'
                    check (state in ('prepared','committed','rejected')),
  gate_run_id     uuid,                          -- fk added after gate tables
  created_at      timestamptz not null default now(),
  committed_at    timestamptz,
  unique (actor_id, idempotency_key)
);
comment on table doctrine_change_set is
  'Multi-section atomic write unit: every section in the set commits in one '
  'transaction or none do (the half-applied-SOP-rename preventer). Replaying '
  'an idempotency_key returns the original result, never a second revision.';

create table doctrine_change_item (
  change_set_id    uuid not null references doctrine_change_set(id),
  section_id       uuid not null references doctrine_section(id),
  op               text not null default 'write'
                     check (op in ('write','move','retire')),
  expected_version bigint not null,
  proposed_body    jsonb,
  primary key (change_set_id, section_id)
);

create table doctrine_revision (
  id                 uuid primary key default gen_random_uuid(),
  section_id         uuid not null references doctrine_section(id),
  version            bigint not null,
  parent_revision_id uuid references doctrine_revision(id),
  change_set_id      uuid references doctrine_change_set(id),
  actor_id           uuid not null references actor(id),
  session_key        text,
  body               jsonb not null,
  plain_text         text not null,
  content_hash       text not null,
  commit_message     text,
  created_at         timestamptz not null default now(),
  search_vector      tsvector generated always as
                       (to_tsvector('english', plain_text)) stored,
  unique (section_id, version)
);
comment on table doctrine_revision is
  'Append-only. body is the structured form (JSON schema per content_class, '
  'enforced by the body_schema gate); plain_text is the searchable rendering '
  'of the same content. content_hash detects no-op writes and drift.';
create index doctrine_revision_section_idx
  on doctrine_revision (section_id, created_at desc);
create index doctrine_revision_search_idx
  on doctrine_revision using gin (search_vector);

alter table doctrine_section
  add constraint doctrine_section_current_rev_fk
  foreign key (current_revision_id) references doctrine_revision(id);

-- ------------------------------------------------------- links versus edges
-- Two tables ON PURPOSE (unanimous council verdict): a citation is not a
-- precedence claim, and collapsing them is how "related" quietly becomes
-- "overrides". Links may target record-layer subjects, not just doctrine —
-- doctrine cites deals, parties and decisions constantly.
create table doctrine_link (
  id                uuid primary key default gen_random_uuid(),
  source_section_id uuid not null references doctrine_section(id),
  target_kind       text not null check (target_kind in
                      ('doctrine_document','doctrine_section',
                       'party','deal','decision','rule','loop','capture')),
  target_id         uuid not null,
  role              text not null default 'citation'
                      check (role in ('citation','related','example','source')),
  created_at        timestamptz not null default now()
);
create index doctrine_link_source_idx on doctrine_link (source_section_id);
create index doctrine_link_target_idx on doctrine_link (target_kind, target_id);

create table doctrine_edge_type (
  edge_type       text primary key,
  acyclic         boolean not null,
  precedence_rank integer,
  description     text not null
);
insert into doctrine_edge_type values
  ('OVERRIDES',      true,  10, 'source wins where both apply'),
  ('SUPERSEDES',     true,  20, 'target is historical, source replaces it'),
  ('EXCEPTION_TO',   true,  30, 'source carves a scoped exception out of target'),
  ('DEPENDS_ON',     true,  null, 'integrity only — target must stay live'),
  ('APPLIES_TO',     false, null, 'scope binding, no precedence'),
  ('CONFLICTS_WITH', false, null,
   'detected conflict — BLOCKS commit unless an OVERRIDES/SUPERSEDES edge resolves it');

create table doctrine_edge (
  id                        uuid primary key default gen_random_uuid(),
  source_section_id         uuid not null references doctrine_section(id),
  target_section_id         uuid not null references doctrine_section(id),
  edge_type                 text not null references doctrine_edge_type(edge_type),
  scope                     jsonb not null default '{}',
  introduced_by_revision_id uuid references doctrine_revision(id),
  retired_by_revision_id    uuid references doctrine_revision(id),
  created_at                timestamptz not null default now()
);
create unique index doctrine_edge_live_uq
  on doctrine_edge (source_section_id, target_section_id, edge_type)
  where retired_by_revision_id is null;
create index doctrine_edge_source_idx on doctrine_edge (source_section_id, edge_type);
create index doctrine_edge_target_idx on doctrine_edge (target_section_id, edge_type);

-- -------------------------------------------------------- review / staleness
create table doctrine_review_policy (
  id                       uuid primary key default gen_random_uuid(),
  name                     text not null unique,
  max_age_days             integer,
  revalidate_on_dep_change boolean not null default true,
  content_classes          text[]
);
comment on table doctrine_review_policy is
  'Staleness is COMPUTED (review_after cursor on the section), never lifecycle '
  'rows — the council held the no-CMS verdict at trajectory scale. '
  'max_age_days null = no calendar staleness.';
insert into doctrine_review_policy (name, max_age_days, content_classes) values
  ('standing-doctrine', 180, array['playbook','sop','reference']),
  ('routing',            90, array['index']),
  ('narrative',        null, array['dossier_narrative','distillation']);

alter table doctrine_document
  add constraint doctrine_document_review_fk
  foreign key (review_policy_id) references doctrine_review_policy(id);

-- --------------------------------------------------- generation + snapshots
create table doctrine_meta (
  id         integer primary key check (id = 1),
  generation bigint not null default 0,
  updated_at timestamptz not null default now()
);
insert into doctrine_meta (id) values (1);
comment on table doctrine_meta is
  'Singleton generation counter: bumps once per successful doctrine commit. '
  'The cache key and the snapshot coherence token for fleet reads.';

create table doctrine_snapshot (
  document_id  uuid primary key references doctrine_document(id),
  generation   bigint not null,
  snapshot_json jsonb not null,
  content_hash text not null,
  built_at     timestamptz not null default now()
);
comment on table doctrine_snapshot is
  'Read-through cache in Postgres (day-one fleet answer; Redis stays behind '
  'its measured trigger). Rebuilt on commit; doc.read prefers it.';

-- -------------------------------------------------------------- soft claims
create table doctrine_claim (
  section_id         uuid primary key references doctrine_section(id),
  holder_actor_id    uuid not null references actor(id),
  holder_session_key text not null,
  purpose            text not null,
  expires_at         timestamptz not null,
  created_at         timestamptz not null default now()
);
create index doctrine_claim_expiry_idx on doctrine_claim (expires_at);
comment on table doctrine_claim is
  'Cooperative expiring claims (default TTL 300s, max 1800s): a foreign '
  'unexpired claim blocks a write so two agents do not spend tokens preparing '
  'the same section. Never a correctness mechanism — base_version is. Expired '
  'claims are free; no sweeper needed (expires_at predicate).';

-- ---------------------------------------------------------- gate framework
create table doctrine_gate_check (
  check_key  text primary key,
  description text not null,
  severity   text not null check (severity in ('block','warn')),
  applies_to jsonb not null default '{}',
  impl_key   text not null,
  config     jsonb not null default '{}',
  enabled    boolean not null default true,
  created_at timestamptz not null default now()
);
comment on table doctrine_gate_check is
  'The validation registry: a check is a code function (impl_key, deployed '
  'with the connector) plus this row. A NEW GATE IS A FUNCTION AND A ROW, '
  'never a verb rewrite. Only deterministic synchronous checks may be '
  'severity=block; a block finding aborts the commit transaction.';

insert into doctrine_gate_check (check_key, description, severity, applies_to, impl_key) values
  ('base_version_match','expected_version equals current_version (also SQL-enforced)',
   'block','{"ops":["write","move","retire"]}','gates.base_version_match'),
  ('body_schema','body validates against its content_class JSON schema',
   'block','{"ops":["write"]}','gates.body_schema'),
  ('target_exists','every link/edge target resolves to a live row',
   'block','{"ops":["refs_set","write"]}','gates.target_exists'),
  ('edge_class_allowed','edge source/target classes legal for the edge_type',
   'block','{"ops":["refs_set"]}','gates.edge_class_allowed'),
  ('edge_acyclic','acyclic edge types stay acyclic after the write',
   'block','{"ops":["refs_set"]}','gates.edge_acyclic'),
  ('unresolved_conflict','CONFLICTS_WITH present without a resolving edge',
   'block','{"ops":["refs_set"]}','gates.unresolved_conflict'),
  ('banned_phrases','writing-rules lint on prospect-visible content classes',
   'block','{"ops":["write"],"content_classes":["playbook","sop","reference"]}',
   'gates.banned_phrases'),
  ('slug_unique','document slug free among documents and aliases',
   'block','{"ops":["create","rename"]}','gates.slug_unique'),
  ('personal_owner_required','visibility=personal requires owner_actor_id',
   'block','{"ops":["create","write"]}','gates.personal_owner_required'),
  ('claim_holder_or_free','no foreign unexpired claim on the section',
   'block','{"ops":["write","move","retire"]}','gates.claim_holder_or_free'),
  ('no_md_escape','body text does not instruct writing vault .md outside the manifest',
   'block','{"ops":["write"]}','gates.no_md_escape');

create table doctrine_gate_run (
  id            uuid primary key default gen_random_uuid(),
  change_set_id uuid references doctrine_change_set(id),
  dry_run       boolean not null default false,
  actor_id      uuid not null references actor(id),
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  result        text check (result in ('pass','fail')),
  report        jsonb not null default '[]'
);

create table doctrine_gate_finding (
  run_id    uuid not null references doctrine_gate_run(id),
  check_key text not null references doctrine_gate_check(check_key),
  severity  text not null,
  passed    boolean not null,
  message   text not null,
  path      text not null default '',
  primary key (run_id, check_key, path)
);

alter table doctrine_change_set
  add constraint doctrine_change_set_gate_fk
  foreign key (gate_run_id) references doctrine_gate_run(id);

-- ------------------------------------------------------- migration ledger
create table doctrine_migration_batch (
  id            uuid primary key default gen_random_uuid(),
  batch_no      integer not null unique,
  phase         text not null check (phase in ('forced_early','bounded','cutoff')),
  source_paths  text[] not null,
  source_hashes jsonb not null default '{}',
  state         text not null default 'pending'
                  check (state in ('pending','running','verified','failed')),
  row_counts    jsonb,
  error         text,
  started_at    timestamptz,
  finished_at   timestamptz
);
comment on table doctrine_migration_batch is
  'The migration ledger (P4/P5): every former vault path is accounted for in '
  'exactly one batch, with pre-import source hashes so reconciliation is a '
  'comparison, not a memory. The cutoff batch closes dual-read.';

commit;
