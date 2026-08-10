#!/bin/zsh
# preflight-watch.sh — keep Dell's migration packet honest until he runs it,
# then delete itself.
#
# Joe, 2026-08-10, on being told to run the preflight before driving over:
# "im not going to remember that. you should make it a scheduled run that goes
# away once dell completes the migration."
#
# WHY IT HAS TO SELF-RETIRE. A watcher for a two-day window that outlives the
# window is landfill: it keeps waking the database, keeps a row on a glanceable
# surface, and eventually someone has to work out what it was for. The retire
# condition is therefore part of the build, not a follow-up (rule 61c64d91 —
# every asset gets its lifecycle at creation, never retrofitted).
#
# THE SIGNAL is action-required item A15, "Dell: run bin/migrate-dell.sh
# --apply". Closing it is the last step of the migration's own session-half, so
# it is the one event that means the work actually happened on his machine —
# not a marker this script invents, and not a date that guesses.
#
# RISK COLOUR: GREEN (rule f04a05aa). It reads the record through the ordinary
# verb path and reads git; it writes only its own log and, once, removes its own
# plist. It sends nothing outside this Mac, drafts nothing, and touches no
# client-facing surface. It cannot write to the record at all: local-verb.mjs
# refuses production writes without CARR_LOCAL_VERB_ALLOW_PRODUCTION, a rail
# that predates this file and that an unattended job must not walk around.
# Its alert channel is therefore a local notification plus this log.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export GIT_TERMINAL_PROMPT=0          # never hang an unattended run on a prompt

LOG="$REPO/out/preflight-watch.log"
PLIST="$HOME/Library/LaunchAgents/com.carr.preflight-watch.plist"
mkdir -p "$REPO/out"
log() { print -r -- "$(date -u +%FT%TZ)  $*" >> "$LOG"; }

notify() {
  # Local only. No credential, no network, nothing leaves the Mac.
  osascript -e "display notification \"$1\" with title \"CARR — Dell migration\" sound name \"Basso\"" \
    >/dev/null 2>&1 || true
}

# ── 1. has Dell done it? ────────────────────────────────────────────────────
# Any failure here means UNKNOWN, and unknown must never retire the watcher —
# a transient database blip would otherwise delete the only thing checking the
# packet. Retire on an explicit closed status and on nothing else.
STATUS=""
if OUT=$("$REPO/run.sh" call loop-board \
        '{"kind":"action_required","owner":"dell","status":"any","search":"migrate"}' \
        </dev/null 2>/dev/null); then
  # run.sh call prints a banner line ("local-verb -> <host>") before the JSON
  # body, so match the object rather than parsing the whole stream.
  STATUS=$(print -r -- "$OUT" | python3 -c '
import json,re,sys
raw = sys.stdin.read()
m = re.search(r"\{.*\}", raw, re.S)
if not m:
    sys.exit(0)
try:
    d = json.loads(m.group(0))
except Exception:
    sys.exit(0)
for row in d.get("loops", []):
    if row.get("number") == "A15":
        print(row.get("status", ""))
        break
' 2>/dev/null)
fi

if [ "$STATUS" = "done" ] || [ "$STATUS" = "dropped" ]; then
  log "A15 is '$STATUS' — Dell has migrated. Retiring this watcher."
  notify "Dell's migration is done. The preflight watcher has removed itself."
  launchctl unload -w "$PLIST" >/dev/null 2>&1
  rm -f "$PLIST"
  log "unloaded and removed $PLIST"
  exit 0
fi

if [ -z "$STATUS" ]; then
  # Not fatal and deliberately not a notification: the packet check below is the
  # point of the run, and it does not need the record to be reachable.
  log "could not read A15 status (record unreachable?) — continuing to check the packet"
else
  log "A15 still open — checking the packet"
fi

# ── 2. is the packet still good? ────────────────────────────────────────────
if OUT=$("$REPO/bin/migrate-dell.sh" --preflight </dev/null 2>&1); then
  log "PREFLIGHT CLEAN"
  exit 0
fi

# Name the failing lines in the log so the next reader does not have to re-run a
# 55-second check to find out what broke (rule 1f3a7372: an unattended run that
# only prints has not reported).
log "PREFLIGHT FAILED:"
print -r -- "$OUT" | grep -E "FAIL|not get|permission denied" | sed 's/^/    /' >> "$LOG"
notify "Preflight FAILED — Dell's migration would not be clean. Tell Claude: the migration packet regressed."
exit 1
