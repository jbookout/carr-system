#!/bin/sh
# install-config.sh — deploy the quill config that wires capture to OUR whisper.
# Idempotent; backs up any existing config beside itself before overwriting.
# transcription.enabled=false: quill's Parakeet engine never runs and never
# downloads its model — transcribe-session.sh (on_stop) owns transcription.
set -eu

CONF_DIR="$HOME/.config/quill"
CONF="$CONF_DIR/config.json"
HOOK="/Users/booko/carr-system/tools/dictation-rig/bin/transcribe-session.sh"

mkdir -p "$CONF_DIR"
if [ -f "$CONF" ]; then
  cp "$CONF" "$CONF.bak-$(date +%Y%m%d%H%M%S)"
fi

cat > "$CONF" <<EOF
{
  "recordings_dir": "~/Recordings",
  "transcription": { "enabled": false },
  "on_stop": "$HOOK"
}
EOF

echo "wrote $CONF:"
cat "$CONF"
