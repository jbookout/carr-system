#!/usr/bin/env python3
"""
weekend-quiet-gate-selftest.py — fixtures for hooks/weekend-quiet-gate.py,
written before it (rule e65efc68).

THE RULE, 236ca227: weekends are off for both partners. Saturday and Sunday are
not workdays for Joe or Dell.

THE HOLE, from the 2026-08-14 enforceability audit and verified still open
before this was built: the session brief STATES the policy at boot and nothing
denies a weekend ping. The record layer has no send verb at all — that half is
structurally absent, and the audit says so. But a session reaches the partners
through tools the record layer never sees: a push notification, and the mail
connector that drafts and sends to Joe's own address. No hook matcher covered
either, and nothing anywhere looked at the day of the week.

WHAT MAKES THIS ENFORCEABLE WHERE MOST OF THE RAIL RULES ARE NOT. "Is it
Saturday" is a predicate, not a judgment (rule 5e89c211: never spend a
cognition token on a decision a predicate can make). Most of the audit's
partial rows are advisory because the binding condition needs judgment. This
one does not.

THE EXCEPTION IS THE PART THAT MATTERS, and it comes from the rule's own text:
"human elects to work weekend". A partner typing into a session on a Sunday IS
that election — he is at the keyboard, he opened the session, and refusing to
answer him would be absurd. So the gate reads his keystrokes, exactly as the
escalation gate does for its "he asked" exemption, and a session cannot grant
itself the exemption.

WHAT MUST STAY TRUE:
  1. A weekday send is never touched. This is the overwhelming common case.
  2. A weekend send with NO human present is refused.
  3. A weekend send is ALLOWED once the partner has spoken in the session.
  4. Reads and non-sending tools are never gated, whatever the day.
  5. It fails OPEN on every error, including an unreadable transcript.

RUNNING IT. No database, no network, no vault:

    .venv/bin/python ops/weekend-quiet-gate-selftest.py
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "weekend-quiet-gate.py"

passed = 0
failures: list[str] = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


# A fixed Saturday and Monday in the partners' zone, so the suite does not
# pass or fail depending on the day it happens to run.
SATURDAY = "2026-08-15T10:00:00-05:00"
SUNDAY = "2026-08-16T10:00:00-05:00"
MONDAY = "2026-08-17T10:00:00-05:00"


def run(tool, when, human_spoke, tool_input=None):
    """Drive the real hook. `when` is injected so the test is date-stable."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as fh:
            if human_spoke:
                fh.write(json.dumps({"type": "user", "origin": {"kind": "user"},
                                     "message": {"content": [{"type": "text",
                                     "text": "working through the weekend on this"}]}}) + "\n")
            fh.write(json.dumps({"type": "assistant",
                                 "message": {"content": [{"type": "text",
                                 "text": "on it"}]}}) + "\n")
        payload = {"tool_name": tool, "session_id": "selftest",
                   "transcript_path": path,
                   "tool_input": tool_input or {"message": "the brief is ready"}}
        env = dict(os.environ, CARR_NOW=when)
        p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env)
        return p.returncode == 2, (p.stdout or "") + (p.stderr or "")
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


print("\nhooks/weekend-quiet-gate.py — Saturday and Sunday are not workdays (236ca227)")

if not HOOK.exists():
    print(f"  FAIL  the gate does not exist at {HOOK}")
    print("\n1 check(s) failed: not implemented")
    sys.exit(1)

# ── 1. the common case: a weekday send is untouched ─────────────────────────
for tool in ("PushNotification", "mcp__mail__send_message", "mcp__mail__create_draft"):
    blocked, _ = run(tool, MONDAY, human_spoke=False)
    check(f"a weekday {tool.split('__')[-1]} is allowed", not blocked)

# ── 2. the hole this closes ─────────────────────────────────────────────────
for day, name in ((SATURDAY, "Saturday"), (SUNDAY, "Sunday")):
    blocked, out = run("PushNotification", day, human_spoke=False)
    check(f"a {name} push with no partner present is refused", blocked)
    blocked, _ = run("mcp__mail__send_message", day, human_spoke=False)
    check(f"a {name} mail send with no partner present is refused", blocked)

blocked, out = run("PushNotification", SATURDAY, human_spoke=False)
check("the refusal names the rule in plain words",
      "weekend" in out.lower() and "monday" in out.lower(), out[:120])

# ── 3. the exception, read off HIS keystrokes ───────────────────────────────
for tool in ("PushNotification", "mcp__mail__send_message"):
    blocked, _ = run(tool, SATURDAY, human_spoke=True)
    check(f"a weekend {tool.split('__')[-1]} IS allowed once he is here", not blocked,
          "a partner typing on a Sunday is the election the rule names")

# ── 4. everything else is untouched, whatever the day ───────────────────────
for tool in ("Read", "Bash", "mcp__carr__add-loop", "mcp__mail__search_threads",
             "SendMessage"):
    blocked, _ = run(tool, SATURDAY, human_spoke=False)
    check(f"a weekend {tool} is not gated", not blocked,
          "only partner-facing SENDS are in scope")

# ── 5. fail open ────────────────────────────────────────────────────────────
p = subprocess.run([sys.executable, str(HOOK)], input="not json",
                   capture_output=True, text=True)
check("malformed input fails open", p.returncode == 0)
payload = {"tool_name": "PushNotification", "transcript_path": "/nonexistent",
           "tool_input": {"message": "x"}}
p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                   capture_output=True, text=True,
                   env=dict(os.environ, CARR_NOW="not-a-date"))
check("an unparseable clock fails open", p.returncode == 0,
      "a gate that cannot tell the day must not guess")

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("WEEKEND QUIET GATE SELFTEST PASSED: the partners' weekend is quiet "
      "unless they are the ones working.")
