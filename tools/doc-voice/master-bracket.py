#!/usr/bin/env python3
"""master-bracket.py — bracket the mastering chain against a NEW engine, by ear.

WHY A BRACKET AND NOT A SETTING. RECIPE.md's ffmpeg chain was dialled by Joe over
~30 iterations against CHATTERBOX output, and the recipe is explicit that a new
engine gets the chain RE-DIALLED rather than pasted on. Two of its stages are the
likely offenders on ElevenLabs, and both would be invisible without an A/B:

  PITCH AND TEMPO. asetrate*0.97 + atempo=0.965 pitch Doc down 3% and ease pace
  ~3.5%. But the ElevenLabs speed dial is ALREADY set to Joe's number (just under
  midpoint, adopted after 0:56/1:00/0:58 testing). Applying the chain's tempo on
  top double-slows him — the exact fault he rejected twice today.

  TEXTURE. The 16k round-trip plus trace bit-crush is what makes Doc a clean
  MACHINE rather than a person. ElevenLabs output is already MP3 128kbps (lossless
  is Pro+), so it arrives with codec artefacts the Chatterbox path never had.
  Crushing artefacts on top of artefacts is not the same as crushing clean audio.

So the variants below each change ONE of those, and RAW is included as the anchor
because a bracket without the untreated reference is just four opinions.

    master-bracket.py <render.mp3>        # writes variants next to it, prints paths
    master-bracket.py <render.mp3> --play # and plays raw -> A -> B -> C -> D

NOTHING HERE IS ADOPTED. This tool produces candidates; the chain in RECIPE.md is
only edited when Joe picks one, because those numbers are his ear's, not a
default anyone may quietly change.
"""
import argparse
import os
import subprocess
import sys

# The frozen chain, split so a variant can drop one stage without retyping it.
PITCH_TEMPO = "asetrate=24000*0.97,aresample=24000,atempo=0.965"
BAND = "highpass=f=90,lowpass=f=7400"
MACHINE = "aresample=16000,aresample=24000,acrusher=bits=14:mode=log:mix=0.08"
MACHINE_LIGHT = "aresample=16000,aresample=24000"          # round-trip, no crush
CHEST = "equalizer=f=185:t=q:w=1.4:g=6.5"
CHEST_LIGHT = "equalizer=f=185:t=q:w=1.4:g=4"
PRESENCE = "equalizer=f=4400:t=q:w=1.2:g=3"
PUNCH = "acompressor=threshold=-16dB:ratio=2.4:attack=8:release=115:makeup=2dB"
ROOM = "aecho=0.75:0.23:21|36:0.10|0.07"
LIMIT = "alimiter=limit=0.9"

VARIANTS = [
    ("A-frozen-verbatim",
     "the RECIPE chain exactly as dialled for Chatterbox — the thing the recipe "
     "says NOT to assume is right, included so the assumption is testable",
     [PITCH_TEMPO, BAND, MACHINE, CHEST, PRESENCE, PUNCH, ROOM, LIMIT]),

    ("B-no-pitch-tempo",
     "same, minus the 3% pitch-down and 3.5% slow — because the engine's own "
     "speed dial already carries Joe's pace",
     [BAND, MACHINE, CHEST, PRESENCE, PUNCH, ROOM, LIMIT]),

    ("C-lighter-chest",
     "B with the chest lift eased from +6.5dB to +4dB at 185Hz — 128kbps MP3 "
     "has less clean low end to lift than Chatterbox wav did",
     [BAND, MACHINE, CHEST_LIGHT, PRESENCE, PUNCH, ROOM, LIMIT]),

    ("D-no-crush",
     "B with the bit-crush removed, keeping the 16k round-trip — tests whether "
     "the machine texture is already supplied by the codec",
     [BAND, MACHINE_LIGHT, CHEST, PRESENCE, PUNCH, ROOM, LIMIT]),
]


def apply(src, chain, dest):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src,
                    "-af", ",".join(chain), dest], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--play", action="store_true")
    a = ap.parse_args()
    src = os.path.expanduser(a.src)
    if not os.path.exists(src):
        sys.exit(f"no such file: {src}")
    out = os.path.join(os.path.dirname(src) or ".", "mastering-bracket")
    os.makedirs(out, exist_ok=True)

    made = []
    for name, why, chain in VARIANTS:
        dest = os.path.join(out, name + ".mp3")
        try:
            apply(src, chain, dest)
            made.append((name, dest, why))
            print(f"  ok    {name:20} {why}")
        except subprocess.CalledProcessError as e:
            print(f"  FAIL  {name:20} ffmpeg exit {e.returncode}")

    if a.play:
        print("\n  playing RAW first as the anchor, then A B C D")
        subprocess.run(["afplay", src])
        for name, dest, _ in made:
            print(f"  {name}")
            subprocess.run(["afplay", dest])
    print(f"\n  variants in {out}")
    print("  NOTHING ADOPTED — RECIPE.md changes only when Joe picks one.")


if __name__ == "__main__":
    main()
