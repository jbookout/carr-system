#!/bin/zsh
# set-machine-role.sh — mark this Mac primary or secondary for CARR's shared jobs.
#
# One partner can own more than one Mac. Exactly one of them should run the
# primary-only launch agents (nightly record layer, rules refresh, local briefs,
# partner ping, cutover watch, video pipeline); the rest are secondary. The
# marker this writes, ~/.config/carr/machine-role.json, outranks the git-email
# fallback in lib/machine_role.py. Rewrite it on each Mac after copying
# ~/.config/carr between machines, because the copy carries the old Mac's role.
#
# USAGE:
#   bin/set-machine-role.sh primary|secondary     # writes the marker, then
#                                                 # re-runs config-as-code install
#
# Marking a Mac secondary unloads its primary-only jobs and moves their plists
# to ~/Library/LaunchAgents-quarantine/carr-primary-only/ (never deleted).
# Mark the old primary secondary BEFORE marking the new one primary, so the
# shared jobs never run on both.
set -eu

ROLE="${1:-}"
if [[ "$ROLE" != "primary" && "$ROLE" != "secondary" ]]; then
  print -ru2 -- "usage: bin/set-machine-role.sh primary|secondary"
  exit 64
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$HOME/.config/carr"
mkdir -p "$DIR"
chmod 700 "$DIR"
TMP="$(mktemp "$DIR/.machine-role.XXXXXX")"
print -r -- "{\"role\": \"$ROLE\"}" > "$TMP"
chmod 600 "$TMP"
mv "$TMP" "$DIR/machine-role.json"
print -r -- "machine role: $ROLE ($DIR/machine-role.json)"

PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
exec "$PY" "$REPO/ops/config-as-code.py" install --apply </dev/null
