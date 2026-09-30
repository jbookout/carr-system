#!/usr/bin/env python3
"""Tailscale up-or-down health row (WR-000178).

After the 2026-09-30 macOS reboot Tailscale on the Mac Studio stayed "stopped"
for about six hours. Nothing reported it; the first symptom was SSH to the
MacBook failing. This row reads `Tailscale status` directly — the artifact,
not a proxy — and FAILS the health run when the node is stopped or logged out,
because either one cuts the same SSH path.

Bound response (rule 590b11e1) is printed inline on every render. The fix for
"stopped" is the start-at-login agent com.carr.tailscale-up (bin/tailscale-up.sh),
which the ordinary config-as-code install places on the primary.
"""
from __future__ import annotations

import os
import subprocess

TAILSCALE_BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
LABEL = "tailscale"

BOUND_ACTION = (
    "on breach: open/update one dedup loop 'Tailscale down on this Mac' · owner orchestrator · "
    "fix: launchctl kickstart gui/$(id -u)/com.carr.tailscale-up "
    "(or config-as-code.py install --apply if the agent is missing; a logged-out node needs "
    "Joe to sign in at the Tailscale menu) · verify: Tailscale status lists peers and "
    "`ssh macbook true` succeeds · auto-clear: the next health run reads a running node"
)


def classify(output: str, returncode: int) -> tuple[str, str]:
    """Map `Tailscale status` output to running | stopped | logged_out | error."""
    text = (output or "").strip()
    first = text.splitlines()[0].strip() if text else ""
    low = text.lower()
    if "tailscale is stopped" in low:
        return "stopped", first
    if "logged out" in low or "needslogin" in low:
        return "logged_out", first
    if returncode == 0:
        peers = sum(1 for ln in text.splitlines() if ln.strip() and ln.split()[0].count(".") == 3)
        return "running", f"{peers} node(s) listed"
    return "error", first or f"exit {returncode}"


def health_row(*, status: str, summary: str) -> tuple[str, bool]:
    """Render the row. Returns (line, failed)."""
    if status == "running":
        return f"  OK {LABEL:<18} running, {summary} · {BOUND_ACTION}", False
    if status == "absent":
        return (f"  ·  {LABEL:<18} app not installed on this Mac — row skipped · "
                f"{BOUND_ACTION}"), False
    word = {"stopped": "STOPPED", "logged_out": "LOGGED OUT"}.get(status, "UNREADABLE")
    return (f"  ✗✗ {LABEL:<18} {word} ({summary}) — SSH to the other Mac is cut off · "
            f"{BOUND_ACTION}"), True


def row(binary: str = TAILSCALE_BIN) -> tuple[str, bool]:
    """Run `Tailscale status` and render its row."""
    if not os.path.exists(binary):
        return health_row(status="absent", summary="")
    try:
        p = subprocess.run([binary, "status"], capture_output=True, text=True,
                           timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return health_row(status="error", summary=type(e).__name__)
    status, summary = classify((p.stdout or "") + (p.stderr or ""), p.returncode)
    return health_row(status=status, summary=summary)


if __name__ == "__main__":
    import sys
    line, failed = row()
    print(line)
    sys.exit(1 if failed else 0)
