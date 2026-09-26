#!/usr/bin/env python3
"""Proves the export warm-up asks whether a provider exists before waiting for one.

WHY THIS EXISTS, and the night that bought it. The 2026-09-22 nightly was the
first run to reach the END of the 1200s provider warm-up instead of being killed
by the step timeout — PR #1099 raised the exports limit to 1800s so the fail-open
could finally be reached. What it proved is that the wait was never the binding
constraint. The log holds 60 poll lines over the full twenty minutes, not one
file ever warming, then "PUBLISH FAILED: OSError: [Errno 11] Resource deadlock
avoided" on all six targets.

The cause was not a slow provider. It was no provider. `ps -Ao pid,lstart` that
afternoon put OneDrive.app's start at 08:55:58 local — three seconds after the
session's "Display is turned on" event, and nearly seven hours after the exports
step ran at 02:05:29 local. OneDrive is a GUI login item. Nothing starts it for a
scheduled 02:05 run, and a read of a dehydrated placeholder answers EDEADLK
forever when no provider exists to service it. The same six files read in about
two seconds each once the client was up.

So the requirement under test is not "the wait can be patient". It is:

  1. When no provider is running, the wait SAYS SO and makes one attempt to
     start it, so a night that would lose six targets can recover on its own.
  2. It attempts that launch ONCE. A relaunch on every poll turns a bounded wait
     into a bounded stream of launches against something that will not come up.
  3. When a provider IS running, nothing is launched. The old reading — cloud-only
     tree, pin the folder or free disk — is still the right one in that case, and
     a spurious `open` would bury it.
  4. The wait STILL FAILS OPEN. Every earlier fix here was sized so a provider
     that never wakes produces honest per-target failures rather than a new way
     for the step to die before it exports anything. A liveness probe must not
     become the thing that raises.
  5. An untestable machine reports NO FINDING rather than a false absence.
     provider_running() returns None without pgrep, and None must never be
     narrated as "no OneDrive process is running".

Run: ./.venv/bin/python ops/export-provider-liveness-selftest.py
"""

import errno
import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from exporters import common  # noqa: E402

passes = 0
failures: list[str] = []


def check(label, ok, why=""):
    global passes
    if ok:
        passes += 1
        print(f"  ok  {label}")
    else:
        failures.append(f"{label} — {why}")
        print(f"  FAIL {label}")


class ColdFile:
    """A path whose every read raises the transient errno the provider returns."""

    def __init__(self, name, err=errno.EDEADLK):
        self.name = name
        self._err = err

    def exists(self):
        return True

    def open(self, _mode="rb"):
        raise OSError(self._err, "Resource deadlock avoided")


class WarmFile:
    def __init__(self, name):
        self.name = name

    def exists(self):
        return True

    def open(self, _mode="rb"):
        return io.BytesIO(b"x" * 64)


def run_wait(paths, alive, budget=1.0, poll=0.25):
    """Drive wait_for_provider with a fake clock, fake liveness and fake launch."""
    launches = []
    log = io.StringIO()
    with redirect_stderr(log):
        cold = common.wait_for_provider(
            paths,
            budget_seconds=budget,
            poll_seconds=poll,
            sleep=lambda _s: None,
            running=lambda: alive,
            launch=lambda: (launches.append(1), True)[1],
        )
    return cold, launches, log.getvalue()


print("export provider liveness selftest")
print()

# 1. Provider absent: the wait names the real cause and tries to start it.
cold, launches, log = run_wait([ColdFile("vendors.xlsx")], alive=False)
check("an absent provider is named, not reported as merely 'still cold'",
      "no OneDrive process is running" in log,
      "the log would say the files are cold and never say why, which is exactly "
      "what five nights of nightly logs said while OneDrive was not running")
check("an absent provider triggers a launch attempt", len(launches) >= 1,
      "nothing would ever start OneDrive for a scheduled run, so the warm-up "
      "budget is spent on a condition it cannot change")
check("the launch is attempted exactly once per wait", len(launches) == 1,
      f"{len(launches)} launches were issued across the budget — a relaunch per "
      "poll is a launch loop, not a recovery")
check("the wait still fails open with an absent provider", cold and len(cold) == 1,
      "the cold list must still come back so each target runs and records its "
      "own receipt; raising here kills the step before it exports anything")
check("an exhausted budget says the launch did not take",
      "still no OneDrive process after the launch attempt" in log,
      "a spent budget must distinguish 'slow' from 'absent', because the remedy "
      "differs: pin the folder versus start the client")

# 2. Provider present: nothing is launched, and the disk/pinning reading stands.
cold, launches, log = run_wait([ColdFile("vendors.xlsx")], alive=True)
check("a running provider is never relaunched", launches == [],
      "a spurious `open` would bury the correct reading — the tree is cloud-only "
      "and the remedy is pinning or free disk, not starting OneDrive")
check("a running provider is reported as up", "provider up" in log,
      "the poll line must carry the liveness finding or the log cannot be read "
      "back to tell the two failures apart")

# 3. Untestable machine: no finding, and no false absence.
cold, launches, log = run_wait([ColdFile("vendors.xlsx")], alive=None)
check("an untestable provider state is not narrated as an absent provider",
      "no OneDrive process is running" not in log,
      "provider_running() returns None when pgrep is missing; claiming absence "
      "from that would put a wrong cause in the nightly log")
check("an untestable provider state launches nothing", launches == [],
      "a launch on an unknown state is an action taken on no evidence")
check("an untestable provider state is said plainly",
      "provider state unknown" in log,
      "silence reads as 'not checked' and sends the next session to re-derive it")

# 4. A warm file returns immediately, before any liveness work.
cold, launches, log = run_wait([WarmFile("vendors.xlsx")], alive=False)
check("a readable file returns at once and launches nothing",
      cold == [] and launches == [],
      "the happy path must not pay for the probe, and must not start OneDrive "
      "on a night when everything already works")

# 5. A non-transient errno escapes immediately — unchanged by this fix.
cold, launches, log = run_wait([ColdFile("vendors.xlsx", errno.EACCES)], alive=False)
check("a non-transient errno is still returned without waiting or launching",
      len(cold) == 1 and launches == [],
      "a permission or path failure reads the same on the last attempt as the "
      "first; spending the budget or starting OneDrive only delays the report")

# 6. provider_running() reports None rather than False when pgrep is missing.
real_which = common.shutil.which
try:
    common.shutil.which = lambda _name: None  # type: ignore[assignment]
    check("provider_running() is None without pgrep, never False",
          common.provider_running() is None,
          "a machine that cannot run the probe must produce no finding; False "
          "would be an absence this code never actually observed")
finally:
    common.shutil.which = real_which

# 7. start_provider() never raises, whatever the launcher does.
check("start_provider() reports False rather than raising",
      common.start_provider(run=lambda *_a, **_k: (_ for _ in ()).throw(OSError("no open"))) is False,
      "a failed launch is one more thing the wait reports, not a new way for the "
      "export step to die before it exports anything")

print()
if failures:
    print(f"FAIL — {len(failures)} of {passes + len(failures)} checks failed")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"PASS — {passes} checks")
