#!/usr/bin/env python3
"""
delta-resend-selftest.py — fixtures for the delta-resend half of
hooks/conduct-stop-gate.py, written before it (rule e65efc68).

THE RULE, 1d50a3bb, in Joe's words on 2026-08-13: "why did you just say the
same thing twice in a row? I feel like you're just wasting my tokens on
purpose." He had read a ~40-line report, the gate blocked the turn asking for
one missing verification, and he got the same ~40 lines back with five new
lines on top. The gate asked for the verification. It did not ask for the
report again.

WHY THIS COMPOUNDS, and why care is not enough: the Stop gates fire on long
working turns, which are exactly the turns whose messages are longest. So the
naive response re-sends the biggest possible message at the worst possible
moment, and does it again on the next block.

WHAT THE CHECK DOES. When the conduct gate blocks a turn it remembers the
message it blocked. On the next Stop for that session it compares the new
reply against the remembered one, and if the new reply is mostly sentences Joe
has already read, it blocks once more asking for the delta alone.

THE LOOP SAFETY IS THE WHOLE DESIGN, and it is why this file exists before the
code. This gate's oldest and most important promise is that it never wedges a
session: `stop_hook_active` short-circuits it today for exactly that reason.
The delta check deliberately fires AT MOST ONCE per blocked message — once it
has spoken, the remembered message is marked spent and the same turn can never
be blocked for repetition again, whatever the session sends next. A gate that
can refuse a turn twice for the same reason is one bad regex away from a
session nobody can close.

WHAT MUST STAY TRUE:
  1. A resend that mostly repeats the blocked message is blocked once.
  2. A resend that carries the delta and a short pointer passes.
  3. A first message, with nothing remembered, is never delta-blocked.
  4. A second resend after a delta block always passes — no loops, ever.
  5. Genuinely new content passes even when it is long.
  6. A missing, empty or corrupt memory file fails OPEN.

RUNNING IT. No database, no network, no production access:

    .venv/bin/python ops/delta-resend-selftest.py
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "hooks" / "conduct-stop-gate.py"

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
    spec = importlib.util.spec_from_file_location("conduct_stop_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


REPORT = (
    "The rebase finished cleanly and every selftest passes.\n"
    "The branch is ready to push once the checkout reconciles.\n"
    "Three gates were blessed in the same commit as their change.\n"
    "The enforcement map checker reports parity exact.\n"
    "Nothing else is outstanding on this branch.\n"
)

# What the gate actually asked for: one missing verification, nothing else.
DELTA_ONLY = (
    "Verified: ops/ci.sh passes all nine classes on the merge result.\n"
    "Everything else is as reported above.\n"
)

# The failure the rule is named for — the whole report again, fix on top.
REPEAT_WITH_FIX = DELTA_ONLY + REPORT

NEW_WORK = (
    "Found a second defect while verifying: the baseline check in CI never\n"
    "failed because the script it calls always exits zero.\n"
    "Built a strict mode, thirteen cases, and landed it.\n"
    "The canonical checkout now boots clean for the first time today.\n"
)


mod = load()

print("\nhooks/conduct-stop-gate.py — the resend carries the delta (1d50a3bb)")

for required in ("remember_blocked", "repeats_blocked"):
    if not hasattr(mod, required):
        print(f"  FAIL  the gate has no {required}() yet")
        print("\n1 check(s) failed: not implemented")
        sys.exit(1)

with tempfile.TemporaryDirectory() as tmp:
    def fresh(session="s1"):
        """A clean memory dir per case, so no case inherits another's state."""
        d = Path(tmp) / session
        d.mkdir(exist_ok=True)
        return str(d)

    # ── 1. the 2026-08-13 failure itself ────────────────────────────────────
    home = fresh("repeat")
    mod.remember_blocked(REPORT, "sess-repeat", home)
    repeats, ratio = mod.repeats_blocked(REPEAT_WITH_FIX, "sess-repeat", home)
    check("a resend that repeats the blocked report is caught", repeats,
          f"overlap={ratio:.2f}")

    # ── 2. the correct behaviour passes ─────────────────────────────────────
    home = fresh("delta")
    mod.remember_blocked(REPORT, "sess-delta", home)
    repeats, ratio = mod.repeats_blocked(DELTA_ONLY, "sess-delta", home)
    check("a delta plus a one-line pointer passes", not repeats,
          f"overlap={ratio:.2f}")

    # ── 3. nothing remembered — never fire ──────────────────────────────────
    home = fresh("first")
    repeats, _ = mod.repeats_blocked(REPORT, "sess-first", home)
    check("a first message with nothing remembered is never delta-blocked",
          not repeats)

    # ── 4. AT MOST ONCE. This is the loop guard and the reason for the file ──
    home = fresh("once")
    mod.remember_blocked(REPORT, "sess-once", home)
    first, _ = mod.repeats_blocked(REPEAT_WITH_FIX, "sess-once", home)
    second, _ = mod.repeats_blocked(REPEAT_WITH_FIX, "sess-once", home)
    third, _ = mod.repeats_blocked(REPORT, "sess-once", home)
    check("the delta check fires on the first repeat", first)
    check("and NEVER again for the same blocked message", not second and not third,
          f"second={second} third={third}")

    # ── 5. long but genuinely new content is not repetition ─────────────────
    home = fresh("new")
    mod.remember_blocked(REPORT, "sess-new", home)
    repeats, ratio = mod.repeats_blocked(NEW_WORK, "sess-new", home)
    check("long but genuinely new content passes", not repeats,
          f"overlap={ratio:.2f}")

    # ── 6. sessions do not bleed into each other ────────────────────────────
    home = fresh("isolate")
    mod.remember_blocked(REPORT, "sess-a", home)
    repeats, _ = mod.repeats_blocked(REPEAT_WITH_FIX, "sess-b", home)
    check("one session's blocked message cannot block another's turn",
          not repeats)

    # ── 7. fail OPEN on anything unreadable ─────────────────────────────────
    home = fresh("broken")
    Path(home, "sess-broken.json").write_text("{ not json at all")
    repeats, _ = mod.repeats_blocked(REPORT, "sess-broken", home)
    check("a corrupt memory file fails open", not repeats)
    repeats, _ = mod.repeats_blocked(REPORT, "sess-missing", "/nonexistent-dir")
    check("a missing memory directory fails open", not repeats)
    repeats, _ = mod.repeats_blocked("", "sess-repeat", fresh("empty"))
    check("an empty reply is never delta-blocked", not repeats)

    # ── 8. remembering must never raise into the caller ─────────────────────
    try:
        mod.remember_blocked(REPORT, "sess-x", "/nonexistent-dir/deeper")
        check("remembering into an unwritable path never raises", True)
    except Exception as exc:
        check("remembering into an unwritable path never raises", False, str(exc))

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("DELTA RESEND SELFTEST PASSED: a repeated message is caught once, a delta "
      "passes, and the check can never block the same turn twice.")
