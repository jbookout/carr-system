#!/usr/bin/env bash
# render-bridges.sh — pre-render the bridge set in Doc's frozen voice, with the
# full roll-and-screen discipline (4 takes each, falling-ending wins). These are
# the phrases the loop plays instantly, so they carry Doc's actual sound even
# while live renders stay slow. Every phrase is TRUTHFUL (council: earcon ack +
# a truthful bridge, then substance — never fake progress).
#
# Needs .venv-tts (bin/setup-tts-env.sh first). Idempotent: cached phrases are
# skipped by speak.py's hash check... but a re-render after a RECIPE change
# means clearing assets/phrases/ first, on purpose, by hand.

set -euo pipefail
TOOL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
SPEAK="$TOOL_DIR/bin/speak.py"
VENV="$TOOL_DIR/.venv-tts/bin/python"
[ -x "$VENV" ] || { echo "no .venv-tts — run bin/setup-tts-env.sh first"; exit 2; }

BRIDGES=(
  "Checking the record."
  "One moment."
  "Let me pull that up."
  "Working on it."
  "That write needs your confirm at the keyboard."
  "I'd need the desk for that one."
  "Say again? I lost part of that."
)

# Render via speak.py's live tier (which caches), but with 4-take screening:
# temporarily route render_phrase through --takes by rendering directly here,
# then letting speak.py adopt the mastered file into its cache.
for text in "${BRIDGES[@]}"; do
  # speak.py's normalize + hash, replicated via speak.py itself in cache-only
  # mode: hit = already rendered, skip.
  if python3 "$SPEAK" --cache-only "$text" >/dev/null 2>&1; then
    echo "cached: $text"; continue
  fi
  echo "rendering (4 takes): $text"
  RAW="$TOOL_DIR/assets/phrases/tmp-bridge.raw.wav"
  mkdir -p "$TOOL_DIR/assets/phrases"
  "$VENV" "$TOOL_DIR/bin/render_phrase.py" "$text" "$RAW" --takes 4
  # Mastering + cache adoption via speak.py's chain: easiest correct path is
  # to master here with the same chain constant.
  python3 - "$text" "$RAW" "$TOOL_DIR" <<'EOF'
import subprocess, sys, hashlib, pathlib
sys.path.insert(0, sys.argv[3] + "/bin")
from speak import normalize, MASTER_CHAIN
text, raw, tool = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
wav = tool / "assets" / "phrases" / (hashlib.sha1(normalize(text).encode()).hexdigest()[:16] + ".wav")
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw,
                "-af", MASTER_CHAIN, "-ar", "24000", "-ac", "1", str(wav)], check=True)
pathlib.Path(raw).unlink(missing_ok=True)
print(f"bridge ready: {wav.name}  «{text}»")
EOF
done
echo "bridge kit complete."
