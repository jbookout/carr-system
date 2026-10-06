"""Identify Grok-owned hook invocations.

grok_session(): any Grok CLI process owns this hook through shell transports.
Joe 2026-10-06: "there is no reason to block any of my subscriptions from doing
real work. the model router is our protection from unqualified work." Grok
imports Claude's user-level hooks but has no CARR session lifecycle, so the
rule boot is never armed for it and every tool call was refused. Context
delivery hooks skip Grok sessions in every mode; effect guards stay on.

bounded_grok_read_only(): the runner's bounded read-only child, not an
interactive session (kept for the lifecycle hooks that only skip that case).

The marker alone is insufficient: inherited or forged markers cannot exempt
a writable/interactive Grok ancestor. Failed process readback keeps hooks on.
This is a context boundary; effect guards do not use it.
"""
import os
import re
import subprocess
import time

READ_ONLY_ENV = "CARR_GROK_RUN_READ_ONLY"
# The shortest enclosing hook has five seconds. Leave most of it for the
# retained gate when ancestry is slow, incomplete, or cannot be authenticated.
PROBE_BUDGET_S = 0.75
TRANSPARENT_SHELLS = frozenset({"sh", "bash", "zsh", "dash"})


def bounded_grok_read_only() -> bool:
    if os.environ.get(READ_ONLY_ENV) != "1":
        return False
    pid = os.getppid()
    deadline = time.monotonic() + PROBE_BUDGET_S
    try:
        for _ in range(8):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            result = subprocess.run(["ps", "-ww", "-o", "ppid=,args=", "-p", str(pid)],
                                    capture_output=True, text=True, timeout=remaining)
            if result.returncode or time.monotonic() >= deadline:
                return False
            parent, command = result.stdout.strip().split(None, 1)
            # Inspect options before --print, never tokens from the prompt.
            print_flag = re.search(r" --print(?:\s|$)", command)
            argv = command[:print_flag.start()].split() if print_flag else command.split()
            binary = os.path.basename(argv[0]).lstrip('-')
            if re.fullmatch(r"grok(?:-\d+\.\d+\.\d+)?", binary):
                if not print_flag:
                    return False
                # Match the runner's complete option prefix. An interactive
                # prompt mentioning these flags cannot nominate a session.
                return (len(argv) == 12 and argv[1] == "--model"
                        and argv[3] == "--reasoning-effort"
                        and argv[5] == "--max-turns" and argv[6].isdigit()
                        and argv[7:] == ["--always-approve", "--sandbox", "read-only",
                                        "--output-format", "streaming-json"])
            # Only shell transports can connect this hook to its owner.
            # A separate agent or an unknown launcher must retain lifecycle
            # hooks, even when a read-only Grok exists farther up the chain.
            if binary not in TRANSPARENT_SHELLS:
                return False
            pid = int(parent)
            if pid <= 1:
                return False
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    return False


def grok_session() -> bool:
    """True when the nearest non-shell ancestor of this hook is the Grok CLI.

    Process ancestry, not an environment marker, so nothing inherited or
    forged can nominate a session. A Claude, Codex or other agent between the
    hook and Grok owns the hook and keeps every gate. Any failed or slow
    readback returns False, which keeps the gates on.
    """
    pid = os.getppid()
    deadline = time.monotonic() + PROBE_BUDGET_S
    try:
        for _ in range(8):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            result = subprocess.run(["ps", "-ww", "-o", "ppid=,args=", "-p", str(pid)],
                                    capture_output=True, text=True, timeout=remaining)
            if result.returncode or time.monotonic() >= deadline:
                return False
            parent, command = result.stdout.strip().split(None, 1)
            binary = os.path.basename(command.split()[0]).lstrip('-')
            if re.fullmatch(r"grok(?:-\d+\.\d+\.\d+)?", binary):
                return True
            if binary not in TRANSPARENT_SHELLS:
                return False
            pid = int(parent)
            if pid <= 1:
                return False
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    return False
