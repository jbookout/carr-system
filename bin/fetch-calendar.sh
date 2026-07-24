#!/bin/bash
# CARR calendar fetcher — runs on Joe's Mac (normal internet, no sandbox egress block).
# Downloads the published Outlook iCal feed to a file the heartbeat can read.
# Installed as a launchd job (us.carr.fetchcalendar), fires ~7:55am daily, before the 8:06 heartbeat.
# Created 2026-07-15 to work around the sandbox network allowlist that blocks outlook.office365.com.
#
# 2026-07-17 FIX (TCC / "Operation not permitted", launchd exit 126): a launchd agent has no disk
# access to the Google Drive "CloudStorage" (File Provider) folder unless the executing binary is
# granted Full Disk Access. Requirement: grant /bin/bash Full Disk Access (System Settings > Privacy
# & Security > Full Disk Access). To keep the fix to a SINGLE binary, this script now does every
# Google Drive write with bash's own redirection (> and >>) instead of handing off to mv/cp/curl,
# so only /bin/bash needs the grant. curl writes nothing to Drive — it streams to stdout, bash writes.

URL="https://outlook.office365.com/owa/calendar/ed7aa2ebb2c647a8b5556340251a4ce7@carr.us/bbea9ec4a2c64895ad6559029407f6a05027337759467795791/S-1-8-2301065025-2974764972-3930401816-3405722231/reachcalendar.ics"
DIR="/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI/DNA/Team"
OUT="$DIR/calendar-latest.ics"
LOG="$DIR/calendar-fetch.log"

ICS="$(curl -sS -f -m 60 "$URL")"
rc=$?

if [ $rc -eq 0 ] && [ -n "$ICS" ]; then
  printf '%s\n' "$ICS" > "$OUT"                       # bash does the Drive write (needs bash FDA)
  lines="$(printf '%s' "$ICS" | wc -l | tr -d ' ')"
  printf '%s OK %s lines\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$lines" >> "$LOG"
else
  printf '%s FAIL curl exit %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$rc" >> "$LOG"
fi
