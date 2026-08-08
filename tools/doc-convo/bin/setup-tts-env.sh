#!/usr/bin/env bash
# setup-tts-env.sh — build the render env (.venv-tts) for Doc's frozen voice.
# python3.12 on purpose: torch wheels lag the newest CPython (system 3.14 has
# none at this writing). Heavy (~2-4GB of wheels); run once, re-run safe.

set -euo pipefail
TOOL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
PYBIN=/opt/homebrew/bin/python3.12
[ -x "$PYBIN" ] || { echo "need $PYBIN (brew install python@3.12)"; exit 2; }

cd "$TOOL_DIR"
[ -d .venv-tts ] || "$PYBIN" -m venv .venv-tts
./.venv-tts/bin/pip install --quiet --upgrade pip
# chatterbox-tts pulls torch/torchaudio pins itself; librosa is the
# take-screening dependency (RECIPE delivery screening).
./.venv-tts/bin/pip install chatterbox-tts librosa
./.venv-tts/bin/python - <<'EOF'
import torch, chatterbox, librosa
print(f"tts env ok: torch {torch.__version__}, mps={torch.backends.mps.is_available()}")
EOF
