#!/usr/bin/env python3
"""Run record-backed hooks with the repository interpreter, never ambient Python.

Claude invokes hook commands through system Python, which intentionally has no
project dependencies on this Mac.  These gates need psycopg for the canonical
record read, so the interpreter is an explicit, baseline-protected part of the
hook contract.  PATH and environment never choose the interpreter.

A bootstrap fault is a closed gate, not a silent loss of context -- and a closed
gate always says which fault closed it.  A mute exit 2 reads to the operator as
an unexplained objection to the assistant's work rather than as the launcher
never reaching the gate at all, so every failure path below names itself on
stderr.  Naming the fault does not soften it: the exit code is still 2.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOWED = frozenset({"drift-claim-gate.py", "drift-assertion-gate.py"})
# Deliberately short: the probe runs on every Stop.  Under heavy concurrent load
# on this Mac it can be exceeded by a healthy interpreter, which closes the gate.
# That tradeoff is a ruling for Joe, not a number to drift upward quietly.
PROBE_TIMEOUT_SECONDS = 3


def closed(reason: str) -> int:
    """Close the gate, on the record.  Never returns anything but 2."""
    print(f"run-record-gate: gate not run: {reason}", file=sys.stderr)
    return 2


def main() -> int:
    if len(sys.argv) != 2:
        return closed(f"expected exactly one gate name, got {len(sys.argv) - 1} argument(s)")
    gate = sys.argv[1]
    if gate not in ALLOWED:
        return closed(f"{gate!r} is not an allowed record gate "
                      f"(allowed: {', '.join(sorted(ALLOWED))})")
    python = REPO / ".venv" / "bin" / "python"
    # Never fall back to system Python: it cannot perform the canonical record
    # read.  A bootstrap fault is a closed gate, not a silent loss of context.
    if not os.access(python, os.X_OK):
        return closed(f"repository interpreter {python} is missing or not executable")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    try:
        ready = subprocess.run([str(python), "-c", "import psycopg"], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return closed(f"psycopg readiness probe exceeded {PROBE_TIMEOUT_SECONDS}s "
                      f"(a loaded machine can do this to a healthy interpreter)")
    except OSError as exc:
        return closed(f"could not run the psycopg readiness probe: {exc}")
    if ready.returncode != 0:
        detail = (ready.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        tail = f": {detail[-1][:200]}" if detail else ""
        return closed(f"psycopg readiness probe failed with rc={ready.returncode}{tail}")
    try:
        os.execve(str(python), [str(python), str(REPO / "hooks" / gate)], env)
    except OSError as exc:
        return closed(f"could not exec {gate}: {exc}")
    return closed(f"exec of {gate} returned without replacing the process")


if __name__ == "__main__":
    raise SystemExit(main())
