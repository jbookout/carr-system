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

# PRODUCTION IS PINNED BY PROJECT ID, and that is not tidiness — it is the fix
# for a real outage of this script.
#
# This line read `neonctl connection-string production --role-name neondb_owner`
# until 2026-08-14 and worked for as long as the account held exactly ONE Neon
# project. Program 1 created a second one (the isolated staging project), and
# from that moment neonctl refused every invocation with "Multiple projects
# found, please provide one with the --project-id option". Because the DSN was
# captured with 2>/dev/null inside a `set -eu` script, the refusal produced
# NOTHING: no message, no stderr, exit 1 and an empty log line. The one
# sanctioned door for applying production migrations was closed for every
# session on this machine and said nothing about why.
#
# The id, not the name. tools/db-tap.py pins production the same way and states
# the reason: a name lookup can drift to whatever it happens to return, and a
# name collision or typo that silently repointed this at another project is the
# worst failure this file could have. Staging is resolved by name over there
# because it is rebuilt often; production is never rebuilt.
NEON_PROJECT_PRODUCTION="steep-field-48688294"

DSN="$(neonctl connection-string production \
        --project-id "$NEON_PROJECT_PRODUCTION" \
        --role-name neondb_owner 2>/tmp/migrate-prod-neonctl.err)"
if [[ -z "$DSN" ]]; then
  # SAY WHY. The old version swallowed neonctl's own explanation, which is how a
  # one-word fix ("--project-id") stayed invisible. stderr is captured to a file
  # rather than passed through because a connection string can appear in
  # neonctl's output, and this script's contract is that no DSN ever reaches a
  # terminal or a transcript.
  reason="$(head -1 /tmp/migrate-prod-neonctl.err 2>/dev/null)"
  rm -f /tmp/migrate-prod-neonctl.err
  stamp "FAIL no DSN from neonctl: ${reason:-no error text}"
  print -u2 "could not derive the production owner DSN from neonctl (logged)."
  print -u2 "neonctl said: ${reason:-nothing at all}"
  exit 1
fi
rm -f /tmp/migrate-prod-neonctl.err

if [[ "${1:-}" == "--apply" ]]; then
  if DATABASE_URL="$DSN" "$REPO/.venv/bin/python" "$REPO/tools/migrate.py" --apply --yes; then
    stamp "OK applied"
    # THE SNAPSHOT REFRESH RIDES WITH THE APPLY, and this is the only place it
    # can. db/schema.sql is a picture of production's structure, and THIS is the
    # moment that structure changes — so leaving the refresh to a later, separate
    # act is what let five layers of drift pile up behind one refresh nobody
    # took, measured 2026-08-20: two app roles that had stopped being created
    # anywhere, 67 PUBLIC revokes the file never carried, three seeded
    # configuration tables, and two tests that had quietly come to depend on the
    # file being stale. Every one of them was found by a rebuild failing, never
    # by the change that caused it.
    #
    # It runs AFTER the apply and cannot refuse it. Production has already
    # changed by this line; a snapshot that fails to regenerate is a reason to
    # shout, never a reason to pretend the migration did not happen. So this
    # never touches the exit code.
    print ""
    print "== refreshing db/schema.sql, because production's structure just moved =="
    if "$REPO/bin/schema-snapshot.sh"; then
      if "$REPO/bin/schema-snapshot.sh" --check >/dev/null 2>&1; then
        stamp "OK snapshot refreshed"
        print "  db/schema.sql now matches production. COMMIT IT — naming the path,"
        print "  in the same change as the migration you just applied."
      else
        stamp "WARN snapshot refreshed but --check still stale"
        print -u2 "  schema-snapshot.sh wrote the file and --check still reports it stale."
        print -u2 "  That is a real finding: read it before committing anything."
      fi
    else
      stamp "WARN snapshot refresh FAILED rc=$?"
      print -u2 "  THE MIGRATION APPLIED; ONLY THE SNAPSHOT DID NOT REFRESH."
      print -u2 "  Production is changed and db/schema.sql now describes a database"
      print -u2 "  that no longer exists. Re-run bin/schema-snapshot.sh by hand and"
      print -u2 "  commit it, or the next rebuild inherits the gap."
    fi
  else
    rc=$?
    stamp "FAIL apply rc=$rc"
    exit $rc
  fi
else
  DATABASE_URL="$DSN" "$REPO/.venv/bin/python" "$REPO/tools/migrate.py"
  stamp "OK dry-run"
fi
