#!/bin/sh
# install-capture-bridge.sh — installs the WO-4 capture-bridge LaunchAgents
# and provisions the local rig's config skeleton.
#
# Mirrors install-consent.sh / install-dictate.sh conventions: idempotent,
# never touches vendor/quill, ~/.config/quill, or git. This script is what
# the orchestrator runs under supervision, once a device token from the
# worker's CAPTURE_TOKENS map is in hand (see
# .claude/worktrees/wo4-capture-bridge/SUMMARY.md, "Human provisioning for
# CAPTURE_TOKENS") — building the rig and installing/provisioning it are
# separate, deliberately gated steps, same discipline as install-consent.sh
# gating on the tool having merged into the durable checkout path first.
#
# Usage: install-capture-bridge.sh [base_url] [device_id]
# Both arguments are optional; an omitted one falls back to a placeholder
# that makes an unprovisioned config obvious (config check / capture-bridge.py
# check will report it) rather than silently pointing nowhere. The token
# field is ALWAYS left empty by this script — it never writes a secret.
# Joe (or the orchestrator) fills token in by hand per the SUMMARY.md
# provisioning steps; every capture-bridge.py subcommand goes from clean
# no-op to live the moment that field is non-empty.
#
# Safe to re-run: never overwrites an existing config.json, and each
# LaunchAgent install does a clean bootout-then-bootstrap (bootout on an
# agent that isn't loaded is treated as a no-op).

set -eu

TOOL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

BASE_URL="${1:-https://REPLACE-ME.workers.dev}"
DEVICE_ID="${2:-REPLACE-ME-device-id}"

CONFIG_DIR="$HOME/.config/carr-capture"
CONFIG="$CONFIG_DIR/config.json"

mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG" ]; then
    umask 077
    cat > "$CONFIG" <<EOF
{
  "base_url": "$BASE_URL",
  "device_id": "$DEVICE_ID",
  "token": ""
}
EOF
    chmod 600 "$CONFIG"
    echo "install-capture-bridge.sh: wrote $CONFIG (token empty — provision it per SUMMARY.md before the bridge goes live)"
else
    echo "install-capture-bridge.sh: kept existing $CONFIG (never overwritten)"
fi

install_agent() {
    label="$1"
    plist_src="$TOOL_DIR/launchd/$label.plist"
    plist_dst="$HOME/Library/LaunchAgents/$label.plist"

    if [ ! -f "$plist_src" ]; then
        echo "install-capture-bridge.sh: plist not found at $plist_src" >&2
        exit 1
    fi

    mkdir -p "$HOME/Library/LaunchAgents"
    cp "$plist_src" "$plist_dst"

    launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "$plist_dst"

    launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 \
        && echo "install-capture-bridge.sh: bootstrapped and verified $label" \
        || echo "install-capture-bridge.sh: WARNING — could not verify $label is loaded"
}

install_agent "com.carr.capture-watch"
install_agent "com.carr.capture-poll"

echo "install-capture-bridge.sh: done"
