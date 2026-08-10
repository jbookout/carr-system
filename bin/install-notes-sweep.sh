#!/bin/zsh
# install-notes-sweep.sh — install the quiet local runner for iPhone call notes.
#
# This is deliberately a NARROW installer. `ops/config-as-code.py install` is
# the broad machine-configuration reconciler; using it here would also rewrite
# every unrelated hook and LaunchAgent. This command installs only
# com.carr.notes-sweep, then proves launchd accepted it.
#
# The job is a LaunchAgent because Apple Notes requires Joe's logged-in macOS
# session. It is not an error if it has not run before the first iPhone call:
# the iOS-created `Call Recordings` folder does not exist until then.
#
# Usage: ./bin/install-notes-sweep.sh

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.carr.notes-sweep"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TMP="$(mktemp -t carr-notes-sweep-plist)"
trap 'rm -f "$TMP"' EXIT

if [ ! -f "$SOURCE" ]; then
  print -u2 -- "install-notes-sweep: missing source plist: $SOURCE"
  exit 1
fi

# The repo template stays portable; only the machine copy contains its actual
# checkout path. `plutil` validates before anything touches LaunchAgents.
/usr/bin/sed "s|{{REPO}}|$REPO|g" "$SOURCE" > "$TMP"
/usr/bin/plutil -lint "$TMP" >/dev/null

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/out"
/usr/bin/install -m 644 "$TMP" "$DEST"

# bootout is intentionally tolerant: a first install has nothing to unload.
/bin/launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "gui/$(id -u)" "$DEST"
/bin/launchctl print "gui/$(id -u)/$LABEL" >/dev/null

print -- "install-notes-sweep: installed and loaded $LABEL"
print -- "install-notes-sweep: durable log: $REPO/out/capture-lanes.log"
