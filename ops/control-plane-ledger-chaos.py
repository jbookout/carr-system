#!/usr/bin/env python3
"""Run the rollback-only ledger resilience drill against an explicit staging DB.

The fixture lives in ``ops/control-plane-db-gate.py`` and rolls its transaction
back.  This wrapper refuses unless an operator explicitly identifies staging;
it never selects or mutates production by default.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "ops" / "control-plane-db-gate.py"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run rollback-only staging ledger chaos evidence.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    evidence = {"exercise": "control-plane-ledger-chaos-v1", "rollback_only": True,
                "gate": str(GATE), "staging_opt_in": os.environ.get("CARR_CONTROL_PLANE_CHAOS_STAGING") == "1"}
    if args.dry_run:
        evidence["would_run"] = evidence["staging_opt_in"] and bool(os.environ.get("DATABASE_URL"))
        print(json.dumps(evidence, sort_keys=True))
        return 0
    if not evidence["staging_opt_in"] or not os.environ.get("DATABASE_URL"):
        evidence["refusal"] = "requires CARR_CONTROL_PLANE_CHAOS_STAGING=1 and staging DATABASE_URL"
        print(json.dumps(evidence, sort_keys=True))
        return 78
    python = str(VENV_PYTHON) if VENV_PYTHON.is_file() else sys.executable
    proc = subprocess.run([python, str(GATE)], cwd=ROOT, text=True, capture_output=True, timeout=120)
    evidence.update({"returncode": proc.returncode, "passed": proc.returncode == 0,
                     "stdout_tail": proc.stdout[-2000:], "stderr_tail": proc.stderr[-1000:]})
    print(json.dumps(evidence, sort_keys=True))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
