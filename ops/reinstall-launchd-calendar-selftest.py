#!/usr/bin/env python3
"""Hermetic proof for `ops/config-as-code.py reinstall-launchd-calendar`.

It runs the real command against a throwaway templates directory, a throwaway
LaunchAgents directory and a stub launchctl that only records its arguments,
so nothing here can reach the machine's own agents. What it proves:

  * a dry run writes nothing and calls launchctl not at all;
  * --apply rewrites and re-bootstraps ONLY the installed agent whose body
    differs from its converted template;
  * an agent whose installed body already matches, one that is not installed,
    and one whose template is not a converted interval are all left alone;
  * nothing is kickstarted unless --kickstart is given;
  * a failed bootstrap restores the previous body and exits 1;
  * the label of the job running the command is refused, not reloaded.
"""
from __future__ import annotations

import importlib.util
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "config-as-code.py"
sys.path.insert(0, str(REPO))
from lib import launchd_calendar  # noqa: E402

spec = importlib.util.spec_from_file_location("cac_for_reinstall_test",
                                              REPO / "ops" / "config-as-code.py")
assert spec and spec.loader
cac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cac)

RESULTS: list[bool] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    RESULTS.append(bool(condition))
    print(f"{'PASS' if condition else 'FAIL'}  {label}"
          + ("" if condition or detail == "" else f": {detail!r}"))


def interval_plist(label: str, seconds: int) -> str:
    return plistlib.dumps({"Label": label, "ProgramArguments": ["/usr/bin/true"],
                           "StartInterval": seconds, "RunAtLoad": True}).decode()


def build(root: Path) -> tuple[Path, Path, Path, Path]:
    templates, agents, log = root / "templates", root / "agents", root / "launchctl.log"
    templates.mkdir()
    agents.mkdir()
    fake = root / "launchctl"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{log}'\n"
        "if [ \"$1\" = bootstrap ] && [ -n \"$FAKE_FAIL_BOOTSTRAP\" ]; then exit 5; fi\n"
        "exit 0\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    for name, seconds in (("a", 300), ("b", 1800), ("c", 60)):
        converted, _ = launchd_calendar.rewrite_template(
            interval_plist(f"com.carr.fixture-{name}", seconds))
        (templates / f"com.carr.fixture-{name}.plist").write_text(converted, encoding="utf-8")
    daily = plistlib.dumps({"Label": "com.carr.fixture-d", "ProgramArguments": ["/usr/bin/true"],
                            "StartCalendarInterval": {"Hour": 3, "Minute": 0}}).decode()
    (templates / "com.carr.fixture-d.plist").write_text(daily, encoding="utf-8")
    # a: installed with the dead StartInterval body -> must be reinstalled.
    (agents / "com.carr.fixture-a.plist").write_text(
        interval_plist("com.carr.fixture-a", 300), encoding="utf-8")
    # b: installed and already matching -> untouched.
    (agents / "com.carr.fixture-b.plist").write_text(
        cac.concrete((templates / "com.carr.fixture-b.plist").read_text()), encoding="utf-8")
    # c: not installed -> untouched. d: not a converted interval -> untouched.
    (agents / "com.carr.fixture-d.plist").write_text("stale daily body\n", encoding="utf-8")
    return templates, agents, log, fake


def run(templates, agents, fake, *extra, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != cac.ACTIVE_LAUNCHD_LABEL_ENV}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), "reinstall-launchd-calendar", "--templates", str(templates),
         "--launch-agents", str(agents), "--launchctl", str(fake), *extra],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)


def snapshot(agents: Path) -> dict:
    return {p.name: p.read_text() for p in sorted(agents.iterdir())}


def calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    before = snapshot(agents)
    dry = run(templates, agents, fake)
    check("dry run exits 0", dry.returncode == 0, dry.stdout + dry.stderr)
    check("dry run changes no file", snapshot(agents) == before)
    check("dry run never calls launchctl", calls(log) == [], calls(log))
    check("dry run names exactly the one agent it would reinstall",
          "would reinstall  com.carr.fixture-a.plist" in dry.stdout
          and "would reinstall  com.carr.fixture-b" not in dry.stdout, dry.stdout)

    applied = run(templates, agents, fake, "--apply")
    after = snapshot(agents)
    check("apply exits 0", applied.returncode == 0, applied.stdout + applied.stderr)
    body_a = plistlib.loads(after["com.carr.fixture-a.plist"].encode())
    check("the differing agent is rewritten to the calendar form",
          "StartInterval" not in body_a
          and launchd_calendar.cadence_seconds(body_a) == 300
          and body_a.get("RunAtLoad") is True, body_a)
    check("the matching, the uninstalled and the non-interval agents are untouched",
          after["com.carr.fixture-b.plist"] == before["com.carr.fixture-b.plist"]
          and after["com.carr.fixture-d.plist"] == before["com.carr.fixture-d.plist"]
          and "com.carr.fixture-c.plist" not in after, sorted(after))
    uid = os.getuid()
    check("launchctl is asked to bootout, bootstrap and print only the differing agent",
          calls(log) == [f"bootout gui/{uid}/com.carr.fixture-a",
                         f"bootstrap gui/{uid} {agents / 'com.carr.fixture-a.plist'}",
                         f"print gui/{uid}/com.carr.fixture-a"], calls(log))
    check("nothing is kickstarted by default",
          not any(c.startswith("kickstart") for c in calls(log)))

    log.unlink()
    again = run(templates, agents, fake, "--apply")
    check("a second apply is a no-op (everything now matches)",
          again.returncode == 0 and calls(log) == [] and snapshot(agents) == after,
          (again.stdout, calls(log)))

with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    kicked = run(templates, agents, fake, "--apply", "--kickstart")
    check("--kickstart kickstarts only the reinstalled agent",
          kicked.returncode == 0
          and [c for c in calls(log) if c.startswith("kickstart")]
          == [f"kickstart gui/{os.getuid()}/com.carr.fixture-a"], calls(log))

with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    before = snapshot(agents)
    failed = run(templates, agents, fake, "--apply", env_extra={"FAKE_FAIL_BOOTSTRAP": "1"})
    check("a failed bootstrap exits 1 and restores the previous body",
          failed.returncode == 1 and snapshot(agents) == before
          and "BOOTSTRAP FAILED" in failed.stdout, (failed.returncode, failed.stdout))

with tempfile.TemporaryDirectory(prefix="carr-reinstall-cal-") as tmp:
    templates, agents, log, fake = build(Path(tmp))
    before = snapshot(agents)
    self_run = run(templates, agents, fake, "--apply",
                   env_extra={cac.ACTIVE_LAUNCHD_LABEL_ENV: "com.carr.fixture-a"})
    check("the job running the command is refused, not reloaded",
          self_run.returncode == 1 and snapshot(agents) == before and calls(log) == [],
          (self_run.stdout, calls(log)))

print(f"reinstall-launchd-calendar-selftest: {sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
