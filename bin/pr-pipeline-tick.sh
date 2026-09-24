#!/bin/zsh
# bin/pr-pipeline-tick.sh — one wake of the scripted review-and-merge pipeline.
#
# Same shape as bin/control-plane-tick.sh: launchd is only a wake-up adapter.
# This wrapper owns no policy of its own — tools/pr-pipeline/pipeline.py
# decides scope, state, dispatch and merge; this file only supplies the
# python interpreter, the repo's working directory, and the local `gh` login
# that is already authenticated on this Mac (same login the human orchestrator
# uses today; nothing here mints or reads a separate credential).
#
# Installed via bin/run-scheduled.sh so a stuck or erroring tick becomes a
# durable ops.run row instead of a silent launchd log line, exactly like
# room-bridge and every other launchd-ticked job in this repo.

set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"

PYTHON="$REPO/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 2>/dev/null || true)"
fi
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  print -ru2 -- "pr-pipeline-tick: python3 is required"
  exit 78
fi

if ! command -v gh >/dev/null 2>&1; then
  print -ru2 -- "pr-pipeline-tick: gh CLI is required (the local login this Mac already has)"
  exit 78
fi

cd "$REPO" || exit 78
exec "$PYTHON" "$REPO/tools/pr-pipeline/pipeline.py" tick
