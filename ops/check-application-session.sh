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
SUITE="$REPO/mcp-server/test/db/application_session_contract.py"
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

# Run TWICE. The suite must be re-runnable; a contract that only passes against
# a virgin database is testing ordering, not the substrate.
"$PYBIN" "$SUITE" "$BASE/subject"
echo "--- second run, same database ---"
"$PYBIN" "$SUITE" "$BASE/subject"
