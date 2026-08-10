#!/usr/bin/env python3
"""gate-edit-gate-selftest.py — fixtures for hooks/gate-edit-gate.py.

Spawns the REAL hook with a REAL Write/Edit payload and reads its exit code.
Exit 2 = denied (must be put to Joe), exit 0 = allowed.

THE ALLOW HALF IS THE IMPORTANT HALF. This gate sits in front of Write/Edit,
the most-used tools in the system. If it denies ordinary work it will be ripped
out within a day, which is the same outcome as never building it. Every normal
file — vault docs, repo code, scheduled tasks, runners, even the council and
precheck scripts — must pass untouched.

THE SELFTEST EXEMPTION IS TESTED EXPLICITLY. Fixtures prove a gate change; they
are not the gate. If gating them ever creeps in, testing a gate becomes harder
than weakening one, which is backwards.
"""
import json
import os
import subprocess
import sys

REPO = os.path.expanduser("~/carr-system")
HOME = os.path.expanduser("~")
HOOK = os.path.join(REPO, "hooks", "gate-edit-gate.py")

# (name, tool, path, expect_deny)
CASES = [
    # ── MUST DENY: these files ARE the enforcement ──────────────────────────
    ("gate-logic",        "Edit",  f"{REPO}/hooks/conduct-stop-gate.py", True),
    ("escalation-gate",   "Write", f"{REPO}/hooks/escalation-gate.py", True),
    ("shared-classifier", "Edit",  f"{REPO}/hooks/conduct_patterns.py", True),
    ("integrity-hook",    "Edit",  f"{REPO}/hooks/gate-integrity.py", True),
    ("egress-guard",      "Edit",  f"{REPO}/hooks/guard-unattended.py", True),
    ("this-gate-itself",  "Edit",  f"{REPO}/hooks/gate-edit-gate.py", True),
    ("wiring-repo",       "Edit",  f"{REPO}/ops/config/hooks.json", True),
    ("wiring-live",       "Edit",  f"{HOME}/.claude/settings.json", True),
    ("wiring-vault",      "Edit",  f"{HOME}/My Drive/CARR AI/.claude/settings.json", True),
    ("the-lock",          "Edit",  f"{REPO}/ops/harden-gates.sh", True),
    ("baseline",          "Write", f"{REPO}/ops/config/gate-baseline.json", True),
    ("multiedit-gate",    "MultiEdit", f"{REPO}/hooks/ledger-sweep.py", True),

    # ── MUST ALLOW: fixtures prove a gate change, they are not the gate ─────
    ("selftest-conduct",  "Write", f"{REPO}/ops/conduct-gate-selftest.py", False),
    ("selftest-escal",    "Edit",  f"{REPO}/ops/escalation-gate-selftest.py", False),
    ("selftest-this",     "Write", f"{REPO}/ops/gate-edit-gate-selftest.py", False),
    ("selftest-git",      "Edit",  f"{REPO}/ops/git-writer-gate-selftest.py", False),

    # ── MUST ALLOW: ordinary work, the overwhelming majority of edits ───────
    ("runner-council",    "Edit",  f"{REPO}/bin/council.sh", False),
    ("runner-precheck",   "Edit",  f"{REPO}/bin/precheck.sh", False),
    ("council-lib",       "Edit",  f"{REPO}/bin/council-lib.sh", False),
    ("ops-other",         "Edit",  f"{REPO}/ops/config-as-code.py", False),
    ("scheduled-task",    "Edit",  f"{REPO}/ops/scheduled-tasks/nightly-record-layer.SKILL.md", False),
    ("mcp-server",        "Edit",  f"{REPO}/mcp-server/src/index.js", False),
    ("migration",         "Write", f"{REPO}/migrations/0082_example.sql", False),
    ("vault-doc",         "Edit",  f"{HOME}/My Drive/CARR AI/00_Context/notes.md", False),
    ("settings-local",    "Edit",  f"{HOME}/.claude/settings.local.json", False),
    ("scratch",           "Write", "/tmp/scratch.py", False),
    ("readme",            "Edit",  f"{REPO}/README.md", False),
]


def fire(tool, path):
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_name": tool,
                          "tool_input": {"file_path": path},
                          "session_id": "selftest"}),
        capture_output=True, text=True, timeout=30)
    return p.returncode == 2


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}")
        return 1
    passed = failed = 0
    bad = []
    for name, tool, path, expect in CASES:
        got = fire(tool, path)
        ok = (got == expect)
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        if not ok:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:18} {tool:9} "
              f"want={'DENY ' if expect else 'allow'} got={'DENY' if got else 'allow'}")
    print()
    print(f"gate-edit-gate-selftest: {passed}/{passed + failed} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
