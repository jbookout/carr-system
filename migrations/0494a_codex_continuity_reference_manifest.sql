-- Candidate only: release sequencing owns the migration ordinal and execution.
-- Empty manifests preserve legacy physical revisions and their v1 digests.
alter table codex_continuity_checkpoint
  add column reference_manifest jsonb not null default '{}'::jsonb
  check (jsonb_typeof(reference_manifest) = 'object'
         and octet_length(reference_manifest::text) <= 128000);

alter table codex_continuity_revision
  add column reference_manifest jsonb not null default '{}'::jsonb
  check (jsonb_typeof(reference_manifest) = 'object'
         and octet_length(reference_manifest::text) <= 128000);
