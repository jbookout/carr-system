#!/usr/bin/env python3
"""The Flash code desk runs model-driven code (the agent and the tests it writes). This checks the containment that
makes that safe: the exploit replay the independent review of PR #1324 asked for (a program that reads a home secret
and reaches the network is REFUSED under the real sandbox), plus the timeout that actually bounds a run even when a
child escapes its process group.

The @sandboxed cases run the REAL macOS sandbox on this Mac; on a machine without sandbox-exec (Linux CI) they are
skipped in code, never disabled through the environment. The timeout case needs no sandbox and runs everywhere."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("flash_run", os.path.join(HERE, "flash-run.py"))
assert spec and spec.loader
fr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fr)

FAILURES: list[str] = []
SANDBOXED = os.path.exists(fr.SANDBOX_EXEC)
TEMPS: list[str] = []


def check(name, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(f"{name}: {exc}")
        print(f"  FAIL  {name}\n          {exc}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{name}: unexpected {exc!r}")
        print(f"  FAIL  {name}\n          unexpected {exc!r}")
    else:
        print(f"  ok    {name}")


def sandboxed(fn):
    fn._sandboxed = True
    return fn


def _work():
    d = os.path.realpath(tempfile.mkdtemp(prefix="flash-run-sbx-"))
    TEMPS.append(d)
    return d


def _run(program, work, *, reads=(), execs=(), port=None, timeout=30):
    """Run a python program string under the real sandbox profile for `work`, group-safe."""
    path = os.path.join(work, "prog.py")
    with open(path, "w") as fh:
        fh.write(program)
    argv = fr.sandbox_wrap([sys.executable, path], work, reads=reads, execs=execs, port=port)
    return fr.bounded_run(argv, work, timeout)


# ── the exploit replay ────────────────────────────────────────────────────────────────────────────────────────
# A harmless stand-in secret under a throwaway home the profile does not list. Real key files are not named, because
# the unattended guard blocks them and because the point is the SANDBOX, not any one path.
EXPLOIT = '''
import socket
SECRET = {secret!r}
try:
    data = open(SECRET).read()
    print("READ_OK:" + data)
except Exception as e:
    print("READ_DENIED:" + type(e).__name__)
try:
    s = socket.create_connection(("10.255.255.1", 80), timeout=3)
    s.close()
    print("NET_OK")
except OSError as e:
    print("NET_DENIED:" + type(e).__name__)
'''


def _plant_secret():
    home = os.path.realpath(tempfile.mkdtemp(prefix="flash-fakehome-"))
    TEMPS.append(home)
    secret = os.path.join(home, "stand_in_secret.txt")
    with open(secret, "w") as fh:
        fh.write("TOP-SECRET-STANDIN-VALUE")
    return secret


@sandboxed
def exploit_is_refused_reading_a_home_secret_and_the_network():
    secret = _plant_secret()
    work = _work()
    code, out = _run(EXPLOIT.format(secret=secret), work, port=fr.flash_port())
    assert "READ_OK" not in out, f"the sandbox let the exploit read the stand-in secret: {out}"
    assert "READ_DENIED" in out, out
    assert "TOP-SECRET-STANDIN-VALUE" not in out, out
    assert "NET_OK" not in out and "NET_DENIED" in out, f"the sandbox let the exploit reach the network: {out}"


def exploit_control_reads_the_secret_when_unsandboxed():
    # Proves the test is meaningful: the same program, run WITHOUT the sandbox, does read the stand-in secret and so
    # the refusal above is the sandbox's doing, not the program failing on its own.
    secret = _plant_secret()
    work = _work()
    path = os.path.join(work, "prog.py")
    with open(path, "w") as fh:
        fh.write(EXPLOIT.format(secret=secret))
    out = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=30).stdout
    assert "READ_OK:TOP-SECRET-STANDIN-VALUE" in out, out


@sandboxed
def a_worktree_written_binary_cannot_be_executed():
    work = _work()
    payload = os.path.join(work, "payload.sh")
    with open(payload, "w") as fh:
        fh.write("#!/bin/sh\necho pwned\n")
    os.chmod(payload, 0o755)
    prog = f'''
import subprocess
try:
    subprocess.run([{payload!r}], check=True)
    print("EXEC_OK")
except Exception as e:
    print("EXEC_DENIED:" + type(e).__name__)
'''
    code, out = _run(prog, work)
    assert "EXEC_OK" not in out and "EXEC_DENIED" in out, f"a model-written binary in the worktree ran: {out}"


@sandboxed
def writes_outside_the_worktree_are_denied():
    work = _work()
    outside = _work()
    target = os.path.join(outside, "escape.txt")
    prog = f'''
try:
    open({target!r}, "w").write("x")
    print("WRITE_OK")
except Exception as e:
    print("WRITE_DENIED:" + type(e).__name__)
'''
    code, out = _run(prog, work)
    assert "WRITE_OK" not in out and "WRITE_DENIED" in out, out
    assert not os.path.exists(target), "a write escaped the worktree"


@sandboxed
def a_write_inside_the_worktree_is_allowed():
    work = _work()
    prog = 'open("made.txt", "w").write("ok"); print("WROTE")'
    code, out = _run(prog, work)
    assert "WROTE" in out and os.path.exists(os.path.join(work, "made.txt")), out


def no_sandbox_binary_means_the_wrap_refuses():
    saved = fr.SANDBOX_EXEC
    fr.SANDBOX_EXEC = "/nonexistent/sandbox-exec"
    try:
        raised = False
        try:
            fr.sandbox_wrap([sys.executable, "-c", "print(1)"], _work())
        except FileNotFoundError:
            raised = True
        assert raised, "sandbox_wrap must refuse when sandbox-exec is absent"
    finally:
        fr.SANDBOX_EXEC = saved


# ── the timeout actually bounds the run ───────────────────────────────────────────────────────────────────────
def a_two_second_timeout_returns_within_about_three_seconds_despite_a_setsid_escape():
    # The reviewer's case: a child that calls setsid() leaves the group and holds the output pipe. bounded_run must
    # not wait on it. Reproduce and require the call to return in ~3 s for a 2 s timeout.
    work = _work()
    prog = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import os,time; os.setsid(); time.sleep(8)'])\n"
        "time.sleep(60)\n"
    )
    path = os.path.join(work, "slow.py")
    with open(path, "w") as fh:
        fh.write(prog)
    started = time.monotonic()
    code, out = fr.bounded_run([sys.executable, path], work, 2)
    elapsed = time.monotonic() - started
    assert code == 124, f"expected a timeout code, got {code}: {out}"
    assert elapsed < 4.0, f"bounded_run held for {elapsed:.1f}s past a 2s timeout (escaped child was waited on)"


def main() -> int:
    if not SANDBOXED:
        print("no macOS sandbox-exec on this machine; @sandboxed exploit-replay cases are skipped (Linux CI)")
    for name, fn in list(globals().items()):
        if not (name and callable(fn) and getattr(fn, "__module__", None) == "__main__"):
            continue
        if name in ("check", "sandboxed", "main"):
            continue
        if name.startswith("_"):
            continue
        if getattr(fn, "_sandboxed", False) and not SANDBOXED:
            print(f"  skip  {name} (needs the macOS sandbox)")
            continue
        check(name.replace("_", " "), fn)
    for d in TEMPS:
        shutil.rmtree(d, ignore_errors=True)
    if FAILURES:
        print(f"{len(FAILURES)} sandbox test(s) failed", file=sys.stderr)
        return 1
    print("flash-run sandbox: every assertion held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
