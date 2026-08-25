#!/usr/bin/env python3
"""Hermetic contract test for the room-bridge LaunchAgent rendering.

The bridge runs under launchd, not an interactive shell.  A scheduled Hermes
lookup therefore needs both the user-local bin directory and a fully rendered
home/repository path.  This test reads only repository files and renders a
temporary fixture; it never invokes launchctl, Hermes, the Worker, or a room
verb.
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLIST = REPO / "ops" / "launchd" / "com.carr.room-bridge.plist"
INSTALLER = REPO / "bin" / "install-room-bridge.sh"
TOKEN = re.compile(r"\{\{[^}]+\}\}")
CONCRETE_HOME = re.compile(r"(?:/Users|/home)/[^/\s\"']+")


def check(label: str, condition: bool, failures: list[str]) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        failures.append(label)


def main() -> int:
    failures: list[str] = []
    template = PLIST.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")

    try:
        parsed = plistlib.loads(template.encode("utf-8"))
        parsed_ok = True
    except Exception as exc:  # pragma: no cover - reported as a named check
        parsed = {}
        parsed_ok = False
        print(f"  detail plist parse: {type(exc).__name__}: {exc}")
    check("room-bridge plist parses before rendering", parsed_ok, failures)

    environment = parsed.get("EnvironmentVariables", {}) if parsed_ok else {}
    launch_path = environment.get("PATH", "")
    check("template PATH includes the home-relative Hermes bin",
          "{{HOME}}/.local/bin:" in launch_path, failures)
    check("template has no concrete user home", not CONCRETE_HOME.search(template), failures)
    check("template retains the repository placeholder", "{{REPO}}" in template, failures)
    check("installer renders {{REPO}}", "s|{{REPO}}|$REPO_REPLACEMENT|g" in installer, failures)
    check("installer renders {{HOME}}", "s|{{HOME}}|$HOME_REPLACEMENT|g" in installer, failures)
    check("installer has no concrete user home", not CONCRETE_HOME.search(installer), failures)
    check("installer refuses unresolved tokens", r"grep -Eq '\{\{[^}]+\}\}'" in installer, failures)
    check("installer exposes a render-only seam", "--render-only" in installer, failures)

    # Exercise the same placeholder contract with paths containing spaces. The
    # installer separately escapes sed replacement metacharacters; plist XML
    # itself still correctly rejects an unescaped ampersand before installation.
    fixture_repo = "/tmp/CARR checkout staging"
    fixture_home = "/tmp/Joe local home"
    rendered = template.replace("{{REPO}}", fixture_repo).replace("{{HOME}}", fixture_home)
    check("fixture rendering leaves no template tokens", not TOKEN.search(rendered), failures)
    try:
        concrete = plistlib.loads(rendered.encode("utf-8"))
        concrete_ok = True
    except Exception as exc:  # pragma: no cover - reported as a named check
        concrete = {}
        concrete_ok = False
        print(f"  detail rendered plist parse: {type(exc).__name__}: {exc}")
    check("rendered fixture plist parses", concrete_ok, failures)
    concrete_path = concrete.get("EnvironmentVariables", {}).get("PATH", "")
    check("rendered PATH points at the fixture home-local bin",
          concrete_path.startswith(f"{fixture_home}/.local/bin:"), failures)
    arguments = concrete.get("ProgramArguments", [])
    check("rendered program arguments point at the fixture checkout",
          any(fixture_repo in str(arg) for arg in arguments), failures)

    # The unresolved-token refusal must happen before install can touch the
    # user's LaunchAgents directory.
    refusal = installer.index("grep -Eq")
    install = installer.index("/usr/bin/install")
    check("unresolved-token refusal precedes destination install",
          refusal >= 0 and refusal < install, failures)

    # Exercise the actual zsh/sed renderer, not a Python reimplementation.
    # A valid spaced HOME must produce a parseable plist and never touch the
    # user's LaunchAgents directory.
    with tempfile.TemporaryDirectory(prefix="room bridge ") as valid_home:
        valid_env = dict(os.environ, HOME=valid_home)
        rendered_run = subprocess.run(
            ["/bin/zsh", str(INSTALLER), "--render-only"],
            cwd=REPO, env=valid_env, capture_output=True, text=True,
        )
        check("actual zsh/sed render succeeds for spaced HOME",
              rendered_run.returncode == 0, failures)
        try:
            actual = plistlib.loads(rendered_run.stdout.encode("utf-8"))
            actual_ok = True
        except Exception as exc:  # pragma: no cover - named below
            actual = {}
            actual_ok = False
            print(f"  detail actual render parse: {type(exc).__name__}: {exc}")
        check("actual render-only output parses", actual_ok, failures)
        actual_path = actual.get("EnvironmentVariables", {}).get("PATH", "")
        check("actual render carries spaced HOME local bin",
              actual_path.startswith(f"{valid_home}/.local/bin:"), failures)
        check("render-only does not create LaunchAgents",
              not (Path(valid_home) / "Library" / "LaunchAgents").exists(), failures)

    # An ampersand is legal in a filesystem path but needs XML escaping before
    # it can become a plist string. The current smallest safe behavior is an
    # explicit validation refusal before mkdir/install/launchctl. Include the
    # other sed-sensitive characters in the same fixture so they cannot be
    # silently dropped or interpreted by the renderer.
    bad_home = tempfile.mkdtemp(prefix="room bridge &| \\")
    try:
        bad_env = dict(os.environ, HOME=bad_home)
        refused = subprocess.run(
            ["/bin/zsh", str(INSTALLER)],
            cwd=REPO, env=bad_env, capture_output=True, text=True,
        )
        check("metacharacter HOME fails closed before install",
              refused.returncode != 0, failures)
        check("metacharacter refusal names validation",
              "failed validation; refusing installation" in refused.stderr, failures)
        check("metacharacter refusal leaves LaunchAgents untouched",
              not (Path(bad_home) / "Library" / "LaunchAgents").exists(), failures)
    finally:
        # The installer must not create anything beneath this fixture on the
        # refusal path. The temporary root itself is disposable test state.
        try:
            Path(bad_home).rmdir()
        except OSError:
            pass

    print(f"room-bridge launchd selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
