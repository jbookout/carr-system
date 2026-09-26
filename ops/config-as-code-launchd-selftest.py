#!/usr/bin/env python3
"""Hermetic regression proof for active LaunchAgent self-install handling."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import plistlib
import stat
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "config_as_code_launchd", REPO / "ops" / "config-as-code.py"
)
assert spec and spec.loader
mod: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def plist(label: str, program: str = "/usr/bin/true") -> str:
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": [program],
        "RunAtLoad": False,
    }).decode("utf-8")


def check(label: str, condition: bool, detail: object = "") -> bool:
    print(f"{'PASS' if condition else 'FAIL'}  {label}"
          + ("" if condition or not detail else f": {detail}"))
    return condition


def _fake_launchctl(root: Path, fail_bootstrap_times: int) -> tuple[Path, Path]:
    """A stub launchctl that records calls and fails its first N bootstraps."""
    log, counter = root / "launchctl.log", root / "bootstrap.count"
    fake = root / "launchctl"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{log}'\n"
        "if [ \"$1\" = bootstrap ]; then\n"
        f"  n=$(cat '{counter}' 2>/dev/null || echo 0); n=$((n + 1)); echo $n > '{counter}'\n"
        f"  [ $n -le {fail_bootstrap_times} ] && exit 5\n"
        "fi\nexit 0\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake, log


def handoff_script_cases() -> list[bool]:
    """Run the real detached one-shot script against a stub launchctl."""
    out: list[bool] = []
    finished = subprocess.Popen(["/usr/bin/true"])
    finished.wait()
    dead_pid = str(finished.pid)          # a process group that has already exited
    for fails, expect_body, expect_log in (
        (0, "NEW", "loaded the new definition"),
        (1, "OLD", "restored the previous definition"),
        (2, "OLD", "RESTORE FAILED, com.carr.fleet-sync is unloaded"),
    ):
        with tempfile.TemporaryDirectory(prefix="carr-self-reload-") as tmp:
            root = Path(tmp)
            fake, calls_log = _fake_launchctl(root, fails)
            dest, staged, log = root / "agent.plist", root / "agent.plist.staged", root / "x.log"
            dest.write_text("OLD", encoding="utf-8")
            staged.write_text("NEW", encoding="utf-8")
            rc = subprocess.run(
                ["/bin/sh", "-c", mod.SELF_RELOAD_SCRIPT, "carr-self-reload", dead_pid,
                 str(fake), "gui/501", "com.carr.fleet-sync", str(staged), str(dest),
                 str(log), "5"], check=False, timeout=60).returncode
            calls = calls_log.read_text().splitlines() if calls_log.exists() else []
            text = log.read_text() if log.exists() else ""
            out.append(check(
                f"self-reload one-shot with {fails} failed bootstrap(s): body {expect_body}, "
                f"'{expect_log}'",
                dest.read_text() == expect_body and expect_log in text
                and (rc == 0) == (fails == 0)
                and calls[:2] == ["bootout gui/501/com.carr.fleet-sync",
                                  f"bootstrap gui/501 {dest}"]
                and not any(c.startswith("kickstart") for c in calls)
                and (fails < 2 or "launchctl bootstrap gui/501" in text),
                (rc, dest.read_text(), text, calls),
            ))
    return out


def check_and_install_cases() -> list[bool]:
    """Drive the refusal through cmd_check and cmd_install, not only the helpers."""
    out: list[bool] = []
    saved = {name: getattr(mod, name) for name in (
        "REPO", "REPO_HERE", "SETTINGS", "CLAUDE_CONTINUITY_MODE_FILE", "CLAUDE_MCP_CONFIG",
        "TASKS_SRC", "TASKS_REPO", "TASKS_QUARANTINE", "LAUNCHD_SRC", "LAUNCHD_REPO",
        "LAUNCHD_ALT_REPO", "HOOKS_REPO", "CODEX_HOOKS_SRC", "CODEX_CONFIG",
        "PREREQUISITE_CHECK")}
    real_run = mod.subprocess.run
    launchctl_calls: list[list[str]] = []

    def stub_run(args, *a, **k):
        if args and os.path.basename(str(args[0])) == "launchctl":
            launchctl_calls.append(list(args))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return real_run(args, *a, **k)

    try:
        with tempfile.TemporaryDirectory(prefix="carr-cac-refusal-") as tmp:
            home = Path(tmp)
            repo = home / "carr-system"
            launchd = repo / "ops" / "launchd"
            launchd.mkdir(parents=True)
            (repo / "ops" / "scheduled-tasks").mkdir(parents=True)
            (repo / "ops" / "config").mkdir(parents=True, exist_ok=True)
            for relative in ("ops/config/claude-continuity-hooks.json",
                             "ops/claude-continuity-hook.py",
                             "mcp-server/continuity-stdio-proxy.mjs"):
                target = repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((REPO / relative).read_bytes())
            (repo / "ops" / "config" / "hooks.json").write_text(
                '{\n  "PreToolUse": []\n}\n', encoding="utf-8")
            planted = plistlib.dumps({"Label": "com.carr.planted",
                                      "ProgramArguments": ["/usr/bin/true"],
                                      "StartInterval": 300}).decode()
            good, _ = mod.launchd_calendar.rewrite_template(plistlib.dumps({
                "Label": "com.carr.good", "ProgramArguments": ["/usr/bin/true"],
                "StartInterval": 600}).decode())
            (launchd / "com.carr.planted.plist").write_text(planted, encoding="utf-8")
            (launchd / "com.carr.good.plist").write_text(good, encoding="utf-8")
            mod.REPO = mod.REPO_HERE = str(repo)
            mod.SETTINGS = str(home / ".claude" / "settings.json")
            mod.CLAUDE_CONTINUITY_MODE_FILE = str(home / ".config/carr/claude-continuity-mode.json")
            mod.CLAUDE_MCP_CONFIG = str(home / ".claude.json")
            mod.TASKS_SRC = str(home / ".claude" / "scheduled-tasks")
            mod.TASKS_REPO = str(repo / "ops" / "scheduled-tasks")
            mod.TASKS_QUARANTINE = str(home / ".claude" / "scheduled-tasks-quarantine")
            mod.LAUNCHD_SRC = str(home / "Library" / "LaunchAgents")
            mod.LAUNCHD_REPO = str(launchd)
            mod.LAUNCHD_ALT_REPO = {}
            mod.HOOKS_REPO = str(repo / "ops" / "config" / "hooks.json")
            mod.CODEX_HOOKS_SRC = str(home / ".codex" / "hooks.json")
            mod.CODEX_CONFIG = str(home / ".codex" / "config.toml")
            mod.PREREQUISITE_CHECK = lambda _repo: []
            mod.subprocess.run = stub_run
            with contextlib.redirect_stdout(io.StringIO()) as install_out:
                install_rc = mod.cmd_install(True)
            agents = Path(mod.LAUNCHD_SRC)
            out.append(check(
                "cmd_install refuses the planted StartInterval template, installs the good one, "
                "and exits nonzero",
                install_rc != 0 and not (agents / "com.carr.planted.plist").exists()
                and (agents / "com.carr.good.plist").exists()
                and "REFUSED  com.carr.planted.plist" in install_out.getvalue()
                and not any(str(agents / "com.carr.planted.plist") in " ".join(c)
                            for c in launchctl_calls),
                (install_rc, install_out.getvalue()[-800:]),
            ))
            with contextlib.redirect_stdout(io.StringIO()) as check_out:
                check_rc = mod.cmd_check()
            text = check_out.getvalue()
            out.append(check(
                "cmd_check reports the planted template as SCHEDULE REFUSED and exits 1",
                check_rc == 1 and text.startswith("config-as-code: DRIFT")
                and "launchd template ops/launchd/com.carr.planted.plist (SCHEDULE REFUSED)" in text
                and "com.carr.good.plist (SCHEDULE REFUSED)" not in text,
                (check_rc, text[:800]),
            ))
    finally:
        mod.subprocess.run = real_run
        for name, value in saved.items():
            setattr(mod, name, value)
    return out


def main() -> int:
    original_run = mod.subprocess.run
    original_active = os.environ.get(mod.ACTIVE_LAUNCHD_LABEL_ENV)
    calls: list[list[str]] = []

    def fake_run(args, *unused_args, **unused_kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    cases: list[bool] = []
    try:
        mod.subprocess.run = fake_run
        with tempfile.TemporaryDirectory(prefix="carr-active-launchd-") as tmp:
            root = Path(tmp)
            fleet_label = "com.carr.fleet-sync"
            other_label = "com.carr.other"
            fleet_dest = root / f"{fleet_label}.plist"
            other_dest = root / f"{other_label}.plist"
            old_fleet = plist(fleet_label, "/usr/bin/false")
            desired_fleet = plist(fleet_label)
            desired_other = plist(other_label)

            fleet_dest.write_text(desired_fleet, encoding="utf-8")
            os.environ[mod.ACTIVE_LAUNCHD_LABEL_ENV] = fleet_label
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()) as unchanged_out:
                unchanged = mod.install_launchd_plist(
                    fleet_dest.name, str(fleet_dest), desired_fleet, True
                )
            cases.append(check(
                "unchanged active fleet job stays loaded",
                unchanged == "kept" and calls == []
                and fleet_dest.read_text(encoding="utf-8") == desired_fleet,
                (unchanged, calls, unchanged_out.getvalue()),
            ))

            fleet_dest.write_text(old_fleet, encoding="utf-8")
            calls.clear()
            spawned: list[tuple[list[str], dict]] = []
            original_popen = mod.subprocess.Popen
            original_handoff = mod.SELF_RELOAD_HANDOFF_DIR
            mod.SELF_RELOAD_HANDOFF_DIR = str(root / "handoff")
            mod.subprocess.Popen = lambda args, **kw: spawned.append((list(args), kw))
            try:
                with contextlib.redirect_stdout(io.StringIO()) as changed_out:
                    changed = mod.install_launchd_plist(
                        fleet_dest.name, str(fleet_dest), desired_fleet, False
                    )
            finally:
                mod.subprocess.Popen = original_popen
            staged = root / "handoff" / f"{fleet_label}.plist.staged"
            argv = spawned[0][0] if spawned else []
            cases.append(check(
                "changed active fleet plist is left untouched and its reload handed "
                "to a detached one-shot (no hourly exit 1)",
                changed == "deferred" and calls == []
                and fleet_dest.read_text(encoding="utf-8") == old_fleet
                and staged.read_text(encoding="utf-8") == desired_fleet
                and len(spawned) == 1 and spawned[0][1].get("start_new_session") is True
                and argv[4] == str(os.getpgrp()) and argv[7] == fleet_label
                and argv[8] == str(staged) and argv[9] == str(fleet_dest)
                and "self-reload deferred" in changed_out.getvalue(),
                (changed, calls, spawned, changed_out.getvalue()),
            ))

            def broken_popen(*_a, **_k):
                raise OSError("synthetic spawn failure")
            mod.subprocess.Popen = broken_popen
            try:
                with contextlib.redirect_stdout(io.StringIO()) as broken_out:
                    broken = mod.install_launchd_plist(
                        fleet_dest.name, str(fleet_dest), desired_fleet, False
                    )
            finally:
                mod.subprocess.Popen = original_popen
                mod.SELF_RELOAD_HANDOFF_DIR = original_handoff
            cases.append(check(
                "a hand-off that cannot start fails closed with the external remedy",
                broken == "failed" and calls == []
                and fleet_dest.read_text(encoding="utf-8") == old_fleet
                and "config-as-code.py install --apply" in broken_out.getvalue(),
                (broken, broken_out.getvalue()),
            ))

            other_dest.write_text(desired_other, encoding="utf-8")
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                other = mod.install_launchd_plist(
                    other_dest.name, str(other_dest), desired_other, True
                )
            cases.append(check(
                "active fleet install still unloads and loads every other plist",
                other == "loaded"
                and [call[:2] for call in calls]
                == [["launchctl", "unload"], ["launchctl", "load"]],
                (other, calls),
            ))

            os.environ.pop(mod.ACTIVE_LAUNCHD_LABEL_ENV, None)
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                external = mod.install_launchd_plist(
                    fleet_dest.name, str(fleet_dest), desired_fleet, False
                )
            cases.append(check(
                "external install renders and reloads the changed fleet plist",
                external == "loaded"
                and [call[:2] for call in calls]
                == [["launchctl", "unload"], ["launchctl", "load"]]
                and fleet_dest.read_text(encoding="utf-8") == desired_fleet,
                (external, calls),
            ))

            # A Mac demoted to secondary: its primary-only job is unloaded and
            # moved to quarantine, never deleted, and a second retire refuses
            # to overwrite the first quarantined copy.
            original_quarantine = getattr(mod, "LAUNCHD_QUARANTINE")
            setattr(mod, "LAUNCHD_QUARANTINE", str(root / "quarantine"))
            try:
                name = "com.carr.nightly-record-layer.plist"
                live = root / name
                live.write_text(plist("com.carr.nightly-record-layer"), encoding="utf-8")
                calls.clear()
                with contextlib.redirect_stdout(io.StringIO()):
                    planned = mod.retire_primary_only_plist(name, str(live), False)
                cases.append(check(
                    "dry-run retire touches nothing",
                    planned == "planned" and calls == [] and live.exists(),
                    (planned, calls),
                ))
                with contextlib.redirect_stdout(io.StringIO()):
                    retired = mod.retire_primary_only_plist(name, str(live), True)
                moved = root / "quarantine" / name
                cases.append(check(
                    "secondary retires a primary-only job: unloaded and moved aside",
                    retired == "retired" and not live.exists() and moved.exists()
                    and [call[:2] for call in calls] == [["launchctl", "unload"]],
                    (retired, calls),
                ))
                live.write_text(plist("com.carr.nightly-record-layer"), encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    again = mod.retire_primary_only_plist(name, str(live), True)
                cases.append(check(
                    "retire refuses to overwrite an earlier quarantined copy",
                    again == "failed" and live.exists(),
                    again,
                ))
            finally:
                setattr(mod, "LAUNCHD_QUARANTINE", original_quarantine)
    finally:
        mod.subprocess.run = original_run
        if original_active is None:
            os.environ.pop(mod.ACTIVE_LAUNCHD_LABEL_ENV, None)
        else:
            os.environ[mod.ACTIVE_LAUNCHD_LABEL_ENV] = original_active

    # STARTINTERVAL IS REFUSED (macOS 27 never fires it). A planted template in
    # a throwaway repo is reported by check and refused by install's gate, a
    # converted one is not, and the real tree carries no refusal at all.
    with tempfile.TemporaryDirectory(prefix="carr-launchd-refuse-") as tmp:
        repo = Path(tmp)
        (repo / "ops" / "launchd").mkdir(parents=True)
        planted = plistlib.dumps({
            "Label": "com.carr.planted",
            "ProgramArguments": ["/usr/bin/true"],
            "StartInterval": 300,
            "RunAtLoad": True,
        }).decode("utf-8")
        (repo / "ops" / "launchd" / "com.carr.planted.plist").write_text(
            planted, encoding="utf-8")
        found = mod.refused_launchd_templates(str(repo))
        cases.append(check(
            "check reports a planted StartInterval template as refused",
            len(found) == 1 and found[0][0] == "ops/launchd/com.carr.planted.plist"
            and "StartInterval" in found[0][1],
            found,
        ))
        cases.append(check(
            "install refuses to render a planted StartInterval template",
            bool(mod.launchd_template_refusal(planted)),
        ))
        converted, _ = mod.launchd_calendar.rewrite_template(planted)
        (repo / "ops" / "launchd" / "com.carr.planted.plist").write_text(
            converted, encoding="utf-8")
        cases.append(check(
            "the converted template is accepted by both",
            mod.refused_launchd_templates(str(repo)) == []
            and mod.launchd_template_refusal(converted) is None
            and plistlib.loads(converted.encode())["RunAtLoad"] is True,
            mod.refused_launchd_templates(str(repo)),
        ))
    cases.extend(handoff_script_cases())
    cases.extend(check_and_install_cases())
    cases.append(check(
        "no tracked CARR template is refused",
        mod.refused_launchd_templates(str(REPO)) == [],
        mod.refused_launchd_templates(str(REPO)),
    ))

    print(f"config-as-code-launchd-selftest: {sum(cases)}/{len(cases)} passed")
    return 0 if all(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
