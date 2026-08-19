#!/usr/bin/env python3
"""hermes-autonomy-check-selftest.py — fixtures for ops/hermes-autonomy-check.py.

The negatives are the point. A check that has only ever been run against a clean
machine cannot be distinguished from a check that always prints OK.

WHY THE FIXTURES GOT THEIR OWN LAUNCH AGENTS DIRECTORY. Setting HERMES_HOME
steered only half the check. The gateway half read the real
~/Library/LaunchAgents and the real launchctl regardless, so every case here
inherited whatever this Mac was running — and the two cases written to prove a
CLEAN machine reads clean failed on Joe's, reporting his live gateway instead of
the fixture they were handed. That is a check testing the machine it runs on.
Each case now gets its own empty agents directory and its own launchctl output,
so what it asserts is the check's logic and nothing else.

WHAT IS ACCEPTED IS ASSERTED HERE TOO. Joe chose the Hermes gateway on
2026-08-18 and the check accepts it BY NAME. The cases below pin both halves of
that: the named gateway reads clean, and a launch agent or service under any
other name still fails. Widening the acceptance to a pattern would turn this
check into decoration, and these cases are what would catch that.

Run: python3 ops/hermes-autonomy-check-selftest.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "hermes-autonomy-check.py")

# Exactly the shape `launchctl list` prints: pid, last exit status, label.
ACCEPTED_SERVICE = "5590\t1\tai.hermes.gateway"
DESKTOP_APP = "87172\t0\tapplication.com.nousresearch.hermes.202624494.202625507"

# name, hermes-home files (None = no install at all), launch agent basenames,
# launchctl lines, expected exit
CASES = [
    ("no Hermes install is clean", None, [], [], 0),
    ("an idle scheduler with no jobs is clean",
     {"cron/executions.db": "", "cron/ticker_heartbeat": "1"}, [], [], 0),
    ("an empty jobs file is clean", {"cron/jobs.json": "{}"}, [], [], 0),
    ("a defined job is unattended",
     {"cron/jobs.json": json.dumps({"nightly": {"schedule": "0 3 * * *"}})}, [], [], 1),
    ("several defined jobs are unattended",
     {"cron/jobs.json": json.dumps([{"a": 1}, {"b": 2}])}, [], [], 1),

    # The acceptance Joe made on 2026-08-18, and its edges.
    ("the gateway Joe accepted is clean, agent and service both",
     {"cron/jobs.json": "{}"}, ["ai.hermes.gateway.plist"], [ACCEPTED_SERVICE], 0),
    ("the desktop app's own window is not a service, accepted or otherwise",
     {"cron/jobs.json": "{}"}, [], [DESKTOP_APP], 0),
    ("a DIFFERENT hermes launch agent is still unattended — acceptance is by name",
     {"cron/jobs.json": "{}"}, ["ai.hermes.secondgateway.plist"], [], 1),
    ("a service under an unaccepted label is still unattended",
     {"cron/jobs.json": "{}"}, [], ["4242\t0\tai.hermes.worker"], 1),
    ("a scheduled job is unattended even when the accepted gateway is present",
     {"cron/jobs.json": json.dumps({"nightly": {"schedule": "0 3 * * *"}})},
     ["ai.hermes.gateway.plist"], [ACCEPTED_SERVICE], 1),
]


def run_case(name, files, agents, launchctl_lines, expected):
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
        for plist in agents:
            with open(os.path.join(agents_dir, plist), "w", encoding="utf-8") as fh:
                fh.write("<plist/>")

        launchctl_out = os.path.join(tmp, "launchctl.txt")
        with open(launchctl_out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(launchctl_lines) + ("\n" if launchctl_lines else ""))

        env = dict(os.environ,
                   HERMES_HOME=home,
                   HERMES_LAUNCH_AGENTS_DIR=agents_dir,
                   HERMES_LAUNCHCTL_OUTPUT=launchctl_out)
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
