#!/usr/bin/env python3
"""rule-shape-gate-selftest.py — fixtures for hooks/rule-shape-gate.py.

Spawns the REAL hook with REAL teach/activate-rule payloads and reads its
stdout. This gate never denies (exit code is always 0 — see the hook's own
docstring: "IT WARNS, IT NEVER BLOCKS"), so the signal under test is whether
the advisory `additionalContext` was printed at all, not an exit code.

ADDED 2026-08-14 for the rule-enforceability audit's teach-time bite: the
activate-rule half nudges a session to classify a rule's enforcement the
moment it goes live, rather than letting it join the silent default the audit
found. The teach-shape half already existed and had no selftest; this file
now covers both halves of the one gate.
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "rule-shape-gate.py")
TOOL_PREFIX = "mcp__b36e17b6-7e3b-4e65-b890-21f21d538440__"

failures: list[str] = []
total = 0


def run(tool_input, tool="activate-rule"):
    payload = {"tool_name": TOOL_PREFIX + tool, "tool_input": tool_input}
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=15)
    return p


def fires(p):
    if not p.stdout.strip():
        return False
    try:
        out = json.loads(p.stdout)
    except Exception:
        return False
    ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    return bool(ctx.strip())


def check(name, cond, detail=""):
    global total
    total += 1
    ok = bool(cond)
    print(f"  {'ok  ' if ok else 'FAIL'} {name} {'' if ok else detail}")
    if not ok:
        failures.append(name)
    return ok


def make_fixture_map(entries):
    """A throwaway rule-enforcement-map.json this run points HOOK at, via a
    real repo copy under REPO substitution — the hook resolves its map path
    from its own file location, so the cleanest fixture is a real temp repo
    with hooks/rule-shape-gate.py and ops/config/rule-enforcement-map.json in
    the expected relative layout, invoked directly rather than through the
    real checkout's map."""
    tmp = tempfile.mkdtemp(prefix="rule-shape-gate-selftest-")
    os.makedirs(os.path.join(tmp, "hooks"))
    os.makedirs(os.path.join(tmp, "ops", "config"))
    with open(os.path.join(tmp, "hooks", "rule-shape-gate.py"), "w") as fh:
        fh.write(open(HOOK).read())
    with open(os.path.join(tmp, "hooks", "hook_meter.py"), "w") as fh:
        fh.write(open(os.path.join(REPO, "hooks", "hook_meter.py")).read())
    with open(os.path.join(tmp, "ops", "config", "rule-enforcement-map.json"), "w") as fh:
        json.dump({"rule_controls": entries}, fh)
    return tmp


def run_against(tmp, tool_input, tool="activate-rule"):
    hook_copy = os.path.join(tmp, "hooks", "rule-shape-gate.py")
    payload = {"tool_name": TOOL_PREFIX + tool, "tool_input": tool_input}
    p = subprocess.run([sys.executable, hook_copy], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=15)
    return p


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}")
        return 1

    # --- teach half: unaffected regression coverage -----------------------
    p = run({"statement": "The system logs Joe's activity when he mentions it, "
                           "and captures the detail for later."}, tool="teach")
    check("teach: proactive statement with no trigger/audit still warns", fires(p),
          f"stdout={p.stdout!r}")

    p = run({"statement": "Never store a client SSN in any record layer field."},
            tool="teach")
    check("teach: pure constraint does not warn", not fires(p), f"stdout={p.stdout!r}")

    p = run({}, tool="some-other-verb")
    check("unrelated tool is a no-op", not fires(p) and p.returncode == 0,
          f"rc={p.returncode} stdout={p.stdout!r}")

    # --- activate-rule half: the new classification nudge ------------------
    classified = make_fixture_map({
        "aaaaaaaa": {"category": "hard_pre_action", "enforcement_class": "deny_gate",
                     "binding_moment": "x", "control": "y", "exceptions": "z"},
        "dddddddd": {"category": "judgment_advisory", "enforcement_class": "unbuilt",
                     "planned_control": "already pending"},
    })
    try:
        p = run_against(classified, {"idempotency_key": "k", "rule_id": "aaaaaaaa"})
        check("activate-rule: already-classified (built) rule does not warn",
              not fires(p), f"stdout={p.stdout!r}")

        p = run_against(classified, {"idempotency_key": "k", "rule_id": "dddddddd"})
        check("activate-rule: already unbuilt/pending rule does not warn",
              not fires(p), f"stdout={p.stdout!r}")

        p = run_against(classified, {"idempotency_key": "k", "rule_id": "cccccccc"})
        check("activate-rule: unclassified short-id rule DOES warn", fires(p),
              f"stdout={p.stdout!r}")

        p = run_against(classified, {"idempotency_key": "k",
                                     "rule_id": "cccccccc-1111-2222-3333-444444444444"})
        ok = fires(p)
        out = json.loads(p.stdout) if ok else {}
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        check("activate-rule: full-uuid form is normalized to the short id",
              ok and "cccccccc" in ctx, f"stdout={p.stdout!r}")
        check("activate-rule: advisory names the obligation rule and the fix",
              "ab814a26" in ctx and "rule-enforcement-map.json" in ctx
              and "run.sh health" in ctx,
              f"ctx={ctx!r}")

        p = run_against(classified, {"idempotency_key": "k", "rule_id": "not-a-rule-id"})
        check("activate-rule: malformed rule_id fails open, no warn", not fires(p),
              f"stdout={p.stdout!r}")
    finally:
        subprocess.run(["rm", "-rf", classified])

    print()
    print(f"rule-shape-gate-selftest: {total - len(failures)}/{total} passed")
    if failures:
        print(f"FAILURES: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
