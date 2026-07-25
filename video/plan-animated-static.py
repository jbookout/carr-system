#!/usr/bin/env python3
"""
plan-animated-static.py — choose a CHOREOGRAPHY for an animated static, and
refuse to repeat one we ran recently.

Joe's rule (2026-07-25): "it can't be the same every time. and we can't rely on
this form of content too much or it will get played out fast." The format's whole
advantage is novelty, and a recognizable house template spends that novelty by
about the third post. So the shape of every build is logged, and the planner
picks against the log rather than against a default.

What varies between pieces: the build ORDER, the entrance directions, the step
cadence, the duration, the tilt, and the landing SOUND. What never varies inside
a piece: the sound (one per piece, quiet, airy — the standing CRITIQUE-LOG v3
rule) and the locked camera.

  plan-animated-static.py <layers-dir> <log-file> [--concept KEY] [--sfx NAME]
                          [--avoid N] [--json-out PATH] [--plan-out PATH]
  plan-animated-static.py --list

Writes the AE job JSON and a shell-sourceable plan file. Appends to the log only
when --commit is passed (the driver does that after a successful render, so a
failed build does not burn a concept).
"""
import argparse, json, os, random, sys, datetime
from PIL import Image

AUD = ("/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us"
       "@gmail.com/My Drive/CARR AI/Marketing/Brand Assets/Audio/library/sfx")

# Landing sounds. Pool is deliberately narrow: short, soft, and airy. Anything
# with a tail or a thump reads as a cheap transition and was the v3 failure.
# Volume is per-sound because they are not mastered to the same level; the
# target is "you notice it stopped", never "you notice the sound".
SOUNDS = [
    ("tick-ui-soft",   "tick-ui-soft_modern-tech-select.wav", 0.30),
    ("select-click",   "click-ui_select-click.wav",           0.26),
    ("shutter",        "camera-shutter-click.mp3",            0.22),
    ("soft-air",       "whoosh-soft-air_air-woosh.wav",       0.28),
    ("small-sweep",    "fast-small-sweep-transition.mp3",     0.24),
]

# Each concept is a genuinely different read, not a cosmetic tweak. `order` is a
# function of the non-plate layer indices; `direction` is a function of the slot
# a layer lands in. Cadence and duration move too, because timing is as legible
# as geometry.
CONCEPTS = [
    {
        "key": "reading-order",
        "desc": "top to bottom in reading order, sides alternating",
        "order": lambda idx, hero: list(idx),
        "dir": lambda slot, n, i, hero: ["left", "right"][slot % 2] if i in hero else "below",
        "step": 3, "dur": 6.0, "tail": 1.6, "tilt": 1.6,
    },
    {
        "key": "outside-in",
        "desc": "first and last elements land first, converging on the middle",
        "order": lambda idx, hero: [x for pair in zip(idx, reversed(idx)) for x in pair][:len(idx)],
        "dir": lambda slot, n, i, hero: "above" if slot % 2 == 0 else "below",
        "step": 3, "dur": 6.5, "tail": 1.8, "tilt": 2.0,
    },
    {
        "key": "hero-last",
        "desc": "supporting elements build first, the headline lands last and alone",
        "order": lambda idx, hero: [i for i in idx if i not in hero] + [i for i in idx if i in hero],
        "dir": lambda slot, n, i, hero: "scale" if i in hero else "below",
        "step": 4, "dur": 6.5, "tail": 2.0, "tilt": 1.2,
    },
    {
        "key": "bottom-up",
        "desc": "builds upward from the byline, headline arrives near the end",
        "order": lambda idx, hero: list(reversed(idx)),
        "dir": lambda slot, n, i, hero: "below",
        "step": 3, "dur": 6.0, "tail": 1.5, "tilt": 1.4,
    },
    {
        "key": "stack-drop",
        "desc": "everything drops in from above on a tight cadence",
        "order": lambda idx, hero: list(idx),
        "dir": lambda slot, n, i, hero: "above",
        "step": 2, "dur": 5.0, "tail": 1.4, "tilt": 2.4,
    },
    {
        "key": "center-out",
        "desc": "headline first, the rest radiate outward from it",
        "order": lambda idx, hero: _center_out(idx, hero),
        "dir": lambda slot, n, i, hero: "scale" if i in hero else ("above" if i < hero[0] else "below"),
        "step": 3, "dur": 6.0, "tail": 1.7, "tilt": 1.8,
    },
    {
        "key": "slow-reveal",
        "desc": "fewer, heavier landings on a long cadence",
        "order": lambda idx, hero: list(idx),
        "dir": lambda slot, n, i, hero: "scale",
        "step": 6, "dur": 7.0, "tail": 2.2, "tilt": 0.9,
    },
]


def _center_out(idx, hero):
    heroes = [i for i in idx if i in hero]
    if not heroes:
        return list(idx)
    rest = sorted((i for i in idx if i not in hero), key=lambda i: abs(i - heroes[0]))
    return heroes + rest


def ink(path):
    """Alpha bbox center, ink MASS, and canvas size.

    Mass is the count of meaningfully-opaque pixels, not the bbox area. Bbox
    area picks the wrong hero: a three-line body block at 33px spans a wide box
    that is mostly empty, while a 92px bold headline fills a smaller one. Mass
    tracks what actually reads as the biggest element on the card.
    """
    im = Image.open(path).convert("RGBA")
    bb = im.getbbox() or (0, 0, im.width, im.height)
    alpha = im.getchannel("A").point(lambda v: 255 if v > 40 else 0)
    mass = sum(alpha.histogram()[255:])
    return ((bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2), mass, im.size


def read_log(path, n):
    """Last n rows as dicts. Missing or short log is normal, not an error."""
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            p = line.split("\t")
            if len(p) >= 4:
                rows.append({"date": p[0], "name": p[1], "concept": p[2], "sfx": p[3]})
    return rows[-n:] if n else rows


def pick(options, recent, key, seed):
    """Choose among the options that have NOT run in the avoid window.

    Taking the *first* unused option would rotate through the list in a fixed
    order, which is a legible pattern of its own — and with an avoid window
    shorter than the list, the tail options would never run at all. So: collect
    every eligible option and choose one with a seeded draw. Seeded rather than
    random so the same name against the same log state re-plans identically,
    which is what makes a failed render safe to retry.
    """
    used = [r[key] for r in recent]
    fresh = [o for o in options if o not in used]
    if fresh:
        return random.Random(seed).choice(fresh), True
    # everything has run lately — fall back to whatever ran longest ago
    return min(options, key=lambda o: max(i for i, u in enumerate(used) if u == o)), False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("layers", nargs="?")
    ap.add_argument("log", nargs="?")
    ap.add_argument("--concept")
    ap.add_argument("--sfx")
    ap.add_argument("--avoid", type=int, default=0,
                    help="how many recent builds a concept/sound must clear. "
                         "0 (default) sizes the window per pool: all but the last two, "
                         "so nearly every option is excluded and the draw covers the "
                         "whole set instead of cycling through the front of it.")
    ap.add_argument("--json-out")
    ap.add_argument("--plan-out")
    ap.add_argument("--name", default="animated_static")
    ap.add_argument("--out-path", default="")
    ap.add_argument("--commit", action="store_true",
                    help="append this build to the log (driver calls it after a good render)")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        print("concepts:")
        for c in CONCEPTS:
            print(f"  {c['key']:<14} step {c['step']}f  {c['dur']}s   {c['desc']}")
        print("\nlanding sounds:")
        for k, f, v in SOUNDS:
            print(f"  {k:<14} vol {v}   {f}")
        return

    if not a.layers or not a.log:
        ap.error("layers dir and log file are required")

    files = sorted(f for f in os.listdir(a.layers) if f.endswith(".png"))
    if not files:
        sys.exit(f"no PNG layers in {a.layers}")
    paths = [os.path.join(a.layers, f) for f in files]

    meta = [ink(p) for p in paths]
    canvas = meta[0][2]
    for f, m in zip(files, meta):
        if m[2] != canvas:
            sys.exit(f"layer size mismatch: {f} is {m[2]}, canvas is {canvas}")

    n = len(files)
    body = list(range(1, n))              # index 0 is always the background plate
    # the "hero" is the element carrying the most ink — the headline, in practice
    tagged = [i for i in body if files[i][:-4].endswith("_hero")]
    if tagged:                             # explicit _hero suffixes always win
        hero = tagged
    elif body:
        top = max(meta[i][1] for i in body)
        # everything within 60% of the heaviest layer counts as the hero, so a
        # headline split across two PNGs stays one element choreographically
        hero = [i for i in body if meta[i][1] >= top * 0.6]
    else:
        hero = []

    ckeys = [c["key"] for c in CONCEPTS]
    snames = [s[0] for s in SOUNDS]
    c_win = a.avoid or max(1, len(ckeys) - 2)
    s_win = a.avoid or max(1, len(snames) - 2)
    all_rows = read_log(a.log, 0)
    seed = f"{a.name}:{len(all_rows)}"

    if a.concept:
        if a.concept not in ckeys:
            sys.exit(f"unknown concept '{a.concept}'. try --list")
        ckey, fresh_c = a.concept, True
    else:
        ckey, fresh_c = pick(ckeys, all_rows[-c_win:], "concept", seed + ":c")
    if a.sfx:
        if a.sfx not in snames:
            sys.exit(f"unknown sound '{a.sfx}'. try --list")
        skey, fresh_s = a.sfx, True
    else:
        skey, fresh_s = pick(snames, all_rows[-s_win:], "sfx", seed + ":s")

    concept = next(c for c in CONCEPTS if c["key"] == ckey)
    sfile, svol = next((f, v) for k, f, v in SOUNDS if k == skey)

    order = [i for i in concept["order"](body, hero) if i in body]
    for i in body:                         # never silently drop a layer
        if i not in order:
            order.append(i)

    step, dur, tail, tilt = concept["step"], concept["dur"], concept["tail"], concept["tilt"]
    lead = 0.25
    window = dur - tail
    slots = len(order)
    beats = {0: 0.0}
    for slot, i in enumerate(order):
        beats[i] = round(lead + (window - lead) * slot / max(slots, 1), 4)

    layers = []
    for i, (f, p) in enumerate(zip(files, paths)):
        if i == 0:
            d, tl = "plate", 0
        else:
            slot = order.index(i)
            d = concept["dir"](slot, slots, i, hero)
            tl = tilt
            for suf, forced in (("_above", "above"), ("_left", "left"),
                                ("_right", "right"), ("_scale", "scale"), ("_below", "below")):
                if f[:-4].endswith(suf):   # an explicit filename suffix always wins
                    d = forced
        layers.append({"src": p, "name": f[:-4], "beat": beats[i], "from": d,
                       "tilt": tl, "anchor": list(meta[i][0])})

    job = {"compName": f"CARR_{a.name}", "width": canvas[0], "height": canvas[1],
           "fps": 30, "duration": dur, "stepFrames": step,
           "layers": layers, "outPath": a.out_path}

    if a.json_out:
        with open(a.json_out, "w") as fh:
            json.dump(job, fh, indent=2)

    land = round(step * 3 / 30, 4)
    beat_list = " ".join(str(round(beats[i] + land, 4)) for i in order)
    if a.plan_out:
        with open(a.plan_out, "w") as fh:
            def kv(k, v):
                # every value quoted: descriptions and paths carry spaces, and an
                # unquoted one makes `source` try to run the second word
                fh.write(f'{k}="{v}"\n')
            kv("CONCEPT", ckey); kv("CONCEPT_DESC", concept["desc"])
            kv("SFX_KEY", skey); kv("SFX_FILE", os.path.join(AUD, sfile)); kv("SFX_VOL", svol)
            kv("DUR", dur); kv("STEP_FRAMES", step); kv("HOLD_TAIL", tail)
            kv("LANDINGS", beat_list)
            kv("CANVAS", f"{canvas[0]}x{canvas[1]}")
            kv("HERO", ",".join(files[i][:-4] for i in hero))

    if a.commit:
        new = not os.path.isfile(a.log)
        with open(a.log, "a") as fh:
            if new:
                fh.write("# animated-static choreography log. One row per rendered build.\n"
                         "# The planner reads this to avoid repeating a recent shape — that is\n"
                         "# the whole point, so do not clear it casually. Keep it append-only;\n"
                         "# only the most recent handful of rows affect a decision.\n"
                         "# date\tname\tconcept\tsfx\torder\n")
            fh.write(f"{datetime.date.today().isoformat()}\t{a.name}\t{ckey}\t{skey}\t"
                     f"{','.join(files[i][:-4] for i in order)}\n")

    warn = ""
    if not fresh_c:
        warn += "  NOTE: every concept has run within the avoid window; reusing the oldest.\n"
    if not fresh_s:
        warn += "  NOTE: every landing sound has run within the avoid window; reusing the oldest.\n"
    print(f"concept: {ckey} — {concept['desc']}")
    print(f"sound:   {skey} @ {svol}   cadence: {step}f/pose   duration: {dur}s")
    print(f"hero:    {', '.join(files[i][:-4] for i in hero) or '(none)'}")
    print(f"order:   {' -> '.join(files[i][:-4] for i in order)}")
    if warn:
        sys.stderr.write(warn)


if __name__ == "__main__":
    main()
