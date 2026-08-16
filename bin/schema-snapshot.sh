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
[ -x "$NEONCTL" ] || { echo "schema-snapshot: neonctl not found at $NEONCTL" >&2; exit 69; }

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

URL="$("$NEONCTL" connection-string production \
        --project-id steep-field-48688294 --role-name neondb_owner 2>/dev/null)"
[ -n "$URL" ] || { echo "schema-snapshot: could not obtain the production connection string" >&2; exit 1; }

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
-- carr_reader, carr_writer and carr_exporter are privilege bundles, so they
-- stay NOLOGIN. carr_jobs is the narrow unattended runtime identity: a fresh
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
  foreach r in array array['carr_reader','carr_writer','carr_exporter'] loop
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
# grantee to the three NOLOGIN bundles plus the narrow carr_jobs login the
# preamble creates. Membership
# bundles may additionally name neondb_owner (0005/0006 grant it the bundles),
# which every loading environment already has: .github/workflows/ci.yml creates
# it for the same reason.
#
# The section rides AFTER the structure because a grant on a table that does
# not exist yet aborts the load, and BEFORE the ledger because it is structure,
# not data. Five shapes, each ordered deterministically so --check reports
# drift and nothing else: schema usage, relation grants (tables, views,
# sequences), column-scoped grants (0021, 0117 — where the columns OUTSIDE the
# list are the point), function execute (0094, 0106), and memberships.
GRANTS_SQL="$(mktemp)"
cat > "$GRANTS_SQL" <<'GRANTSQL'
with app(rolname) as (
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter')
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
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter')
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
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter')
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
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter')
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
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter')
)
select format('grant %s to %s;', gr.rolname, mem.rolname)
  from pg_auth_members m
  join pg_roles gr  on gr.oid  = m.roleid
  join pg_roles mem on mem.oid = m.member
 where gr.rolname in (select rolname from app)
   and mem.rolname in (select rolname from app union select 'neondb_owner')
 order by gr.rolname, mem.rolname;
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
VOCAB_TABLES="activity_kind client_status client_type contact_state deal_lane \
deal_phase deal_type_ref lead_lane lead_stage loop_domain negotiation_claim_type \
participant_role party_link_kind vendor_category vendor_disposition \
vendor_relationship_level diagnostic_route submarket_condition doctrine_edge_type \
doctrine_review_policy actor"

VOCAB_ARGS=""
for t in $VOCAB_TABLES; do
  VOCAB_ARGS="$VOCAB_ARGS --table=$t"
done

# shellcheck disable=SC2086
if ! "$PG_DUMP" --data-only --no-owner --no-acl $VOCAB_ARGS "$URL" >> "$TMP"; then
  echo "schema-snapshot: could not dump the reference vocabulary — nothing written" >&2
  exit 1
fi

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
    -e '/^\\unrestrict /d' "$TMP" > "$TMP.clean"
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
