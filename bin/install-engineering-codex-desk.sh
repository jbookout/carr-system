#!/bin/zsh
# Install/read back the one dedicated, unseated Codex desk allowed to receive
# an Engineering Passport.  This never starts a job or loads a DB credential.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
[[ -x "$PYTHON" ]] || { print -ru2 -- "engineering-codex: repository Python is required"; exit 78; }
exec "$PYTHON" "$REPO/tools/room-bridge/engineering_dispatch_adapter.py" --install-desk
