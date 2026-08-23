#!/usr/bin/env python3
"""Run record-backed hooks with the repository interpreter, never ambient Python.

Claude invokes hook commands through system Python, which intentionally has no
project dependencies on this Mac.  These gates need psycopg for the canonical
record read, so the interpreter is an explicit, baseline-protected part of the
hook contract.  PATH and environment never choose the interpreter.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOWED = frozenset({"drift-claim-gate.py", "drift-assertion-gate.py"})

# THE READINESS PROBE'S CEILING, and why it is not 3 (defect 6beabc84,
# 2026-08-23).  The probe below exists to prove psycopg imports before the gate
# needs it.  At timeout=3 it began FAILING ON A HEALTHY MACHINE: with eleven
# sessions running, `import psycopg` measured 5.26s / 5.42s / 4.62s against a 3s
# ceiling, so the wrapper timed out three times in one turn and the Stop hook
# blocked with no reason attached.  The gate it fronts was passing the whole
# time.  This Mac's load is memory thrash, not CPU, so the import is slow
# exactly when many sessions are open — the moment the gate is most needed.  A
# ceiling has to clear the thrashing case, not the idle one.
PROBE_TIMEOUT_SECONDS = 30


def _closed(reason: str) -> int:
    """A closed gate that SAYS SO.

    Every path here used to be a bare `return 2`.  The file's own contract is
    "a bootstrap fault is a closed gate, not a silent loss of context" — but a
    blocking exit with an empty stderr is precisely a silent loss of context,
    and the session receiving it cannot tell a real drift finding from a failed
    import.  Naming the fault is what makes the difference actionable.
    """
    sys.stderr.write(f"run-record-gate: {reason}\n")
    return 2


def main() -> int:
    gate = sys.argv[1] if len(sys.argv) == 2 else ""
    if gate not in ALLOWED:
        return _closed(f"refusing to run {gate!r}: not one of {sorted(ALLOWED)}")
    python = REPO / ".venv" / "bin" / "python"
    # Never fall back to system Python: it cannot perform the canonical record
    # read.  A bootstrap fault is a closed gate, not a silent loss of context.
    if not os.access(python, os.X_OK):
        return _closed(f"repo interpreter missing or not executable: {python}")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    try:
        ready = subprocess.run([str(python), "-c", "import psycopg"], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return _closed(
            f"psycopg import exceeded {PROBE_TIMEOUT_SECONDS}s, so {gate} never ran. "
            "This is a bootstrap timeout, NOT a finding from that gate. On this Mac a "
            "high load average is memory thrash; check `uptime` before reading this as drift.")
    except OSError as exc:
        return _closed(f"could not spawn the repo interpreter for the psycopg probe: {exc}")
    if ready.returncode != 0:
        return _closed(
            f"psycopg is not importable under {python}, so {gate} never ran. "
            "Not a finding from that gate — repair the venv.")
    try:
        os.execve(str(python), [str(python), str(REPO / "hooks" / gate)], env)
    except OSError as exc:
        return _closed(f"exec of {gate} failed: {exc}")
    return _closed(f"exec of {gate} returned, which is unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
