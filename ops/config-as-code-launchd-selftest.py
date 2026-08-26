#!/usr/bin/env python3
"""Hermetic regression proof for active LaunchAgent self-install handling."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import plistlib
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "config_as_code_launchd", REPO / "ops" / "config-as-code.py"
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
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
            with contextlib.redirect_stdout(io.StringIO()) as changed_out:
                changed = mod.install_launchd_plist(
                    fleet_dest.name, str(fleet_dest), desired_fleet, False
                )
            cases.append(check(
                "changed active fleet plist stays untouched and fails closed",
                changed == "failed" and calls == []
                and fleet_dest.read_text(encoding="utf-8") == old_fleet
                and "SELF-RELOAD REFUSED" in changed_out.getvalue()
                and "config-as-code.py install --apply" in changed_out.getvalue(),
                (changed, calls, changed_out.getvalue()),
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
    finally:
        mod.subprocess.run = original_run
        if original_active is None:
            os.environ.pop(mod.ACTIVE_LAUNCHD_LABEL_ENV, None)
        else:
            os.environ[mod.ACTIVE_LAUNCHD_LABEL_ENV] = original_active

    print(f"config-as-code-launchd-selftest: {sum(cases)}/{len(cases)} passed")
    return 0 if all(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
