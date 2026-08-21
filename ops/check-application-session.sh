#!/usr/bin/env bash
# check-application-session.sh — ONE command that proves the authenticated
# application-session substrate, end to end, against a real PostgreSQL.
#
# WHY THIS EXISTS. The contract suite it runs was, for a while, reachable only
# by a human reading a docstring and retyping the command. That is the same
# shape as the defect the suite was written to catch: a guarantee that looks
# present and is never exercised. A review found the suite wired into nothing —
# no runner, no gate, no reference anywhere outside its own file.
#
# NOT WIRED INTO HOSTED CI, deliberately. It needs a live PostgreSQL, and this
# repo's GitHub Actions minutes are metered and over the free allowance. Wiring
# it there is a cost decision for Joe, not a default. Run it locally before
# touching the substrate, and after any change to migration 0231.
#
# Risk colour GREEN: entirely local. No network, no Neon, no staging, no
# production, nothing metered.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Resolve an interpreter rather than hardcoding one checkout's venv: a path into
# ~/carr-system/.venv does not exist in a worktree or on a second machine.
PYBIN="${CARR_PYTHON:-}"
if [ -z "$PYBIN" ]; then
  for cand in "$REPO/.venv/bin/python" "$HOME/carr-system/.venv/bin/python" "$(command -v python3 || true)"; do
    [ -x "$cand" ] && { PYBIN="$cand"; break; }
  done
fi
[ -n "$PYBIN" ] || { echo "no python interpreter found; set CARR_PYTHON" >&2; exit 1; }
"$PYBIN" -c "import psycopg" 2>/dev/null || {
  echo "psycopg is not importable under $PYBIN; set CARR_PYTHON to an interpreter that has it" >&2
  exit 1; }
MIGRATION="$REPO/migrations/0231_authenticated_application_session.sql"
# 0233 chooses the credential that joins carr_session_minter. It MUST be applied
# after 0232 and never before: 0232 asserts the role is memberless, which is its
# "inert by construction" contract, and 0233 is what ends that state.
#
# ROLE MEMBERSHIP IS CLUSTER-WIDE WHILE MIGRATIONS ARE PER-DATABASE. Once 0233
# has run anywhere in this cluster, 0232 can no longer apply to a FRESH database
# in the same cluster -- it will find the member it requires to be absent. That
# is an artifact of one cluster hosting many test databases, not a production
# path (each Neon database is its own cluster), but it means this script must
# stand up its own cluster per run, which it does.
MIGRATION_ISSUER="$REPO/migrations/0239_session_issuer_credential.sql"
# 0234 lets the door mint from an actor SLUG. The door has no actor id --
# actor.id is not resolved until callTool, long after authentication -- and the
# issuer holds no table privilege with which to resolve one.
MIGRATION_SLUG_MINT="$REPO/migrations/0240_mint_session_by_actor_slug.sql"
# 0235 adds write receipts: a session, a claimed digest, and a readback the
# DATABASE computes from the frozen evidence row rather than accepting.
MIGRATION_RECEIPT="$REPO/migrations/0241_write_receipt.sql"
# 0236 introduces the reducer and the acceptance surface — deliberately, and
# only after receipts can prove themselves.
MIGRATION_ACCEPT="$REPO/migrations/0242_continuity_reducer_and_acceptance.sql"
# 0237 is the LAST slice: Drive retirement resolved from proven receipts plus
# an authority acceptance, which is the record-layer verifier the static
# preflight says it cannot be.
MIGRATION_RETIRE="$REPO/migrations/0243_drive_retirement.sql"
# 0238 splits the receipt digest. 0235 made claimed_digest carry both the proof
# that a receipt is attached to a real call AND the claim about what a subject
# now says; those are different facts, and one column could not be honest about
# both. Under the old shape an exact reversal could never prove and a single
# unproven receipt barred acceptance forever — this suite bricked itself on its
# own first run. It applies AFTER 0237 because it rewrites 0237's retirement
# trigger as well as 0235's and 0236's functions.
MIGRATION_SPLIT="$REPO/migrations/0244_receipt_digest_split.sql"
# 0246 takes the Drive retirement DENOMINATOR away from the runtime. 0237 granted
# carr_writer INSERT on ops.drive_dependency and nothing in the repository ever
# populated it, so the count readiness divides by was whatever the guarded party
# had written. It applies last because it replaces 0238's readiness function.
MIGRATION_INVENTORY="$REPO/migrations/0246_drive_inventory_is_not_the_runtime_s_to_declare.sql"
SUITE="$REPO/mcp-server/test/db/application_session_contract.py"
GRANDFATHERED="$REPO/mcp-server/test/db/grandfathered_receipt_contract.py"
export LC_ALL=C LANG=C
export CARR_DISPOSABLE_PG_DIR="${CARR_DISPOSABLE_PG_DIR:-${TMPDIR:-/tmp}/carr-appsession-check}"

cleanup() { "$REPO/ops/disposable-pg.sh" stop >/dev/null 2>&1 || true; }

# Start FIRST, arm the trap only after it succeeds. Armed beforehand, a refused
# start ("already initialised") aborted under set -e and the trap deleted a
# directory this run did not create — and RUNDIR defaults to a fixed shared path
# on a machine that runs many worktree sessions at once, so the victim was
# whatever was already there.
DSN="$("$REPO/ops/disposable-pg.sh" start)"
trap cleanup EXIT
# An empty DSN sends psql to the default socket, where everything "fails" for
# the wrong reason and a whole run reports nonsense. Refuse instead.
[ -n "$DSN" ] || { echo "harness did not start; no DSN" >&2; exit 1; }

BASE="${DSN%/carr_h}"
psql "$BASE/postgres" -q -c "create database subject template carr_h"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION"
echo "migration 0231 applied (its own apply-time assertions passed)"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_ISSUER"
echo "migration 0239 applied (the minting credential is chosen and asserted)"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_SLUG_MINT"
echo "migration 0240 applied (the door can mint without an actor id)"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_RECEIPT"
echo "migration 0241 applied (receipts bind a session and prove by readback)"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_ACCEPT"
echo "migration 0242 applied (the reducer folds, and acceptance is gated)"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_RETIRE"
echo "migration 0243 applied (retirement needs two proven receipts and authority)"

# THE GRANDFATHERED DATABASE, forked HERE and nowhere else. 0244 backfills
# every pre-existing receipt's material digest with three statements that stand
# the immutability trigger down, and its own apply-time proof cannot observe a
# single one of them: that block runs in a transaction it rolls back, so every
# row it can see is one it created AFTER the backfill. The backfill's actual
# subject is a receipt that existed BEFORE the migration, and this is the only
# place in the repository where one is made.
#
# The copy is taken from `subject` at exactly this point -- 0243 applied, 0244
# not yet -- because a template copy taken any later has already been through
# the backfill and proves nothing.
psql "$BASE/postgres" -q -c "create database grandfathered template subject"
"$PYBIN" "$GRANDFATHERED" seed "$BASE/grandfathered"
echo "pre-0244 receipts seeded under the OLD rules"

psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_SPLIT"
echo "migration 0244 applied (the call digest and the material claim are two columns)"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_INVENTORY"
echo "migration 0246 applied (the inventory is declared, not written by the runtime)"

# ...and now bring the grandfathered copy forward THROUGH the backfill, so the
# contract below runs against rows the UPDATE actually rewrote.
psql "$BASE/grandfathered" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_SPLIT"
psql "$BASE/grandfathered" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_INVENTORY"
echo "grandfathered database brought forward through the 0244 backfill"
"$PYBIN" "$GRANDFATHERED" verify "$BASE/grandfathered"

# THE PRODUCER, AGAINST A REAL DATABASE, before the contract suite runs. Its own
# unit test drives a hand-written fake client, and an audit showed that fake
# refuses what the database refuses -- so the exact SQL the Worker sends had no
# coverage anywhere. Runs against a TEMPLATE COPY taken now, so the rows it
# writes cannot colour the contract suite's global acceptance counts.
psql "$BASE/postgres" -q -c "create database producer template subject"
( cd "$REPO/mcp-server" && CARR_TEST_DSN="$BASE/producer" \
    node --test test/receipt-producer-live.test.mjs )
echo "producer exercised against a real database"

# Run TWICE. The suite must be re-runnable; a contract that only passes against
# a virgin database is testing ordering, not the substrate.
"$PYBIN" "$SUITE" "$BASE/subject"
echo "--- second run, same database ---"
"$PYBIN" "$SUITE" "$BASE/subject"
