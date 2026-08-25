#!/bin/zsh
# Invoke the Engineering Passport controller from the existing room-bridge
# wake.  It is not a scheduler: jobs enter only via MCP admission and this
# command claims only the fixed engineering definition.

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
NODE="/opt/homebrew/opt/node@22/bin/node"
PYTHON="$REPO/.venv/bin/python"

# Resolve the tracked, fixed implementation and dedicated desk before any
# credential is loaded.  Neither an ambient PATH nor a CARR_* override can
# choose code or a target that will later receive the jobs capability.
[[ -x "$NODE" ]] || { print -ru2 -- "engineering-dispatch: fixed Node 22 executable is required"; exit 78; }
[[ -x "$PYTHON" ]] || { print -ru2 -- "engineering-dispatch: repository Python is required"; exit 78; }
"$PYTHON" "$REPO/tools/room-bridge/engineering_dispatch_adapter.py" --preflight

source "$REPO/bin/routine-credential-env.sh"
carr_clear_routine_db_env
carr_load_routine_db_env CARR_DB_JOBS_URL
[[ -n "${CARR_DB_JOBS_URL:-}" ]] || { print -ru2 -- "engineering-dispatch: CARR_DB_JOBS_URL is required"; exit 78; }

# The controller receives the jobs credential; its Python adapter builds a new
# allowlisted environment before it starts Codex, so no database credential can
# reach the agent process.
exec env -i HOME="$HOME" PATH="$PATH" LANG="${LANG:-C}" TMPDIR="${TMPDIR:-/tmp}" \
  CARR_DB_JOBS_URL="$CARR_DB_JOBS_URL" \
  "$NODE" "$REPO/mcp-server/bin/run-engineering-dispatch.mjs"
