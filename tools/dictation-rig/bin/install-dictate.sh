#!/bin/sh
# install-dictate.sh — deploy quill-dictate's config and launchd agent.
# Mirrors install-config.sh / install-consent.sh conventions: idempotent,
# never touches ~/.config/quill (meeting mode's config) or vendor/quill.
#
# After install, two one-time permission grants belong to Joe, both in
# System Settings > Privacy & Security:
#   - Accessibility  (the event tap + paste injection)
#   - Microphone     (prompted automatically on first capture)
# A rebuild changes the binary's code hash, so macOS may ask again after a
# rebuild — that is TCC working as designed, not a defect.
set -eu

TOOL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BINARY="$TOOL_DIR/dictate/.build/release/quill-dictate"
CONFIG_DIR="$HOME/.config/quill-dictate"
CONFIG="$CONFIG_DIR/config.json"
PLIST_SRC="$TOOL_DIR/launchd/com.carr.quill-dictate.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.carr.quill-dictate.plist"

[ -x "$BINARY" ] || { echo "build first: bin/build-dictate.sh" >&2; exit 1; }

mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG" ]; then
    cat > "$CONFIG" <<'JSON'
{
  "trigger_key_codes": [54, 62],
  "conversation_key_code": 49,
  "hold_threshold_ms": 200,
  "tap_max_ms": 350,
  "double_tap_gap_ms": 400,
  "space_hold_threshold_ms": 200,
  "insertion": "paste",
  "sounds": true,
  "min_peak_level": 0.015,
  "whisper_cli": "/opt/homebrew/bin/whisper-cli",
  "model_path": "~/.cache/whisper-cpp/models/ggml-large-v3-turbo.bin",
  "fallback_model_path": "~/.cache/whisper-cpp/models/ggml-small.en.bin",
  "vocab_prompt_path": "~/carr-system/tools/dictation-rig/vocab-prompt.txt",
  "work_dir": "~/Library/Caches/quill-dictate",
  "log_path": "~/Library/Logs/quill-dictate.log"
}
JSON
    echo "wrote $CONFIG"
else
    echo "kept existing $CONFIG"
fi

if [ "${1:-}" = "--config-only" ]; then
    echo "config-only install done (launchd agent not touched)"
    exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST_SRC" "$PLIST_DST"
launchctl bootout "gui/$(id -u)/com.carr.quill-dictate" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
echo "launchd agent com.carr.quill-dictate loaded"
echo "if the menu-bar mic shows a badge: grant Accessibility in System Settings"
