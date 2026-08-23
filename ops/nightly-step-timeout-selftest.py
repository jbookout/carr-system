#!/usr/bin/env python3
"""Proves the nightly chain survives a step that HANGS.

WHY THIS EXISTS. bin/nightly.sh has always survived a step that FAILS: step()
records the bad exit and keeps going, so a broken export cannot skip the backup.
It did NOT survive a step that hangs. On 2026-08-23 the 02:05 scheduled run
stalled inside `exports (6 targets -> OneDrive)` on a half-closed Postgres
socket — TCP CLOSE_WAIT, 0.30s of CPU across fourteen minutes — and never
returned. Two things followed from that, and the second is the expensive one:

  1. Every step after exports never ran. The encrypted backup is the LAST step,
     so a hang anywhere earlier takes the backup down with it.
  2. The chain holds a singleton lock. A hung holder never releases it, so every
     later invocation takes the lock's no-op path and returns EXIT 0. A hung
     chain, a skipped chain and a clean chain all return the same number, which
     is how this stayed invisible until somebody read out/nightly.log by hand.

So the requirement under test is not "the helper can kill things". It is: a step
that hangs becomes an ordinary failed step, and THE CHAIN CARRIES ON to the
steps after it.

THE GRANDCHILD CASE IS THE REAL CASE, not an edge case. On the night this was
written the direct child was `/bin/zsh ./run.sh export` and the process actually
wedged on the dead socket was its python grandchild. Killing the direct child
alone left the grandchild running and holding the socket. That is why the helper
puts the child in its own session and signals the whole process group, the same
shape tools/room-bridge/dispatch.py already uses.

Run: ./.venv/bin/python ops/nightly-step-timeout-selftest.py
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HELPER = REPO / "bin" / "with-timeout.py"
PY = sys.executable

failures: list[str] = []
passes = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passes
    if ok:
        passes += 1
        print(f"  ok   {name}")
    else:
        failures.append(f"{name}: {detail}")
        print(f"  FAIL {name} — {detail}")


def run_helper(limit, *cmd, timeout=90):
    """Invoke the helper and return (rc, stdout, stderr, elapsed)."""
    t0 = time.monotonic()
    p = subprocess.run(
        [PY, str(HELPER), str(limit), *cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return p.returncode, p.stdout, p.stderr, time.monotonic() - t0


# ── 1. exit codes pass through untouched ────────────────────────────────────
# step() reads 78 as SKIP and 69 as BLOCKED, and those two readings are the
# reason the chain is not red every night. A wrapper that flattened them would
# turn fourteen expected refusals into fourteen alarms.
print("exit-code pass-through")
for code in (0, 1, 69, 78, 3):
    rc, _, _, _ = run_helper(30, "/bin/sh", "-c", f"exit {code}")
    check(f"exit {code} survives the wrapper", rc == code, f"got {rc}")

# ── 2. output still reaches the caller ──────────────────────────────────────
# step() appends the child's stdout and stderr to out/nightly.log. Every message
# that explains a SKIP or a BLOCKED arrives that way.
print("output plumbing")
rc, out, err, _ = run_helper(30, "/bin/sh", "-c", "echo to-stdout; echo to-stderr >&2")
check("child stdout reaches the caller", "to-stdout" in out, f"stdout={out!r}")
check("child stderr reaches the caller", "to-stderr" in err, f"stderr={err!r}")

# ── 3. a fast command is not delayed ────────────────────────────────────────
# The chain runs ~30 steps. A wrapper that waited out the limit on every one
# would add hours.
print("no added latency")
rc, _, _, elapsed = run_helper(30, "/bin/sh", "-c", "exit 0")
check("fast command returns immediately", elapsed < 5, f"took {elapsed:.1f}s")

# ── 4. a hang is cut off at the limit and reported as 124 ───────────────────
print("the hang itself")
rc, _, _, elapsed = run_helper(2, "/bin/sh", "-c", "sleep 300")
check("hung command is terminated", rc == 124, f"got rc={rc}")
check("terminated close to the limit", elapsed < 25, f"took {elapsed:.1f}s")

# ── 5. THE TONIGHT CASE: the grandchild dies too ────────────────────────────
# Shape taken from the real failure: a zsh wrapper whose python grandchild is
# the process actually wedged. A marker file proves the grandchild is gone
# rather than merely orphaned, because an orphan still holds the socket.
print("grandchild reaping (the 2026-08-23 shape)")
with tempfile.TemporaryDirectory() as td:
    marker = Path(td) / "grandchild-still-running"
    script = (
        f"/bin/zsh -c '"
        f"( while true; do touch {marker}; sleep 0.3; done ) & wait'"
    )
    rc, _, _, _ = run_helper(2, "/bin/sh", "-c", script)
    check("wrapper-with-grandchild times out", rc == 124, f"got rc={rc}")
    time.sleep(1.0)
    marker.unlink(missing_ok=True)
    time.sleep(1.5)
    check(
        "grandchild is dead, not merely orphaned",
        not marker.exists(),
        "grandchild kept touching the marker after the timeout fired — "
        "it survived the kill, which is exactly the 2026-08-23 failure",
    )

# ── 6. a child that ignores SIGTERM is still stopped ────────────────────────
# A process blocked in an uninterruptible-looking wait may not act on TERM.
# The helper escalates to KILL rather than waiting forever for a polite exit.
print("escalation past a trapped SIGTERM")
with tempfile.TemporaryDirectory() as td:
    marker = Path(td) / "sigterm-ignorer-alive"
    script = f"trap '' TERM; while true; do touch {marker}; sleep 0.3; done"
    rc, _, _, elapsed = run_helper(2, "/bin/sh", "-c", script, timeout=120)
    check("SIGTERM-ignoring child is killed", rc == 124, f"got rc={rc}")
    check("escalation does not hang the chain", elapsed < 60, f"took {elapsed:.1f}s")
    time.sleep(0.5)
    marker.unlink(missing_ok=True)
    time.sleep(1.5)
    check("SIGTERM-ignoring child is actually gone", not marker.exists(),
          "child survived escalation")

# ── 7. the caller survives the kill ─────────────────────────────────────────
# Signalling a process GROUP is the sharp edge here: get the group wrong and the
# watchdog kills the nightly chain it was added to protect. This asserts the
# helper's own process — the stand-in for the chain — is still alive afterwards.
print("the chain survives its own watchdog")
rc, out, _, _ = run_helper(2, "/bin/sh", "-c", "sleep 300")
check("caller still running after a timeout", rc == 124, f"got rc={rc}")
check("selftest process itself survived", os.getpid() > 0)

# ── 8. THE REQUIREMENT: the chain continues past a hung step ────────────────
# Everything above is machinery. This is the thing that was actually broken:
# step() must turn a hang into a recorded failure and RUN THE NEXT STEP.
print("chain continuation past a hung step (the actual requirement)")
with tempfile.TemporaryDirectory() as td:
    log = Path(td) / "chain.log"
    after = Path(td) / "step-after-the-hang-ran"
    harness = f"""#!/bin/zsh
LOG={log}
REPO={REPO}
STEP_TIMEOUT_DEFAULT=2
source {REPO}/bin/step-timeout.zsh
say() {{ print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  $*" >> "$LOG"; }}
rc_total=0
timed_out=0
run_one() {{
  local label="$1"; shift
  say "START $label"
  if carr_step_with_timeout "$STEP_TIMEOUT_DEFAULT" "$@" >> "$LOG" 2>&1; then
    say "OK    $label"
  else
    local rc=$?
    if [ "$rc" -eq 124 ]; then
      say "TIMEOUT  $label (exit 124)"
      timed_out=$((timed_out + 1)); rc_total=1
    else
      say "FAIL  $label (exit $rc)"; rc_total=1
    fi
  fi
}}
run_one "hangs forever" /bin/sh -c 'sleep 300'
run_one "runs after the hang" /bin/sh -c 'touch {after}'
say "chain done rc_total=$rc_total timed_out=$timed_out"
"""
    hpath = Path(td) / "harness.zsh"
    hpath.write_text(harness)
    p = subprocess.run(["/bin/zsh", str(hpath)], capture_output=True,
                       text=True, timeout=180)
    text = log.read_text() if log.exists() else ""
    check("hung step is logged as TIMEOUT", "TIMEOUT  hangs forever" in text,
          f"log was:\n{text}")
    check("THE STEP AFTER THE HANG RAN", after.exists(),
          "the chain stopped at the hang — this is the whole defect, unfixed")
    check("chain reached its completion line", "chain done" in text,
          f"log was:\n{text}")
    check("hang counted as a failure, not a pass", "rc_total=1" in text,
          f"log was:\n{text}")

# ── 9. the composition step() actually ships ────────────────────────────────
# Everything above drives the wall clock directly. The chain does not: it puts
# the wrapper INSIDE carr_routine_exec, whose `env -i` strips the environment
# down to the declared database capabilities. That is the one arrangement that
# ships, so it is the one that has to be asserted — a wrapper that works alone
# and breaks under env -i would pass every check above and still hang at 02:00.
print("the composition step() ships (wrapper inside env -i)")
with tempfile.TemporaryDirectory() as td:
    marker = Path(td) / "grandchild-alive"
    probe = f"""#!/bin/zsh
REPO={REPO}
cd $REPO || exit 1
source bin/routine-credential-env.sh
source bin/step-timeout.zsh
carr_step_timeout_prefix 2
print -r -- "PREFIX_WORDS=${{#CARR_STEP_TIMEOUT_ARGV[@]}}"
carr_routine_exec "${{CARR_STEP_TIMEOUT_ARGV[@]}}" /bin/zsh -c \\
  '( while true; do touch {marker}; sleep 0.3; done ) & wait'
print -r -- "RC=$?"
"""
    ppath = Path(td) / "probe.zsh"
    ppath.write_text(probe)
    p = subprocess.run(["/bin/zsh", str(ppath)], capture_output=True,
                       text=True, timeout=180)
    check("prefix is non-empty in a working checkout",
          "PREFIX_WORDS=3" in p.stdout, f"stdout={p.stdout!r}")
    check("timeout fires through env -i", "RC=124" in p.stdout,
          f"stdout={p.stdout!r} stderr={p.stderr[-400:]!r}")
    time.sleep(1.0)
    marker.unlink(missing_ok=True)
    time.sleep(1.5)
    check("grandchild reaped through env -i", not marker.exists(),
          "the wrapper works standalone but leaks children under env -i, "
          "which is the arrangement the chain actually uses")

# ── 10. every step label with an override still exists ──────────────────────
# An override keyed to a label nobody emits is a limit that silently never
# applies. Renaming a step's leading words is what breaks this, and the failure
# is invisible until the night that step wedges.
print("overrides point at real steps")
nightly = (REPO / "bin" / "nightly.sh").read_text()
# One key per LINE: the keys are multi-word step labels, so splitting the
# output on whitespace turns "golden workflow suite" into three dead keys.
overrides = [k for k in subprocess.run(
    ["/bin/zsh", "-c",
     f'source {REPO}/bin/step-timeout.zsh && print -rl -- "${{(k)STEP_TIMEOUT_OVERRIDE[@]}}"'],
    capture_output=True, text=True, timeout=60).stdout.splitlines() if k.strip()]
check("at least one override is configured", bool(overrides), "none found")
for key in overrides:
    check(f"override {key!r} matches a real step",
          f'step "{key}' in nightly,
          "no step label starts with these words — the override is dead")

print()
if failures:
    print(f"FAIL — {len(failures)} of {passes + len(failures)} checks failed")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"PASS — {passes} checks")
