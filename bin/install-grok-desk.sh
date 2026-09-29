#!/bin/zsh
# Register the authenticated Grok CLI lane; no model work or login change.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
[[ -x "$PYTHON" ]] || { print -ru2 -- "grok-desk: repository Python is required"; exit 78; }
exec "$PYTHON" "$REPO/tools/room-bridge/grok_desk.py" "$@"
