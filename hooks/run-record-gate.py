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


def main() -> int:
    gate = sys.argv[1] if len(sys.argv) == 2 else ""
    if gate not in ALLOWED:
        return 2
    python = REPO / ".venv" / "bin" / "python"
    # Never fall back to system Python: it cannot perform the canonical record
    # read.  A bootstrap fault is a closed gate, not a silent loss of context.
    if not os.access(python, os.X_OK):
        return 2
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    try:
        ready = subprocess.run([str(python), "-c", "import psycopg"], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return 2
    if ready.returncode != 0:
        return 2
    try:
        os.execve(str(python), [str(python), str(REPO / "hooks" / gate)], env)
    except OSError:
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
