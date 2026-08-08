#!/bin/sh
# purge-recordings.sh — the retention law for ~/Recordings (decision db7b0231,
# Joe 2026-08-08: "no we dont want to keep recordings forever. we want the
# context of the recording only. thatas way too much storage.") as AMENDED the
# same day by his second ruling: "only purge transcripts if claude has
# processed them and ingested the data."
#
# THE GATE IS INGESTION, NOT TIME. The first cut of this script purged audio
# purely on elapsed time, which would have destroyed the audio of a meeting
# nobody ever distilled — the exact case where the raw capture is the ONLY
# copy of what was said. A session is now purged only when BOTH hold:
#   1. an ingestion marker exists (ingested.json, contract below), meaning the
#      distilled context is safely in the record layer, AND
#   2. the grace period has elapsed since that ingestion.
# No marker, no purge — forever, however old. That is deliberate: unprocessed
# audio accumulating is a visible, recoverable problem; audio deleted before
# anyone read it is not.
#
# THE MARKER CONTRACT — whatever ingests a session writes, into the session
# directory, ingested.json:
#   {"ingested_at": "<ISO-8601 UTC>", "by": "<who>", "records": ["<ref>", ...]}
# `ingested_at` is what the grace period is measured from. The WO-4 capture
# bridge (Deal Room lane) is the intended writer; until it ships NOTHING is
# written, so this job will correctly purge NOTHING and simply report the
# backlog. That is the safe failure direction, not a bug.
#
# WHAT SURVIVES A PURGE, always: transcript.json/md (the distilled context),
# announcement.json (the consent proof — it must outlive the audio it
# documents), meta.json, ingested.json. Only mic.caf/system.caf go.
set -eu

RECORDINGS="$HOME/Recordings"
GRACE_HOURS=72
LOG="$HOME/Library/Logs/recordings-purge.log"

log_line() {
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >> "$LOG"
}

[ -d "$RECORDINGS" ] || exit 0

purged=0
waiting=0
for dir in "$RECORDINGS"/*/; do
    [ -d "$dir" ] || continue
    case "$(basename "$dir")" in
        [0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]-[0-9][0-9][0-9][0-9]) ;;
        *) continue ;;
    esac
    # Nothing to reclaim if the audio is already gone.
    [ -f "$dir/mic.caf" ] || [ -f "$dir/system.caf" ] || continue

    marker="$dir/ingested.json"
    if [ ! -f "$marker" ]; then
        waiting=$((waiting + 1))
        continue
    fi
    # Grace measured from the MARKER's mtime — time since the data was
    # ingested, not since the meeting happened. 72h and not less because
    # weekends are not workdays (rule 236ca227): a Friday ingestion still
    # leaves the audio re-runnable on Monday morning.
    aged=$(find "$marker" -mmin +$((GRACE_HOURS * 60)) 2>/dev/null | wc -l | tr -d ' ')
    [ "$aged" = "1" ] || { waiting=$((waiting + 1)); continue; }

    for caf in "$dir/mic.caf" "$dir/system.caf"; do
        if [ -f "$caf" ]; then
            size=$(du -h "$caf" | cut -f1)
            rm "$caf"
            purged=$((purged + 1))
            log_line "PURGED $caf ($size) — ingested + ${GRACE_HOURS}h grace elapsed"
        fi
    done
done

if [ "$purged" -gt 0 ] || [ "$waiting" -gt 0 ]; then
    log_line "RUN complete: $purged audio file(s) purged, $waiting session(s) holding audio (not yet ingested, or inside grace)"
fi
exit 0
