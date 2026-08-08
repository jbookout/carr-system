#!/usr/bin/env bash
# render-reflexes.sh — pre-render Doc's reflex replies in his frozen voice.
# Reflexes are only instant if they're already in the phrase cache; a reflex
# that has to render first is just a slow answer with fewer words. Run after
# adding any reply to reflexes.py. Renders WITHOUT playing (prepare, not play).

set -euo pipefail
BIN=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$BIN"
python3 - <<'PY'
import sys, time
sys.path.insert(0, ".")
import reflexes, speak

for reply in reflexes.all_replies():
    t0 = time.time()
    wav = speak.prepare(reply)
    if wav is None:
        print(f"FAILED  «{reply}»")
        continue
    cached = time.time() - t0 < 0.5
    print(f"{'cached ' if cached else 'rendered'} {time.time()-t0:5.1f}s  «{reply}»")
PY
