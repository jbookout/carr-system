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

# FIXTURES DESCRIBE THE WHOLE MACHINE, NOT HALF OF IT. Until 2026-08-19 this file
# isolated HERMES_HOME and nothing else, so the two clean cases below inherited
# the real ~/Library/LaunchAgents and the real launchctl. On Joe's Mac, where the
# Hermes gateway is genuinely installed and running, both "clean" cases failed —
# a fixture reporting the machine rather than itself. That refused every push
# from that Mac, because the pre-push hook runs ops/ci.sh and ops/ci.sh runs
# every selftest. Each case now names its own launch-agent dir and its own
# launchctl stub, so a case is clean or dirty because the case says so.
#
# `agents` lists launch-agent filenames to plant; `launchctl` is the stdout the
# stubbed `launchctl list` prints. Both default to empty, which is the clean
# machine the first three cases mean to describe.
CASES = [
    ("no Hermes install is clean", None, {}, 0),
    ("an idle scheduler with no jobs is clean",
     {"cron/executions.db": "", "cron/ticker_heartbeat": "1"}, {}, 0),
    ("an empty jobs file is clean", {"cron/jobs.json": "{}"}, {}, 0),
    ("a defined job is unattended",
     {"cron/jobs.json": json.dumps({"nightly": {"schedule": "0 3 * * *"}})}, {}, 1),
    ("several defined jobs are unattended",
     {"cron/jobs.json": json.dumps([{"a": 1}, {"b": 2}])}, {}, 1),
    # The gateway half had no fixture of its own at all — it was only ever
    # exercised by whatever the host machine happened to be running, which is
    # the same as not testing it. These two pin both of its findings.
    ("an installed launch agent is unattended",
     {"cron/executions.db": ""}, {"agents": ["ai.hermes.gateway.plist"]}, 1),
    ("a running launchd service is unattended",
     {"cron/executions.db": ""}, {"launchctl": "86661\t1\tai.hermes.gateway\n"}, 1),
    # The desktop app Joe opened is a window, not an unattended service, and the
    # check deliberately ignores `application.` labels. Pin that too, so a later
    # tightening of the regex cannot start failing on an open app.
    ("the desktop app alone is clean",
     {"cron/executions.db": ""},
     {"launchctl": "86878\t0\tapplication.com.nousresearch.hermes.202624494.202625507\n"}, 0),
]

STUB = "#!/bin/sh\nprintf '%s' \"$HERMES_STUB_OUT\"\n"


def run_case(name, files, machine, expected):
    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, ".hermes")
        if files is not None:
            for rel, content in files.items():
                path = os.path.join(home, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)

        agents_dir = os.path.join(tmp, "LaunchAgents")
        os.makedirs(agents_dir, exist_ok=True)
        for agent in machine.get("agents", []):
            with open(os.path.join(agents_dir, agent), "w", encoding="utf-8") as fh:
                fh.write("<plist/>\n")

        stub = os.path.join(tmp, "launchctl-stub")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write(STUB)
        os.chmod(stub, 0o755)

        env = dict(os.environ, HERMES_HOME=home,
                   HERMES_CHECK_LAUNCHAGENTS=agents_dir,
                   HERMES_CHECK_LAUNCHCTL=stub,
                   HERMES_STUB_OUT=machine.get("launchctl", ""))
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
