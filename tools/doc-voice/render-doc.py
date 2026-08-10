#!/usr/bin/env python3
"""render-doc.py — render a line as Doc, roll N takes, keep the one that lands.

WHY THIS EXISTS. Every TTS render re-rolls delivery. On 2026-08-10 Joe heard a
flat "I have a concern" in the ElevenLabs web UI and we spent the next twenty
minutes moving sliders — but three later takes on IDENTICAL settings all landed
correctly. The flat one was an unlucky roll, not a bad setting. A UI that renders
once cannot tell those apart, and neither can a person listening to one take.
RECIPE.md already prescribes the answer for the Chatterbox path: "Roll N takes
per sentence, score terminal f0 slope ... reject rising finals." This is that
discipline on the ElevenLabs path.

WHAT IT DOES NOT DO. It does not judge whether the CONCERN REGISTER landed — the
tightening, the flattened smile. Terminal slope is a falsifiable proxy for one
doctrine law (no upspeak on statements); it is not a proxy for character. It
narrows the field so a human ear judges 1 take instead of 5, and says so rather
than implying the machine decided.

    render-doc.py "text"                 # 3 takes, keep best, print the path
    render-doc.py "text" --takes 5
    render-doc.py "text" --keep-all      # keep every take for A/B by ear
    render-doc.py "text" --no-cache

CACHING IS THE COST CONTROL. Starter is ~40k credits/month and a daily brief is
~800 credits; rolling 3 takes on everything would exhaust the month around day
16. Fixed phrases — the self-introduction above all — must render once and be
replayed forever. Cache key is the text PLUS the settings, so changing a dial
correctly invalidates it.

OUTPUT IS RAW, PRE-MASTERING. The frozen ffmpeg chain in RECIPE.md runs at
render time and must be RE-DIALLED by ear against this engine rather than pasted
on; that is the recipe's own instruction and it is not done yet.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

VOICE_ID = "lHIYZnoq0Z0mhYRJ8YYr"           # "Doc" — an id, not a secret
MODEL = "eleven_multilingual_v2"            # v3 rejected 2026-08-10: no speed control
KEY_FILE = os.path.expanduser("~/.config/carr/elevenlabs.env")
CACHE = os.path.expanduser("~/carr-system/out/doc-voice-cache")

# Joe's ear, 2026-08-10. speed is the one value inferred from a slider position
# rather than read off a tooltip — flagged in RECIPE.md, not silently precise.
SETTINGS = {
    "stability": 0.65,
    "similarity_boost": 0.87,
    "style": 0.0,
    "use_speaker_boost": True,
    "speed": 0.95,
}

SR = 24000
FMIN, FMAX = 70, 300
FRAME, HOP = 1024, 256
TAIL_SEC = 0.8

# The flat end of the gate, in Hz/s. Evidence, not a guess: a take measured at
# -3 Hz/s is the delivery Joe heard as flat in the ElevenLabs UI on 2026-08-10,
# while -24 and -36 were both acceptable to him (he mildly preferred the -24).
# So the failure mode is "barely falls", and 8 sits clear of the bad case with
# room under the known-good ones. Revise it when there is more ear data; it is
# one observation, and this comment exists so nobody mistakes it for a
# calibrated threshold.
FLAT_FLOOR = 8.0


def api_key():
    """Read-only. The value is never printed, logged or returned to a caller
    that prints it — secrets-inventory.md calls a credential in a transcript an
    incident."""
    try:
        for line in open(KEY_FILE, encoding="utf-8"):
            if line.startswith("ELEVENLABS_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    except OSError:
        pass
    sys.exit(f"no ELEVENLABS_API_KEY in {KEY_FILE} — paste it in by hand")


def render(text, key):
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
        data=json.dumps({"text": text, "model_id": MODEL,
                         "voice_settings": SETTINGS}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json",
                 "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


# ── terminal-slope screen ───────────────────────────────────────────────────
# Autocorrelation rather than librosa.pyin: librosa is a heavy dependency for a
# single measurement. Cruder on absolute pitch, adequate for the SIGN of the
# slope, which is the question. If the two ever disagree, believe librosa.
def _pcm(path):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1",
                          "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    import numpy as np
    return np.frombuffer(out, dtype=np.float32)


def terminal_slope(path):
    """Hz/second across the final voiced stretch. Negative = falling = Doc."""
    import numpy as np
    x = _pcm(path)
    lo, hi = SR // FMAX, SR // FMIN
    ts, fs = [], []
    for i in range(0, len(x) - FRAME, HOP):
        fr = x[i:i + FRAME].astype(np.float64)
        if np.sqrt(np.mean(fr ** 2)) < 0.01:
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, mode="full")[FRAME - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:hi]
        if not len(seg):
            continue
        lag = lo + int(np.argmax(seg))
        if ac[lag] / ac[0] < 0.3:
            continue
        ts.append(i / SR)
        fs.append(SR / lag)
    if len(fs) < 6:
        return None
    t, f = np.array(ts), np.array(fs)
    m = t >= (t[-1] - TAIL_SEC)
    tt, ff = (t[m], f[m]) if m.sum() >= 6 else (t[-12:], f[-12:])
    med = np.median(ff)
    keep = (ff > med * 0.6) & (ff < med * 1.7)
    tt, ff = tt[keep], ff[keep]
    if len(ff) < 5:
        return None
    return float(np.polyfit(tt, ff, 1)[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--takes", type=int, default=3)
    ap.add_argument("--out")
    ap.add_argument("--keep-all", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()

    os.makedirs(CACHE, exist_ok=True)
    stamp = hashlib.sha256(
        (a.text + json.dumps(SETTINGS, sort_keys=True) + MODEL).encode()
    ).hexdigest()[:16]
    cached = os.path.join(CACHE, stamp + ".mp3")

    if os.path.exists(cached) and not a.no_cache:
        print(f"  cache hit ({stamp}) — 0 credits")
        winner = cached
    else:
        key = api_key()
        takes = []
        for i in range(1, a.takes + 1):
            p = os.path.join(CACHE, f"{stamp}-take{i}.mp3")
            try:
                open(p, "wb").write(render(a.text, key))
            except Exception as e:
                print(f"  FAIL  take{i}: {str(e)[:70]}")
                continue
            s = terminal_slope(p)
            takes.append((p, s))
            print(f"  take{i}: {'no read' if s is None else f'{s:+.0f} Hz/s'}"
                  f"{'' if s is None else ('  FALLING ok' if s < 0 else '  RISING reject')}")
        # A GATE, NOT A RANKER — corrected 2026-08-10 the same day it shipped.
        # The first cut picked the STEEPEST fall and called it "most settled".
        # That was my invention: the recipe's rule is only "reject rising
        # finals". Joe then preferred a -24 Hz/s take over a -36 one, which the
        # steepest-wins rule had ranked below it. Steeper is not better.
        # What IS real is the flat end of the range: the take that measured
        # -3 Hz/s is the delivery he heard as flat in the browser. So the gate
        # rejects BOTH directions of failure — rising, and barely-falling — and
        # among the takes that pass it does NOT pretend to rank character.
        # Default is the median passer (avoids both extremes); --keep-all hands
        # the ear the whole set, which is the honest instrument for the rest.
        passing = [(p, s) for p, s in takes if s is not None and s <= -FLAT_FLOOR]
        if not passing:
            print(f"  NO TAKE PASSED (need a fall steeper than {FLAT_FLOOR} Hz/s) "
                  "— nothing shipped.")
            print("  Shipping the best available when none clears the bar is how a corpus rots.")
            return 1
        passing.sort(key=lambda r: r[1])
        winner = passing[len(passing) // 2][0]      # median fall, not steepest
        print(f"  {len(passing)}/{len(takes)} passed the gate; shipping the "
              "median fall (steepness is not a quality ranking)")
        os.replace(winner, cached)
        winner = cached
        if not a.keep_all:
            for p, _ in takes:
                if os.path.exists(p):
                    os.remove(p)

    dest = a.out or winner
    if a.out:
        with open(winner, "rb") as s, open(dest, "wb") as d:
            d.write(s.read())
    print(f"  -> {dest}")
    print("  RAW, pre-mastering: the RECIPE ffmpeg chain has not been applied "
          "and must be re-dialled against this engine first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
