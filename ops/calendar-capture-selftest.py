#!/usr/bin/env python3
"""calendar-capture-selftest.py — prove the unattended calendar capture refuses
rather than reporting an empty answer.

THE PROPERTY UNDER TEST is one sentence: a DENIED read and an empty calendar must
never look the same. That distinction is the whole reason this job exists in the
shape it does, and it is not theoretical — on 2026-08-14 a verb answered emptily
instead of refusing, a session read the empty answer as truth, and concluded a
settled council ruling did not exist. The same confusion in calendar capture would
silently stop touches reaching the deal record while every run reported success.

The bundle is stubbed by putting a fake `open` first on PATH, so these cases run
with no calendar, no permission prompt and no GUI — which is what lets them run in
CI on a machine that has none of those.

    .venv/bin/python ops/calendar-capture-selftest.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "bin" / "calendar-eventkit-capture.sh"

failures: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def fixture(app_exists=True, appends=""):
    """A throwaway CARR_REPO with a stub `open` that appends `appends` to the log."""
    root = Path(tempfile.mkdtemp(prefix="calcap-"))
    (root / "out").mkdir()
    (root / "bin").mkdir()
    (root / "tools").mkdir()
    if app_exists:
        (root / "tools" / "CARR Calendar Access.app").mkdir()
    # The stub stands in for the bundle: it writes whatever this case needs into
    # the access log, exactly as the real bundle does, and never touches a calendar.
    stub = root / "stubbin"
    stub.mkdir()
    (stub / "open").write_text(
        "#!/bin/sh\n"
        f"cat >> '{root}/out/calendar-access.log' <<'EOF'\n{appends}\nEOF\n"
        "exit 0\n")
    (stub / "open").chmod(0o755)
    return root, stub


def run(root, stub, *args, timeout=90):
    env = dict(os.environ)
    env["CARR_REPO"] = str(root)
    env["PATH"] = f"{stub}:{env['PATH']}"
    return subprocess.run(["sh", str(SCRIPT), *args], env=env,
                          capture_output=True, text=True, timeout=timeout)


print("calendar capture — refusal beats an empty answer")

# 1. THE CENTRAL CASE. Access denied must be a hard failure, and must NOT be
#    reported as a successful run that happened to find nothing.
root, stub = fixture(appends="RESULT: Calendars access DENIED.\nexit=3")
p = run(root, stub)
out = p.stdout + p.stderr
check("DENIED exits non-zero", p.returncode != 0, f"exit={p.returncode}")
check("DENIED exits with its OWN code (3), distinct from a generic failure",
      p.returncode == 3, f"exit={p.returncode}")
check("DENIED says permission, not emptiness", "DENIED" in out)
check("DENIED never claims zero touches",
      "no exact matches" not in out and "0 touches" not in out,
      "a permission answer was dressed up as an empty result")
check("DENIED tells the reader how to grant it",
      "Privacy" in out and "Calendars" in out)

# 2. A stale SUCCESS from an earlier run must not be mistaken for this one's.
#    A naive tail of the log would read the old success and pass.
root, stub = fixture(appends="RESULT: Calendars access DENIED.\nexit=3")
(root / "out" / "calendar-access.log").write_text(
    "--- 2026-08-13T19:13:04Z CARR Calendar Access (dump) ---\n"
    "events scanned: 936; carrying attendees: 386\n"
    "exit=0\n")
p = run(root, stub)
check("a previous run's success does not mask this run's denial",
      p.returncode == 3, f"exit={p.returncode} — the marker scoping failed")

# 3. A missing bundle is its own diagnosis, not a permission error.
root, stub = fixture(app_exists=False, appends="exit=0")
p = run(root, stub)
out = p.stdout + p.stderr
check("a missing bundle fails and names the bundle",
      p.returncode != 0 and "bundle is missing" in out, f"exit={p.returncode}")
check("a missing bundle explains WHY it matters",
      "cannot prompt" in out)

# 4. A read that never finishes must fail rather than hang forever or pass.
root, stub = fixture(appends="--- started, no exit line ever written ---")
p = run(root, stub, timeout=120)
check("a read that never completes fails", p.returncode != 0, f"exit={p.returncode}")
check("an unfinished read says so", "did not finish" in (p.stdout + p.stderr))

print(f"\n{'OK all checks passed' if not failures else f'FAIL {len(failures)}: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
