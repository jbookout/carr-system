#!/usr/bin/env python3
"""Hermetic contract test for the Call Mode LaunchAgent's Python interpreter.

Bug (verified 2026-09-23 on the Mac Studio): com.carr.call-mode.plist ran
under /usr/bin/python3, the macOS xcrun shim. A LaunchAgent loaded from
~/Library/LaunchAgents gets its Accessibility (TCC) responsibility attributed
to ProgramArguments[0], and the shim is not a path a human can usefully grant
Accessibility to -- System Events clicks failed with -25211 even after
enabling python3, Python.app and osascript. The fix resolves the REAL
interpreter that shim forwards to (the Command Line Tools python3) at
install time and substitutes it into the plist through a {{PYTHON}}
placeholder, exactly the way {{REPO}} and {{HOME}} already work.

This test never touches the real ~/Library/LaunchAgents and never invokes
launchctl for real: it renders the tracked plist template through the actual
installer script with HOME pointed at a temp directory and launchctl/curl
replaced by no-op stubs on PATH, then reads back the rendered file.
"""
from __future__ import annotations

import os
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL_DIR = REPO / "tools" / "dictation-rig"
PLIST = TOOL_DIR / "launchd" / "com.carr.call-mode.plist"
INSTALLER = TOOL_DIR / "bin" / "install-call-mode.sh"
CONFIG_AS_CODE = REPO / "ops" / "config-as-code.py"
TOKEN = re.compile(r"\{\{[^}]+\}\}")

STUB_LAUNCHCTL = "#!/bin/sh\nexit 0\n"
STUB_CURL = "#!/bin/sh\nexit 0\n"


def check(label: str, condition: bool, failures: list[str]) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        failures.append(label)


def write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def main() -> int:
    failures: list[str] = []

    template = PLIST.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")

    check("plist template no longer hardcodes the xcrun shim",
          "/usr/bin/python3" not in template, failures)
    check("plist template carries the {{PYTHON}} placeholder",
          "{{PYTHON}}" in template, failures)
    try:
        parsed = plistlib.loads(template.replace("{{PYTHON}}", "/tmp/py")
                                 .replace("{{REPO}}", "/tmp/repo")
                                 .replace("{{HOME}}", "/tmp/home")
                                 .encode("utf-8"))
        parsed_ok = True
    except Exception as exc:  # pragma: no cover - reported as a named check
        parsed = {}
        parsed_ok = False
        print(f"  detail plist parse: {type(exc).__name__}: {exc}")
    check("plist template parses once tokens are filled", parsed_ok, failures)
    args = parsed.get("ProgramArguments", []) if parsed_ok else []
    check("{{PYTHON}} renders as ProgramArguments[0], not a later argument",
          bool(args) and args[0] == "/tmp/py", failures)

    check("installer resolves the real interpreter via the xcrun shim",
          "/usr/bin/python3 -c 'import sys; print(sys.executable)'" in installer,
          failures)
    check("installer refuses when the resolved interpreter is empty or not executable",
          '[ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]' in installer, failures)
    check("installer substitutes {{PYTHON}} alongside {{REPO}} and {{HOME}}",
          "s|{{PYTHON}}|$PYTHON_BIN|g" in installer, failures)
    check("installer prints which interpreter it installed",
          "Call Mode will run under" in installer, failures)
    check("installer reminds the operator to grant that exact path Accessibility",
          "Accessibility" in installer, failures)

    # config-as-code.py's own machine-converge loop renders the very same
    # tracked plist (LAUNCHD_ALT_REPO points it at this file) via a second,
    # independent token-substitution path. If that path does not also learn
    # {{PYTHON}}, the next `config-as-code.py install --apply` (run nightly by
    # bin/fleet-sync.sh) silently overwrites a correctly-installed agent with
    # one whose ProgramArguments[0] is the literal, unresolved string.
    cac = CONFIG_AS_CODE.read_text(encoding="utf-8")
    check("config-as-code.py resolves the same real interpreter for {{PYTHON}}",
          "_find_call_mode_python" in cac and
          "import sys; print(sys.executable)" in cac, failures)
    check("config-as-code.py's token table carries {{PYTHON}}",
          '"{{PYTHON}}"' in cac, failures)
    check("config-as-code.py refuses to install a plist with an unresolved token",
          'if "{{" in body:' in cac, failures)

    # Exercise the actual sh/sed renderer end to end, hermetically: HOME is a
    # throwaway temp dir, and launchctl/curl are no-op stubs on PATH so the
    # installer's own bootstrap/health-check tail cannot reach the real
    # launchd domain or make a network call.
    with tempfile.TemporaryDirectory(prefix="carr-call-mode-selftest-home-") as home, \
         tempfile.TemporaryDirectory(prefix="carr-call-mode-selftest-bin-") as stub_bin:
        write_stub(Path(stub_bin), "launchctl", STUB_LAUNCHCTL)
        write_stub(Path(stub_bin), "curl", STUB_CURL)

        env = dict(os.environ)
        env["HOME"] = home
        env["PATH"] = f"{stub_bin}:{env.get('PATH', '')}"

        result = subprocess.run(
            ["/bin/sh", str(INSTALLER)],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=30,
        )
        dest = Path(home) / "Library" / "LaunchAgents" / "com.carr.call-mode.plist"
        check("installer run against a fixture HOME wrote the rendered plist",
              dest.exists(), failures)
        if dest.exists():
            rendered = dest.read_text(encoding="utf-8")
            check("rendered plist leaves no template token behind",
                  not TOKEN.search(rendered), failures)
            try:
                rendered_parsed = plistlib.loads(rendered.encode("utf-8"))
                rendered_ok = True
            except Exception as exc:  # pragma: no cover - named check
                rendered_parsed = {}
                rendered_ok = False
                print(f"  detail rendered plist parse: {type(exc).__name__}: {exc}")
            check("rendered plist parses", rendered_ok, failures)
            rendered_args = rendered_parsed.get("ProgramArguments", []) if rendered_ok else []
            check("rendered ProgramArguments[0] is a real, executable interpreter "
                  "(not the xcrun shim, not a literal token)",
                  bool(rendered_args)
                  and rendered_args[0] != "/usr/bin/python3"
                  and os.path.isfile(rendered_args[0])
                  and os.access(rendered_args[0], os.X_OK),
                  failures)
            check("installer told the operator which interpreter was installed",
                  "Call Mode will run under" in (result.stdout or ""), failures)
            check("installer named the Accessibility reminder for the installed path",
                  "Accessibility" in (result.stdout or ""), failures)
        else:
            print(f"  detail installer stdout: {(result.stdout or '')[:400]!r}")
            print(f"  detail installer stderr: {(result.stderr or '')[:400]!r}")

    print(f"call-mode-python-launchd selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
