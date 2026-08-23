#!/usr/bin/env python3
"""Run a command under a wall-clock limit. Exit 124 if it outlives the limit.

    with-timeout.py <seconds> <command> [args...]

WHY THIS IS NOT `timeout(1)`. There is no `timeout` or `gtimeout` on this Mac —
they ship with GNU coreutils, which is not installed, and adding a Homebrew
dependency to the one path that has to work unattended at 02:00 trades a hang
for a different hang. Everything here is in the standard library.

WHY IT SIGNALS THE PROCESS GROUP AND NOT THE CHILD. On 2026-08-23 the nightly
chain wedged inside `exports (6 targets -> OneDrive)`. The direct child was
`/bin/zsh ./run.sh export`; the process actually stuck on a half-closed Postgres
socket was its python grandchild. Killing the direct child would have left the
grandchild holding the socket, so the child is put in a NEW SESSION and the
whole group is signalled at once. tools/room-bridge/dispatch.py already uses
this shape (start_new_session plus killpg); this is the same pattern, not a
second way of doing it.

WHY A NEW SESSION IS THE SAFE CHOICE RATHER THAN THE RISKY ONE. Signalling a
group is the sharp edge of this file: aim at the caller's group and the watchdog
kills the nightly chain it exists to protect. start_new_session guarantees the
child's group id is the child's own pid and contains nothing that was already
running, so `killpg` can never reach back up into the chain. The selftest holds
that line explicitly.

EXIT CODES. The child's own code is passed through untouched, because the chain
reads 78 as SKIP and 69 as BLOCKED and those two readings are the reason it is
not red every night. A child killed by a signal reports 128+signal, as a shell
does. 124 means the limit was hit, matching the GNU convention so the number is
recognisable to anyone who has used timeout(1). 127 means the command could not
be started.
"""

import os
import signal
import subprocess
import sys

# Seconds between the polite TERM and the unconditional KILL. A process blocked
# on a dead socket often will not act on TERM at all, and the chain cannot wait
# out a graceful exit that is never coming.
GRACE_SECONDS = 10

EXIT_TIMEOUT = 124
EXIT_CANNOT_RUN = 127
EXIT_USAGE = 2


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(f"usage: {os.path.basename(argv[0])} <seconds> <command> [args...]",
              file=sys.stderr)
        return EXIT_USAGE
    try:
        limit = float(argv[1])
    except ValueError:
        print(f"with-timeout: not a number of seconds: {argv[1]!r}", file=sys.stderr)
        return EXIT_USAGE
    cmd = argv[2:]

    # A limit of zero or less means "no limit". The chain uses this to opt a
    # step out by configuration rather than by editing this file.
    if limit <= 0:
        try:
            return subprocess.call(cmd)
        except (OSError, ValueError) as exc:
            print(f"with-timeout: cannot run {cmd[0]!r}: {exc}", file=sys.stderr)
            return EXIT_CANNOT_RUN

    try:
        # stdout/stderr are deliberately inherited: step() appends them to
        # out/nightly.log, and every message explaining a SKIP or a BLOCKED
        # arrives that way. Capturing them here would silence the log.
        proc = subprocess.Popen(cmd, start_new_session=True)
    except (OSError, ValueError) as exc:
        print(f"with-timeout: cannot run {cmd[0]!r}: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN

    try:
        rc = proc.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        _terminate_group(proc)
        print(
            f"with-timeout: TIMEOUT after {limit:g}s — {' '.join(cmd)}",
            file=sys.stderr,
        )
        return EXIT_TIMEOUT

    # Popen reports a signal death as a negative number; shells report 128+n.
    return 128 - rc if rc < 0 else rc


def _terminate_group(proc: subprocess.Popen) -> None:
    """TERM the child's process group, then KILL whatever is left."""
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        # Already reaped between the timeout firing and this call.
        return

    # Refuse to signal our own group. This cannot happen while the Popen above
    # passes start_new_session=True, and that is exactly why it is asserted
    # here: if a later edit drops that argument, this file must fail loudly
    # rather than quietly killing the nightly chain.
    if pgid == os.getpgrp():
        print("with-timeout: refusing to signal my own process group — "
              "the child was not started in a new session",
              file=sys.stderr)
        return

    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        return  # group is already gone

    try:
        proc.wait(timeout=GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass

    # Reaping the direct child is NOT the same as emptying the group: on
    # 2026-08-23 the wrapper exited while its python grandchild kept the dead
    # socket open. Signal 0 delivers nothing and only asks whether anyone is
    # still in the group, so a group that really is empty stops here instead of
    # taking a blind SIGKILL at a process-group id that may since have been
    # recycled onto something unrelated.
    try:
        os.killpg(pgid, 0)
    except OSError:
        return

    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


if __name__ == "__main__":
    sys.exit(main(sys.argv))
