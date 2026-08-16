#!/usr/bin/env python3
"""hermes-autonomy-check-selftest.py — fixtures for ops/hermes-autonomy-check.py.

The negatives are the point. A check that has only ever been run against a clean
machine cannot be distinguished from a check that always prints OK.

Run: python3 ops/hermes-autonomy-check-selftest.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "hermes-autonomy-check.py")

CASES = [
    ("no Hermes install is clean", None, 0),
    ("an idle scheduler with no jobs is clean", {"cron/executions.db": "", "cron/ticker_heartbeat": "1"}, 0),
    ("an empty jobs file is clean", {"cron/jobs.json": "{}"}, 0),
    ("a defined job is unattended", {"cron/jobs.json": json.dumps({"nightly": {"schedule": "0 3 * * *"}})}, 1),
    ("several defined jobs are unattended", {"cron/jobs.json": json.dumps([{"a": 1}, {"b": 2}])}, 1),
]


def run_case(name, files, expected):
    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, ".hermes")
        if files is not None:
            for rel, content in files.items():
                path = os.path.join(home, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)
        env = dict(os.environ, HERMES_HOME=home)
        proc = subprocess.run([sys.executable, CHECK], capture_output=True, text=True, env=env)
        ok = proc.returncode == expected
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
        if not ok:
            print(f"       expected exit {expected}, got {proc.returncode}")
            for line in (proc.stdout or proc.stderr or "").splitlines()[:5]:
                print(f"       {line}")
        return ok


def main():
    print("hermes-autonomy-check selftest")
    results = [run_case(*c) for c in CASES]
    passed, total = sum(results), len(results)
    print(f"passed {passed} · failed {total - passed}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
