#!/usr/bin/env python3
"""recovery-point-selftest.py — the checks that make ops/recovery-point.py worth
having.

THE ONE THING THIS MUST PROVE. The module exists because a two-state answer
(fresh / stale) produced a four-day false alarm while a twelve-hour-old backup
sat in GitHub. The fix is a THIRD state, and a third state is only worth
anything if it never silently collapses into one of the other two. Both
collapses are failures, in opposite directions:

  · unknown read as FRESH hides a genuinely dead workflow — the worst outcome,
    because it is the one nobody investigates.
  · unknown read as a GAP rebuilds the false alarm one layer up, and trains
    people to ignore the line.

So both directions get their own case, and neither is inferred from the other.

The cases drive assess() and the path readers with fabricated inputs rather
than the real filesystem or the real GitHub API: a check that needs the network
to run is a check that goes amber on a plane, and a check that reads the real
backups/ directory would pass or fail depending on the day it is run.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "recovery-point.py"

# NEVER LET BYTECODE ANSWER FOR SOURCE. Found the hard way on 2026-08-21 while
# mutation-testing this very file: changing `min` to `max` in assess() leaves
# the source the SAME NUMBER OF BYTES, and restoring it with `cp` in the same
# second leaves the mtime unchanged too. Python validates its cache on exactly
# (mtime, size), so the mutated .pyc was still being executed against restored
# source — the checks read red while the file on disk was correct, and the
# obvious conclusion, that the restore had failed, was wrong.
#
# A check that can be answered by a stale artifact is not checking the artifact
# (rule a9ecd5b4). So: drop any cached bytecode before loading, and never write
# any from this process.
sys.dont_write_bytecode = True
importlib.invalidate_caches()
_cached = MODULE_PATH.parent / "__pycache__"
if _cached.is_dir():
    for stale in _cached.glob("recovery-point.*.pyc"):
        stale.unlink()

spec = importlib.util.spec_from_file_location("recovery_point", MODULE_PATH)
if spec is None or spec.loader is None:      # mypy: both are Optional by signature
    raise SystemExit(f"recovery-point-selftest: cannot load {MODULE_PATH}")
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, ok: bool) -> None:
    (PASSED if ok else FAILED).append(label)
    print(f"{'PASS' if ok else 'FAIL'}  {label}")


def present(path: str, age_hours: float) -> dict:
    when = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return {"path": path, "state": "present", "configured": True,
            "at": when.isoformat().replace("+00:00", "Z"),
            "age_hours": age_hours, "detail": "fixture"}


def unknown(path: str) -> dict:
    return {"path": path, "state": "unknown", "detail": "fixture: could not ask"}


def none(path: str, configured: bool = True) -> dict:
    return {"path": path, "state": "none", "configured": configured,
            "detail": "fixture: nothing on record"}


def main() -> int:
    # ── the two collapses, each in its own direction ─────────────────────────
    r = rp.assess([none("local", configured=True), unknown("cloud")])
    check("unknown is NOT reported as fresh (a dead workflow stays visible)",
          r["verdict"] != "ok" and r["age_hours"] is None)
    check("unknown is NOT reported as a gap (the false alarm is not rebuilt)",
          r["verdict"] == "unknown")

    r = rp.assess([unknown("local"), unknown("cloud")])
    check("no path readable at all is unknown, not a gap", r["verdict"] == "unknown")

    r = rp.assess([none("local"), none("cloud")])
    check("every path answering 'nothing on record' IS a real gap",
          r["verdict"] == "gap")

    # ── the newest path wins, whichever one it is ────────────────────────────
    r = rp.assess([present("local", 104.0), present("cloud", 12.0)])
    check("newest across paths wins when cloud is fresher",
          r["verdict"] == "ok" and r["newest_path"] == "cloud" and r["age_hours"] == 12.0)

    r = rp.assess([present("local", 3.0), present("cloud", 30.0)])
    check("newest across paths wins when local is fresher",
          r["verdict"] == "ok" and r["newest_path"] == "local")

    # THE EXACT SHAPE OF THE 2026-08-21 FALSE ALARM, pinned as a regression:
    # a stale local dump beside a fresh cloud run must read OK, not out of
    # contract. This is the case the old one-line shell test got wrong.
    r = rp.assess([present("local", 109.9), present("cloud", 17.5)])
    check("the 2026-08-21 case reads OK, not out of contract",
          r["verdict"] == "ok" and r["age_hours"] == 17.5)

    # ── a real gap is still a real gap ───────────────────────────────────────
    r = rp.assess([present("local", 50.0), present("cloud", 40.0)])
    check("both paths past the objective IS out of contract",
          r["verdict"] == "out_of_contract" and r["age_hours"] == 40.0)

    r = rp.assess([present("cloud", 25.0)], rpo_hours=24)
    check("one hour past the objective still trips it",
          r["verdict"] == "out_of_contract")

    # ── the cloud reader only ever counts SUCCESSES ──────────────────────────
    # A workflow that ran and failed produced no backup, so counting its
    # timestamp would report a recovery point that does not exist — the same
    # class as the 2026-08-07 corrupt 200-byte dump that pinged OK.
    src = MODULE_PATH.read_text(encoding="utf-8")
    check("cloud reader asks the API for successful runs only",
          '"--status", "success"' in src)

    # ── degradation is real, not claimed ─────────────────────────────────────
    # Point the reader at a PATH with no gh on it. It must return unknown
    # rather than raising, because an unattended 2am chain cannot handle a
    # traceback and must not be stopped by a missing optional tool.
    with tempfile.TemporaryDirectory() as empty:
        saved = os.environ.get("PATH")
        try:
            os.environ["PATH"] = empty
            out = rp.cloud_path()
        finally:
            os.environ["PATH"] = saved if saved is not None else ""
    check("cloud reader degrades to unknown when gh is absent, without raising",
          out["state"] == "unknown" and "gh" in out["detail"])

    # ── local: absent credential is not a failure on this machine ────────────
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "backups"), exist_ok=True)
        saved = os.environ.pop("CARR_DB_BACKUP_URL", None)
        try:
            out = rp.local_path(tmp)
        finally:
            if saved is not None:
                os.environ["CARR_DB_BACKUP_URL"] = saved
    check("local with no dumps and no credential reports state+configured, not a crash",
          out["state"] == "none" and out["configured"] is False)

    # ── exit codes are the contract the chain branches on ────────────────────
    codes = {"ok": 0, "out_of_contract": 1, "gap": 1, "unknown": 2}
    check("exit-code map covers every verdict assess() can return",
          set(codes) == {"ok", "out_of_contract", "gap", "unknown"}
          and all(f'"{k}"' in src for k in codes))

    # ── the module actually runs end to end ──────────────────────────────────
    proc = subprocess.run([sys.executable, str(MODULE_PATH), "--hours"],
                          capture_output=True, text=True, timeout=60)
    printed = (proc.stdout or "").strip()
    check("--hours prints a whole number or the word 'unknown'",
          printed == "unknown" or printed.isdigit())
    check("--hours exits with one of the three contract codes",
          proc.returncode in (0, 1, 2))

    print(f"\nrecovery-point-selftest: {len(PASSED)}/{len(PASSED) + len(FAILED)} passed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
