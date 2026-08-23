#!/bin/zsh
# sync-rule-admission-prod.sh — the ONE sanctioned door for installing the
# reviewed rule-admission contract into the PRODUCTION database.
#
# WHY THIS EXISTS (2026-08-23). The control-plane roadmap's Phase 1 exit is one
# named test: ops/rule-admission-audit.py passes clean against Production, with
# zero rules in needs_revision and no exception list. On 2026-08-23 that audit
# had never been run against Production by anything. It reported:
#
#   total=218 admitted=4 needs_revision=0 missing=214 incomplete=0
#
# Zero needs_revision, which is the number the roadmap's status section named —
# and 214 active rules with no admission row at all, which it did not. The
# roadmap read "one test owed" and the database read "the contract is very
# nearly unpopulated". Only four rules had ever been admitted, all four through
# the admit-rule verb, one rule at a time.
#
# The cause was the same structural one bin/sync-control-plane-prod.sh names for
# Phase 2: tools/sync-rule-admission.py, the bulk backfill that compiles
# ops/config/rule-enforcement-map.json into the admission shape, had no
# production caller. There was no door, so there was no drift to notice —
# production simply never received the contract.
#
# Usage:
#   ./bin/sync-rule-admission-prod.sh           # read-only: where does production stand?
#   ./bin/sync-rule-admission-prod.sh --apply   # backfill, then prove it landed
#
# RAILS, in order:
#   1. An uncommitted enforcement map REFUSES to apply — the map is a REVIEWED
#      inventory and production takes reviewed, committed configuration only.
#      (Read-only runs are always allowed.)
#   2. --apply always re-runs ops/rule-admission-audit.py against production
#      afterwards and takes ITS exit code as the verdict, never the backfill's.
#      The backfill's own output counts what it was asked to write; the audit
#      reads production back (rule a9ecd5b4).
#   3. The backfill REFUSES on any active rule absent from the reviewed map,
#      by design — a rule taught after the map was last extended is a coverage
#      gap, and this door reports it rather than inventing a contract for it.
#   4. Every invocation logs to out/sync-rule-admission-prod.log with outcome.
#   5. The DSN is derived inside this process and never reaches a command line,
#      a terminal or a transcript.

set -eu
REPO="${0:A:h:h}"
LOG="$REPO/out/sync-rule-admission-prod.log"
mkdir -p "$REPO/out"

APPLY=0
while (( $# )); do
  case "$1" in
    --apply) APPLY=1; shift ;;
    *) print -u2 "unknown argument: $1"; exit 2 ;;
  esac
done

stamp() { print -r -- "$(date -u +%FT%TZ) sync-rule-admission-prod $*" >> "$LOG" }

MAP="ops/config/rule-enforcement-map.json"

if (( APPLY )); then
  dirty=$(cd "$REPO" && git status --porcelain -- "$MAP")
  if [[ -n "$dirty" ]]; then
    stamp "REFUSED uncommitted map: ${dirty//$'\n'/ · }"
    print -u2 "REFUSED: $MAP is uncommitted — commit the reviewed map first."
    print -u2 "$dirty"
    exit 1
  fi
fi

# Same non-interactive auth and same project pin as bin/migrate-prod.sh.
if [ -z "${NEON_API_KEY:-}" ] && [ -f "$HOME/.config/carr/db.env" ]; then
  set -a; . "$HOME/.config/carr/db.env"; set +a
fi
if [ -z "${NEON_API_KEY:-}" ]; then
  stamp "WARN no NEON_API_KEY — falling back to interactive neonctl auth"
  print -u2 "note: NEON_API_KEY is not set, so this needs a browser login if the"
  print -u2 "      saved neonctl session has expired."
fi

NEON_PROJECT_PRODUCTION="steep-field-48688294"
NEONCTL="$REPO/mcp-server/node_modules/.bin/neonctl"
[[ -x "$NEONCTL" ]] || NEONCTL="neonctl"

DSN="$("$NEONCTL" connection-string production \
        --project-id "$NEON_PROJECT_PRODUCTION" \
        --role-name neondb_owner 2>/tmp/sync-rule-admission-neonctl.err)"
if [[ -z "$DSN" ]]; then
  reason="$(head -1 /tmp/sync-rule-admission-neonctl.err 2>/dev/null)"
  rm -f /tmp/sync-rule-admission-neonctl.err
  stamp "FAIL no DSN from neonctl: ${reason:-no error text}"
  print -u2 "could not derive the production owner DSN from neonctl (logged)."
  print -u2 "neonctl said: ${reason:-nothing at all}"
  exit 1
fi
rm -f /tmp/sync-rule-admission-neonctl.err

PY="$REPO/.venv/bin/python"

if (( ! APPLY )); then
  print "== read-only: ops/rule-admission-audit.py against production =="
  set +e
  DATABASE_URL="$DSN" "$PY" "$REPO/ops/rule-admission-audit.py"
  rc=$?
  set -e
  if (( rc == 0 )); then
    stamp "OK read-only: production admission contract is complete"
  else
    stamp "GAP read-only rc=$rc"
    print ""
    print "production's admission contract is incomplete (counts above)."
    print "  missing        = active rules with no admission row at all"
    print "  needs_revision = admitted with a control that is not installed"
    print "  incomplete     = admitted without applicability/projection/reachability"
    print "Re-run with --apply once $MAP covers every active rule."
  fi
  exit $rc
fi

print "== backfilling the reviewed enforcement map into production =="
if ! DATABASE_URL="$DSN" "$PY" "$REPO/tools/sync-rule-admission.py"; then
  rc=$?
  stamp "FAIL backfill rc=$rc"
  print -u2 "the backfill refused or failed; production is unchanged."
  print -u2 "if it named an active rule absent from the reviewed enforcement map,"
  print -u2 "that rule is the finding: extend $MAP with its"
  print -u2 "category and control, have the entry reviewed, commit, and re-run."
  exit $rc
fi

print ""
print "== reading production back through ops/rule-admission-audit.py =="
set +e
DATABASE_URL="$DSN" "$PY" "$REPO/ops/rule-admission-audit.py"
rc=$?
set -e
if (( rc == 0 )); then
  stamp "OK applied and verified"
  print "every active rule in production now carries a complete admission contract."
else
  stamp "FAIL applied but audit rc=$rc"
  print -u2 "THE BACKFILL RAN AND THE AUDIT STILL DISAGREES. Read the counts above:"
  print -u2 "they name which of the four failure shapes is left."
fi
exit $rc
