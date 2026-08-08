#!/usr/bin/env bash
# convo.sh — stable entry point for the Doc conversation loop (loop #250).
# The interaction lives in convo.py (Python raw-key input; bash 3.2's read
# cannot debounce Enter autorepeat — first live run, 2026-08-08). This wrapper
# survives so docs, muscle memory, and future launchers keep one path.
exec python3 "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)/convo.py" "$@"
