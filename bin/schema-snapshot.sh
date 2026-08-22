#!/bin/sh
# schema-snapshot.sh — write (or verify) db/schema.sql, the checked-in structure
# of the production database.
#
# WHY THIS EXISTS. The 130 migrations were treated as the way to build a
# database. On 2026-08-13 CI applied them to a genuinely empty Postgres for the
# first time and proved they cannot: several are DATA backfills carrying guards
# like "remapped ZERO deals — stop and report, do not force". Those guards are
# correct — they catch a backfill that silently did nothing to production — but
# they assert on business data that only production has, so an empty database
# legitimately refuses them. That is by design, not a bug, and no amount of
# adding rows fixes it.
#
# THE DECISION (2026-08-13, internal, mine to make per the escalation gate).
# Stop treating replay-from-zero as how an environment gets built. This snapshot
# is the structure; the migrations govern changes going FORWARD. Doctrine's
# requirement that "a fresh non-production environment can be reconstructed from
# repository declarations" is still met, because this file IS a repository
# declaration — and unlike the replay it actually works.
#
# The two rejected alternatives, recorded so nobody re-litigates them blind:
#   * Teach every data backfill to skip its guard when the table is empty. Keeps
#     the replay story literally true, but every future backfill has to remember
#     the pattern and forgetting it is SILENT — the guard just quietly stops
#     protecting anything. Ongoing discipline debt with nothing enforcing it.
#   * Split into replayable schema migrations and production-only data lanes.
#     Cleanest on paper, but it adds a judgment call to every future change, and
#     a wrong call puts a data move where it will be replayed against an empty
#     database — the exact failure being fixed.
#
# NO BUSINESS DATA, EVER. Three things go in and nothing else: the STRUCTURE
# (--schema-only), the APPLIED-MIGRATION LEDGER, and the REFERENCE VOCABULARY
# named in the explicit list below. No clients, no deals, no parties, no notes,
# no events. That is what makes this file safe to commit, and the vocabulary list
# is hand-checked rather than pattern-matched precisely so it can never widen
# into business data by accident. pipelines/staging-isolation-proof.sql asserts
# the business tables are empty after a load, so a mistake here is caught rather
# than discovered. --no-owner --no-acl for the same reason
# bin/backup-dump.sh uses them: an embedded OWNER TO / GRANT names roles a fresh
# database has never heard of, and the first such statement aborts the load.
#
# "Grants are rebuilt by the migrations, which are in git" — this file's
# original claim, and it has the same shelf life the role claim had: true only
# while the granting migrations are PENDING. Once the ledger absorbs them, the
# grants stop running anywhere, and a database built from this snapshot has the
# roles (preamble below) holding NOTHING. That is exactly how CI's migration
# class ran green while proving nothing about privileges — discovered 2026-08-14
# on PR #75, where verifying carr_writer's insert on lead meant replaying
# 0001-0004 by hand because the database CI builds could not answer the
# question. So the snapshot carries the app roles' ACLs itself: the CARR GRANTS
# section below reads them from production's catalogs and emits plain GRANTs,
# scoped to the app roles only so no other principal's ACLs enter the tree.
# tools/test-schema-snapshot-grants.py pins the shapes it must carry.
#
# THE ROLES THEMSELVES ARE NOT, and assuming they were is a bug this file shipped
# on 2026-08-14. The reasoning above was true only while every role-creating
# migration was still PENDING relative to this snapshot: 0115 declares
# carr_reader, carr_writer and carr_exporter (NOLOGIN privilege bundles), so while the
# ledger stopped at 0114 a fresh database got the roles by replaying it. The
# moment a refresh advanced the ledger PAST 0115, that migration stopped being
# pending, the role creation stopped running anywhere, and the next migration to
# grant against those roles died with `role "carr_jobs" does not exist` — which
# is exactly how CI failed on migration 0117.
#
# The snapshot is the base for every fresh database, so it has to carry the
# roles itself rather than inherit them from a migration it has already absorbed.
# The privilege bundles remain NOLOGIN. carr_jobs is different: it is the
# narrow unattended runtime identity, so the preamble creates it LOGIN with an
# unprinted random placeholder, or converts an older NOLOGIN placeholder. An
# already-login carr_jobs is left exactly as it is, including its password.
# Production is untouched because its existing carr_jobs is already LOGIN.
#
# Usage:
#   bin/schema-snapshot.sh            # regenerate db/schema.sql from production
#   bin/schema-snapshot.sh --check    # non-zero if the checked-in file is stale
#   bin/schema-snapshot.sh --from-disposable-local postgres://carr_ci@127.0.0.1:<port>/carr_ci
#       # generate only from the loopback disposable PG17 full-build target
#
# Needs production access, so it runs on Joe's Mac and never in CI — CI consumes
# the committed file and cannot reach production by construction.

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO/db/schema.sql"
NEONCTL="$REPO/mcp-server/node_modules/.bin/neonctl"

PG_DUMP=""
for c in /opt/homebrew/opt/libpq/bin/pg_dump /usr/local/opt/libpq/bin/pg_dump pg_dump; do
  if command -v "$c" >/dev/null 2>&1; then PG_DUMP="$c"; break; fi
done
[ -n "$PG_DUMP" ] || { echo "schema-snapshot: no pg_dump found" >&2; exit 69; }

PSQL=""
for c in /opt/homebrew/opt/libpq/bin/psql /usr/local/opt/libpq/bin/psql psql; do
  if command -v "$c" >/dev/null 2>&1; then PSQL="$c"; break; fi
done
[ -n "$PSQL" ] || { echo "schema-snapshot: no psql found (needed for the grants section)" >&2; exit 69; }

# pg_dump's completion trailer has varied in its number of terminal blank
# records across client versions. Keep every interior blank line intact, but
# make the tracked snapshot's EOF a one-newline invariant.
EOF_NORMALIZER='
/^[[:space:]]*$/ { trailing = trailing $0 ORS; next }
{ printf "%s", trailing; trailing = ""; print }
'

normalise_eof() {
  awk "$EOF_NORMALIZER"
}
CHECK=0
URL=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --from-disposable-local)
      [ "$#" -ge 2 ] || { echo "schema-snapshot: --from-disposable-local needs a DSN" >&2; exit 64; }
      URL="$2"; shift ;;
    *) echo "schema-snapshot: unknown argument $1" >&2; exit 64 ;;
  esac
  shift
done

if [ -n "$URL" ]; then
  printf '%s\n' "$URL" | grep -Eq '^postgres://carr_ci@127\.0\.0\.1:[0-9]{4,5}/carr_ci$' \
    || { echo "schema-snapshot: disposable source must be passwordless carr_ci on 127.0.0.1/carr_ci" >&2; exit 64; }
else
  [ -x "$NEONCTL" ] || { echo "schema-snapshot: neonctl not found at $NEONCTL" >&2; exit 69; }
  URL="$("$NEONCTL" connection-string production \
          --project-id steep-field-48688294 --role-name neondb_owner 2>/dev/null)"
  [ -n "$URL" ] || { echo "schema-snapshot: could not obtain the production connection string" >&2; exit 1; }
fi

# Some bounded build seeds belong only to schema that has actually entered the
# source ledger.  A production-truth snapshot taken immediately before that
# migration must leave the seed pending with the migration; embedding it early
# makes the pending migration fail on its own primary key.
RENEWAL_SOURCE_APPLIED="$("$PSQL" "$URL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0230_renewal_decision_delivery.sql')" \
  2>/dev/null)"
case "$RENEWAL_SOURCE_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the renewal-delivery ledger state" >&2; exit 1 ;;
esac

# pg_dump renders timestamptz in the server session timezone; pin it so the
# Production and disposable-local paths serialize identical instants alike.
export PGOPTIONS='-c timezone=UTC'

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# THE ROLE PREAMBLE, first in the file so the roles exist before anything that
# could reference them. See the header for why this cannot be left to 0115.
cat > "$TMP" <<'ROLES'
--
-- CARR ROLE PREAMBLE (bin/schema-snapshot.sh) — not produced by pg_dump.
--
-- This dump is --no-owner --no-acl, so it names no roles and grants nothing.
-- The roles still have to EXIST before the pending migrations that grant to
-- them run, and they can no longer be got by replaying 0115: once this
-- snapshot's ledger passed 0115 that migration stopped being pending anywhere.
-- carr_exporter aged into the same trap by way of 0006 and joined the list on
-- 2026-08-14, when the grants section below started carrying its privileges.
-- carr_authority aged into it by way of 0161 and joined on 2026-08-19: the
-- refresh that carried the ledger past 0161 stopped that migration replaying,
-- and the next rebuild failed five db-gates with `role "carr_authority" does
-- not exist`. That is the third time this exact trap has been sprung, so the
-- rule it teaches is worth stating plainly: ANY migration that creates a role
-- must add that role here in the same change, because the day its ledger entry
-- lands is the day it stops creating anything.
-- carr_device_evidence made it FOUR, the same day and on this same branch, by
-- way of 0163. It surfaced only after the guidance and retrieval seed rows were
-- added, because control-plane-db-gate could not reach the privilege check that
-- names it until the gates ahead of it stopped failing — and when it did reach
-- it, it did not fail cleanly: `has_function_privilege('carr_device_evidence',
-- ...)` RAISES on a missing role, so the gate crashed with a traceback instead
-- of a finding. Four for four, every one caught by a rebuild rather than by the
-- change that created the role.
--
-- All privilege bundles whose creating migrations are in the snapshot ledger
-- are created here. carr_backup (LOGIN) is deliberately NOT: it is the backup credential,
-- bin/backup-dump.sh supplies it, no gate asks for it, and creating a second
-- login role with a placeholder password to satisfy nothing is a cost with no
-- buyer. If a gate ever needs it, add it the way carr_jobs is added, not by
-- widening a pattern.
-- carr_reader, carr_writer, carr_exporter, carr_authority,
-- carr_device_evidence, the four calendar-prebrief roles, and the renewal
-- source-attestor role are privilege
-- bundles, so they stay NOLOGIN. carr_jobs is
-- the narrow unattended runtime identity: a fresh
-- rebuild must make it LOGIN. If an older snapshot created it NOLOGIN, convert
-- it with a fresh random placeholder password; an already-login role is left
-- completely unchanged. The placeholder is generated in-process and never
-- selected, logged, or written into this dump.
--
do $$
declare
  r text;
  jobs_can_login boolean;
  jobs_placeholder text;
begin
  foreach r in array array[
    'carr_reader','carr_writer','carr_exporter','carr_authority','carr_device_evidence',
    'carr_calendar_prebrief_jobs','carr_calendar_prebrief_canary_jobs',
    'carr_calendar_prebrief_attestors','carr_calendar_prebrief_email_resolver',
    'carr_renewal_source_attestors'
  ] loop
    if not exists (select 1 from pg_roles where rolname = r) then
      execute format('create role %I nologin', r);
    end if;
  end loop;
  select rolcanlogin into jobs_can_login from pg_roles where rolname='carr_jobs';
  if not found then
    jobs_placeholder := replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '');
    execute format('create role %I login password %L', 'carr_jobs', jobs_placeholder);
  elsif not jobs_can_login then
    jobs_placeholder := replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '');
    execute format('alter role %I login password %L', 'carr_jobs', jobs_placeholder);
  end if;

  -- THE AGING TRAP ONE LEVEL DOWN: not creating a role, but joining one.
  -- 0273 grants the carr_authority bundle to the human authority login roles,
  -- and the day a refresh carried this ledger past 0273 that grant stopped
  -- replaying anywhere. Nothing else carried it: the CARR GRANTS section below
  -- admits only app roles and neondb_owner as members, and neither login role
  -- is either. So a database rebuilt from this file had carr_authority holding
  -- its 24 grants and NOBODY holding carr_authority — the ledger claiming 0273
  -- applied while the file it vouches for described a database where it never
  -- had. Found by an independent review seat, loop #506 finding 3.
  --
  -- WHY THE LOGIN ROLES ARE NOT CREATED HERE, only joined. They are human
  -- authority credentials, provisioned in the database provider's console
  -- outside this repository. Minting a local carr_authority_joe so a grant has
  -- somewhere to land would manufacture a login that authenticates as Joe's
  -- authority principal on every machine that rebuilds. That is the carr_backup
  -- reasoning above, except the cost is a security regression rather than an
  -- unused role.
  --
  -- SO EACH IS GUARDED AND THE ABSENCE IS ANNOUNCED, which is 0273's own shape
  -- reproduced rather than production's current state copied. Reproducing the
  -- loop is what makes this self-maintaining: carr_authority_dell does not
  -- exist today (the control-plane contract marks Dell's authority login
  -- optional_nonblocking), and on the day it is provisioned a rebuild grants him
  -- too with no edit here. Announced rather than skipped in silence, per rule
  -- 88e9b5eb: "not authorized" and "not possible" are different findings.
  -- Re-granting an existing membership is a no-op, so this is idempotent.
  foreach r in array array['carr_authority_joe', 'carr_authority_dell'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant carr_authority to %I', r);
    else
      raise notice 'authority login role % is absent, so the carr_authority membership 0273 grants it was not applied', r;
    end if;
  end loop;
end $$;

ROLES

if ! "$PG_DUMP" --schema-only --no-owner --no-acl "$URL" >> "$TMP"; then
  echo "schema-snapshot: pg_dump failed — nothing written" >&2
  exit 1
fi

# THE CARR GRANTS SECTION. --no-acl stays — a raw ACL dump names Neon's own
# principals and whatever login roles neonctl has minted per environment, and
# the first grant naming an absent role aborts the load. Instead the app roles'
# privileges are read from the catalogs and emitted as plain GRANTs, scoped by
# grantee to the NOLOGIN bundles plus the narrow carr_jobs login the preamble
# creates. Membership
# bundles may additionally name neondb_owner (0005/0006 grant it the bundles),
# which every loading environment already has: .github/workflows/ci.yml creates
# it for the same reason.
#
# The section rides AFTER the structure because a grant on a table that does
# not exist yet aborts the load, and BEFORE the ledger because it is structure,
# not data. SIX shapes, each ordered deterministically so --check reports
# drift and nothing else: PUBLIC revokes, schema usage, relation grants (tables,
# views, sequences), column-scoped grants (0021, 0117 — where the columns
# OUTSIDE the list are the point), function execute (0094, 0106), and
# memberships.
#
# THE PUBLIC REVOKES COME FIRST AND ARE THE ONLY SHAPE THAT TAKES A PRIVILEGE
# AWAY, added 2026-08-19. Postgres grants EXECUTE on every new function to
# PUBLIC by default, and PUBLIC includes every role, so a migration that means
# to keep a function to one principal has to revoke that default explicitly —
# 0168 does exactly this: `revoke all on function ops.record_guidance_decision
# (uuid,text,text,text) from public,carr_writer`. This section emitted only
# GRANTs, so a database built from the snapshot got every function back at its
# permissive default while the revoke that tightened it sat in a migration the
# ledger had already absorbed.
#
# That is the same aging trap as the roles, one level down, and it fails in the
# more dangerous direction: a rebuilt environment is LOOSER than production, not
# broken, so nothing refuses to start — CI's db-gates caught it as
# "carr_writer can execute authority function ops.record_guidance_decision",
# "browser challenge ledger grant leaked" and "raw plan/acceptance authority
# leaked" only because those gates assert the boundary directly. Production is
# unaffected: it holds the revokes already, and this file is never loaded into
# it.
#
# Read from production's catalogs like everything else here. A NULL proacl means
# the function still carries the default, so there is nothing to revoke; a
# non-null proacl that does not name PUBLIC (grantee 0) is a function whose
# default was deliberately taken away, and that is what gets emitted.
GRANTS_SQL="$(mktemp)"
cat > "$GRANTS_SQL" <<'GRANTSQL'
-- Force pg_get_function_identity_arguments() to schema-qualify composite
-- argument types.  Otherwise a type visible through the dump connection's
-- search_path is rendered bare and the later grants section cannot load under
-- a fresh session's search_path.
set search_path = '';

-- Built from nspname + proname + identity arguments rather than from
-- oid::regprocedure, which omits the schema for anything the current
-- search_path already covers. That produced bare `revoke all on function
-- capture_call_context(uuid[])` lines, which resolve to whatever the LOADING
-- session's search_path happens to point at — the one thing a snapshot must
-- never depend on.
select format('revoke all on function %s.%s(%s) from public;',
              quote_ident(n.nspname), quote_ident(p.proname),
              pg_get_function_identity_arguments(p.oid))
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
 where n.nspname in ('ops', 'public')
   and p.proacl is not null
   and not exists (select 1
                     from aclexplode(p.proacl) a
                    where a.grantee = 0
                      and a.privilege_type = 'EXECUTE')
 order by 1;

with app(rolname) as (
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter'), ('carr_authority'), ('carr_device_evidence'),
         ('carr_calendar_prebrief_jobs'), ('carr_calendar_prebrief_canary_jobs'),
         ('carr_calendar_prebrief_attestors'), ('carr_calendar_prebrief_email_resolver'),
         ('carr_renewal_source_attestors')
)
select format('grant %s on schema %s to %s;',
              string_agg(distinct lower(a.privilege_type), ', '
                         order by lower(a.privilege_type)),
              n.nspname, r.rolname)
  from pg_namespace n
  cross join lateral aclexplode(n.nspacl) a
  join pg_roles r on r.oid = a.grantee
 where r.rolname in (select rolname from app)
 group by n.nspname, r.rolname
 order by n.nspname, r.rolname;

with app(rolname) as (
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter'), ('carr_authority'), ('carr_device_evidence'),
         ('carr_calendar_prebrief_jobs'), ('carr_calendar_prebrief_canary_jobs'),
         ('carr_calendar_prebrief_attestors'), ('carr_calendar_prebrief_email_resolver'),
         ('carr_renewal_source_attestors')
)
select format('grant %s on %s %s.%s to %s;',
              string_agg(distinct lower(a.privilege_type), ', '
                         order by lower(a.privilege_type)),
              case c.relkind when 'S' then 'sequence' else 'table' end,
              n.nspname, c.relname, r.rolname)
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  cross join lateral aclexplode(c.relacl) a
  join pg_roles r on r.oid = a.grantee
 where r.rolname in (select rolname from app)
 group by n.nspname, c.relname, c.relkind, r.rolname
 order by n.nspname, c.relname, r.rolname;

with app(rolname) as (
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter'), ('carr_authority'), ('carr_device_evidence'),
         ('carr_calendar_prebrief_jobs'), ('carr_calendar_prebrief_canary_jobs'),
         ('carr_calendar_prebrief_attestors'), ('carr_calendar_prebrief_email_resolver'),
         ('carr_renewal_source_attestors')
)
select format('grant %s (%s) on table %s.%s to %s;',
              lower(a.privilege_type),
              string_agg(att.attname, ', ' order by att.attnum),
              n.nspname, c.relname, r.rolname)
  from pg_attribute att
  join pg_class c on c.oid = att.attrelid
  join pg_namespace n on n.oid = c.relnamespace
  cross join lateral aclexplode(att.attacl) a
  join pg_roles r on r.oid = a.grantee
 where r.rolname in (select rolname from app)
   and not att.attisdropped
 group by n.nspname, c.relname, r.rolname, a.privilege_type
 order by n.nspname, c.relname, r.rolname, lower(a.privilege_type);

with app(rolname) as (
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter'), ('carr_authority'), ('carr_device_evidence'),
         ('carr_calendar_prebrief_jobs'), ('carr_calendar_prebrief_canary_jobs'),
         ('carr_calendar_prebrief_attestors'), ('carr_calendar_prebrief_email_resolver'),
         ('carr_renewal_source_attestors')
)
select format('grant execute on function %s.%s(%s) to %s;',
              n.nspname, p.proname,
              pg_get_function_identity_arguments(p.oid), r.rolname)
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  cross join lateral aclexplode(p.proacl) a
  join pg_roles r on r.oid = a.grantee
 where r.rolname in (select rolname from app)
   and lower(a.privilege_type) = 'execute'
 order by n.nspname, p.proname, r.rolname;

with app(rolname) as (
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter'), ('carr_authority'), ('carr_device_evidence'),
         ('carr_calendar_prebrief_jobs'), ('carr_calendar_prebrief_canary_jobs'),
         ('carr_calendar_prebrief_attestors'), ('carr_calendar_prebrief_email_resolver'),
         ('carr_renewal_source_attestors')
)
-- pg_auth_members permits different grantors for the same role/member pair.
-- The snapshot has no grantor field, so render each semantically identical
-- membership exactly once without deduplicating any object ACL shape above.
select distinct format('grant %s to %s;', gr.rolname, mem.rolname)
  from pg_auth_members m
  join pg_roles gr  on gr.oid  = m.roleid
  join pg_roles mem on mem.oid = m.member
 where gr.rolname in (select rolname from app)
   and mem.rolname in (select rolname from app union select 'neondb_owner')
 order by 1;
GRANTSQL

cat >> "$TMP" <<'GRANTHDR'
--
-- CARR GRANTS (bin/schema-snapshot.sh) — not produced by pg_dump.
--
-- The app roles' privileges, read from production's catalogs. Without them a
-- database built from this file has the roles holding nothing, and CI's
-- migration class answers has_table_privilege() with false for everything —
-- the 2026-08-14 gap. Grantees are scoped to the preamble's four roles (plus
-- neondb_owner, membership bundles only) so no other principal's ACLs can
-- enter the tree. Shapes pinned by tools/test-schema-snapshot-grants.py.
--

GRANTHDR

if ! "$PSQL" -X -Atq -v ON_ERROR_STOP=1 "$URL" -f "$GRANTS_SQL" >> "$TMP"; then
  rm -f "$GRANTS_SQL"
  echo "schema-snapshot: could not read the app-role grants — nothing written" >&2
  exit 1
fi
rm -f "$GRANTS_SQL"

# An empty grants section is the truncation problem in a new coat: production
# certainly holds grants for these roles, so zero emitted statements means the
# read failed in a way psql did not report, and committing the result would
# silently define a database where the roles hold nothing — the exact gap this
# section exists to close.
if ! grep -q '^grant .* to carr_' "$TMP"; then
  echo "schema-snapshot: grants section came back empty — nothing written" >&2
  exit 1
fi

# THE APPLIED-MIGRATION LEDGER RIDES ALONG, and it is the difference between a
# snapshot that works and one that does not. schema_migrations is BUILD
# metadata, not business data — it records which migration filenames have run.
# Without its rows, a database loaded from this file has the full structure but
# an empty ledger, so the runner would try to apply all 130 migrations again and
# die on the first CREATE TABLE that already exists. With them, the loaded
# database honestly reports itself up to date and the ONLY thing pending is a
# genuinely new migration — which is exactly the question worth gating a change
# on: does this new change apply cleanly to the database we actually have?
if ! "$PG_DUMP" --data-only --no-owner --no-acl --table=schema_migrations "$URL" >> "$TMP"; then
  echo "schema-snapshot: could not dump the applied-migration ledger — nothing written" >&2
  exit 1
fi

# REFERENCE VOCABULARY IS A THIRD CATEGORY, and leaving it out made the first
# staging build useless. It is neither structure nor business data: closed sets
# like the eight deal phases or the six client statuses, seeded by migrations,
# pointed at by foreign keys. A database with the structure and none of these
# cannot hold a single row — client.status is a foreign key to client_status,
# which was empty. Verified on the first staging load: 115 tables, 0 statuses.
#
# THE LIST IS EXPLICIT AND HAND-CHECKED, never pattern-matched. Every table below
# was counted on production first (2 to 18 rows each, all closed vocabularies).
# A pattern like "small tables" or "tables seeded by a migration" would sweep in
# client, party and event, which the same migrations also insert into — dumping
# those would put real client data in a tracked file, which is the one outcome
# this whole design exists to prevent.
#
# DELIBERATELY EXCLUDED, and worth stating rather than leaving to inference:
#   * system_config (21 rows) — operational settings, not vocabulary, and the
#     kind of table where a value that should never be committed can appear.
#     Staging can set its own.
#   * client, party, deal, event and every other business table — the point.
# `actor` (12 rows) IS included: internal actor identities (joe, dell, system,
# automation) that events carry foreign keys to. Not client data.
#
# TWO SEEDED CONFIGURATION TABLES JOINED ON 2026-08-19, on Joe's ruling, and they
# widen this list's category from "closed vocabulary" to "rows production has
# that a rebuild silently lacks". Both are data steps inside migrations the
# ledger has now absorbed, so the migration no longer replays and a database
# built from this file gets the table and none of its rows — the same aging trap
# as the roles and the PUBLIC revokes above, one level further in:
#   * ops.guidance_registry (1 row) — the registry's own header row, created by
#     0168's `insert into ops.guidance_registry(created_by)`. Without it
#     guidance-registry-db-gate fails on "expected one row ... where singleton".
#   * retrieval_proposal (10 rows, all pending) — 0135's own "Seed evidence
#     only" block, which it says "activate nothing until Joe or Dell approves
#     the batch". Without them situation-retrieval-db-gate fails on "expected 10
#     pending seed proposals, found 0".
#   * retrieval_ranking_policy (2 rows) — the versioned ranking policies,
#     coequal-normalized-v1 (active, default) and lexical-dominant-v1
#     (candidate). The active row carries golden_suite_digest, which
#     assert_situation_retrieval_golden() looks up BY DIGEST; with no policy row
#     the lookup returns null and it raises "golden suite digest mismatch".
#     Found only after adding the two above, because the gate could not reach
#     this check until it got past the proposals.
#   * The exact two reviewed ops.enforcement_control_catalog controls are
#     verified separately and appended as deterministic SQL below — never via
#     the vocabulary pg_dump. Their fixed implementation/test references,
#     enforcement classes, and installed/verification metadata contain no
#     client, deal, event, secret, or runtime usage data. 0194 is already in a
#     rebuilt snapshot's ledger, so its seed does not replay; without this
#     controlled block the 0228 lifecycle cannot validate pinned rules. Do not
#     add rule_control_binding or any receipt/rule table: those are per-rule
#     history, not bounded internal control configuration.
#   * The disabled renewal-radar-source-daily v1 job definition is a fixed
#     internal contract required by 0230's lease-bound delivery gate. Carry
#     only that exact row; never widen this to arbitrary job/runtime rows.
#
# I counted every other table 0135 and 0168 seed, so the next rebuild does not
# discover a fourth one the same way: doctrine_concept_mapping,
# retrieval_concept, retrieval_phrase, ops.guidance_authority_binding,
# ops.guidance_situation_mapping and ops.guidance_registry_event are all EMPTY on
# production and need nothing. retrieval_query_log has 185 rows and is
# deliberately EXCLUDED — it is a usage log, not configuration, it carries the
# text of real queries, and no gate asks for it.
# HAND-CHECKED BEFORE ADDING, which is the rule this list lives by: counted on
# production (exactly 1 and exactly 10, all ten still pending), and every payload
# read. They are search concepts and phrases about this system's own runbooks —
# "record layer outage diagnosis", "how the operating playbook learns from
# mistakes". No client, party, deal, note or event appears in either table.
#
# THE LINE THAT STILL HOLDS, so this widening does not become a licence: a table
# qualifies because someone read its rows and can say what is in them, never
# because a migration seeded it. "Tables seeded by a migration" would sweep in
# client, party and event, which the same migrations also insert into.
VOCAB_TABLES="activity_kind client_status client_type contact_state deal_lane \
deal_phase deal_type_ref lead_lane lead_stage loop_domain negotiation_claim_type \
participant_role party_link_kind vendor_category vendor_disposition \
vendor_relationship_level diagnostic_route submarket_condition doctrine_edge_type \
doctrine_review_policy actor retrieval_proposal retrieval_ranking_policy \
ops.guidance_registry"

VOCAB_ARGS=""
for t in $VOCAB_TABLES; do
  VOCAB_ARGS="$VOCAB_ARGS --table=$t"
done

# shellcheck disable=SC2086
if ! "$PG_DUMP" --data-only --no-owner --no-acl $VOCAB_ARGS "$URL" >> "$TMP"; then
  echo "schema-snapshot: could not dump the reference vocabulary — nothing written" >&2
  exit 1
fi

# The control catalog is deliberately NOT a --table dump: carrying every row
# would let arbitrary implementation prose into a tracked snapshot. Instead the
# REPOSITORY names which controls may be carried, the source is verified to hold
# every one of them, and only those are rendered — so an applied seed ledger
# cannot hide missing mutable seed rows, and nothing reaches the snapshot that
# this repository has not declared.
#
# WIDENED 2026-08-22, from two hand-listed keys to the full declared set, and the
# reason is the trap that made it necessary. The catalog is what approve-rule
# consults, and until this week it held three controls against the 59 the
# repository described, so approving a rule enforced by any of the rest needed a
# hand-written migration first. Migrations 0274 and 0275 seeded it from
# ops/config/rule-enforcement-map.json and its companion class file. But the
# moment those migrations entered Production's ledger they entered this snapshot
# too, so a database rebuilt from the snapshot considered them applied, never ran
# them, and came up with an empty catalog — failing
# ops/control-catalog-parity-gate.py on 60 absent controls. A snapshot that
# carries a migration's LEDGER ROW but not the rows it seeded describes a
# database nobody can rebuild.
#
# THE BOUNDARY THAT MATTERS IS UNCHANGED, and it is not the number two: it is
# that the key list comes from the repository rather than from whatever happens
# to be in Production. ops/sync_control_catalog.py compiles it from the same two
# declaration files the seeding migrations were generated from, so a row can only
# ride along in this snapshot if a reviewed repository change put its key there.
# A control present in the source and absent from the declarations is NOT
# rendered — it is left for ops/control-catalog-parity-gate.py to report, which
# is how ci_gates was found on 2026-08-22.
# The declared control keys, compiled from the repository's own declarations by
# the same module that generates the seeding migrations. A quoted, comma-joined
# SQL list; the count is asserted separately so a silently empty list cannot pass.
CATALOG_PY="$REPO/.venv/bin/python"
[ -x "$CATALOG_PY" ] || CATALOG_PY=python3
if ! DECLARED_KEYS="$("$CATALOG_PY" - "$REPO" <<'DECLARED_CONTROL_KEYS'
import importlib.util, pathlib, sys
repo = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("scc", repo / "ops" / "sync_control_catalog.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
keys = sorted(r["control_key"] for r in mod.compile_catalog())
if not keys:
    raise SystemExit("no declared control keys")
print(",".join("'" + k.replace("'", "''") + "'" for k in keys))
DECLARED_CONTROL_KEYS
)"; then
  echo "schema-snapshot: could not compile the declared control keys — nothing written" >&2
  exit 1
fi
DECLARED_COUNT="$(printf '%s' "$DECLARED_KEYS" | tr ',' '\n' | grep -c .)"
if [ "$DECLARED_COUNT" -lt 2 ]; then
  echo "schema-snapshot: declared control key list is implausibly short — nothing written" >&2
  exit 1
fi

if ! sed -e "s/__DECLARED_KEYS__/$DECLARED_KEYS/g" -e "s/__DECLARED_COUNT__/$DECLARED_COUNT/g" <<'CONTROL_CATALOG_VERIFY' | "$PSQL" -X -q -v ON_ERROR_STOP=1 "$URL"
do $$
declare present int;
begin
  select count(*) into present from ops.enforcement_control_catalog
   where control_key in (__DECLARED_KEYS__);
  if present <> __DECLARED_COUNT__ then
    raise exception 'schema snapshot refused: exact reviewed control catalog is missing or drifted';
  end if;
  -- The two controls that predate the declaration files are still pinned by
  -- their full identity, because they are the ones no repository file generated
  -- and so the ones a drift would be silent about.
  if (select count(*) from ops.enforcement_control_catalog where
        (control_key='human_authority_runtime'
         and implementation_ref='migrations/0161_control_plane_authority_boundary.sql; mcp-server/src/mcp.js'
         and test_ref='mcp-server/test/control-plane-authority-boundary.test.mjs; ops/control-plane-authority-runtime-preflight-selftest.py'
         and enforcement_class='transactional_schema'
         and installed and verified_at is not null)
     or (control_key='platform_metering_pre_dispatch'
         and implementation_ref='lib/platform_metering.py; ops/platform-metering-gate.py; hooks/guard-unattended.py'
         and test_ref='ops/platform-metering-gate-selftest.py; ops/platform-metering-policy-selftest.py; ops/guard-selftest.py'
         and enforcement_class='deny_gate'
         and installed and verified_at is not null)) <> 2 then
    raise exception 'schema snapshot refused: exact reviewed control catalog is missing or drifted';
  end if;
end $$;
CONTROL_CATALOG_VERIFY
then
  echo "schema-snapshot: exact reviewed control catalog is missing or drifted — nothing written" >&2
  exit 1
fi

cat >> "$TMP" <<'CONTROL_CATALOG_HEADER'
-- CARR REVIEWED CONTROL CATALOG (bin/schema-snapshot.sh) — exact declared seed.
-- Every key here is declared in ops/config/rule-enforcement-map.json and its
-- companion class file, and the source was verified to hold all of them
-- immediately before this block was written. The following safely quoted rows
-- preserve the source verification and update timestamps.
-- never dump arbitrary ops.enforcement_control_catalog rows.
CONTROL_CATALOG_HEADER

if ! sed -e "s/__DECLARED_KEYS__/$DECLARED_KEYS/g" <<'CONTROL_CATALOG_ROWS' | "$PSQL" -X -Atq -v ON_ERROR_STOP=1 "$URL" >> "$TMP"
select format(
  'insert into ops.enforcement_control_catalog (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at,updated_at) values (%L,%L,%L,%L,%L,%L::timestamptz,%L::timestamptz) on conflict (control_key) do nothing;',
  control_key,implementation_ref,test_ref,enforcement_class,installed,
  to_char(verified_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  to_char(updated_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
  from ops.enforcement_control_catalog
 where control_key in (__DECLARED_KEYS__)
 order by control_key;
CONTROL_CATALOG_ROWS
then
  echo "schema-snapshot: could not render the reviewed control catalog — nothing written" >&2
  exit 1
fi

if [ "$RENEWAL_SOURCE_APPLIED" = t ]; then
cat >> "$TMP" <<'RENEWAL_SOURCE_JOB'
-- CARR RENEWAL SOURCE JOB (bin/schema-snapshot.sh) — exact disabled contract.
-- This is the one canonical 0230 definition needed to reconstruct its FK and
-- gate surface after 0230 has entered the migration ledger. It carries no run,
-- lease, client, party, deal, event, or credential data.
insert into ops.job_definition
  (key,version,enabled,risk,owner_actor,execution_kind,execution_contract,
   inventory_contract,recurrence,state_contract,routing_contract,
   filtering_contract,validation_contract,retry_policy,deduplication,
   completion_contract,legacy_schedule)
values
  ('renewal-radar-source-daily',1,false,'yellow','system','deterministic',
   '{"entrypoint":"ops.seal_renewal_decision_source_run","activation":"pending source-run adapter"}'::jsonb,
   '{"owner":"ops.job","inputs":["renewal-radar candidate import"],"canonical_writes":["ops.renewal_decision_source_run","ops.renewal_decision_source_run_member"]}'::jsonb,
   '{"cron":null,"timezone":"America/Chicago","source":"disabled pending source-run adapter"}'::jsonb,
   '{"owner":"ops.job","initial":"queued"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["renewal.source_complete"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["renewal.pool_imported"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["renewal.source_run_sealed"]}}'::jsonb,
   '{"max_attempts":2,"backoff":"exponential","base_seconds":60,"cap_seconds":600,"timeout_seconds":300}'::jsonb,
   '{"key_template":"renewal-radar-source-daily:{scheduled_for}"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["renewal.source_run_sealed"]},"receipt_kind":"renewal_source_run"}'::jsonb,
   '{"provider":"none","status":"disabled","activation":"explicit source-run adapter required"}'::jsonb)
on conflict (key,version) do nothing;

RENEWAL_SOURCE_JOB
fi

# doctrine_meta is a singleton bootstrap rather than reference vocabulary: its
# live generation advances with successful doctrine commits and must never be
# copied into a tracked rebuild declaration.  A rebuilt database always starts
# from the canonical counter value, exactly as 0075 originally established.
cat >> "$TMP" <<'DOCTRINE_META'
--
-- CARR DOCTRINE META BOOTSTRAP (bin/schema-snapshot.sh) — canonical, not
-- production data.  A snapshot rebuild starts generation at zero.
--

insert into public.doctrine_meta (id, generation) values (1, 0);

DOCTRINE_META

# A truncated dump is the failure mode that matters: pg_dump has lost a Neon
# connection mid-stream before (2026-08-07, on the nightly backup). A short file
# that parses is worse than no file, because it would silently define a smaller
# database. Require the terminator pg_dump writes last.
if ! grep -q 'PostgreSQL database dump complete' "$TMP"; then
  echo "schema-snapshot: dump has no completion marker — treating as truncated, nothing written" >&2
  exit 1
fi

# Normalise the two things pg_dump varies between identical dumps, so --check
# reports STRUCTURE drift and nothing else. A check that cries on every run is a
# check people stop reading.
#   * the version banner, which moves whenever the client or server is upgraded;
#   * \restrict / \unrestrict, which carry a fresh RANDOM token per dump (a psql
#     restore guard). Left in, every single check would report the file stale
#     while the schema was byte-identical — which is exactly how a drift check
#     gets ignored and then removed.
sed -e '/^-- Dumped from database version/d' \
    -e '/^-- Dumped by pg_dump version/d' \
    -e '/^\\restrict /d' \
    -e '/^\\unrestrict /d' "$TMP" | normalise_eof > "$TMP.clean"
mv "$TMP.clean" "$TMP"

if [ "$CHECK" = "1" ]; then
  if [ ! -f "$OUT" ]; then
    echo "schema-snapshot: $OUT does not exist — run bin/schema-snapshot.sh" >&2
    exit 1
  fi
  if diff -q "$OUT" "$TMP" >/dev/null; then
    echo "schema snapshot: current"
    exit 0
  fi
  echo "schema-snapshot: db/schema.sql is STALE — production's structure has moved." >&2
  echo "Regenerate and commit it: bin/schema-snapshot.sh" >&2
  diff "$OUT" "$TMP" | head -40 >&2
  exit 1
fi

mkdir -p "$REPO/db"
cp "$TMP" "$OUT"
echo "wrote $OUT ($(wc -l < "$OUT" | tr -d ' ') lines: structure + migration ledger + reference vocabulary; no business data)"
