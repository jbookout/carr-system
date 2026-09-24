#!/bin/zsh
# install-nightly-exports-retry.sh — install the daytime safety-net retry for
# the nightly "exports (6 targets -> OneDrive)" step.
#
# This is deliberately a NARROW installer, same shape as
# bin/install-notes-sweep.sh. `ops/config-as-code.py install` is the broad
# machine-configuration reconciler; using it here would also reconcile every
# other tracked hook and LaunchAgent. This command installs only
# com.carr.nightly-exports-daytime-retry, then proves launchd accepted it.
#
# Usage: ./bin/install-nightly-exports-retry.sh

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.carr.nightly-exports-daytime-retry"
SOURCE="$REPO/ops/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TMP="$(mktemp -t carr-nightly-exports-retry-plist)"
trap 'rm -f "$TMP"' EXIT

if [ ! -f "$SOURCE" ]; then
  print -u2 -- "install-nightly-exports-retry: missing source plist: $SOURCE"
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

print -- "install-nightly-exports-retry: installed and loaded $LABEL"
print -- "install-nightly-exports-retry: fires daily at 16:00 UTC; a no-op if"
print -- "  tonight's own nightly export already landed OK; log: $REPO/out/nightly.log"
