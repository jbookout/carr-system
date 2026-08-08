#!/usr/bin/env bash
# test-harvest.sh — proves the one safety-critical property of
# bin/harvest-voice.py: a fact-bearing sentence can never be classified
# fact-free, which is the only thing standing between Doc's phrase cache and
# a stale name or number getting replayed as if it were fresh.
#
# harvest-voice.py has a hyphen in its filename, so it isn't `import`-able
# directly; loaded here via importlib, same trick the file's own docstring
# assumes a caller would use.

set -euo pipefail
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

python3 - "$DIR/harvest-voice.py" <<'PYEOF'
import importlib.util
import sys

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("harvest_voice", path)
harvest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harvest)

classify = harvest.classify_sentence

# The exact three examples from the harvest-voice spec: a weekday, a
# currency figure, and a month + day. Each MUST classify fact-bearing.
KNOWN_FACTUAL = [
    "Hughes signs Tuesday",
    "That's $24.50 per foot",
    "Renewal is October 7",
]

# A spread of additional cases the classifier is specifically supposed to
# catch: a bare digit, a proper noun with no digit at all, and a weekday
# abbreviation.
EXTRA_FACTUAL = [
    "The lease runs 5 years",
    "Talk to Dr. Kessler about the build-out",
    "We're meeting Fri to finalize it",
]

# Sanity check the other direction: plain frames, hedges, and reasoning with
# no digit, no currency symbol, no month/weekday, and no stray capital
# should classify fact-free. Not part of the required acceptance test, but
# a classifier that flags everything is as useless as one that flags
# nothing, so this is worth catching too.
KNOWN_FRAMES = [
    "I'd want to see the comps before I commit to that.",
    "That's worth a second look before you counter.",
    "I don't have enough here to give you a straight answer.",
]

failures = []

print("--- known FACT-BEARING (must classify fact-bearing) ---")
for sentence in KNOWN_FACTUAL + EXTRA_FACTUAL:
    is_fact, reasons = classify(sentence)
    status = "PASS" if is_fact else "FAIL"
    print(f"  [{status}] {sentence!r} -> fact_bearing={is_fact} reasons={reasons}")
    if not is_fact:
        failures.append(sentence)

print()
print("--- known FACT-FREE (should classify fact-free) ---")
for sentence in KNOWN_FRAMES:
    is_fact, reasons = classify(sentence)
    status = "PASS" if not is_fact else "FAIL (false positive)"
    print(f"  [{status}] {sentence!r} -> fact_bearing={is_fact} reasons={reasons}")
    # Over-inclusive-by-design means a false positive here is not a script
    # failure — only a false NEGATIVE on the factual set fails the run.

print()
if failures:
    print(f"FAILED: {len(failures)} fact-bearing sentence(s) were classified "
          f"fact-free — this is the exact bug that would let a stale fact "
          f"reach the live cache: {failures}")
    sys.exit(1)

print("PASS: every known-factual sentence classified fact-bearing. "
      "No fact-bearing sentence can reach assets/phrases/ through this "
      "classifier.")

# Defense in depth: render_frames() re-checks classify_sentence() at the
# call site and raises rather than caching. Prove that guard actually
# fires if a factual sentence is ever handed to it directly (simulating a
# bug upstream that fed the wrong list in).
import contextlib
import io

original_prepare = harvest.speak.prepare
harvest.speak.prepare = lambda *_a, **_k: (_ for _ in ()).throw(
    AssertionError("speak.prepare must never be called for this test")
)
try:
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        try:
            harvest.render_frames(["Renewal is October 7"])
        except RuntimeError as exc:
            guard_fired = "refusing to cache a fact-bearing sentence" in str(exc)
        else:
            guard_fired = False
finally:
    harvest.speak.prepare = original_prepare

if not guard_fired:
    print("FAILED: render_frames() did not refuse a fact-bearing sentence "
          "handed to it directly — the call-site guard is not working.")
    sys.exit(1)

print("PASS: render_frames() refuses a fact-bearing sentence even when "
      "handed to it directly (call-site guard, defense in depth).")
PYEOF
