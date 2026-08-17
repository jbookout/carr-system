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
import json
import atexit
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "bin" / "calendar-eventkit-capture.sh"

failures: list[str] = []
fixture_roots: list[Path] = []


def cleanup_fixtures() -> None:
    for root in fixture_roots:
        shutil.rmtree(root, ignore_errors=True)


atexit.register(cleanup_fixtures)


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def fixture(app_exists=True, appends="", *, dump_json="", matcher_json=""):
    """A throwaway CARR_REPO with a stub `open` that appends `appends` to the log."""
    root = Path(tempfile.mkdtemp(prefix="calcap-"))
    fixture_roots.append(root)
    (root / "out").mkdir()
    (root / "bin").mkdir()
    (root / "tools").mkdir()
    if app_exists:
        (root / "tools" / "CARR Calendar Access.app").mkdir()
    # The stub stands in for the bundle: it writes whatever this case needs into
    # the access log, exactly as the real bundle does, and never touches a calendar.
    stub = root / "stubbin"
    stub.mkdir()
    open_script = (
        "#!/bin/sh\n"
        f'target="${{CARR_CALENDAR_OUTPUT_ROOT:-{root}/out}}"\n'
        'mkdir -p "$target"\n'
        f"cat >> \"$target/calendar-access.log\" <<'EOF'\n{appends}\nEOF\n"
    )
    if dump_json:
        open_script += f"cat > \"$target/calendar-attendees.json\" <<'EOF'\n{dump_json}\nEOF\n"
    open_script += "exit 0\n"
    (stub / "open").write_text(open_script)
    (stub / "open").chmod(0o755)
    if matcher_json:
        (root / "tools" / "calendar-touch-matcher.py").write_text(
            "#!/usr/bin/env python3\n"
            f"print({matcher_json!r})\n")
    return root, stub


def run(root, stub, *args, timeout=90, extra_env=None):
    env = dict(os.environ)
    env["CARR_REPO"] = str(root)
    env["CARR_CALENDAR_CAPTURE_WAIT_SECONDS"] = "2"
    env["PATH"] = f"{stub}:{env['PATH']}"
    env.update(extra_env or {})
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

# 5. The Control Plane shadow stores stdout in an immutable receipt.  Its
# aggregate-only mode must isolate scratch files and never print attendee data.
sensitive = {
    "counts": {"emails": 3, "exact": 1, "domain": 1, "unknown": 1,
               "internal": 0, "upcoming": 0},
    "exact": [{"ref": "L-PRIVATE", "email": "person@example.com",
               "last_seen": "2026-08-16", "events": []}],
    "domain": [{"email": "domain@example.com", "org": "Private Org"}],
    "unknown": [{"email": "unknown@example.com", "last_seen": "2026-08-16"}],
}
root, stub = fixture(
    appends="events scanned: 12; carrying attendees: 3\nexit=0",
    dump_json="{}", matcher_json=json.dumps(sensitive))
isolated = root / "isolated-calendar-shadow"
p = run(root, stub, "--dry-run", "--receipt-safe", "--days", "7",
        extra_env={"CARR_CALENDAR_OUTPUT_ROOT": str(isolated)})
out = p.stdout + p.stderr
check("receipt-safe EventKit shadow succeeds with a finite aggregate marker",
      p.returncode == 0
      and "calendar-capture: source=eventkit mode=shadow scanned=12 exact=1 domain=1 unknown=1 writes=0 failed=0" in out,
      f"exit={p.returncode} output={out[-300:]}")
check("receipt-safe EventKit shadow prints no attendee or record identity",
      not any(value in out for value in (
          "L-PRIVATE", "person@example.com", "domain@example.com",
          "unknown@example.com", "Private Org")))
check("receipt-safe EventKit shadow confines scratch evidence to its output root",
      (isolated / "calendar-access.log").is_file()
      and (isolated / "calendar-attendees.json").is_file()
      and (isolated / "calendar-touch-proposals.json").is_file()
      and not (root / "out" / "calendar-access.log").exists())

print(f"\n{'OK all checks passed' if not failures else f'FAIL {len(failures)}: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
