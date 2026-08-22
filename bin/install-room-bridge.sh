#!/bin/zsh
# install-room-bridge.sh — install the partner-room bridge's launchd agent.
#
# Deliberately narrow, same shape as bin/install-notes-sweep.sh. This installs
# only com.carr.room-bridge, then proves launchd accepted it. It never touches
# any other hook or LaunchAgent — for that, ops/config-as-code.py is the
# broad reconciler and this script is not it.
#
# Usage: ./bin/install-room-bridge.sh

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.carr.room-bridge"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TMP="$(mktemp -t carr-room-bridge-plist)"
trap 'rm -f "$TMP"' EXIT

if [ ! -f "$SOURCE" ]; then
  print -u2 -- "install-room-bridge: missing source plist: $SOURCE"
  exit 1
fi

# The repo template stays portable; only the machine copy contains its actual
# checkout path. plutil validates before anything touches LaunchAgents.
/usr/bin/sed "s|{{REPO}}|$REPO|g" "$SOURCE" > "$TMP"
/usr/bin/plutil -lint "$TMP" >/dev/null

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/out"
/usr/bin/install -m 644 "$TMP" "$DEST"

# bootout is intentionally tolerant: a first install has nothing to unload.
/bin/launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "gui/$(id -u)" "$DEST"
/bin/launchctl print "gui/$(id -u)/$LABEL" >/dev/null

print -- "install-room-bridge: installed and loaded $LABEL"
print -- "install-room-bridge: durable log: $REPO/out/room-bridge-launchd.log"
print -- "install-room-bridge: state file: $HOME/.config/carr/room-bridge-state.json"
print -- "install-room-bridge: stop it with: launchctl bootout gui/\$(id -u)/$LABEL"
