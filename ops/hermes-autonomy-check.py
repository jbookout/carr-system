#!/usr/bin/env python3
"""hermes-autonomy-check.py — is the Hermes runtime able to act while nobody is watching?

WHY THIS SHIPS IN THE SAME CHANGE AS THE WRITE GRANT. On 2026-08-16 Joe granted
the Hermes runtime nine additive write verbs so it could file real work. The
grant is safe for the reason every verb in it is additive, keyed, and unable to
destroy or re-point a record — and that reasoning holds only while a human is in
the session. A write nobody asked for, at 3am, from a timer nobody registered,
is a different act performed by the same verb.

WHAT MAKES HERMES DIFFERENT FROM EVERY OTHER WRITING SEAT. CARR's own scheduled
runs are governed: rule f04a05aa requires every scheduled task, trigger and
subagent to declare its scope and risk colour at creation, and Program 3 made
Claude Code's scheduled runs land as durable rows in the ops schema. Hermes
carries its own scheduler and its own messaging gateway, inside its own
application, and neither declares anything to CARR. A job created there produces
no ops row, appears on no board, and answers to none of those rules. Its ticker
was already running within hours of install, with no jobs defined.

So this is the condition on the grant, expressed as a predicate rather than as a
sentence somebody has to remember:
  - no scheduled jobs defined in Hermes
  - no messaging gateway installed or running
  - no launch agent starting either at login

FAILING IS NOT A VERDICT THAT SOMETHING BAD HAPPENED. It means the runtime can
now act unattended, and that its write grant should be reconsidered or its
scheduling brought under the rules that govern every other scheduled seat.

Exit 0 clean, 1 on findings. `run.sh health` reads the summary line.
"""
import glob
import json
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.join(HOME, ".hermes")
CRON_DIR = os.path.join(HERMES_HOME, "cron")
EXECUTIONS_DB = os.path.join(CRON_DIR, "executions.db")


def scheduled_jobs():
    """Jobs defined in Hermes' own scheduler.

    Read from the jobs file rather than by shelling out to `hermes cron list`:
    a check that depends on the very runtime it is auditing can be defeated by
    that runtime failing to start, and would then read clean.
    """
    findings = []
    for path in glob.glob(os.path.join(CRON_DIR, "*.json")) + [os.path.join(CRON_DIR, "jobs.yaml")]:
        if not os.path.exists(path):
            continue
        try:
            text = open(path, encoding="utf-8").read().strip()
        except OSError:
            continue
        if not text or text in ("{}", "[]"):
            continue
        try:
            data = json.loads(text)
            count = len(data) if isinstance(data, (list, dict)) else 1
        except ValueError:
            count = text.count("\n") + 1
        if count:
            findings.append(f"{os.path.basename(path)} defines {count} scheduled job(s)")
    return findings


# THE TWO SEAMS BELOW EXIST SO THE NEGATIVE CASES CAN BE TESTED AT ALL, and they
# were added on 2026-08-19 after this check's own selftest had been failing on
# Joe's Mac for as long as Hermes has been installed there. The selftest points
# HERMES_HOME at a temp dir, so the scheduled-jobs half was always hermetic — but
# gateway_state() read the real ~/Library/LaunchAgents and shelled out to the
# real launchctl, so both "clean" fixtures picked up the machine's actual Hermes
# gateway and reported a failure the fixture never described. That failure is the
# one every push from this Mac hit: the pre-push hook runs ops/ci.sh, the gates
# class runs every selftest, and a machine-state leak inside one of them refuses
# pushes that have nothing to do with it.
#
# Overriding either seam does NOT let anything silence the check where it counts.
# The nightly chain and the pre-push hook each construct their own environment
# and neither sets these. The same argument already applied to HERMES_HOME, which
# has been overridable since this file shipped.
LAUNCH_AGENTS = os.environ.get("HERMES_CHECK_LAUNCHAGENTS") or \
    os.path.join(HOME, "Library", "LaunchAgents")
LAUNCHCTL = os.environ.get("HERMES_CHECK_LAUNCHCTL") or "launchctl"


def gateway_state():
    """The messaging gateway: installed as a service, or currently running."""
    findings = []
    # A user LaunchAgent is how `hermes gateway install` persists itself.
    for plist in glob.glob(os.path.join(LAUNCH_AGENTS, "*hermes*")) + \
                 glob.glob(os.path.join(LAUNCH_AGENTS, "*nousresearch*")):
        findings.append(f"launch agent installed: {os.path.basename(plist)}")
    try:
        out = subprocess.run([LAUNCHCTL, "list"], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        out = ""
    for line in out.splitlines():
        # The desktop app itself appears here as an `application.` label, which
        # is a window Joe opened rather than an unattended service. Only
        # non-application labels count.
        if re.search(r"hermes|nousresearch", line, re.I) and ".application." not in line \
                and not line.split("\t")[-1].startswith("application."):
            findings.append(f"launchd service running: {line.split(chr(9))[-1]}")
    return findings


def main():
    if not os.path.isdir(HERMES_HOME):
        print("hermes-autonomy: OK (no Hermes install; nothing can act here)")
        return 0

    findings = scheduled_jobs() + gateway_state()

    if findings:
        print(f"hermes-autonomy: UNATTENDED — {len(findings)} way(s) Hermes can act with nobody watching")
        for f in findings:
            print(f"  · {f}")
        print("  Hermes holds nine additive write verbs against the CARR record. That grant assumed")
        print("  a human in the session. Either remove the schedule, or bring it under the rules that")
        print("  govern every other scheduled seat (scope and risk colour declared at creation, runs")
        print("  landing as durable ops rows) before it writes unattended.")
        return 1

    note = ""
    if os.path.exists(EXECUTIONS_DB):
        note = " · its scheduler process is alive with no jobs defined, which is the expected idle state"
    print(f"hermes-autonomy: OK (no scheduled jobs, no gateway service{note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
