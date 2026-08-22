#!/bin/zsh
# The bundle's executable. This bundle exists only so the calendar read has a
# BUNDLE IDENTITY, which is what macOS requires before it will show a permission
# prompt at all. A bare python binary has none, so the request returns denied
# WITHOUT prompting and nothing ever appears in Privacy & Security — which is
# exactly what happened, and was misread as a denial, on 2026-08-13.
#
# It logs rather than prints because `open` gives a bundle no terminal.
#   open -a "tools/CARR Calendar Access.app"                -> coverage probe
#   open -a "tools/CARR Calendar Access.app" --args dump    -> attendee dump
REPO="${CARR_REPO:-$HOME/carr-system}"
case "${1:-probe}" in
  discover-catalog)
    [ "$#" -eq 2 ] || exit 64
    umask 077
    exec "$REPO/.venv/bin/python" "$REPO/tools/calendar-prebrief-discover.py" catalog "$2"
    ;;
  discover-allowlist)
    [ "$#" -ge 4 ] || exit 64
    umask 077
    exec "$REPO/.venv/bin/python" "$REPO/tools/calendar-prebrief-discover.py" allowlist "$2" "$3" "${@:4}"
    ;;
  # The dedicated prebrief runtime invokes the installed bundle executable,
  # not a bare Python process. `exec` preserves stdout as its bounded IPC
  # pipe; raw calendar material never enters this log file.
  collector)
    umask 077
    [ "$#" -eq 6 ] || exit 64
    [ -p "$2" ] && [ -p "$3" ] || exit 64
    export CARR_CALENDAR_PREBRIEF_ALLOWLIST="$4"
    export CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY="$5"
    export CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION="$6"
    "$REPO/.venv/bin/python" "$REPO/tools/calendar-prebrief-collector.py" < "$2" > "$3"
    exit $?
    ;;
  dump)  SCRIPT="$REPO/tools/calendar-attendee-dump.py" ;;
  *)     SCRIPT="$REPO/tools/calendar-attendee-probe.py" ;;
esac
OUTPUT_ROOT="${2:-${CARR_CALENDAR_OUTPUT_ROOT:-$REPO/out}}"
LOG="$OUTPUT_ROOT/calendar-access.log"
mkdir -p "$OUTPUT_ROOT"
{
  print -r -- "--- $(date -u +%FT%TZ) CARR Calendar Access (${1:-probe}) ---"
  CARR_CALENDAR_OUTPUT_ROOT="$OUTPUT_ROOT" "$REPO/.venv/bin/python" "$SCRIPT" 2>&1
  print -r -- "exit=$?"
} >> "$LOG"
