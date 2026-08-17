#!/bin/zsh
# local-briefs.sh — rebuild the two repo-bound heartbeat outputs, locally.
#
# WHY THIS EXISTS (2026-08-04, loop #181, Joe's go). The daily heartbeat IS
# scheduled — in Cowork, which a local session cannot enumerate — and it fires
# every morning. But brief-pack and review-queue had not been rebuilt since
# 2026-08-02 15:18, an ad-hoc session run, while Monday 2026-08-03 was a full
# business day on which other scheduled work landed normally. So the trigger runs
# and these two jobs do not.
#
# THE LEADING HYPOTHESIS, and it is why the fix is a LOCAL job rather than a
# second heartbeat: a Cowork session cannot reach ~/carr-system, so it cannot
# execute `run.sh brief-pack` at all. CLAUDE.md already states a cloud session may
# see no repos; the salesforce and costar SOPs restrict themselves to local Claude
# Code for the same reason. IT IS UNTESTED — nobody has read that task's own run
# history — so this job is deliberately narrow: it fixes the OUTPUT gap without
# asserting the cause, and it is not a replacement for the heartbeat.
#
# THIS IS COMPLEMENTARY, NOT A DUPLICATE. The Cowork heartbeat does the analysis
# half — surfacing due loops, the staleness sweep, the ack clocks. This job only
# rebuilds the two artifacts that need the repo. If the Cowork side is later
# proven able to run them, retire this rather than leaving both.
#
# WEEKDAYS ONLY, before the morning heartbeat, matching the standing weekend
# stand-down: Sat/Sun are not workdays for Joe or Dell and Monday absorbs.
#
# SKIP-not-FAIL on missing env, per house convention.
#
# NORMAL MODE writes only repo-local document outputs. Drive projection is an
# explicit recovery act: pass --recovery and set CARR_RECOVERY_REASON. The
# scheduled launchd path passes neither, so it can never silently write Drive.
REPO="$HOME/carr-system"
LOG="$REPO/out/local-briefs.log"
[ -f "$HOME/.config/carr/db.env" ] || { echo "$(date -u +%FT%TZ) SKIP no db.env" >> "$LOG"; exit 0; }
cd "$REPO" || exit 1

RECOVERY=0
if [ "${1:-}" = "--recovery" ]; then
  RECOVERY=1
  [ -n "${CARR_RECOVERY_REASON:-}" ] || {
    echo "local-briefs: --recovery requires CARR_RECOVERY_REASON" >&2
    exit 2
  }
elif [ -n "${1:-}" ]; then
  echo "usage: bin/local-briefs.sh [--recovery]" >&2
  exit 2
fi

rc=0
brief_ok=0
if [ "$RECOVERY" -eq 1 ]; then
  echo "$(date -u +%FT%TZ) RECOVERY brief-pack reason=${CARR_RECOVERY_REASON}" >> "$LOG"
  if ./run.sh brief-pack --quiet --recovery >> "$LOG" 2>&1; then
    echo "$(date -u +%FT%TZ) OK brief-pack recovery" >> "$LOG"
    brief_ok=1
  else
    echo "$(date -u +%FT%TZ) FAIL brief-pack recovery rc=$?" >> "$LOG"; rc=1
  fi
else
  # The weekday artifact consumes exactly these three canonical-safe sections.
  # Do not ask for `all`: that includes prebriefs, whose only current source is
  # the explicitly-recovery Drive ICS path and must keep refusing normal use.
  brief_ok=1
  for section in one-thing claim-card renewal-shortlist; do
    if ./run.sh brief-pack --quiet --section "$section" >> "$LOG" 2>&1; then
      echo "$(date -u +%FT%TZ) OK brief-pack section=$section" >> "$LOG"
    else
      echo "$(date -u +%FT%TZ) FAIL brief-pack section=$section rc=$?" >> "$LOG"
      brief_ok=0
      rc=1
    fi
  done
fi

if ./run.sh review-queue >> "$LOG" 2>&1; then
  echo "$(date -u +%FT%TZ) OK review-queue" >> "$LOG"
else
  echo "$(date -u +%FT%TZ) FAIL review-queue rc=$?" >> "$LOG"; rc=1
fi

# DELIVER THE CALL LIST TO A SURFACE JOE ACTUALLY OPENS. Added 2026-08-09 by the
# system-design council: brief_pack writes one-thing.md and renewal-shortlist.md
# into out/brief-pack/, and .gitignore line 5 excludes out/ entirely. Nothing
# copied them anywhere, and grep found no consumer but health-check.py watching
# the mtime — a freshness check on a file with no reader. Meanwhile the shortlist
# named 15 Pensacola healthcare tenants with phone numbers and lease windows, and
# THREE of those windows expired unread, each carrying its own auto-generated
# "est window already past, verify before outreach" footnote. The file recorded
# the opportunity it was losing.
#
# The canonical document output is repo-local. It is not projected to Drive in
# normal mode. Do not build it from stale component files after brief-pack has
# refused: that would convert a loud missing-calendar failure into a plausible
# but degraded daily brief.
TODAY="$REPO/out/brief-pack/today.md"
if [ "$brief_ok" -eq 1 ]; then
  { echo "# Today, generated $(date -u +%FT%TZ). Do not hand-edit."
    echo
    cat "$REPO/out/brief-pack/one-thing.md" 2>/dev/null
    echo
    cat "$REPO/out/brief-pack/claim-card.md" 2>/dev/null
    echo
    cat "$REPO/out/brief-pack/renewal-shortlist.md" 2>/dev/null
  } > "$TODAY.tmp" \
    && mv "$TODAY.tmp" "$TODAY" \
    && echo "$(date -u +%FT%TZ) OK today.md -> canonical document output" >> "$LOG" \
    || { echo "$(date -u +%FT%TZ) FAIL canonical today.md" >> "$LOG"; rc=1; }
else
  echo "$(date -u +%FT%TZ) REFUSED today.md because brief-pack was incomplete" >> "$LOG"
fi

# The retired delivery behavior survives only as explicit, labeled recovery.
# It projects the already-built document; it is never the source of truth.
if [ "$RECOVERY" -eq 1 ] && [ "$brief_ok" -eq 1 ]; then
  VAULT="${CARR_VAULT:-/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI}"
  if [ -d "$VAULT/00_Context" ]; then
    { echo "# RECOVERY NONCANONICAL PROJECTION. Source: $TODAY"
      cat "$TODAY"
    } > "$VAULT/00_Context/today.md" \
      && echo "$(date -u +%FT%TZ) RECOVERY today.md -> Drive" >> "$LOG" \
      || { echo "$(date -u +%FT%TZ) FAIL recovery Drive projection" >> "$LOG"; rc=1; }
  else
    echo "$(date -u +%FT%TZ) FAIL recovery Drive projection: vault not mounted" >> "$LOG"
    rc=1
  fi
fi

# Known defect, tracked as loop #182: review-queue's touches lane reports 0 while
# ingest_inbox holds real rows, because the read fails and the handler discards
# the error message. A zero here is not yet trustworthy.

tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit $rc
