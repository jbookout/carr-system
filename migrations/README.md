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

## Two migrations name a file that is not themselves (2026-08-15)

`0131_ops_release.sql` raises `0130 FAILED: ...` from eleven places in its own
proof block, names its probe row `migration-0130-proof`, and says "before 0130 it
pointed at nothing". `0132_work_shape_revision.sql` raises `0130 FAILED` too and
describes "pre-0130 ready rows".

**Both mean themselves.** They were written as `0130`, and the series was
renumbered the same day to restore unique ordering. `0130` is now
`0130_compiled_rules_supersedes.sql`, about something else entirely, so anyone
following one of those messages lands in the wrong file.

**The labels cannot be corrected, by design.** `tools/migrate.py` records a
sha256 per applied migration and refuses any applied file whose content changed:
"was EDITED after being applied (sha mismatch). Write a new migration instead;
never rewrite an applied one." Both files are applied to production and staging,
so editing a comment in either would break every future migrate run on both.

**The rule this leaves behind.** A rename inside a checksummed, append-only
series gets exactly one chance to fix the file's own self-references, and that
chance closes the moment the migration applies anywhere. Renaming and rewriting
the internals are one commit, or they are never.
