#!/bin/zsh
# install-pr-pipeline.sh — install the scripted review-and-merge pipeline's
# launchd agent.
#
# Same shape as bin/install-room-bridge.sh. This installs only
# com.carr.pr-pipeline, then proves launchd accepted it. It never touches any
# other hook or LaunchAgent — for that, ops/config-as-code.py is the broad
# reconciler and this script is not it.
#
# NOT RUN AS PART OF THIS PR. com.carr.pr-pipeline.plist is listed in
# DEFINITION_ONLY in ops/config-as-code.py: a pipeline that can squash-merge
# to main on its own is a live-effect change this repo's convention (see
# com.carr.repo-hygiene-janitor.plist) asks a human to start deliberately.
# Remove the DEFINITION_ONLY entry, then run this script, when Joe decides to
# turn the schedule on.
#
# Usage: ./bin/install-pr-pipeline.sh [--render-only]

set -eu

RENDER_ONLY=0
if [ "${1:-}" = "--render-only" ]; then
  [ "$#" -eq 1 ] || { print -u2 -- 'install-pr-pipeline: --render-only takes no arguments'; exit 64; }
  RENDER_ONLY=1
elif [ "$#" -ne 0 ]; then
  print -u2 -- 'usage: install-pr-pipeline.sh [--render-only]'
  exit 64
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.carr.pr-pipeline"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TMP="$(mktemp "${TMPDIR:-/tmp}/carr-pr-pipeline-plist.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

if [ ! -f "$SOURCE" ]; then
  print -u2 -- "install-pr-pipeline: missing source plist: $SOURCE"
  exit 1
fi

escape_sed_replacement() {
  print -r -- "$1" | /usr/bin/sed 's/[\\&|]/\\&/g'
}

validate_plist() {
  local plist_path="$1"
  local validator="${CARR_PR_PIPELINE_PLIST_VALIDATOR-auto}"
  if [ "$validator" = "python" ] || {
       [ "$validator" = "auto" ] && [ ! -x /usr/bin/plutil ]
     }; then
    local python_bin
    python_bin="$(command -v python3 || true)"
    [ -n "$python_bin" ] || return 1
    "$python_bin" - "$plist_path" >/dev/null <<'PY'
import plistlib
import sys

try:
    with open(sys.argv[1], "rb") as handle:
        plistlib.load(handle)
except Exception:
    raise SystemExit(1)
PY
    return $?
  fi
  [ "$validator" = "auto" ] || return 1
  /usr/bin/plutil -lint "$plist_path" >/dev/null 2>&1
}

REPO_REPLACEMENT="$(escape_sed_replacement "$REPO")"
HOME_REPLACEMENT="$(escape_sed_replacement "$HOME")"
/usr/bin/sed -e "s|{{REPO}}|$REPO_REPLACEMENT|g" \
            -e "s|{{HOME}}|$HOME_REPLACEMENT|g" "$SOURCE" > "$TMP"

if /usr/bin/grep -Eq '\{\{[^}]+\}\}' "$TMP"; then
  print -u2 -- "install-pr-pipeline: rendered plist still contains an unresolved template token"
  exit 1
fi
if ! validate_plist "$TMP"; then
  print -u2 -- "install-pr-pipeline: rendered plist failed validation; refusing installation"
  exit 1
fi

if [ "$RENDER_ONLY" -eq 1 ]; then
  /bin/cat "$TMP"
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  print -u2 -- "install-pr-pipeline: gh CLI is required and was not found; refusing installation"
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  print -u2 -- "install-pr-pipeline: gh is not logged in on this machine; refusing installation"
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/out"
/usr/bin/install -m 644 "$TMP" "$DEST"

/bin/launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "gui/$(id -u)" "$DEST"
/bin/launchctl print "gui/$(id -u)/$LABEL" >/dev/null

print -- "install-pr-pipeline: installed and loaded $LABEL"
print -- "install-pr-pipeline: durable log: $REPO/out/pr-pipeline-launchd.log"
print -- "install-pr-pipeline: state file: $REPO/out/pr-pipeline-state.json"
print -- "install-pr-pipeline: kill switches: ops/config/pr-pipeline-policy.json (enabled) and $REPO/out/pr-pipeline.disable"
