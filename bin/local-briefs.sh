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
REPO="$HOME/carr-system"
LOG="$REPO/out/local-briefs.log"
[ -f "$HOME/.config/carr/db.env" ] || { echo "$(date -u +%FT%TZ) SKIP no db.env" >> "$LOG"; exit 0; }
cd "$REPO" || exit 1

rc=0
if ./run.sh brief-pack --quiet >> "$LOG" 2>&1; then
  echo "$(date -u +%FT%TZ) OK brief-pack" >> "$LOG"
else
  echo "$(date -u +%FT%TZ) FAIL brief-pack rc=$?" >> "$LOG"; rc=1
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
# Vault, not email: this deliberately does NOT depend on the handover channel,
# which cannot run (~/.config/carr/gmail.env is absent) and is a separate fix.
# 00_Context/ is a folder Joe already opens, so this needs no new habit.
VAULT="${CARR_VAULT:-/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI}"
if [ -d "$VAULT/00_Context" ]; then
  { echo "# Today, generated $(date -u +%FT%TZ). Do not hand-edit."
    echo
    cat "$REPO/out/brief-pack/one-thing.md" 2>/dev/null
    echo
    cat "$REPO/out/brief-pack/claim-card.md" 2>/dev/null
    echo
    cat "$REPO/out/brief-pack/renewal-shortlist.md" 2>/dev/null
  } > "$VAULT/00_Context/today.md" \
    && echo "$(date -u +%FT%TZ) OK today.md -> vault" >> "$LOG" \
    || { echo "$(date -u +%FT%TZ) FAIL today.md -> vault" >> "$LOG"; rc=1; }
else
  echo "$(date -u +%FT%TZ) SKIP today.md — vault not mounted" >> "$LOG"
fi

# Known defect, tracked as loop #182: review-queue's touches lane reports 0 while
# ingest_inbox holds real rows, because the read fails and the handler discards
# the error message. A zero here is not yet trustworthy.

tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit $rc
