#!/usr/bin/env python3
"""
sync-map-retirement-selftest.py — fixtures for drop_retired_entries() in
bin/sync-enforcement-map.py, the half of the hourly sync that handles a RULE
BEING RETIRED.

WHY THIS FILE EXISTS. The removal path shipped without a test. That is backwards
under rule e65efc68 (write the test before the thing, and especially before a
gate), and it matters more here than almost anywhere else in the repo, because
this function:

  · runs UNATTENDED, hourly, with nobody reading the diff;
  · performs REGEX AND SCAN SURGERY, not a json.dump, deliberately — a full
    re-render would touch every hand-authored line and bury the real change;
  · edits ops/config/rule-enforcement-map.json, the file that decides what the
    gates enforce and whose hash the gate baseline pins.

A silent mistake here does not look like a crash. It looks like a map that
still parses, still passes its hash check, and quietly stops naming a control.

WHAT IT MUST DO, one assertion per line of the real function:

  1. Remove the id from a category_overrides list in all THREE positions —
     middle, last, and sole element (the sole case must leave `[]`, never a
     dangling comma or an empty slot).
  2. Remove the rule's rule_controls member, taking exactly one separating
     comma, whether the member is in the middle or is the LAST one in the block.
  3. Leave a map that still PARSES. This is the invariant that catches every
     comma bug at once, so every case below re-parses the result.
  4. Handle BOTH shapes the docstring names: entries written on one line, as
     the live map does, and pretty-printed over several, as the fixtures do.
     A line-shaped fix would pass one and corrupt the other.
  5. Touch nothing else — other rules keep their entries and their order.
  6. Refuse (return None) on anything that is not a plain 8-hex rule id, rather
     than run an unbounded substitution over the file.
  7. Return the text unchanged when nothing was retired.

RUNNING IT. No database, no network, no vault, no production access:

    .venv/bin/python ops/sync-map-retirement-selftest.py
"""

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "sync-enforcement-map.py"

passed = 0
failures: list[str] = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def load():
    spec = importlib.util.spec_from_file_location("sync_enforcement_map", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def one_line_map():
    """The shape the LIVE map uses: one rule_controls entry per line."""
    return (
        '{\n'
        '  "default_category": "judgment_advisory",\n'
        '  "rule_controls": {\n'
        '    "aaaaaaaa": {"category": "session_task_rail", "control": "conduct_stop"},\n'
        '    "bbbbbbbb": {"category": "session_task_rail", "control": "conduct_stop"},\n'
        '    "cccccccc": {"category": "judgment_advisory", "control": "none"}\n'
        '  },\n'
        '  "active_rule_ids": {\n'
        '    "joe": ["aaaaaaaa", "cccccccc"]\n'
        '  },\n'
        '  "category_overrides": {\n'
        '    "session_task_rail": ["aaaaaaaa", "bbbbbbbb", "cccccccc"],\n'
        '    "solo_lane": ["bbbbbbbb"],\n'
        '    "tail_lane": ["cccccccc", "bbbbbbbb"]\n'
        '  }\n'
        '}\n'
    )


def pretty_map():
    """The shape the FIXTURES use: rule_controls pretty-printed over lines.
    end_of_object() exists precisely so both shapes work; this proves it."""
    return json.dumps({
        "default_category": "judgment_advisory",
        "rule_controls": {
            "aaaaaaaa": {"category": "session_task_rail", "control": "conduct_stop"},
            "bbbbbbbb": {"category": "session_task_rail", "control": "conduct_stop"},
            "cccccccc": {"category": "judgment_advisory", "control": "none"},
        },
        "active_rule_ids": {"joe": ["aaaaaaaa", "cccccccc"]},
        "category_overrides": {
            "session_task_rail": ["aaaaaaaa", "bbbbbbbb", "cccccccc"],
            "solo_lane": ["bbbbbbbb"],
            "tail_lane": ["cccccccc", "bbbbbbbb"],
        },
    }, indent=2) + "\n"


def parsed(text):
    """Parse, or fail loudly with the text — a comma bug shows up here first."""
    return json.loads(text)


print("\nbin/sync-enforcement-map.py drop_retired_entries() — the retirement half")

if not SCRIPT.exists():
    print(f"  FAIL  the script does not exist at {SCRIPT}")
    sys.exit(1)

mod = load()
drop = mod.drop_retired_entries

for shape_name, source in (("one-line", one_line_map()), ("pretty", pretty_map())):
    print(f"\n  --- {shape_name} rule_controls shape ---")

    # ── a MIDDLE member, present in three category lists ────────────────────
    out = drop(source, ["bbbbbbbb"])
    check(f"[{shape_name}] returns text, not None", out is not None)
    if out is not None:
        try:
            d = parsed(out)
            check(f"[{shape_name}] the map still parses", True)
            check(f"[{shape_name}] its rule_controls entry is gone",
                  "bbbbbbbb" not in d["rule_controls"])
            check(f"[{shape_name}] it leaves a mid-list category clean",
                  d["category_overrides"]["session_task_rail"] == ["aaaaaaaa", "cccccccc"],
                  repr(d["category_overrides"]["session_task_rail"]))
            check(f"[{shape_name}] a sole-element list becomes empty, not broken",
                  d["category_overrides"]["solo_lane"] == [],
                  repr(d["category_overrides"]["solo_lane"]))
            check(f"[{shape_name}] a LAST-element list drops no comma",
                  d["category_overrides"]["tail_lane"] == ["cccccccc"],
                  repr(d["category_overrides"]["tail_lane"]))
            check(f"[{shape_name}] every other rule is untouched",
                  set(d["rule_controls"]) == {"aaaaaaaa", "cccccccc"}
                  and d["rule_controls"]["aaaaaaaa"]["control"] == "conduct_stop",
                  repr(sorted(d["rule_controls"])))
        except ValueError as exc:
            check(f"[{shape_name}] the map still parses", False, str(exc))

    # ── the LAST member of rule_controls, the dangling-comma trap ───────────
    out = drop(source, ["cccccccc"])
    if out is not None:
        try:
            d = parsed(out)
            check(f"[{shape_name}] dropping the LAST rule_controls member parses",
                  True)
            check(f"[{shape_name}] and that member is gone",
                  "cccccccc" not in d["rule_controls"], repr(sorted(d["rule_controls"])))
        except ValueError as exc:
            check(f"[{shape_name}] dropping the LAST rule_controls member parses",
                  False, str(exc))
    else:
        check(f"[{shape_name}] dropping the LAST rule_controls member parses",
              False, "returned None")

    # ── several retirements in one run ─────────────────────────────────────
    out = drop(source, ["aaaaaaaa", "bbbbbbbb"])
    if out is not None:
        try:
            d = parsed(out)
            check(f"[{shape_name}] two retirements at once still parse", True)
            check(f"[{shape_name}] and both are gone everywhere",
                  set(d["rule_controls"]) == {"cccccccc"}
                  and d["category_overrides"]["session_task_rail"] == ["cccccccc"],
                  repr(d["category_overrides"]["session_task_rail"]))
        except ValueError as exc:
            check(f"[{shape_name}] two retirements at once still parse", False, str(exc))
    else:
        check(f"[{shape_name}] two retirements at once still parse", False, "returned None")

print("\n  --- refusals and no-ops ---")

# An unbounded substitution over this file is the one thing worse than skipping.
for bogus in ("not-a-rule", "aaaaaaa", "aaaaaaaaa", "AAAAAAAA", ".*", "a225b744x"):
    check(f"refuses the malformed id {bogus!r}",
          drop(one_line_map(), [bogus]) is None)

unchanged = one_line_map()
check("an empty retirement list returns the text unchanged",
      drop(unchanged, []) == unchanged)

# An id the map never mentions must be a clean no-op, not a corruption: the
# render can legitimately drop an id this map never classified.
out = drop(one_line_map(), ["deadbeef"])
check("an unknown-but-well-formed id is a safe no-op",
      out is not None and parsed(out) == parsed(one_line_map()))

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("SYNC MAP RETIREMENT SELFTEST PASSED: a retirement is erased from all "
      "three homes, in both file shapes, and the map still parses every time.")
