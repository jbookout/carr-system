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
# 0239 chooses the credential that joins carr_session_minter. It MUST be applied
# after 0231 and never before: 0231 asserts the role is memberless, which is its
# "inert by construction" contract, and 0239 is what ends that state.
#
# ROLE MEMBERSHIP IS CLUSTER-WIDE WHILE MIGRATIONS ARE PER-DATABASE. Once 0239
# has run anywhere in this cluster, 0231 can no longer apply to a FRESH database
# in the same cluster -- it will find the member it requires to be absent. That
# is an artifact of one cluster hosting many test databases, not a production
# path (each Neon database is its own cluster), but it means this script must
# stand up its own cluster per run, which it does.
MIGRATION_ISSUER="$REPO/migrations/0239_session_issuer_credential.sql"
# 0240 lets the door mint from an actor SLUG. The door has no actor id --
# actor.id is not resolved until callTool, long after authentication -- and the
# issuer holds no table privilege with which to resolve one.
MIGRATION_SLUG_MINT="$REPO/migrations/0240_mint_session_by_actor_slug.sql"
# 0241 adds write receipts: a session, a claimed digest, and a readback the
# DATABASE computes from the frozen evidence row rather than accepting.
MIGRATION_RECEIPT="$REPO/migrations/0241_write_receipt.sql"
# 0242 introduces the reducer and the acceptance surface — deliberately, and
# only after receipts can prove themselves.
MIGRATION_ACCEPT="$REPO/migrations/0242_continuity_reducer_and_acceptance.sql"
# 0243 is the LAST slice: Drive retirement resolved from proven receipts plus
# an authority acceptance, which is the record-layer verifier the static
# preflight says it cannot be.
MIGRATION_RETIRE="$REPO/migrations/0243_drive_retirement.sql"
# 0244 splits the receipt digest. 0241 made claimed_digest carry both the proof
# that a receipt is attached to a real call AND the claim about what a subject
# now says; those are different facts, and one column could not be honest about
# both. Under the old shape an exact reversal could never prove and a single
# unproven receipt barred acceptance forever — this suite bricked itself on its
# own first run. It applies AFTER 0243 because it rewrites 0243's retirement
# trigger as well as 0241's and 0242's functions.
MIGRATION_SPLIT="$REPO/migrations/0244_receipt_digest_split.sql"
# 0246 takes the Drive retirement DENOMINATOR away from the runtime. 0243 granted
# carr_writer INSERT on ops.drive_dependency and nothing in the repository ever
# populated it, so the count readiness divides by was whatever the guarded party
# had written. It applies last because it replaces 0244's readiness function.
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
# AND A FIXTURE FOR THE "NOTHING QUALIFIES" VERDICT, taken at the same moment
# and for the same reason: the migrations are applied, every apply-time probe
# has rolled itself back, and not one row of qualifying evidence exists. That is
# the exact state tools/phase4-qualification.py was written to catch and the one
# state production cannot be put into on purpose.
psql "$BASE/postgres" -q -c "create database nothing_qualifies template subject"

# AND THE FIXTURE THAT ISOLATES THE EMPTY-INVENTORY CLAUSE. See that file's own
# header: on any ordinary database six of the verifier's seven NOT-READY reasons
# are true at once, so a deleted clause is unobservable. This one satisfies every
# other clause and records no Drive dependency at all.
psql "$BASE/postgres" -q -c "create database empty_but_accepted template subject"
psql "$BASE/empty_but_accepted" -v ON_ERROR_STOP=1 -q -f "$REPO/mcp-server/test/db/empty_inventory_fixture.sql"
echo "empty-but-accepted fixture built (no Drive dependency, everything else satisfied)"

psql "$BASE/postgres" -q -c "create database producer template subject"
# AND COUNT WHAT RAN. The file used to skip itself when CARR_TEST_DSN was
# unset: node --test reported "tests 0" and exited 0, so this stage could pass
# having executed nothing. The file now fails loudly instead of skipping, and
# this is the second lock on the same door -- a stage that reports success must
# report having actually run the six tests it exists to run.
PRODUCER_OUT="$(cd "$REPO/mcp-server" && CARR_TEST_DSN="$BASE/producer" \
    node --test test/db/receipt-producer-live.test.mjs 2>&1)"
printf '%s\n' "$PRODUCER_OUT"
# `[^ ]*` rather than `.`: node prefixes its summary with U+2139, and this
# script runs under LC_ALL=C where `.` matches one BYTE, not one character.
# Caught by this guard failing on a run where all six tests visibly passed.
PRODUCER_PASSED="$(printf '%s' "$PRODUCER_OUT" | sed -n 's/^[^ ]* pass \([0-9][0-9]*\)$/\1/p' | tail -1)"
if [ "${PRODUCER_PASSED:-0}" -lt 6 ]; then
  echo "GATE FAILED: the live producer stage reported ${PRODUCER_PASSED:-0} passing tests, expected at least 6." >&2
  echo "             A stage that runs nothing must not report success." >&2
  exit 1
fi
echo "producer exercised against a real database ($PRODUCER_PASSED tests)"

# Run TWICE. The suite must be re-runnable; a contract that only passes against
# a virgin database is testing ordering, not the substrate.
"$PYBIN" "$SUITE" "$BASE/subject"
echo "--- second run, same database ---"
"$PYBIN" "$SUITE" "$BASE/subject"

# ══════════════════════════════════════════════════════════════════════════
# THE TWO RECORD-LAYER VERIFIERS, against fixtures for BOTH verdicts.
#
# WHY THIS BLOCK EXISTS. tools/phase4-qualification.py and
# tools/drive-retirement-verifier.py were referenced by no test, no gate and no
# script -- not one line in the repository ran either of them. Their exit codes
# are the entire product: 0 means "deployed and qualifying" or "ready", 1 means
# the guarantees are not load-bearing. Delete the live-qualifying check from the
# first and a deployed-and-qualifying-nothing database flips from FAIL/exit 1 to
# PASS/exit 0 with nothing anywhere to notice.
#
# BOTH VERDICTS, NOT JUST THE HAPPY ONE. A gate that only ever runs the passing
# fixture cannot tell a working verifier from one that returns 0 unconditionally
# -- which is the same "a gate that can only say yes" shape these tools exist to
# refuse. So each runs twice: once where the answer must be no, once where it
# must be yes.
#
# CARR_QUALIFICATION_DSN IS SET EXPLICITLY ON EVERY INVOCATION. Both tools now
# refuse to guess a target rather than defaulting to production, but this block
# does not lean on that: it names the disposable database every time.
# AND THE EXIT CODE IS NOT ALWAYS ENOUGH. The verifier reports NOT READY for
# seven reasons, and on an ordinary fresh database six are true at once — so
# deleting one clause changed nothing observable: still exit 1, just for a
# different reason. Found exactly that way, by a mutant that survived this very
# block. Where a clause is the SUBJECT of a check, the fifth argument names the
# phrase that clause raises, which is the same "a refusal names WHICH bar was
# not met" discipline the contract suite's refuses() enforces.
verifier_says() {
  local want="$1" tool="$2" db="$3" why="$4" saying="${5:-}"
  local out rc
  set +e
  out="$(CARR_QUALIFICATION_DSN="$BASE/$db" "$PYBIN" "$REPO/tools/$tool" 2>&1)"
  rc=$?
  set -e
  if [ "$rc" -ne "$want" ]; then
    printf '%s\n' "$out"
    echo "GATE FAILED: $tool against '$db' exited $rc, expected $want — $why" >&2
    exit 1
  fi
  if [ -n "$saying" ] && ! printf '%s' "$out" | grep -q -- "$saying"; then
    printf '%s\n' "$out"
    echo "GATE FAILED: $tool against '$db' exited $want but never said: $saying" >&2
    echo "             The right verdict for the wrong reason is not a passing check." >&2
    exit 1
  fi
  echo "  ok  $tool on '$db' exited $want ($why)"
}

# AND NEITHER TOOL MAY GUESS A TARGET. Both used to take --project
# default="production", so a run with no environment and no argument opened a
# PRODUCTION connection — which is what happened during the review that found
# it. This is the regression guard for that fix, and it is the reason this whole
# block can exist at all: without it, wiring these tools into a gate would mean
# wiring a possible production connection into a gate.
#
# DELIBERATELY NOT MUTATION-TESTED, and this is the honest reason. Proving this
# guard by re-adding the production default would make the tool call
# tools/db-tap.py, which shells out to neonctl. That is a live Neon call, which
# this work is not permitted to make, so the mutant is named here and left
# unrun rather than quietly skipped.
for guessing_tool in phase4-qualification.py drive-retirement-verifier.py; do
  set +e
  guess_out="$(env -u CARR_QUALIFICATION_DSN "$PYBIN" "$REPO/tools/$guessing_tool" 2>&1)"
  guess_rc=$?
  set -e
  if [ "$guess_rc" -ne 2 ] || ! printf '%s' "$guess_out" | grep -q "REFUSING TO GUESS A TARGET"; then
    printf '%s\n' "$guess_out"
    echo "GATE FAILED: $guessing_tool with no target exited $guess_rc; it must exit 2 and refuse" >&2
    echo "             A default target here is a default PRODUCTION connection." >&2
    exit 1
  fi
  echo "  ok  $guessing_tool refuses to guess a target (exit 2)"
done

echo "--- record-layer verifiers, both verdicts ---"
verifier_says 1 phase4-qualification.py nothing_qualifies   "a deployed substrate with NOTHING qualifying must FAIL; this is the state the tool exists for"
verifier_says 0 phase4-qualification.py subject   "the substrate is deployed and the suite above wrote qualifying evidence"
verifier_says 1 drive-retirement-verifier.py nothing_qualifies   "no operational Drive dependency on record must be NOT READY; nothing proven about nothing is not proof"
verifier_says 1 drive-retirement-verifier.py empty_but_accepted   "the dangerous empty case: everything satisfied EXCEPT an inventory, so the empty-inventory clause is the only thing that can refuse"   "no operational Drive dependencies are on record"
verifier_says 0 drive-retirement-verifier.py subject   "every operational dependency retired on proven receipts, with an authority acceptance and a bound inventory"
