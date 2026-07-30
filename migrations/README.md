# migrations/ — the record layer's versioned DDL

*Scaffolded 2026-07-30 by the record-layer build session. Design doc:
`CARR AI/DNA/Deal Management/record-layer/` (blueprint + schema v2 + the
binding stress-test addendum). STATUS: schema v2 pending Joe's grain review
(blueprint §11) — nothing here has been applied to any database yet.*

## Rules

- **Forward-only, numbered.** `NNNN_name.sql`, applied in filename order by
  `tools/migrate.py` (`run.sh migrate`). A bad migration is fixed by a NEW
  migration; the runner sha-checks applied files and refuses drift.
- **Rehearse on a Neon branch first.** Risky changes run against a branch of
  production data before production (blueprint §4). Build sessions get
  BRANCH credentials only, never the production writer (addendum A14).
- **0001 mirrors the vault schema file.** `0001_init.sql` is the applied copy
  of `record-layer/schema-draft-2026-07-30.sql` (v2). Until first apply, keep
  them identical — after first apply, the vault file freezes as the design
  record and changes land here as new migrations.
- **Seeds are honest.** `0002_seed.sql` seeds only documented vocab;
  lead stages come from the live registry at import, human-reviewed, never
  guessed.

## Files

| file | what |
|---|---|
| `0001_init.sql` | Full schema v2: tables, triggers, indexes, sequences (addendum §A + §D applied) |
| `0002_seed.sql` | Actors, reference vocab, system_config thresholds |
| (build day) `0003_roles_views.sql` | carr_writer / carr_reader roles + the views (v_deal_board, v_export_deals, v_integrity_digest, ...) — needs the Neon project to exist so grants bind to real roles |
