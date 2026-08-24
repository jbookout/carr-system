#!/usr/bin/env python3
"""Run record-backed hooks with the repository interpreter, never ambient Python.

Claude invokes hook commands through system Python, which intentionally has no
project dependencies on this Mac.  These gates need psycopg for the canonical
record read, so the interpreter is an explicit, baseline-protected part of the
hook contract.  PATH and environment never choose the interpreter.

EVERY REFUSAL HERE NAMES ITS PRECONDITION.  A bootstrap fault is a closed gate,
not a silent loss of context -- but for one afternoon it was a SILENT closed
gate, and that is what cost the time.  See closed() below.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOWED = frozenset({"drift-claim-gate.py", "drift-assertion-gate.py"})

# WHY THIS IS 12 AND NOT 3, AND NOT 30 EITHER.
#
# A TIMEOUT IS NOT A MISSING DEPENDENCY, and conflating the two closed the
# record layer's only learning mechanism on 2026-08-23. Measured that day:
# `import psycopg` took 3.83s, three times running, at load average ~395 with
# 27.5 GB of 28.6 GB swap in use. It SUCCEEDED every time -- rc=0. A second
# measurement the same evening at load ~282 put the same import at 5.4-6.3s,
# against ~0.5s of bare interpreter startup. The three-second probe expired
# first, this function returned 2, and the write was refused with no stderr, so
# the operator saw only "No stderr output". Every record-layer write behind this
# wrapper was blocked machine-wide for as long as the Mac stayed loaded, and the
# thing being blocked was a defect filing about a different gate.
#
# Rule 88e9b5eb is the governing one: "not authorized" and "not possible" are
# different findings and must never be reported as the same one. A probe that
# could not finish has not established that psycopg is absent; it has
# established that it could not look.
#
# The ceiling is not arbitrary either. ops/config/hooks.json gives this launcher
# 10s as the claim gate's PreToolUse hook and 20s as the assertion gate's Stop
# hook, and the harness kills the process at that budget. A probe budget at or
# above the outer timeout can never actually expire -- the harness reaps it
# first, and a reaped hook prints nothing, which is the original silent failure
# wearing a different hat. 12s is ~2x the worst measured import and still leaves
# the exec'd gate room inside the 20s Stop budget, so the message below can be
# reached and printed rather than being theoretical.
#
# The bootstrap-fault-is-a-closed-gate intent is UNCHANGED -- a genuine import
# failure still closes the gate, and so does a probe that times out. What
# changed is that the budget is no longer tight enough for an ordinary loaded
# laptop to trip it, and that no outcome here is silent any more.
PROBE_TIMEOUT_ENV = "CARR_RECORD_GATE_PROBE_TIMEOUT"
PROBE_TIMEOUT_DEFAULT = 12.0


def closed(reason: str) -> int:
    """Close the gate, out loud.  Never returns anything but 2.

    The 2026-08-23 fault was not that the gate closed; closing was correct. It
    was that it closed mutely, so a thrashing machine and a real drift ruling
    were the same observation from outside -- three consecutive Stop hooks
    reporting only "No stderr output". A closed gate that says why is still a
    closed gate, so every `return 2` in this file goes through here.
    """
    print(f"run-record-gate: gate not run: {reason}", file=sys.stderr)
    return 2


def probe_budget():
    """Seconds allowed for the readiness probe, or (None, reason) if overridden badly."""
    raw = os.environ.get(PROBE_TIMEOUT_ENV)
    if raw is None or not raw.strip():
        return PROBE_TIMEOUT_DEFAULT, None
    try:
        value = float(raw)
    except ValueError:
        return None, (f"{PROBE_TIMEOUT_ENV}={raw!r} is not a number of seconds; "
                      "unset it to use the default")
    if value <= 0:
        return None, (f"{PROBE_TIMEOUT_ENV}={raw!r} must be greater than zero; "
                      "unset it to use the default")
    return value, None


def main() -> int:
    if len(sys.argv) != 2:
        return closed(f"expected exactly one gate name, got {len(sys.argv) - 1} "
                      f"argument(s): {sys.argv[1:]!r}")
    gate = sys.argv[1]
    if gate not in ALLOWED:
        return closed(f"{gate!r} is not a record-backed gate "
                      f"(allowed: {', '.join(sorted(ALLOWED))})")
    python = REPO / ".venv" / "bin" / "python"
    # Never fall back to system Python: it cannot perform the canonical record
    # read.  A bootstrap fault is a closed gate, not a silent loss of context.
    if not os.access(python, os.X_OK):
        return closed(f"repository interpreter {python} is missing or not "
                      "executable, so the venv is not bootstrapped; system "
                      "Python cannot perform the canonical record read")
    budget, bad_override = probe_budget()
    if bad_override is not None:
        return closed(bad_override)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    try:
        ready = subprocess.run([str(python), "-c", "import psycopg"], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, timeout=budget)
    except subprocess.TimeoutExpired:
        return closed(f"the psycopg readiness probe did not finish within "
                      f"{budget:g}s, so this gate could not run. That is 'could "
                      f"not look', not 'psycopg is missing' -- on this Mac a high "
                      f"load average with idle CPU means memory thrash and the "
                      f"import alone takes seconds. NO RECORD WAS READ, so this is "
                      f"not a finding about the turn. Raise {PROBE_TIMEOUT_ENV} if "
                      f"this machine is legitimately this slow")
    except OSError as exc:
        return closed(f"could not start the psycopg readiness probe with {python}: {exc}")
    if ready.returncode != 0:
        # The probe's own stderr is the difference between "psycopg is absent"
        # and "psycopg is present and broken", which are different repairs.
        detail = (ready.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        tail = f": {detail[-1][:200]}" if detail else ""
        return closed(f"the repository interpreter cannot import psycopg "
                      f"(probe exited {ready.returncode}), so the canonical record "
                      f"read is impossible and this gate is closed rather than "
                      f"skipped{tail}")
    try:
        os.execve(str(python), [str(python), str(REPO / "hooks" / gate)], env)
    except OSError as exc:
        return closed(f"could not exec hooks/{gate} with {python}: {exc}")
    return closed(f"exec of hooks/{gate} returned without replacing this process")


if __name__ == "__main__":
    raise SystemExit(main())
