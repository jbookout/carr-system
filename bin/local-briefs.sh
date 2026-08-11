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
# execute `run.sh brief-pack` at all. Standing context already states a cloud session may
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

# The brief-pack stays repo-local operational output.  Its previous Drive
# projection retired with the doctrine Markdown cutoff; interactive surfaces
# obtain their actionable queue through today-triage and record verbs.  Never
# create a second, unregistered Markdown projection here: it would bypass both
# the exporter registry and the cutoff gate.
echo "$(date -u +%FT%TZ) OK local brief outputs rebuilt; interactive queue is store-first" >> "$LOG"

# Known defect, tracked as loop #182: review-queue's touches lane reports 0 while
# ingest_inbox holds real rows, because the read fails and the handler discards
# the error message. A zero here is not yet trustworthy.

tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit $rc
