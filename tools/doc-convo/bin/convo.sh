#!/usr/bin/env bash
# convo.sh — the v0 push-to-talk front end (loop #250). Terminal only; the
# phone app fronts the same engine later (README: engine vs front end).
#
#   Enter = start talking · Enter again = done · Ctrl-C = leave
#
# Cadence, honestly: ack is instant, the bridge phrase covers the brain
# (3-15s), and a NEW spoken sentence renders at ~0.25x realtime on this Mac —
# the reply prints immediately and speaks when the render lands. Cached
# phrases speak instantly.

set -uo pipefail

TOOL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
RIG_DIR="$TOOL_DIR/../dictation-rig"
WHISPER=/opt/homebrew/bin/whisper-cli
MODEL="$HOME/.cache/whisper-cpp/models/ggml-large-v3-turbo.bin"
VOCAB="$RIG_DIR/vocab-prompt.txt"
EARCON="$TOOL_DIR/assets/earcon-ack.wav"
CONTEXT="$TOOL_DIR/assets/hot-context.md"
PREAMBLE="$TOOL_DIR/prompt/preamble.md"
SESSION_FILE="$TOOL_DIR/assets/.brain-session-id"
MIC="${DOC_MIC_DEVICE:-:0}"                 # ffmpeg avfoundation audio device
BRAIN_MODEL="${DOC_BRAIN_MODEL:-sonnet}"    # fast + capable; override to taste
WORKDIR=$(mktemp -d /tmp/doc-convo.XXXXXX)
trap 'rm -rf "$WORKDIR"' EXIT

[ -x "$WHISPER" ] || { echo "missing $WHISPER (dictation rig installs it)"; exit 2; }
[ -f "$MODEL" ]   || { echo "missing whisper model $MODEL"; exit 2; }
[ -f "$EARCON" ]  || "$TOOL_DIR/bin/make-earcon.sh"

# Snapshot freshness: refresh when absent or older than 30 minutes. A stale
# snapshot silently served as current is how a voice assistant lies by accident.
if [ ! -f "$CONTEXT" ] || [ -n "$(find "$CONTEXT" -mmin +30 2>/dev/null)" ]; then
  echo "· refreshing hot context ..."
  "$TOOL_DIR/bin/refresh-context.sh" || echo "· refresh failed — using last snapshot if any"
fi
[ -f "$CONTEXT" ] || { echo "no hot-context snapshot and refresh failed"; exit 2; }

SYSTEM_PROMPT=$(cat "$PREAMBLE" "$CONTEXT")

echo "Doc is at the desk. Enter to talk, Enter to stop, Ctrl-C to leave."
turn=0
while true; do
  read -r -p "you ⏎ " _ || break
  turn=$((turn + 1))
  mkdir -p "$WORKDIR"          # /tmp cleaners can reap it mid-session
  UTT="$WORKDIR/utt-$turn.wav"

  ffmpeg -y -loglevel error -f avfoundation -i "$MIC" -ac 1 -ar 16000 "$UTT" \
    2>"$WORKDIR/rec-err-$turn" &
  REC_PID=$!
  read -r -p "· listening — ⏎ when done " _ || { kill "$REC_PID" 2>/dev/null; break; }
  kill -INT "$REC_PID" 2>/dev/null; wait "$REC_PID" 2>/dev/null
  afplay "$EARCON" &

  if [ ! -s "$UTT" ]; then
    echo "· mic gave no audio. Check System Settings → Privacy & Security →"
    echo "  Microphone → Terminal is ON. If it is, find your mic's index with:"
    echo "  ffmpeg -f avfoundation -list_devices true -i \"\"   then rerun as:"
    echo "  DOC_MIC_DEVICE=':1' $0"
    sed -n '1,2p' "$WORKDIR/rec-err-$turn" 2>/dev/null
    continue
  fi

  # Silence gate: whisper large-v3 famously hallucinates captions ("Sous-titrage
  # Société Radio-Canada" class) on near-silent audio. Below -45dB mean, say so
  # instead of transcribing noise into fiction.
  MEANVOL=$(ffmpeg -i "$UTT" -af volumedetect -f null - 2>&1 \
            | sed -n 's/.*mean_volume: \(-*[0-9.]*\) dB.*/\1/p')
  if [ -n "$MEANVOL" ] && [ "$(printf '%.0f' "$MEANVOL" 2>/dev/null || echo 0)" -lt -45 ]; then
    echo "· heard only silence (mic level ${MEANVOL}dB) — is the right mic set?"
    continue
  fi

  TEXT=$("$WHISPER" -m "$MODEL" -f "$UTT" --prompt "$(cat "$VOCAB")" \
          -nt -np 2>/dev/null | tr '\n' ' ' | sed 's/^ *//;s/ *$//')
  [ -n "$TEXT" ] || { echo "· heard nothing"; continue; }
  echo "you: $TEXT"

  # Truthful bridge while the brain works — cache-only so a missing bridge
  # never blocks the turn (it just stays silent until the reply).
  python3 "$TOOL_DIR/bin/speak.py" --cache-only "Checking the record." >/dev/null 2>&1 &

  # No arrays: macOS ships bash 3.2, where an empty array under `set -u` is an
  # "unbound variable" abort (bit us live, first conversation, 2026-08-08).
  if [ -f "$SESSION_FILE" ]; then
    BRAIN_JSON=$(claude -p "$TEXT" --model "$BRAIN_MODEL" \
      --append-system-prompt "$SYSTEM_PROMPT" \
      --output-format json --resume "$(cat "$SESSION_FILE")" \
      2>"$WORKDIR/brain-err-$turn")
  else
    BRAIN_JSON=$(claude -p "$TEXT" --model "$BRAIN_MODEL" \
      --append-system-prompt "$SYSTEM_PROMPT" \
      --output-format json 2>"$WORKDIR/brain-err-$turn")
  fi
  REPLY=$(printf '%s' "$BRAIN_JSON" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("result", "").strip())
    sid = d.get("session_id", "")
    if sid: open(sys.argv[1], "w").write(sid)
except Exception:
    pass
' "$SESSION_FILE")
  if [ -z "$REPLY" ]; then
    echo "· brain error:"
    [ -f "$WORKDIR/brain-err-$turn" ] && tail -3 "$WORKDIR/brain-err-$turn"
    continue
  fi

  echo "doc: $REPLY"
  python3 "$TOOL_DIR/bin/speak.py" "$REPLY" || true
done
