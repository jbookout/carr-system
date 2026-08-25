-- 0317_tour_operations_foundation.sql
-- Additive, forward-only foundation for facts, provenance, rights, tour
-- projections, secure sharing, editable internal sheets, QC and publication.

begin;

create table if not exists ops.tour_rights_receipt (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  provider text not null,
  sku text,
  terms_url text not null,
  reviewed_at timestamptz not null,
  reviewer text not null,
  storage_class text not null,
  storable_fields jsonb not null,
  retention_limit text not null,
  purge_rule text not null,
  attribution text not null,
  map_compatibility text not null,
  export_exit_path text not null,
  quota_cost_gate text not null,
  status text not null check (status in ('active','expired','revoked','unknown')),
  created_at timestamptz not null default now(),
  check (jsonb_typeof(storable_fields) = 'array')
);

create table if not exists ops.tour_property (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  canonical_name text not null,
  canonical_address text not null,
  property_status text not null check (property_status in ('active','inactive','withdrawn','unknown')),
  created_at timestamptz not null default now(),
  retired_at timestamptz
);

create table if not exists ops.tour_source_evidence (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  stable_locator text not null,
  evidence_class text not null check (evidence_class in ('direct_source','linked_artifact','public_mirror','inference')),
  retrieved_at timestamptz not null,
  retrieval_status text not null check (retrieval_status in ('read','partial','inaccessible','failed')),
  content_digest text not null check (content_digest ~ '^sha256:[a-f0-9]{64}$'),
  rights_receipt_id uuid not null references ops.tour_rights_receipt(id),
  data_classification text not null check (data_classification in ('public','client_authorized','internal','restricted')),
  created_at timestamptz not null default now()
);

create table if not exists ops.tour_field_assertion (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  property_id uuid not null references ops.tour_property(id),
  field_key text not null,
  value jsonb not null,
  source_evidence_id uuid not null references ops.tour_source_evidence(id),
  rights_receipt_id uuid not null references ops.tour_rights_receipt(id),
  observed_at timestamptz not null,
  effective_from timestamptz not null,
  effective_to timestamptz,
  confidence text not null check (confidence in ('low','medium','high','unknown')),
  data_classification text not null check (data_classification in ('public','client_authorized','internal','restricted')),
  review_state text not null check (review_state in ('unreviewed','reviewed','conflicted','superseded','withdrawn')),
  created_at timestamptz not null default now(),
  check (effective_to is null or effective_to >= effective_from),
  check (jsonb_typeof(value) in ('object','array','string','number','boolean','null'))
);
create index if not exists tour_field_assertion_property_field_active_idx
  on ops.tour_field_assertion (organization_tenant_id, property_id, field_key, effective_from desc)
  where review_state in ('unreviewed','reviewed','conflicted');

create table if not exists ops.tour (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  tour_name text not null,
  tour_status text not null check (tour_status in ('draft','active','completed','cancelled','archived')),
  route_version integer not null check (route_version > 0),
  canonical_dataset_version text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists ops.tour_public_projection (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  tour_id uuid not null references ops.tour(id),
  projection_version integer not null check (projection_version > 0),
  facts_only boolean not null check (facts_only),
  property_count integer not null check (property_count > 0),
  render_payload jsonb not null,
  source_freshness jsonb not null default '[]'::jsonb,
  projection_digest text not null check (projection_digest ~ '^sha256:[a-f0-9]{64}$'),
  status text not null check (status in ('draft','qc_blocked','approved','published','superseded','quarantined','rolled_back')),
  created_at timestamptz not null default now(),
  unique (tour_id, projection_version),
  check (jsonb_typeof(render_payload) = 'object'),
  check (jsonb_typeof(source_freshness) = 'array'),
  check (not (render_payload ?| array['broker_recommendation','ranking','internal_contact','listing_agent_contact','internal_note','client_requirements','source_credentials','share_token','audit_detail']))
);

create table if not exists ops.tour_cheat_sheet_revision (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  tour_id uuid not null references ops.tour(id),
  revision_number integer not null check (revision_number > 0),
  content jsonb not null,
  editor_actor_id text not null,
  status text not null check (status in ('draft','saved','superseded')),
  created_at timestamptz not null default now(),
  unique (tour_id, revision_number),
  check (jsonb_typeof(content) = 'object')
);

create table if not exists ops.tour_share_grant (
  id uuid primary key default gen_random_uuid(),
  projection_id uuid not null references ops.tour_public_projection(id),
  token_digest text not null unique check (token_digest ~ '^sha256:[a-f0-9]{64}$'),
  audience text not null check (audience in ('client','internal')),
  created_at timestamptz not null default now(),
  expires_at timestamptz,
  revoked_at timestamptz,
  status text not null check (status in ('active','revoked','expired','rotated')),
  check (expires_at is null or expires_at > created_at),
  check (status <> 'revoked' or revoked_at is not null)
);

create table if not exists ops.tour_qc_finding (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  artifact_type text not null check (artifact_type in ('public_projection','pdf','map','cheat_sheet','share_grant')),
  artifact_id uuid not null,
  check_id text not null,
  severity text not null check (severity in ('blocker','error','warning','info')),
  state text not null check (state in ('open','accepted_risk','resolved','superseded')),
  evidence jsonb not null,
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  check (jsonb_typeof(evidence) = 'object')
);

create table if not exists ops.tour_publication (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  projection_id uuid not null references ops.tour_public_projection(id),
  publication_state text not null check (publication_state in ('draft','pending_qc','approved','published','quarantined','rolled_back')),
  projection_digest text not null check (projection_digest ~ '^sha256:[a-f0-9]{64}$'),
  actor_id text,
  created_at timestamptz not null default now(),
  state_changed_at timestamptz not null default now()
);

comment on table ops.tour_public_projection is
  'The only client-facing tour read model. It is facts-only and deliberately excludes internal notes, contacts, recommendations, requirements, and secrets.';
comment on table ops.tour_cheat_sheet_revision is
  'Internal, append-only editable cheat-sheet revisions. Never canonical property facts or public projection content.';
comment on table ops.tour_share_grant is
  'Stores only opaque-token digests; never a share URL token in plaintext.';

commit;

-- Rollback is forward-only: revoke application access and mark projections
-- quarantined/rolled_back. Never drop these evidence-bearing tables.
