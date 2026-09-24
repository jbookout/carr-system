#!/bin/zsh
# install-timebomb-audit.sh — install the weekly time-bomb audit LaunchAgent.
#
# A NARROW installer, same shape as bin/install-notes-sweep.sh: this
# installs only com.carr.timebomb-audit, then proves launchd accepted it,
# rather than reaching for the broad ops/config-as-code.py reconciler
# (which would also touch every unrelated hook and LaunchAgent).
#
# Usage: ./bin/install-timebomb-audit.sh

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.carr.timebomb-audit"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TMP="$(mktemp -t carr-timebomb-audit-plist)"
trap 'rm -f "$TMP"' EXIT

if [ ! -f "$SOURCE" ]; then
  print -u2 -- "install-timebomb-audit: missing source plist: $SOURCE"
  exit 1
fi

/usr/bin/sed "s|{{REPO}}|$REPO|g" "$SOURCE" > "$TMP"
/usr/bin/plutil -lint "$TMP" >/dev/null

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/out"
/usr/bin/install -m 644 "$TMP" "$DEST"

# bootout is intentionally tolerant: a first install has nothing to unload.
/bin/launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "gui/$(id -u)" "$DEST"
/bin/launchctl print "gui/$(id -u)/$LABEL" >/dev/null

print -- "install-timebomb-audit: installed and loaded $LABEL"
print -- "install-timebomb-audit: durable log: $REPO/out/timebomb-audit.log"
print -- "install-timebomb-audit: weekly report: $REPO/out/timebomb-audit/<date>.json"
