#!/usr/bin/env python3
"""Selftest for the Tailscale health row and the start-at-login agent (WR-000178).

After the 2026-09-30 macOS reboot Tailscale on the Mac Studio sat "stopped" for
about six hours and SSH to the MacBook was cut off with nothing reporting it.
Three pieces close that, and this suite proves each one hermetically, with a
stub Tailscale binary so no test touches the real daemon:

  1. ops/tailscale_health.py  — classifies `Tailscale status` and renders the
     health row, which must FAIL on "stopped" and print its bound action.
  2. bin/tailscale-up.sh      — runs `Tailscale up` only when status says
     stopped; never when already running, never when logged out (that path
     would wait on a browser nobody is at).
  3. ops/launchd/com.carr.tailscale-up.plist — RunAtLoad agent, primary-only,
     pointing at the script above.
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
SCRIPT = REPO / "bin" / "tailscale-up.sh"
PLIST = REPO / "ops" / "launchd" / "com.carr.tailscale-up.plist"

spec = importlib.util.spec_from_file_location("tailscale_health", REPO / "ops" / "tailscale_health.py")
assert spec and spec.loader
th = importlib.util.module_from_spec(spec)
spec.loader.exec_module(th)

cac_spec = importlib.util.spec_from_file_location("config_as_code_ts", REPO / "ops" / "config-as-code.py")
assert cac_spec and cac_spec.loader
cac = importlib.util.module_from_spec(cac_spec)
cac_spec.loader.exec_module(cac)

FAILS: list[str] = []


def check(label: str, cond: bool, detail: object = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + ("" if cond or not detail else f": {detail}"))
    if not cond:
        FAILS.append(label)


def stub(root: Path, status_out: str, status_rc: int) -> tuple[Path, Path]:
    """A fake Tailscale CLI: `status` prints the given text, every call is logged."""
    log = root / "calls.log"
    fake = root / "Tailscale"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{log}'\n"
        "if [ \"$1\" = status ]; then\n"
        f"  printf '%s\\n' '{status_out}'\n"
        f"  exit {status_rc}\n"
        "fi\nexit 0\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake, log


# ---- 1. classification and the health row ---------------------------------
RUNNING = "100.64.0.1   studio   joe@   macOS   -\n100.64.0.2   macbook  joe@   macOS   -"

s, _ = th.classify("Tailscale is stopped.", 1)
check("stopped status classifies as stopped", s == "stopped", s)
s, _ = th.classify(RUNNING, 0)
check("peer list classifies as running", s == "running", s)
s, _ = th.classify("Logged out.\nLog in at: <auth link>", 1)
check("logged-out status classifies as logged_out", s == "logged_out", s)
s, _ = th.classify("failed to connect to local Tailscale service", 1)
check("any other nonzero status classifies as error", s == "error", s)

line, failed = th.health_row(status="stopped", summary="Tailscale is stopped.")
check("stopped row FAILS the health run", failed is True)
check("stopped row is marked red", line.lstrip().startswith("✗✗"), line)
check("stopped row prints its bound action inline", "on breach:" in line, line)
check("bound action names the start-at-login agent", "com.carr.tailscale-up" in line, line)
check("bound action names owner, verify and auto-clear",
      all(k in line for k in ("owner", "verify", "auto-clear")), line)

line, failed = th.health_row(status="running", summary="2 peers")
check("running row passes", failed is False and line.lstrip().startswith("OK"), line)
check("running row still prints its bound action", "on breach:" in line, line)

line, failed = th.health_row(status="logged_out", summary="Logged out.")
check("logged-out row FAILS (SSH is just as cut off)", failed is True, line)

line, failed = th.health_row(status="absent", summary="")
check("absent app is a visible skip, not a failure", failed is False and "not installed" in line, line)

with tempfile.TemporaryDirectory() as d:
    fake, _ = stub(Path(d), "Tailscale is stopped.", 1)
    line, failed = th.row(binary=str(fake))
    check("row() runs the binary and fails on stopped", failed is True and "stopped" in line, line)
    fake, _ = stub(Path(d), RUNNING, 0)
    line, failed = th.row(binary=str(fake))
    check("row() passes on a running node", failed is False, line)
line, failed = th.row(binary="/nonexistent/Tailscale")
check("row() with no app installed skips", failed is False, line)


# ---- 2. the idempotent start script ---------------------------------------
def run_script(status_out: str, status_rc: int) -> tuple[int, list[str], str]:
    with tempfile.TemporaryDirectory() as d:
        fake, log = stub(Path(d), status_out, status_rc)
        p = subprocess.run(["/bin/zsh", str(SCRIPT)], capture_output=True, text=True,
                           env={**os.environ, "TAILSCALE_BIN": str(fake)}, timeout=30)
        calls = log.read_text().split("\n") if log.exists() else []
        return p.returncode, [c for c in calls if c], p.stdout + p.stderr


rc, calls, out = run_script("Tailscale is stopped.", 1)
check("stopped: script runs `up`", any(c.startswith("up") for c in calls), calls)
check("stopped: `up` carries a timeout so it cannot hang",
      any("--timeout" in c for c in calls if c.startswith("up")), calls)
check("stopped: script exits 0 after up", rc == 0, out)

rc, calls, out = run_script(RUNNING, 0)
check("running: script does NOT run `up` (idempotent)", not any(c.startswith("up") for c in calls), calls)
check("running: script exits 0", rc == 0, out)

rc, calls, out = run_script("Logged out.", 1)
check("logged out: script does NOT run `up` (would wait on a browser)",
      not any(c.startswith("up") for c in calls), calls)
check("logged out: script exits nonzero so the log shows it", rc != 0, out)

with tempfile.TemporaryDirectory() as d:
    p = subprocess.run(["/bin/zsh", str(SCRIPT)], capture_output=True, text=True,
                       env={**os.environ, "TAILSCALE_BIN": f"{d}/missing"}, timeout=30)
    check("absent app: script exits 0 without error", p.returncode == 0, p.stdout + p.stderr)

# ---- 3. the LaunchAgent definition ----------------------------------------
check("plist exists in ops/launchd", PLIST.exists())
if PLIST.exists():
    d = plistlib.loads(PLIST.read_bytes())
    check("plist label matches filename", d.get("Label") == "com.carr.tailscale-up", d.get("Label"))
    check("plist runs at login", d.get("RunAtLoad") is True)
    check("plist runs bin/tailscale-up.sh",
          any("bin/tailscale-up.sh" in a for a in d.get("ProgramArguments", [])),
          d.get("ProgramArguments"))
    check("plist uses the repo portability token",
          any(a.startswith("{{REPO}}") for a in d.get("ProgramArguments", [])))
    check("plist is not KeepAlive (one-shot at login)", not d.get("KeepAlive"))
check("agent is primary-only (the Studio is the hub)", "com.carr.tailscale-up.plist" in cac.PRIMARY_ONLY)
check("agent is not definition-only (the normal install path loads it)",
      "com.carr.tailscale-up.plist" not in cac.DEFINITION_ONLY)

print(f"tailscale-health-selftest: {'FAIL ' + str(len(FAILS)) if FAILS else 'all passed'}")
sys.exit(1 if FAILS else 0)
