#!/bin/bash
# consent-watch.sh — CARR dictation rig, consent-announcement layer.
#
# Run by launchd (com.carr.dictation-consent) with WatchPaths on ~/Recordings,
# and safe to run by hand for testing. Each invocation scans the recordings
# root for session directories that are (a) still recording (no meta.json yet)
# and (b) not yet announced (no announcement.json yet), plays the "now
# recording" clip aloud for each one found, and stamps announcement.json so
# the same session is never announced twice.
#
# Spec: Addendum 5 / dictation-rig-build-plan-2026-08-07.md, Phase A step 2.
# Recording must never run silently.
#
# Env:
#   CARR_RECORDINGS_DIR  — override the recordings root (default: ~/Recordings)
#
# Exit code is always 0 on a normal scan (nothing to announce, or several
# announced) so launchd never treats a quiet run as a failure. Only a hard
# internal error (e.g. the clip asset is missing) exits non-zero.

set -u
set -o pipefail

# --- paths -------------------------------------------------------------

# Resolve our own directory so the script works from any checkout (the
# dictation-rig worktree during build, or the durable main-repo path once
# merged) without hardcoding either one.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
TOOL_DIR="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
CLIP_PATH="$TOOL_DIR/assets/now-recording.aiff"

RECORDINGS_DIR="${CARR_RECORDINGS_DIR:-$HOME/Recordings}"
LOG_FILE="/Users/booko/carr-system/out/dictation-consent.log"

AFPLAY_BIN="/usr/bin/afplay"
OSASCRIPT_BIN="/usr/bin/osascript"

# --- helpers -------------------------------------------------------------

log_line() {
    # $1 = message
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    local log_dir
    log_dir="$(dirname "$LOG_FILE")"
    mkdir -p "$log_dir" 2>/dev/null || true
    printf '%s %s\n' "$ts" "$1" >> "$LOG_FILE" 2>/dev/null || true
}

iso_now_utc() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

announce_session() {
    # $1 = full path to session directory
    local session_dir="$1"
    local session_name
    session_name="$(basename "$session_dir")"
    local marker="$session_dir/announcement.json"
    local tmp_marker="$marker.tmp.$$"

    # Play the clip aloud. afplay blocks until playback finishes, which is
    # what we want — we only write the marker once the announcement has
    # actually fired.
    "$AFPLAY_BIN" "$CLIP_PATH"
    local play_status=$?
    if [ "$play_status" -ne 0 ]; then
        log_line "ERROR afplay exit=$play_status session=$session_name clip=$CLIP_PATH"
        return 1
    fi

    local fired_at
    fired_at="$(iso_now_utc)"

    # Write to a temp file then rename into place — the closest we get to an
    # atomic write with plain shell, so a half-written announcement.json is
    # never mistaken for a completed one.
    {
        printf '{\n'
        printf '  "announcement_fired_at": "%s",\n' "$fired_at"
        printf '  "clip": "now-recording.aiff",\n'
        printf '  "played_by": "consent-watch"\n'
        printf '}\n'
    } > "$tmp_marker"

    if ! mv -f "$tmp_marker" "$marker"; then
        log_line "ERROR could not write announcement.json for session=$session_name"
        rm -f "$tmp_marker" 2>/dev/null || true
        return 1
    fi

    # Best-effort desktop notification. Never let a notification failure
    # (e.g. no GUI session attached, permission not granted yet) abort the
    # run — the audible announcement plus the transcript-header marker are
    # the actual consent mechanism; the notification is a courtesy.
    "$OSASCRIPT_BIN" -e 'display notification "Recording started — announcement played. Ask-first applies on 1:1 calls." with title "CARR Dictation Rig"' >/dev/null 2>&1 || true

    log_line "ANNOUNCED session=$session_name fired_at=$fired_at"
    return 0
}

# --- main -------------------------------------------------------------

main() {
    # Safe when the recordings root doesn't exist yet (e.g. before the first
    # quill session ever runs, or in a fresh test fixture) — just no-op.
    if [ ! -d "$RECORDINGS_DIR" ]; then
        exit 0
    fi

    if [ ! -f "$CLIP_PATH" ]; then
        log_line "ERROR clip asset missing at $CLIP_PATH"
        exit 1
    fi

    local dir
    for dir in "$RECORDINGS_DIR"/*/; do
        # Glob with no matches leaves the literal pattern; guard against that.
        [ -d "$dir" ] || continue
        dir="${dir%/}"

        local meta="$dir/meta.json"
        local marker="$dir/announcement.json"

        # (a) still recording: no meta.json yet (meta.json is written only at
        #     stop). (b) not yet announced: no announcement.json yet.
        if [ -f "$meta" ]; then
            continue
        fi
        if [ -f "$marker" ]; then
            continue
        fi

        announce_session "$dir"
    done

    exit 0
}

main "$@"
