#!/bin/sh
# purge-recordings.sh — the retention law for ~/Recordings (decision db7b0231,
# Joe 2026-08-08: "no we dont want to keep recordings forever. we want the
# context of the recording only. thatas way too much storage." — amending
# 28e35509's indefinite retention).
#
# INTERIM SHAPE, until the WO-4 distiller exists: the transcript IS the
# distilled context, so what purges is the RAW AUDIO (mic.caf / system.caf,
# the actual storage burden), 72 hours after the session's transcript was
# written. 72h and not less: Sat/Sun are not workdays (rule 236ca227), so a
# Friday call's audio survives past Monday morning in case a transcript came
# out wrong and needs a re-run (re-running needs the audio). What SURVIVES,
# per the ruling: transcript.json/md (the context), announcement.json (the
# consent proof — must outlive the audio), meta.json (clock offsets). When
# WO-4's distiller lands, transcripts join the post-distill purge and this
# script gets amended — that is a planned second step, not scope creep here.
#
# Scope guard: touches ONLY ~/Recordings/<yyyy.MM.dd-HHmm>/{mic,system}.caf,
# and only when a transcript exists beside them. A session with audio but NO
# transcript is never purged (the pipeline may have failed; deleting the
# audio would destroy the only copy of the meeting).
set -eu

RECORDINGS="$HOME/Recordings"
GRACE_HOURS=72
LOG="$HOME/Library/Logs/recordings-purge.log"

log_line() {
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >> "$LOG"
}

[ -d "$RECORDINGS" ] || exit 0

purged=0
for dir in "$RECORDINGS"/*/; do
    [ -d "$dir" ] || continue
    case "$(basename "$dir")" in
        [0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]-[0-9][0-9][0-9][0-9]) ;;
        *) continue ;;
    esac
    transcript="$dir/transcript.json"
    [ -f "$transcript" ] || continue
    # -mmin on the transcript: age since the pipeline FINISHED, not since the
    # meeting started.
    aged=$(find "$transcript" -mmin +$((GRACE_HOURS * 60)) 2>/dev/null | wc -l | tr -d ' ')
    [ "$aged" = "1" ] || continue
    for caf in "$dir/mic.caf" "$dir/system.caf"; do
        if [ -f "$caf" ]; then
            size=$(du -h "$caf" | cut -f1)
            rm "$caf"
            purged=$((purged + 1))
            log_line "PURGED $caf ($size) — transcript retained, grace ${GRACE_HOURS}h elapsed"
        fi
    done
done

[ "$purged" -gt 0 ] && log_line "RUN complete: $purged audio file(s) purged" || true
exit 0
