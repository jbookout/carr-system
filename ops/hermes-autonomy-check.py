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
  - no messaging gateway installed or running, BEYOND WHAT JOE HAS ACCEPTED
  - no launch agent starting either at login

WHAT JOE ACCEPTED, 2026-08-18: the gateway. His words when this check reported
it — "no im leaving it. i chose it" — and he was explicit that it is permanent,
not a temporary state to be nagged about. So the gateway below is listed by
name as ACCEPTED rather than treated as a finding, and this check goes green on
his machine.

Accepted is not invisible. The state is still printed on the OK line, so
`run.sh health` continues to say out loud that Hermes can act unattended; what
changed is that a chosen configuration no longer reads as a defect. And the
acceptance is BY NAME: a second gateway, a different launch agent, a service
under another label, or any scheduled job still fails, because none of those
are the thing he looked at and chose.

FAILING IS NOT A VERDICT THAT SOMETHING BAD HAPPENED. It means the runtime can
act unattended in a way NOBODY HAS ACCEPTED, and that its write grant should be
reconsidered or its scheduling brought under the rules that govern every other
scheduled seat.

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

# THE ONE TEST SEAM, and why it is a single obviously-named variable rather than
# one override per probe. HERMES_HOME was already injectable, so the selftest
# could fixture the scheduled-jobs predicate — but gateway_state() read the real
# ~/Library/LaunchAgents and shelled out to the real launchctl, so on any machine
# where the gateway is installed the three "clean" cases could not pass no matter
# what the fixture contained. A selftest that reads the world instead of its
# fixture is not testing what it claims to test; it was reporting the machine.
#
# Set CARR_HERMES_CHECK_FIXTURE to a directory to read the launch agents from
# <dir>/LaunchAgents and the launchctl listing from <dir>/launchctl.txt. Absent
# — which is every real run, including run.sh health and the nightly — both
# probes read the live machine exactly as before. The name says test, and its
# presence is visible in the environment, so this cannot quietly neuter a check
# in production the way a per-probe override could.
FIXTURE = os.environ.get("CARR_HERMES_CHECK_FIXTURE") or ""
CRON_DIR = os.path.join(HERMES_HOME, "cron")
EXECUTIONS_DB = os.path.join(CRON_DIR, "executions.db")

# The two per-probe seams this branch originally carried,
# HERMES_LAUNCH_AGENTS_DIR and HERMES_LAUNCHCTL_OUTPUT, are gone: decision
# b2f85c76 (2026-08-19) settled that a security-relevant check gets ONE
# obviously-test-named fixture root and never a per-probe override, because a
# per-probe override can quietly neuter one predicate while the check still
# reports itself as having run. CARR_HERMES_CHECK_FIXTURE above is that root and
# does the same job for both probes.

# ACCEPTED BY NAME, 2026-08-18. Joe read this check's own output naming these
# two and answered "no im leaving it. i chose it". Matching is exact: anything
# not on this list is still a finding, which is what keeps the check worth
# running.
ACCEPTED_LAUNCH_AGENTS = {"ai.hermes.gateway.plist"}
ACCEPTED_LAUNCHD_LABELS = {"ai.hermes.gateway"}


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


def gateway_state():
    """The messaging gateway: installed as a service, or currently running.

    Returns (findings, accepted). Findings are the unattended paths nobody has
    signed off; accepted are the ones Joe named and chose, kept separate so
    they can still be PRINTED rather than silently dropped.
    """
    findings, accepted = [], []
    # A user LaunchAgent is how `hermes gateway install` persists itself.
    agents_dir = os.path.join(FIXTURE, "LaunchAgents") if FIXTURE \
        else os.path.join(HOME, "Library", "LaunchAgents")
    for plist in glob.glob(os.path.join(agents_dir, "*hermes*")) + \
                 glob.glob(os.path.join(agents_dir, "*nousresearch*")):
        name = os.path.basename(plist)
        line = f"launch agent installed: {name}"
        (accepted if name in ACCEPTED_LAUNCH_AGENTS else findings).append(line)
    if FIXTURE:
        listing = os.path.join(FIXTURE, "launchctl.txt")
        try:
            out = open(listing, encoding="utf-8").read() if os.path.exists(listing) else ""
        except OSError:
            out = ""
    else:
        try:
            out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15).stdout
        except Exception:
            out = ""
    for line in out.splitlines():
        # The desktop app itself appears here as an `application.` label, which
        # is a window Joe opened rather than an unattended service. Only
        # non-application labels count.
        if re.search(r"hermes|nousresearch", line, re.I) and ".application." not in line \
                and not line.split("\t")[-1].startswith("application."):
            label = line.split(chr(9))[-1]
            text = f"launchd service running: {label}"
            (accepted if label in ACCEPTED_LAUNCHD_LABELS else findings).append(text)
    return findings, accepted


def main():
    if not os.path.isdir(HERMES_HOME):
        print("hermes-autonomy: OK (no Hermes install; nothing can act here)")
        return 0

    gateway_findings, accepted = gateway_state()
    findings = scheduled_jobs() + gateway_findings

    if findings:
        print(f"hermes-autonomy: UNATTENDED — {len(findings)} way(s) Hermes can act with nobody watching")
        for f in findings:
            print(f"  · {f}")
        print("  Hermes holds nine additive write verbs against the CARR record. That grant assumed")
        print("  a human in the session. Either remove the schedule, or bring it under the rules that")
        print("  govern every other scheduled seat (scope and risk colour declared at creation, runs")
        print("  landing as durable ops rows) before it writes unattended.")
        print("  If Joe has looked at one of these and chosen it, add it BY NAME to the accepted")
        print("  list at the top of this file — never widen the pattern to make the check quiet.")
        return 1

    note = ""
    if os.path.exists(EXECUTIONS_DB):
        note = " · its scheduler process is alive with no jobs defined, which is the expected idle state"
    if accepted:
        # Say it out loud every run. Joe chose this; he did not choose to stop
        # being told what is running.
        print(f"hermes-autonomy: OK (no scheduled jobs · {len(accepted)} accepted "
              f"gateway path(s), chosen by Joe 2026-08-18{note})")
        for a in accepted:
            print(f"  · accepted: {a}")
        return 0
    print(f"hermes-autonomy: OK (no scheduled jobs, no gateway service{note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
