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

# `files` are laid down under the fixture's .hermes; `gateway` is the machine
# state the check would otherwise read for real — a list of LaunchAgents plist
# names, and the lines a `launchctl list` would print. Both halves are fixtured
# now, so these cases assert what the CHECK does rather than what THIS MACHINE
# happens to have installed.
CASES = [
    ("no Hermes install is clean", None, None, 0),
    ("an idle scheduler with no jobs is clean", {"cron/executions.db": "", "cron/ticker_heartbeat": "1"}, None, 0),
    ("an empty jobs file is clean", {"cron/jobs.json": "{}"}, None, 0),
    ("a defined job is unattended", {"cron/jobs.json": json.dumps({"nightly": {"schedule": "0 3 * * *"}})}, None, 1),
    ("several defined jobs are unattended", {"cron/jobs.json": json.dumps([{"a": 1}, {"b": 2}])}, None, 1),
    # The gateway half, which had no coverage at all and was silently reading the
    # real machine. These are the states Joe's Mac is actually in today.
    ("an installed launch agent is unattended", {"cron/jobs.json": "{}"},
     {"agents": ["ai.hermes.gateway.plist"], "launchctl": ""}, 1),
    ("a running gateway service is unattended", {"cron/jobs.json": "{}"},
     {"agents": [], "launchctl": "86661\t1\tai.hermes.gateway\n"}, 1),
    ("both together are unattended", {"cron/jobs.json": "{}"},
     {"agents": ["ai.hermes.gateway.plist"], "launchctl": "86661\t1\tai.hermes.gateway\n"}, 1),
    # The carve-out the check documents: the desktop app Joe opened is a window,
    # not an unattended service, and must NOT count.
    ("the desktop app alone is clean", {"cron/jobs.json": "{}"},
     {"agents": [], "launchctl": "86878\t0\tapplication.com.nousresearch.hermes.202624494.202625507\n"}, 0),
]


def run_case(name, files, gateway, expected):
    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, ".hermes")
        if files is not None:
            for rel, content in files.items():
                path = os.path.join(home, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)
        fixture = os.path.join(tmp, "fixture")
        agents_dir = os.path.join(fixture, "LaunchAgents")
        os.makedirs(agents_dir, exist_ok=True)
        for plist in (gateway or {}).get("agents", []):
            open(os.path.join(agents_dir, plist), "w", encoding="utf-8").close()
        with open(os.path.join(fixture, "launchctl.txt"), "w", encoding="utf-8") as fh:
            fh.write((gateway or {}).get("launchctl", ""))
        env = dict(os.environ, HERMES_HOME=home, CARR_HERMES_CHECK_FIXTURE=fixture)
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
