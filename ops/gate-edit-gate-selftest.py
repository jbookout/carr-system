#!/usr/bin/env python3
"""gate-edit-gate-selftest.py — fixtures for the gate-protection layer, BOTH doors.

Spawns the REAL hooks with REAL payloads and reads what they actually do.

THREE VERDICTS, NOT TWO — and the third is the whole point of this rewrite.
Before 2026-08-10 this file asserted DENY on every enforcement file. Joe
downgraded the gate to announce-and-allow that morning (decision bd30b665) and
the fixtures were never moved, so the suite sat at 15/27 with all twelve
protection cases red. A suite that is chronically red detects nothing — it is
the identical failure this system already ate on 2026-08-08, when a stale
baseline made a real five-gate wipe print the same headline as a benign drift.

So "allow" alone is no longer a passing answer for a protected file. ANNOUNCE
means allowed AND the session was told, in the tool result, that enforcement
just changed. If someone deletes the announce block, the call still succeeds and
the old style of test would still pass — this one goes red, which is correct,
because a silent gate edit is exactly the thing loop #231 was opened about.

    ANNOUNCE  exit 0 + the announcement on stdout   (protected file)
    ALLOW     exit 0, silent                        (ordinary work)
    DENY      exit 2                                (nothing here should)

BOTH DOORS ARE COVERED HERE. The Write/Edit door is gate-edit-gate.py; the Bash
door is guard-unattended.py, added 2026-08-10 after the shell path was proven
open — append, `sed -i`, `>` onto settings.json and tee onto a gate all returned
a silent ALLOW, while gate-edit-gate.py's own docstring had claimed since
2026-08-09 that guard-unattended.py covered them. Both doors read one list
(hooks/gate_paths.py), so a fixture that passes on one and fails on the other is
reporting real drift rather than a fixture bug.

THE ALLOW HALF IS STILL THE IMPORTANT HALF. These gates sit in front of the
most-used tools in the system. If they announce on ordinary work the noise makes
them worth ignoring, which is the same outcome as never building them. Every
normal file must come back silent.

THE SELFTEST EXEMPTION IS TESTED EXPLICITLY. Fixtures prove a gate change; they
are not the gate. If gating them ever creeps in, testing a gate becomes harder
than weakening one, which is backwards.
"""
import json
import os
import subprocess
import sys

# See the note in git-writer-gate-selftest.py: derive the repo from this file's
# own location so the selftest works at any clone path, not just ~/carr-system.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
EDIT_HOOK = os.path.join(REPO, "hooks", "gate-edit-gate.py")
BASH_HOOK = os.path.join(REPO, "hooks", "guard-unattended.py")

ANNOUNCE, ALLOW, DENY = "ANNOUNCE", "ALLOW", "DENY"

# ── door 1: Write / Edit / MultiEdit ─────────────────────────────────────────
# (name, tool, path, expect)
EDIT_CASES = [
    # these files ARE the enforcement — allowed, but never quietly
    ("gate-logic",        "Edit",      f"{REPO}/hooks/conduct-stop-gate.py", ANNOUNCE),
    ("escalation-gate",   "Write",     f"{REPO}/hooks/escalation-gate.py", ANNOUNCE),
    ("shared-classifier", "Edit",      f"{REPO}/hooks/conduct_patterns.py", ANNOUNCE),
    ("integrity-hook",    "Edit",      f"{REPO}/hooks/gate-integrity.py", ANNOUNCE),
    ("egress-guard",      "Edit",      f"{REPO}/hooks/guard-unattended.py", ANNOUNCE),
    ("this-gate-itself",  "Edit",      f"{REPO}/hooks/gate-edit-gate.py", ANNOUNCE),
    ("shared-path-list",  "Edit",      f"{REPO}/hooks/gate_paths.py", ANNOUNCE),
    ("wiring-repo",       "Edit",      f"{REPO}/ops/config/hooks.json", ANNOUNCE),
    ("wiring-live",       "Edit",      f"{HOME}/.claude/settings.json", ANNOUNCE),
    ("wiring-vault",      "Edit",      f"{HOME}/My Drive/CARR AI/.claude/settings.json", ANNOUNCE),
    ("the-lock",          "Edit",      f"{REPO}/ops/harden-gates.sh", ANNOUNCE),
    ("baseline",          "Write",     f"{REPO}/ops/config/gate-baseline.json", ANNOUNCE),
    ("multiedit-gate",    "MultiEdit", f"{REPO}/hooks/ledger-sweep.py", ANNOUNCE),

    # fixtures prove a gate change, they are not the gate
    ("selftest-conduct",  "Write", f"{REPO}/ops/conduct-gate-selftest.py", ALLOW),
    ("selftest-escal",    "Edit",  f"{REPO}/ops/escalation-gate-selftest.py", ALLOW),
    ("selftest-this",     "Write", f"{REPO}/ops/gate-edit-gate-selftest.py", ALLOW),
    ("selftest-git",      "Edit",  f"{REPO}/ops/git-writer-gate-selftest.py", ALLOW),

    # ordinary work, the overwhelming majority of edits
    ("runner-council",    "Edit",  f"{REPO}/bin/council.sh", ALLOW),
    ("runner-precheck",   "Edit",  f"{REPO}/bin/precheck.sh", ALLOW),
    ("council-lib",       "Edit",  f"{REPO}/bin/council-lib.sh", ALLOW),
    ("ops-other",         "Edit",  f"{REPO}/ops/config-as-code.py", ALLOW),
    ("scheduled-task",    "Edit",  f"{REPO}/ops/scheduled-tasks/nightly-record-layer.SKILL.md", ALLOW),
    ("mcp-server",        "Edit",  f"{REPO}/mcp-server/src/index.js", ALLOW),
    ("migration",         "Write", f"{REPO}/migrations/0082_example.sql", ALLOW),
    ("vault-doc",         "Edit",  f"{HOME}/My Drive/CARR AI/00_Context/notes.md", ALLOW),
    ("settings-local",    "Edit",  f"{HOME}/.claude/settings.local.json", ALLOW),
    ("scratch",           "Write", "/tmp/scratch.py", ALLOW),
    ("readme",            "Edit",  f"{REPO}/README.md", ALLOW),
]

# ── door 2: Bash ─────────────────────────────────────────────────────────────
# The four shapes below are the ones verified OPEN on 2026-08-10, before this
# door existed. They are the regression test for loop #231's actual finding.
BASH_CASES = [
    ("sh-append-guard",   f'echo "KNOWN_HOSTS.add(1)" >> {REPO}/hooks/guard-unattended.py', ANNOUNCE),
    ("sh-sed-inplace",    f'sed -i "" s/x/y/ {REPO}/hooks/record-home-gate.py', ANNOUNCE),
    ("sh-clobber-live",   f'echo {{}} > {HOME}/.claude/settings.json', ANNOUNCE),
    ("sh-tee-gate",       f'cat x | tee {REPO}/hooks/gate-edit-gate.py', ANNOUNCE),
    # turning a gate off needs no write at all — the quietest removal there is
    ("sh-chmod-off",      f'chmod -x {REPO}/hooks/lint-gate.py', ANNOUNCE),
    ("sh-rm-gate",        f'rm {REPO}/hooks/rule-shape-gate.py', ANNOUNCE),
    ("sh-cp-over-gate",   f'cp /tmp/evil.py {REPO}/hooks/ledger-sweep.py', ANNOUNCE),
    ("sh-python-write",   f'python3 -c "open(\'{REPO}/hooks/gate-integrity.py\',\'w\').write(1)"', ANNOUNCE),
    ("sh-baseline",       f'echo x >> {REPO}/ops/config/gate-baseline.json', ANNOUNCE),

    # READING a gate is ordinary work and must stay silent — a gate that fires
    # on `cat` or `grep` is a gate people learn to ignore.
    ("sh-read-cat",       f'cat {REPO}/hooks/guard-unattended.py', ALLOW),
    ("sh-read-grep",      f'grep -n KNOWN_HOSTS {REPO}/hooks/guard-unattended.py', ALLOW),
    ("sh-read-wc",        f'wc -l {REPO}/hooks/*.py', ALLOW),
    ("sh-copy-out",       f'cp {REPO}/hooks/lint-gate.py /tmp/backup.py', ALLOW),
    ("sh-run-selftest",   f'python3 {REPO}/ops/guard-selftest.py', ALLOW),
    ("sh-selftest-write", f'echo x >> {REPO}/ops/gate-edit-gate-selftest.py', ALLOW),
    ("sh-ordinary",       f'echo x >> {REPO}/README.md', ALLOW),

    # a stronger rule must still win — this is destructive, not a gate tweak
    ("sh-destructive",    f'rm -rf {REPO}/hooks', DENY),
]


def verdict(hook, payload):
    p = subprocess.run([sys.executable, hook], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=30)
    if p.returncode == 2:
        return DENY
    out = (p.stdout or "")
    if "GATE CHANGE ANNOUNCED" in out or "GATE EDIT ANNOUNCED" in out:
        return ANNOUNCE
    return ALLOW


def main():
    for h in (EDIT_HOOK, BASH_HOOK):
        if not os.path.exists(h):
            print(f"FAIL: hook not found at {h}")
            return 1

    bad = []
    total = 0

    print("  ── door 1: Write / Edit / MultiEdit  (hooks/gate-edit-gate.py)")
    for name, tool, path, expect in EDIT_CASES:
        got = verdict(EDIT_HOOK, {"tool_name": tool, "tool_input": {"file_path": path},
                                  "session_id": "selftest"})
        ok = got == expect
        total += 1
        if not ok:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:18} {tool:9} "
              f"want={expect:8} got={got}")

    print("\n  ── door 2: Bash  (hooks/guard-unattended.py)")
    for name, cmd, expect in BASH_CASES:
        got = verdict(BASH_HOOK, {"tool_name": "Bash", "tool_input": {"command": cmd},
                                  "session_id": "selftest"})
        ok = got == expect
        total += 1
        if not ok:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:18} {'Bash':9} "
              f"want={expect:8} got={got}")

    print()
    print(f"gate-edit-gate-selftest: {total - len(bad)}/{total} passed "
          f"· {len(EDIT_CASES)} edit-door, {len(BASH_CASES)} bash-door")
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
