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
# NO DATA, EVER. --schema-only means structure and nothing else: no clients, no
# deals, no parties, no notes. That is what makes this file safe to commit at
# all, and it is not a flag to relax. --no-owner --no-acl for the same reason
# bin/backup-dump.sh uses them: an embedded OWNER TO / GRANT names roles a fresh
# database has never heard of, and the first such statement aborts the load.
# Roles and grants are rebuilt by the migrations, which are in git.
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
[ -x "$NEONCTL" ] || { echo "schema-snapshot: neonctl not found at $NEONCTL" >&2; exit 69; }

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

URL="$("$NEONCTL" connection-string production \
        --project-id steep-field-48688294 --role-name neondb_owner 2>/dev/null)"
[ -n "$URL" ] || { echo "schema-snapshot: could not obtain the production connection string" >&2; exit 1; }

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

if ! "$PG_DUMP" --schema-only --no-owner --no-acl "$URL" > "$TMP"; then
  echo "schema-snapshot: pg_dump failed — nothing written" >&2
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
echo "wrote $OUT ($(wc -l < "$OUT" | tr -d ' ') lines, schema only, no data)"
