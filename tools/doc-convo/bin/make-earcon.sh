#!/usr/bin/env bash
# make-earcon.sh — generate the ack earcon (run once; regenerating is safe,
# the sound is deterministic). Voice-doctrine §4 spec: under 200ms, soft-fast
# attack, no roughness, warm low-mid fundamental. "The sound of 'heard you',
# final and calm." Served instantly instead of TTS — that design retires the
# 300ms acknowledgment budget entirely.

set -euo pipefail
TOOL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
mkdir -p "$TOOL_DIR/assets"

# G3 (196 Hz) sine, 160ms, 5ms attack, long soft decay, rounded by a lowpass.
ffmpeg -y -loglevel error -f lavfi -i "sine=frequency=196:duration=0.16" \
  -af "afade=t=in:d=0.005,afade=t=out:st=0.05:d=0.11,lowpass=f=1400,volume=0.4" \
  -ar 24000 -ac 1 "$TOOL_DIR/assets/earcon-ack.wav"
echo "earcon: $TOOL_DIR/assets/earcon-ack.wav"
