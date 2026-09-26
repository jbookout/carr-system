#!/usr/bin/env python3
"""reinstall-launchd-calendar.py — re-render and re-bootstrap the CARR agents
whose schedule moved from StartInterval to StartCalendarInterval.

WHY. macOS 27 launchd never fires a StartInterval agent (runs = 0, even
RunAtLoad); see lib/launchd_calendar.py. The templates in ops/launchd now carry
calendar schedules, but an installed agent keeps its old StartInterval body, and
keeps being dead, until it is rewritten AND bootstrapped again. This does that
for exactly the affected agents and nothing else.

WHAT IT TOUCHES. An installed agent (present in ~/Library/LaunchAgents) whose
tracked template carries the converter's marker and whose installed body differs
from the rendered template. Everything else is left alone:

  * an installed agent that already matches is not rewritten and not reloaded;
  * an agent that is not installed is not installed here (that is
    `ops/config-as-code.py install --apply`, which decides machine scope);
  * a definition-only agent, a primary-only agent on a secondary, and an agent
    whose program is not built on this machine are skipped, by the same rules
    ops/config-as-code.py applies;
  * the label named in CARR_CONFIG_AS_CODE_ACTIVE_LAUNCHD_LABEL is refused,
    because reloading the job that runs this would kill it mid-write.

NOTHING IS KICKSTARTED unless --kickstart is given. A re-bootstrapped agent
with RunAtLoad true runs once at bootstrap because that is what RunAtLoad
means; everything else waits for its next calendar minute.

DRY RUN BY DEFAULT. Without --apply it prints the plan and changes nothing. A
failed bootstrap restores the previous body and bootstraps it again, so a
failure leaves the machine as it found it, and the exit status is 1.

    bin/reinstall-launchd-calendar.py              # plan only
    bin/reinstall-launchd-calendar.py --apply      # rewrite + bootout/bootstrap
    bin/reinstall-launchd-calendar.py --apply --kickstart

--launch-agents, --templates and --launchctl exist for the hermetic selftest
(ops/reinstall-launchd-calendar-selftest.py) and are not needed otherwise.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from lib import launchd_calendar  # noqa: E402


def _config_as_code():
    spec = importlib.util.spec_from_file_location(
        "config_as_code_for_reinstall", HERE / "ops" / "config-as-code.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def plan(cac, templates_dir: Path, agents_dir: Path) -> list[dict]:
    """One row per CARR template: what this run would do with it and why."""
    rows = []
    active = os.environ.get(cac.ACTIVE_LAUNCHD_LABEL_ENV, "").strip()
    for template in sorted(templates_dir.glob("com.carr.*.plist")):
        name = template.name
        source_path = Path(cac.LAUNCHD_ALT_REPO.get(name, template))
        source = source_path.read_text(encoding="utf-8")
        row = {"name": name, "dest": agents_dir / name, "action": "skip", "why": ""}
        rows.append(row)
        if launchd_calendar.MARKER not in source:
            row["why"] = "not a converted interval schedule"
            continue
        problems = launchd_calendar.audit_template(source)
        if problems:
            row.update(action="fail", why=f"template refused: {problems[0]}")
            continue
        if name in cac.DEFINITION_ONLY:
            row["why"] = "definition only"
            continue
        if name in cac.PRIMARY_ONLY and not cac.IS_PRIMARY:
            row["why"] = "primary-only job on a secondary (config-as-code retires it)"
            continue
        if not row["dest"].exists():
            row["why"] = "not installed here (ops/config-as-code.py install owns first installs)"
            continue
        installed = row["dest"].read_text(encoding="utf-8")
        if cac.launchd_texts_match(installed, source):
            row["why"] = "installed plist already matches"
            continue
        body = cac.concrete(source)
        gone = cac.missing_targets(body)
        if gone:
            row["why"] = f"not built on this machine: {gone[0]}"
            continue
        label = launchd_calendar.plist_label(source)
        if active and label == active:
            row.update(action="fail", why=f"{label} is the job running this; reinstall it from outside")
            continue
        row.update(action="reinstall", why="installed body differs from the calendar template",
                   label=label, body=body, previous=installed)
    return rows


def _run(launchctl: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([launchctl, *args], capture_output=True, text=True, check=False)


def reinstall(row: dict, launchctl: str, domain: str, kickstart: bool) -> bool:
    dest, label = row["dest"], row["label"]
    _run(launchctl, "bootout", f"{domain}/{label}")   # fails when not loaded; fine
    dest.write_text(row["body"], encoding="utf-8")
    booted = _run(launchctl, "bootstrap", domain, str(dest))
    if booted.returncode == 0 and _run(launchctl, "print", f"{domain}/{label}").returncode == 0:
        if kickstart:
            kicked = _run(launchctl, "kickstart", f"{domain}/{label}")
            if kicked.returncode != 0:
                print(f"      kickstart failed: {(kicked.stderr or kicked.stdout).strip()[:120]}")
                return False
        return True
    print(f"      BOOTSTRAP FAILED: {(booted.stderr or booted.stdout).strip()[:120]}"
          " — restoring the previous body")
    dest.write_text(row["previous"], encoding="utf-8")
    _run(launchctl, "bootstrap", domain, str(dest))
    return False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="rewrite and re-bootstrap")
    parser.add_argument("--kickstart", action="store_true",
                        help="also kickstart each re-bootstrapped agent (off by default)")
    parser.add_argument("--launch-agents", type=Path, default=None)
    parser.add_argument("--templates", type=Path, default=None)
    parser.add_argument("--launchctl", default="/bin/launchctl")
    args = parser.parse_args(argv)

    cac = _config_as_code()
    templates_dir = args.templates or Path(cac.LAUNCHD_REPO)
    agents_dir = args.launch_agents or Path(cac.LAUNCHD_SRC)
    domain = f"gui/{os.getuid()}"

    rows = plan(cac, templates_dir, agents_dir)
    failures = [r for r in rows if r["action"] == "fail"]
    todo = [r for r in rows if r["action"] == "reinstall"]
    for row in rows:
        if row["action"] == "skip":
            print(f"  ok    {row['name']}: {row['why']}")
        elif row["action"] == "fail":
            print(f"  FAIL  {row['name']}: {row['why']}")
    for row in todo:
        print(f"  {'REINSTALL' if args.apply else 'would reinstall'}  {row['name']}: {row['why']}")
        if args.apply and not reinstall(row, args.launchctl, domain, args.kickstart):
            failures.append(row)
    verb = "reinstalled" if args.apply else "to reinstall (dry run; --apply to act)"
    left_alone = sum(1 for r in rows if r["action"] == "skip")
    print(f"reinstall-launchd-calendar: {len(todo)} {verb}, {left_alone} left alone, "
          f"{len(failures)} failed; kickstart {'on' if args.kickstart else 'off'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
