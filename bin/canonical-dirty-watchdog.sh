#!/bin/zsh
# canonical-dirty-watchdog.sh — the INDEPENDENT half of WR-000040's AC-FRESH.
#
# AC-FRESH asks for two things and they are separable: a fast-forward that keeps
# canonical current, and a page when the tree is dirty or the fast-forward
# fails. This file is the second. bin/canonical-fast-forward.sh is the first,
# and this one must not import, call or depend on it.
#
# WHY INDEPENDENT, stated as a property rather than a preference. A freshness
# machine whose only alarm lives inside the freshness job is silent in the one
# failure that matters most — the job never fired at all. launchd drops an agent
# whose plist is unloaded, whose program is missing, or whose machine slept
# through every window, and it says nothing. The fast-forward job cannot page
# about its own absence. So this watchdog observes the OUTCOME (how stale is
# canonical, how dirty is it) and never the job, and it reaches its own verdict
# from the repository alone. If the fast-forward job were deleted tomorrow this
# watchdog would still page, which is the test of whether it is independent.
#
# WHAT IT PAGES ON, in the accepted plan's own words — "an independent watchdog
# paging on tracked dirt only":
#
#   * TRACKED dirt: any modified, staged, renamed or deleted tracked path in
#     canonical. That is somebody's edit sitting in a tree nobody is supposed to
#     edit, and it also blocks the fast-forward.
#   * STALENESS past the AC-FRESH bar: more than one day behind origin/main.
#   * AHEAD: canonical holding commits origin/main does not have.
#
# WHAT IT DOES NOT PAGE ON, deliberately: untracked paths. AC-CLEAN routes those
# to Joe for a per-path discard-or-land ruling, and a watchdog that paged daily
# about four paths awaiting a human decision would train its reader to ignore
# it. They are COUNTED and NAMED in every report — visible, never alarming.
#
# HOW IT PAGES, by two independent routes so one channel failing is not silence:
#
#   1. A durable record-layer problem report through the Bash door,
#      `./run.sh call report-problem`, which is the alarm channel WR-000040's
#      plan names (dependency `safe:record-layer/incident-verb-alarm-channel`).
#   2. A nonzero exit, so bin/run-scheduled.sh records a failed run and the
#      service shows red in `ops-record health`.
#
# Route 1 failing never suppresses route 2. A watchdog that could be silenced by
# an unreachable store would be worse than none.
#
#   usage: bin/canonical-dirty-watchdog.sh [--repository PATH]
#                                          [--max-age-hours N] [--no-page]
#
# Exit codes:
#   0  canonical is clean of tracked dirt and within the freshness bar
#   6  ALARM: tracked dirt, excessive staleness, or ahead-of-origin
#   64 the repository argument is unusable (a configuration error, not an alarm)
set -u

REPO="${CARR_CANONICAL_REPO:-$HOME/carr-system}"
MAX_AGE_HOURS=24
PAGE=1
while [ $# -gt 0 ]; do
  case "$1" in
    --repository) REPO="$2"; shift 2 ;;
    --max-age-hours) MAX_AGE_HOURS="$2"; shift 2 ;;
    --no-page) PAGE=0; shift ;;
    *) print -u2 "canonical-dirty-watchdog: unknown argument $1"; exit 64 ;;
  esac
done

[ -d "$REPO/.git" ] || { print -u2 "canonical-dirty-watchdog: $REPO is not a git checkout"; exit 64; }

git -C "$REPO" fetch --quiet origin main 2>/dev/null
fetch_ok=$?

tracked_dirty=$(git -C "$REPO" status --porcelain --untracked-files=no | wc -l | tr -d ' ')
untracked=$(git -C "$REPO" status --porcelain --untracked-files=normal | grep -c '^??' || true)
behind=$(git -C "$REPO" rev-list --count HEAD..origin/main 2>/dev/null || echo "unknown")
ahead=$(git -C "$REPO" rev-list --count origin/main..HEAD 2>/dev/null || echo "unknown")

# Age of the tip canonical is sitting on, which is what "within one day" means:
# not when the job last ran, but how old the code in the tree is.
head_epoch=$(git -C "$REPO" log -1 --format=%ct HEAD 2>/dev/null || echo 0)
now_epoch=$(date +%s)
age_hours=$(( (now_epoch - head_epoch) / 3600 ))

print "canonical-dirty-watchdog: repository=$REPO"
print "canonical-dirty-watchdog: tracked_modified=$tracked_dirty untracked=$untracked behind=$behind ahead=$ahead head_age_hours=$age_hours"
if [ "$untracked" -ne 0 ]; then
  print "canonical-dirty-watchdog: untracked paths (reported, not alarmed — AC-CLEAN is Joe's ruling):"
  git -C "$REPO" status --porcelain --untracked-files=normal | grep '^??' | sed 's/^/  /'
fi

reasons=""
[ "$tracked_dirty" -ne 0 ] && reasons="${reasons}$tracked_dirty tracked path(s) modified in a tree no session may edit; "
[ "$ahead" != "unknown" ] && [ "$ahead" -ne 0 ] 2>/dev/null && reasons="${reasons}canonical is $ahead commit(s) ahead of origin/main; "
[ "$age_hours" -gt "$MAX_AGE_HOURS" ] && [ "$behind" != "unknown" ] && [ "$behind" -ne 0 ] 2>/dev/null \
  && reasons="${reasons}canonical HEAD is ${age_hours}h old and $behind commit(s) behind origin/main, past the ${MAX_AGE_HOURS}h bar; "
[ "$fetch_ok" -ne 0 ] && reasons="${reasons}could not fetch origin/main, so freshness is unverifiable; "

if [ -z "$reasons" ]; then
  print "canonical-dirty-watchdog: OK — no tracked dirt, within ${MAX_AGE_HOURS}h of origin/main"
  exit 0
fi

print -u2 "canonical-dirty-watchdog: ALARM — $reasons"
git -C "$REPO" status --porcelain --untracked-files=no >&2

if [ "$PAGE" -eq 1 ]; then
  situation="Canonical checkout freshness alarm: $reasons(tracked_modified=$tracked_dirty untracked=$untracked behind=$behind ahead=$ahead head_age_hours=$age_hours). WR-000040 AC-FRESH."
  # The store is a best-effort SECOND channel. Its failure is printed and then
  # ignored: the exit code below is the channel that cannot be silenced.
  if ! "$REPO/run.sh" call report-problem "{\"situation\": $(print -r -- "$situation" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))')}" </dev/null; then
    print -u2 "canonical-dirty-watchdog: the record-layer page did not land; the nonzero exit below still stands"
  fi
fi

exit 6
