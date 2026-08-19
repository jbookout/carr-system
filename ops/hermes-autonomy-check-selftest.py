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

# Both halves are fixtured, so these cases assert what the CHECK does rather
# than what THIS MACHINE happens to have installed. The three cases main carried
# here — "an installed launch agent is unattended", "a running gateway service
# is unattended", "both together are unattended" — are deliberately NOT kept:
# they expect exit 1 for ai.hermes.gateway, which is the exact path Joe accepted
# on 2026-08-18. Post-acceptance they assert the opposite of the ruling. Their
# real content, that the gateway half is covered at all, survives in the
# acceptance-edge cases below, which pin both directions: the accepted names
# read clean, anything else still fails.
#
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
        # ONE fixture root, per decision b2f85c76: the check reads its launch
        # agents from <root>/LaunchAgents and its launchctl listing from
        # <root>/launchctl.txt, so a single obviously-test-named variable steers
        # both probes and neither can be neutered on its own.
        fixture = os.path.join(tmp, "fixture")
        agents_dir = os.path.join(fixture, "LaunchAgents")
        os.makedirs(agents_dir, exist_ok=True)
        for plist in agents:
            with open(os.path.join(agents_dir, plist), "w", encoding="utf-8") as fh:
                fh.write("<plist/>")

        with open(os.path.join(fixture, "launchctl.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(launchctl_lines) + ("\n" if launchctl_lines else ""))

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
