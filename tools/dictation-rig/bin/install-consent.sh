#!/bin/bash
# install-consent.sh — installs the CARR dictation-rig consent-announcement
# LaunchAgent.
#
# This script is what the orchestrator runs under supervision, after this
# tool has merged into the main ~/carr-system checkout at the durable path
# the plist references (/Users/booko/carr-system/tools/dictation-rig/...).
# It is NOT run as part of the build pass that produced it — building the
# files and installing them are separate, deliberately gated steps.
#
# What it does:
#   1. Copies com.carr.dictation-consent.plist into ~/Library/LaunchAgents/
#   2. If the agent is already loaded, bootout first (clean reload, no
#      "already bootstrapped" error on a re-install).
#   3. Bootstraps the agent into the current user's GUI domain so WatchPaths
#      is live against ~/Recordings.
#
# Safe to re-run: bootout on an agent that isn't loaded is treated as a
# no-op (its failure is swallowed), and bootstrap after a clean bootout
# always succeeds.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
TOOL_DIR="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"

LABEL="com.carr.dictation-consent"
PLIST_SRC="$TOOL_DIR/launchd/$LABEL.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DEST="$LAUNCH_AGENTS_DIR/$LABEL.plist"
CONSENT_SCRIPT="/Users/booko/carr-system/tools/dictation-rig/bin/consent-watch.sh"

if [ ! -f "$PLIST_SRC" ]; then
    echo "install-consent.sh: plist not found at $PLIST_SRC" >&2
    exit 1
fi

if [ ! -f "$CONSENT_SCRIPT" ]; then
    echo "install-consent.sh: WARNING — $CONSENT_SCRIPT does not exist yet." >&2
    echo "The plist points at the durable main-checkout path. If this tool" >&2
    echo "has not merged into ~/carr-system yet, launchd will fail to run" >&2
    echo "the job even though bootstrap succeeds. Merge first, then install." >&2
fi

mkdir -p "$LAUNCH_AGENTS_DIR"
cp "$PLIST_SRC" "$PLIST_DEST"
echo "install-consent.sh: copied $PLIST_SRC -> $PLIST_DEST"

UID_NUM="$(id -u)"
DOMAIN_TARGET="gui/$UID_NUM"
SERVICE_TARGET="$DOMAIN_TARGET/$LABEL"

# Clean reload: bootout first if already loaded. launchctl bootout exits
# non-zero if the service isn't loaded — that's expected on a first
# install, so don't let it abort the script under set -e.
launchctl bootout "$SERVICE_TARGET" >/dev/null 2>&1 || true

launchctl bootstrap "$DOMAIN_TARGET" "$PLIST_DEST"
echo "install-consent.sh: bootstrapped $LABEL into $DOMAIN_TARGET"

launchctl print "$SERVICE_TARGET" >/dev/null 2>&1 \
    && echo "install-consent.sh: verified $LABEL is loaded" \
    || echo "install-consent.sh: WARNING — could not verify $LABEL is loaded"
