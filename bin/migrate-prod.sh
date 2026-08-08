#!/bin/zsh
# migrate-prod.sh — the ONE sanctioned door for applying migrations to the
# PRODUCTION database. Sanctioned by Joe 2026-08-07 night ("yea go ahead and
# build it"), widening addendum A14's build-session fence for this script only:
# sessions apply production migrations HERE, never by juggling credentials
# inline. Same code path every time (rule a8c55a47): derives the owner DSN via
# neonctl inside the process — the db-tap.py pattern — so no credential ever
# appears in a command line or a transcript.
#
# Usage:
#   ./bin/migrate-prod.sh            # dry run: list pending against production
#   ./bin/migrate-prod.sh --apply    # apply (migrate.py adds --yes only from us)
#
# RAILS, in order:
#   1. Uncommitted migration files REFUSE to apply — production runs reviewed,
#      committed DDL only. (Dry runs are always allowed.)
#   2. Rehearse-first stays doctrine: migrations rehearse on a Neon branch
#      before this script applies them (migrations/README.md). This script
#      cannot verify that mechanically — the discipline is the builder's, and
#      the branch step is in every phase's acceptance criteria.
#   3. Every invocation logs to out/migrate-prod.log with host and outcome.

set -eu
REPO="${0:A:h:h}"
LOG="$REPO/out/migrate-prod.log"
mkdir -p "$REPO/out"

stamp() { print -r -- "$(date -u +%FT%TZ) migrate-prod $*" >> "$LOG" }

if [[ "${1:-}" == "--apply" ]]; then
  dirty=$(cd "$REPO" && git status --porcelain migrations/)
  if [[ -n "$dirty" ]]; then
    stamp "REFUSED uncommitted migrations: ${dirty//$'\n'/ · }"
    print -u2 "REFUSED: uncommitted files under migrations/ — commit them first."
    print -u2 "$dirty"
    exit 1
  fi
fi

DSN="$(neonctl connection-string production --role-name neondb_owner 2>/dev/null)"
if [[ -z "$DSN" ]]; then
  stamp "FAIL no DSN from neonctl"
  print -u2 "could not derive the production owner DSN from neonctl (logged)."
  exit 1
fi

if [[ "${1:-}" == "--apply" ]]; then
  if DATABASE_URL="$DSN" "$REPO/.venv/bin/python" "$REPO/tools/migrate.py" --apply --yes; then
    stamp "OK applied"
  else
    rc=$?
    stamp "FAIL apply rc=$rc"
    exit $rc
  fi
else
  DATABASE_URL="$DSN" "$REPO/.venv/bin/python" "$REPO/tools/migrate.py"
  stamp "OK dry-run"
fi
