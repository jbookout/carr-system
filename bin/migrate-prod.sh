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

# NON-INTERACTIVE AUTH, added 2026-08-10. neonctl's saved browser login expires
# on its own schedule, and when it does it does not fail — it prompts, waits 60
# seconds for a browser nobody is at, and times out. That took down FOUR things
# at once on 2026-08-03 and again tonight: this script, bin/import-doctrine.sh,
# bin/restore-rehearse.sh (the only proof the encrypted backups can be restored)
# and ops/partner_ping.py, the Joe/Dell interrupt channel, which then printed
# "nothing new since" 382 consecutive times over six days. A quiet channel and a
# dead one produced identical output, which is the defect loop #272 already names
# in another lane.
#
# A NEON API KEY does not expire on a timer and needs no browser. neonctl reads
# it from NEON_API_KEY. This preserves the property this script was built for —
# the DSN is still derived per invocation and still never appears in a command
# line or a transcript — and only removes the human from the refresh.
#
# Joe creates the key once in the Neon console and puts it in db.env beside the
# credentials already there. Until he does, the fallback is the old interactive
# path, so nothing breaks in the meantime; it just still needs a browser.
if [ -z "${NEON_API_KEY:-}" ] && [ -f "$HOME/.config/carr/db.env" ]; then
  set -a; . "$HOME/.config/carr/db.env"; set +a
fi
if [ -z "${NEON_API_KEY:-}" ]; then
  stamp "WARN no NEON_API_KEY — falling back to interactive neonctl auth"
  print -u2 "note: NEON_API_KEY is not set, so this needs a browser login if the"
  print -u2 "      saved neonctl session has expired. Add NEON_API_KEY to"
  print -u2 "      ~/.config/carr/db.env to make this path unattended."
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
