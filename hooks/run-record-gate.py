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
    # A TIMEOUT IS NOT A MISSING DEPENDENCY, and conflating the two closed the
    # record layer's only learning mechanism on 2026-08-23. Measured that day:
    # `import psycopg` took 3.83s, three times running, at load average ~395
    # with 27.5 GB of 28.6 GB swap in use. It SUCCEEDED every time -- rc=0. The
    # three-second probe expired first, this function returned 2, and the write
    # was refused with no stderr, so the operator saw only "No stderr output".
    # Every record-layer write behind this wrapper was blocked machine-wide for
    # as long as the Mac stayed loaded, and the thing being blocked was a defect
    # filing about a different gate.
    #
    # Rule 88e9b5eb is the governing one: "not authorized" and "not possible"
    # are different findings and must never be reported as the same one. A probe
    # that could not finish has not established that psycopg is absent; it has
    # established that it could not look.
    #
    # The bootstrap-fault-is-a-closed-gate intent above is UNCHANGED -- a
    # genuine import failure still closes the gate, and so does a probe that
    # times out. What changed is that the budget is no longer tight enough for
    # an ordinary loaded laptop to trip it, and that neither outcome is silent
    # any more. The silence is what made this cost an afternoon to find.
    probe_timeout = float(os.environ.get("CARR_RECORD_GATE_PROBE_TIMEOUT", "30"))
    try:
        ready = subprocess.run([str(python), "-c", "import psycopg"], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=probe_timeout)
    except subprocess.TimeoutExpired:
        print(f"run-record-gate: the psycopg readiness probe did not finish "
              f"within {probe_timeout:g}s, so this gate could not run. That is "
              f"'could not look', not 'psycopg is missing' -- on a heavily "
              f"loaded machine the import alone can take seconds. Raise "
              f"CARR_RECORD_GATE_PROBE_TIMEOUT if this machine is legitimately "
              f"this slow.", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"run-record-gate: could not start the readiness probe ({exc}).",
              file=sys.stderr)
        return 2
    if ready.returncode != 0:
        print("run-record-gate: the repository interpreter cannot import "
              "psycopg, so the canonical record read is impossible and this "
              "gate is closed rather than skipped.", file=sys.stderr)
        return 2
    try:
        os.execve(str(python), [str(python), str(REPO / "hooks" / gate)], env)
    except OSError:
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
