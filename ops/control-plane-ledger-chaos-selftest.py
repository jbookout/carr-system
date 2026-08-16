#!/usr/bin/env python3
"""The staging ledger chaos wrapper must refuse without explicit staging opt-in."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRILL = ROOT / "ops" / "control-plane-ledger-chaos.py"
env = dict(os.environ)
env.pop("CARR_CONTROL_PLANE_CHAOS_STAGING", None)
env.pop("DATABASE_URL", None)
proc = subprocess.run([sys.executable, str(DRILL)], text=True, capture_output=True, env=env, timeout=30)
evidence = json.loads(proc.stdout)
if proc.returncode != 78 or evidence.get("rollback_only") is not True or "refusal" not in evidence:
    raise SystemExit("ledger chaos staging guard failed")
dry = subprocess.run([sys.executable, str(DRILL), "--dry-run"], text=True, capture_output=True, env=env, timeout=30)
if dry.returncode != 0 or json.loads(dry.stdout).get("would_run") is not False:
    raise SystemExit("ledger chaos dry-run guard failed")
print("control-plane ledger chaos selftest — pass")
