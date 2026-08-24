#!/usr/bin/env python3
"""Run record-backed hooks with the repository interpreter, never ambient Python.

Claude invokes hook commands through system Python, which intentionally has no
project dependencies on this Mac.  These gates need psycopg for the canonical
record read, so the interpreter is an explicit, baseline-protected part of the
hook contract.  PATH and environment never choose the interpreter.

THE DEPENDENCY CHECK RUNS INSIDE THE GATE'S OWN PROCESS, and that is a
correction dated 2026-08-23 rather than a style preference.  This file used to
spawn a SEPARATE `python -c "import psycopg"` probe with a 3-second timeout and
treat expiry as a missing dependency.  Three seconds is a wall-clock number
asserted about a host, not a measurement of the bootstrap: on 2026-08-23, at
load average 263-490 with seven worktrees running ops/ci.sh, that same import
took 6.3-7.3 seconds on a machine where psycopg was installed and healthy.  The
probe therefore closed the gate because the Mac was BUSY, and a closed Stop gate
reopens the turn, which costs a full model turn and adds yet more load — so the
next import was slower still.  One session was reopened at least six times in a
row.  The probe could not tell "psycopg is missing" from "the machine is thrashing"
and called both missing.

Raising the constant is not available and the reason is worth writing down: the
installed commands carry harness timeouts of 10s and 20s (ops/config/hooks.json).
A 30-second internal budget never gets to run — the harness kills the hook first,
and a killed hook is a NON-blocking failure, i.e. it fails OPEN.  Any timeout
large enough to survive a loaded host is large enough to break the policy this
file exists to enforce.  So the deadline is gone entirely instead of retuned, and
the check now costs one interpreter startup rather than two, which is the
difference between fitting inside a 10-second budget and not.

THE PROBE COULD NOT SIMPLY BE DROPPED.  Both gates end in
`except Exception: sys.exit(0)` — deliberately, so no internal error strands a
turn — which means an ImportError raised inside the gate's own record read exits
0 and the gate is silently skipped.  The dependency check has to happen BEFORE
the gate body for a bootstrap fault to stay a closed gate rather than a silent
loss of context.  So it does, in the same process, immediately before the gate
runs.

EVERY CLOSED PATH SAYS WHY.  A Stop hook that reopens a turn with no message
cannot be complied with: the session is handed back its turn, told nothing, and
retries, which reopens it again.  Exit 2 without stderr is a loop, not a gate.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOWED = frozenset({"drift-claim-gate.py", "drift-assertion-gate.py"})

# Runs under the repository interpreter, in the gate's own process, ahead of the
# gate body.  argv[1] is the gate path; runpy keeps __file__ and __name__ what a
# direct `python hooks/<gate>.py` would have set, which is what the gates use to
# locate REPO and their sibling modules.  SystemExit from the gate propagates, so
# its own exit codes are unchanged.
BOOTSTRAP = """
import sys
try:
    import psycopg  # noqa: F401
except BaseException as exc:
    sys.stderr.write(
        "run-record-gate: closed. The repository interpreter cannot import "
        "psycopg (%s: %s), so the canonical record read this gate performs is "
        "impossible. A bootstrap fault is a closed gate, not a silent loss of "
        "context. Fix the interpreter, then retry: "
        "./.venv/bin/python -m pip install psycopg\\n"
        % (type(exc).__name__, exc))
    raise SystemExit(2)
import runpy
runpy.run_path(sys.argv[1], run_name="__main__")
"""


def closed(reason: str) -> int:
    """Exit 2 is a blocked turn; it must always arrive with something to act on."""
    sys.stderr.write(f"run-record-gate: closed. {reason}\n")
    return 2


def main() -> int:
    gate = sys.argv[1] if len(sys.argv) == 2 else ""
    if gate not in ALLOWED:
        got = " ".join(sys.argv[1:]) or "(no argument)"
        return closed(
            f"{got} is not a record gate this launcher may run. Expected exactly "
            f"one of: {', '.join(sorted(ALLOWED))}. The installed command in "
            f"ops/config/hooks.json is wrong or was edited.")
    python = REPO / ".venv" / "bin" / "python"
    # Never fall back to system Python: it cannot perform the canonical record
    # read.  A bootstrap fault is a closed gate, not a silent loss of context.
    if not os.access(python, os.X_OK):
        return closed(
            f"the repository interpreter {python} is missing or not executable, "
            f"and system Python cannot perform the canonical record read. "
            f"Rebuild the venv (./run.sh worktree, or python3 -m venv .venv), "
            f"then retry.")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    try:
        os.execve(str(python), [str(python), "-c", BOOTSTRAP,
                                str(REPO / "hooks" / gate)], env)
    except OSError as exc:
        return closed(
            f"the repository interpreter {python} could not be executed "
            f"({type(exc).__name__}: {exc}).")
    return closed(f"exec of {python} returned instead of replacing this process.")


if __name__ == "__main__":
    raise SystemExit(main())
