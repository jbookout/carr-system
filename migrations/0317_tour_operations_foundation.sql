-- 0317_tour_operations_foundation.sql
-- Additive, tenant-safe foundation. Canonical facts are append-only and
-- client render data is always derived from normalized public assertions.

begin;

create table if not exists ops.tour_rights_receipt (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  provider text not null, sku text, receipt_version integer not null check (receipt_version > 0),
  receipt_digest text not null check (receipt_digest ~ '^sha256:[a-f0-9]{64}$'), terms_url text not null,
  reviewed_at timestamptz not null, reviewer text not null, intended_use text not null,
  allowed_field_classes jsonb not null, allowed_use_classes jsonb not null,
  effective_at timestamptz not null, expires_at timestamptz, revoked_at timestamptz,
  status text not null check (status in ('active','expired','revoked','unknown')),
  created_at timestamptz not null default now(), unique (organization_tenant_id, id),
  unique (organization_tenant_id, provider, receipt_version),
  check (jsonb_typeof(allowed_field_classes) = 'array' and jsonb_typeof(allowed_use_classes) = 'array'),
  check (expires_at is null or expires_at > effective_at), check (status <> 'revoked' or revoked_at is not null)
);

create table if not exists ops.tour_property (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  property_status text not null check (property_status in ('active','inactive','withdrawn','unknown')),
  created_at timestamptz not null default now(), retired_at timestamptz,
  unique (organization_tenant_id, id)
);

create table if not exists ops.tour_source_evidence (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  stable_locator text not null, evidence_class text not null check (evidence_class in ('direct_source','linked_artifact','public_mirror','inference')),
  retrieved_at timestamptz not null, retrieval_status text not null check (retrieval_status in ('read','partial','inaccessible','failed')),
  content_digest text not null check (content_digest ~ '^sha256:[a-f0-9]{64}$'), rights_receipt_id uuid not null,
  data_classification text not null check (data_classification in ('public','client_authorized','internal','restricted')),
  created_at timestamptz not null default now(), unique (organization_tenant_id, id),
  foreign key (organization_tenant_id, rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id, id)
);

create table if not exists ops.tour_field_assertion (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, property_id uuid not null,
  field_key text not null, value jsonb not null, source_evidence_id uuid not null, rights_receipt_id uuid not null,
  observed_at timestamptz not null, effective_from timestamptz not null, effective_to timestamptz,
  confidence text not null check (confidence in ('low','medium','high','unknown')),
  data_classification text not null check (data_classification in ('public','client_authorized','internal','restricted')),
  review_state text not null check (review_state in ('unreviewed','reviewed','conflicted','superseded','withdrawn')),
  created_at timestamptz not null default now(), unique (organization_tenant_id, id),
  foreign key (organization_tenant_id, property_id) references ops.tour_property (organization_tenant_id, id),
  foreign key (organization_tenant_id, source_evidence_id) references ops.tour_source_evidence (organization_tenant_id, id),
  foreign key (organization_tenant_id, rights_receipt_id) references ops.tour_rights_receipt (organization_tenant_id, id),
  check (effective_to is null or effective_to >= effective_from),
  check (jsonb_typeof(value) in ('object','array','string','number','boolean','null'))
);
create index if not exists tour_field_assertion_property_field_active_idx on ops.tour_field_assertion (organization_tenant_id, property_id, field_key, effective_from desc) where review_state in ('unreviewed','reviewed','conflicted');

create table if not exists ops.tour_fact_conflict (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, property_id uuid not null,
  field_key text not null, state text not null check (state in ('open','resolved','superseded')),
  opened_at timestamptz not null default now(), unique (organization_tenant_id, id),
  foreign key (organization_tenant_id, property_id) references ops.tour_property (organization_tenant_id, id)
);
create table if not exists ops.tour_fact_conflict_participant (
  conflict_id uuid not null, organization_tenant_id text not null, field_assertion_id uuid not null,
  participant_role text not null check (participant_role in ('candidate','selected','rejected')),
  created_at timestamptz not null default now(), primary key (organization_tenant_id, conflict_id, field_assertion_id),
  foreign key (organization_tenant_id, conflict_id) references ops.tour_fact_conflict (organization_tenant_id, id),
  foreign key (organization_tenant_id, field_assertion_id) references ops.tour_field_assertion (organization_tenant_id, id)
);
create table if not exists ops.tour_conflict_resolution_receipt (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, conflict_id uuid not null,
  selected_field_assertion_id uuid not null, rationale text not null, evidence jsonb not null, resolver_actor_id text not null,
  resolved_at timestamptz not null, receipt_digest text not null check (receipt_digest ~ '^sha256:[a-f0-9]{64}$'),
  created_at timestamptz not null default now(), unique (organization_tenant_id, id),
  foreign key (organization_tenant_id, conflict_id) references ops.tour_fact_conflict (organization_tenant_id, id),
  foreign key (organization_tenant_id, selected_field_assertion_id) references ops.tour_field_assertion (organization_tenant_id, id),
  check (jsonb_typeof(evidence) = 'object')
);

create table if not exists ops.tour (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, tour_name text not null,
  tour_status text not null check (tour_status in ('draft','active','completed','cancelled','archived')),
  route_version integer not null check (route_version > 0), canonical_dataset_version text not null,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique (organization_tenant_id, id)
);
create table if not exists ops.tour_property_membership (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, tour_id uuid not null, property_id uuid not null,
  route_version integer not null check (route_version > 0), route_sequence integer not null check (route_sequence > 0), route_label text not null,
  assertion_set_digest text not null check (assertion_set_digest ~ '^sha256:[a-f0-9]{64}$'), selected_at timestamptz not null default now(),
  unique (organization_tenant_id, id), unique (organization_tenant_id, tour_id, route_version, property_id),
  unique (organization_tenant_id, tour_id, route_version, route_sequence), unique (organization_tenant_id, tour_id, route_version, route_label),
  foreign key (organization_tenant_id, tour_id) references ops.tour (organization_tenant_id, id),
  foreign key (organization_tenant_id, property_id) references ops.tour_property (organization_tenant_id, id)
);

create table if not exists ops.tour_public_projection (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, tour_id uuid not null,
  projection_version integer not null check (projection_version > 0), route_version integer not null check (route_version > 0),
  facts_only boolean not null check (facts_only), projection_digest text not null check (projection_digest ~ '^sha256:[a-f0-9]{64}$'),
  derived_render_digest text, status text not null check (status in ('draft','qc_blocked','approved','published','superseded','quarantined','rolled_back')),
  created_at timestamptz not null default now(), unique (organization_tenant_id, id), unique (organization_tenant_id, tour_id, projection_version),
  foreign key (organization_tenant_id, tour_id) references ops.tour (organization_tenant_id, id)
);
create table if not exists ops.tour_public_projection_fact (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, projection_id uuid not null,
  property_id uuid not null, field_assertion_id uuid not null, route_version integer not null, display_field_key text not null,
  created_at timestamptz not null default now(), unique (organization_tenant_id, id),
  unique (organization_tenant_id, projection_id, property_id, display_field_key),
  foreign key (organization_tenant_id, projection_id) references ops.tour_public_projection (organization_tenant_id, id),
  foreign key (organization_tenant_id, property_id) references ops.tour_property (organization_tenant_id, id),
  foreign key (organization_tenant_id, field_assertion_id) references ops.tour_field_assertion (organization_tenant_id, id),
  check (display_field_key in ('display.name','display.address','suite','property_type','size','asking_economics','availability','parking','access','photos','floor_plan','source_attribution','as_of','caveat'))
);

create table if not exists ops.tour_cheat_sheet_revision (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, tour_id uuid not null,
  revision_number integer not null check (revision_number > 0), content jsonb not null, editor_actor_id text not null,
  status text not null check (status in ('draft','saved','superseded')), created_at timestamptz not null default now(),
  unique (organization_tenant_id, id), unique (organization_tenant_id, tour_id, revision_number),
  foreign key (organization_tenant_id, tour_id) references ops.tour (organization_tenant_id, id), check (jsonb_typeof(content) = 'object')
);
create table if not exists ops.tour_share_grant (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, projection_id uuid not null,
  grant_version integer not null check (grant_version > 0), token_digest text not null check (token_digest ~ '^sha256:[a-f0-9]{64}$'),
  permission_scopes jsonb not null, rotated_from_grant_id uuid, created_at timestamptz not null default now(), expires_at timestamptz,
  revoked_at timestamptz, status text not null check (status in ('active','revoked','expired','rotated')),
  unique (organization_tenant_id, id), unique (organization_tenant_id, projection_id, grant_version), unique (token_digest),
  foreign key (organization_tenant_id, projection_id) references ops.tour_public_projection (organization_tenant_id, id),
  foreign key (organization_tenant_id, rotated_from_grant_id) references ops.tour_share_grant (organization_tenant_id, id),
  check (jsonb_typeof(permission_scopes) = 'array'), check (expires_at is null or expires_at > created_at), check (status <> 'revoked' or revoked_at is not null)
);

create table if not exists ops.tour_qc_finding (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null,
  artifact_type text not null check (artifact_type in ('public_projection','pdf','map','cheat_sheet','share_grant')),
  artifact_id uuid not null, check_id text not null, severity text not null check (severity in ('blocker','error','warning','info')),
  state text not null check (state in ('open','accepted_risk','resolved','superseded')), evidence jsonb not null,
  created_at timestamptz not null default now(), resolved_at timestamptz, unique (organization_tenant_id, id), check (jsonb_typeof(evidence) = 'object')
);
create table if not exists ops.tour_publication (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, projection_id uuid not null,
  publication_state text not null check (publication_state in ('draft','pending_qc','approved','published','quarantined','rolled_back')),
  projection_digest text not null check (projection_digest ~ '^sha256:[a-f0-9]{64}$'), actor_id text,
  created_at timestamptz not null default now(), state_changed_at timestamptz not null default now(), unique (organization_tenant_id, id),
  foreign key (organization_tenant_id, projection_id) references ops.tour_public_projection (organization_tenant_id, id)
);
create table if not exists ops.tour_audit_event (
  id uuid primary key default gen_random_uuid(), organization_tenant_id text not null, event_type text not null,
  entity_type text not null, entity_id uuid not null, actor_id text, occurred_at timestamptz not null,
  event_digest text not null check (event_digest ~ '^sha256:[a-f0-9]{64}$'), payload jsonb not null,
  created_at timestamptz not null default now(), unique (organization_tenant_id, id), unique (organization_tenant_id, event_digest), check (jsonb_typeof(payload) = 'object')
);

create or replace function ops.tour_reject_mutation() returns trigger language plpgsql as $$ begin raise exception '% is append-only', tg_table_name; end $$;
create or replace function ops.tour_assertion_rights_guard() returns trigger language plpgsql as $$
begin
  if not exists (select 1 from ops.tour_rights_receipt r where r.id=new.rights_receipt_id and r.organization_tenant_id=new.organization_tenant_id and r.status='active' and r.effective_at <= new.observed_at and (r.expires_at is null or r.expires_at > new.observed_at) and r.revoked_at is null and r.allowed_use_classes ? 'canonical_fact' and (r.allowed_field_classes ? new.field_key or r.allowed_field_classes ? '*')) then raise exception 'rights receipt refuses asserted field/use'; end if;
  return new;
end $$;
create or replace function ops.tour_source_rights_guard() returns trigger language plpgsql as $$
begin
  if not exists (select 1 from ops.tour_rights_receipt r where r.id=new.rights_receipt_id and r.organization_tenant_id=new.organization_tenant_id and r.status='active' and r.effective_at <= new.retrieved_at and (r.expires_at is null or r.expires_at > new.retrieved_at) and r.revoked_at is null and r.allowed_use_classes ? 'source_intake') then raise exception 'rights receipt refuses source intake'; end if;
  return new;
end $$;
create or replace function ops.tour_projection_fact_guard() returns trigger language plpgsql as $$
begin
  if not exists (select 1 from ops.tour_public_projection p join ops.tour_field_assertion a on a.id=new.field_assertion_id and a.organization_tenant_id=new.organization_tenant_id join ops.tour_property_membership m on m.tour_id=p.tour_id and m.property_id=new.property_id and m.organization_tenant_id=new.organization_tenant_id and m.route_version=p.route_version where p.id=new.projection_id and p.organization_tenant_id=new.organization_tenant_id and new.route_version=p.route_version and a.property_id=new.property_id and a.review_state='reviewed' and a.data_classification='public') then raise exception 'projection fact lacks selected reviewed public assertion'; end if;
  return new;
end $$;
create trigger tour_source_evidence_append_only before update or delete on ops.tour_source_evidence for each row execute function ops.tour_reject_mutation();
create trigger tour_field_assertion_append_only before update or delete on ops.tour_field_assertion for each row execute function ops.tour_reject_mutation();
create trigger tour_cheat_sheet_revision_append_only before update or delete on ops.tour_cheat_sheet_revision for each row execute function ops.tour_reject_mutation();
create trigger tour_audit_event_append_only before update or delete on ops.tour_audit_event for each row execute function ops.tour_reject_mutation();
create trigger tour_rights_receipt_append_only before update or delete on ops.tour_rights_receipt for each row execute function ops.tour_reject_mutation();
create trigger tour_fact_conflict_participant_append_only before update or delete on ops.tour_fact_conflict_participant for each row execute function ops.tour_reject_mutation();
create trigger tour_conflict_resolution_receipt_append_only before update or delete on ops.tour_conflict_resolution_receipt for each row execute function ops.tour_reject_mutation();
create trigger tour_source_rights_guard before insert on ops.tour_source_evidence for each row execute function ops.tour_source_rights_guard();
create trigger tour_assertion_rights_guard before insert on ops.tour_field_assertion for each row execute function ops.tour_assertion_rights_guard();
create trigger tour_projection_fact_guard before insert or update on ops.tour_public_projection_fact for each row execute function ops.tour_projection_fact_guard();

comment on table ops.tour_public_projection is 'Client renderer cache metadata only. Facts exist only in normalized tour_public_projection_fact rows.';
comment on table ops.tour_share_grant is 'Stores opaque-token digests and scope/version/rotation lineage; never plaintext tokens.';

commit;
-- Rollback is forward-only: revoke grants and quarantine projections. Do not drop evidence-bearing tables.
