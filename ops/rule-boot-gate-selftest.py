#!/usr/bin/env python3
"""Selftest for hooks/rule-boot-gate.py, lib/rule_boot_gate.py and the
SessionStart re-arm in hooks/gate-integrity.py.

Runs the real hook as a subprocess against a throwaway state directory
(CARR_RULE_BOOT_STATE_DIR) with the store answer stubbed
(CARR_RULE_BOOT_FETCH_STUB), so it is offline and deterministic.

Cases run in the risk order Jev gave on 2026-09-26 (out/jev-judge.jsonl, kind
rule-boot-gate-test-risk): unreachable-no-deadlock 0.79, fetch-never-denied
0.77, deny-before/allow-after 0.74, digest-change re-arms 0.69, re-arm on
compact 0.68, subagent path 0.65. Pages-complete (0.54) and no-sponsor-leak
(0.34) are properties of the verb and are proven in
mcp-server/test/rule-boot.test.mjs.

PLANTED MUTANTS. The same cases are re-run against copies of the gate with
one defect planted each; every mutant must turn at least one case red:
  never-denies, denies-the-fetch-itself (deadlock), no-re-arm-after-compact,
  digest-change-ignored, deadlock-after-unreachable-attempt.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION = "sess-selftest"
RUN_SH = os.path.join(REPO, "run.sh")


class Case:
    def __init__(self, tree, work):
        self.tree = tree
        self.work = work
        self.state = os.path.join(work, "state")
        self.env = {**os.environ, "CARR_RULE_BOOT_STATE_DIR": self.state,
                    "CARR_HOOK_GUARD_LOG": os.path.join(work, "guard.log")}
        self.env.pop("CARR_RULE_BOOT_FETCH_STUB", None)

    def stub(self, digest=None, pages=3):
        if digest is None:
            self.env["CARR_RULE_BOOT_FETCH_STUB"] = "unreachable"
            return
        path = os.path.join(self.work, f"stub-{digest}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"ok": True, "rule_boot": {"digest": f"sha256:{digest * 8}", "page": 1,
                                                 "pages_total": pages, "text": "x"}}, fh)
        self.env["CARR_RULE_BOOT_FETCH_STUB"] = path

    def arm(self, source="startup"):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "from lib.rule_boot_gate import arm_session; print(arm_session(sys.argv[2], sys.argv[3]))")
        return subprocess.run([sys.executable, "-c", code, self.tree, SESSION, source],
                              capture_output=True, text=True, env=self.env, timeout=30).stdout

    def call(self, tool, tool_input=None, agent=None, cwd=REPO):
        payload = {"hook_event_name": "PreToolUse", "session_id": SESSION, "cwd": cwd,
                   "tool_name": tool, "tool_input": tool_input or {}}
        if agent:
            payload["agent_id"] = agent
        out = subprocess.run([sys.executable, os.path.join(self.tree, "hooks", "rule-boot-gate.py")],
                             input=json.dumps(payload), capture_output=True, text=True,
                             env=self.env, timeout=30).stdout.strip()
        return json.loads(out)["hookSpecificOutput"] if out else None


def denied(r):
    return bool(r) and r.get("permissionDecision") == "deny"


def mcp_fetch(page):
    return ("mcp__carr__standing-context", {"detail": "boot", "page": page})


def bash_fetch(page):
    return ("Bash", {"command": f"./run.sh call standing-context '{{\"detail\":\"boot\",\"page\":{page}}}'"})


READ = ("Read", {"file_path": "/etc/hosts"})


# ------------------------------------------------------------------ cases

def case_unreachable_no_deadlock(c):
    c.stub(None)
    text = c.arm()
    assert "RULES UNAVAILABLE" in text, text
    assert denied(c.call(*READ)), "before any attempt an ordinary tool is held"
    r = c.call(*bash_fetch(1))
    assert not denied(r), f"the fetch attempt itself must run: {r}"
    r = c.call(*READ)
    assert not denied(r), f"after one attempt the context must be unlocked: {r}"
    assert "RULES UNAVAILABLE" in (r or {}).get("additionalContext", ""), r
    # A subagent is unlocked the same way: one attempt, never a deadlock.
    assert denied(c.call(*READ, agent="sub-1"))
    assert not denied(c.call(*mcp_fetch(1), agent="sub-1"))
    assert not denied(c.call(*READ, agent="sub-1"))
    # Unarmed session: same shape, its own notice.
    shutil.rmtree(c.state)
    assert denied(c.call(*READ))
    assert not denied(c.call(*mcp_fetch(1)))
    r = c.call(*READ)
    assert not denied(r) and "NOT ARMED" in r.get("additionalContext", ""), r


def case_fetch_never_denied(c):
    c.stub("a", pages=3)
    c.arm()
    for tool, args in (mcp_fetch(2), bash_fetch(3), mcp_fetch(1),
                       ("mcp__b36e17b6-7e3b__standing-context", {}),
                       ("mcp__carr__applicable-rules", {"situation": "x"}),
                       ("ToolSearch", {"query": "select:mcp__carr__standing-context"}),
                       ("Bash", {"command": f"{RUN_SH} call standing-context '{{\"detail\":\"boot\",\"page\":1}}'"})):
        r = c.call(tool, args)
        assert not denied(r), f"{tool} {args} must never be denied: {r}"
    # ...and nothing dressed up as one gets through.
    c2 = Case(c.tree, tempfile.mkdtemp(dir=c.work))
    c2.stub("a", pages=3)
    c2.arm()
    for command in ("./run.sh call standing-context '{\"detail\":\"boot\"}'; touch /tmp/x",
                    "./run.sh call standing-context '{\"detail\":\"boot\"}' && echo hi",
                    "./run.sh call add-loop '{\"kind\":\"idea\"}'",
                    "/tmp/elsewhere/run.sh call standing-context '{\"detail\":\"boot\"}'",
                    "./run.sh call --reason x standing-context '{\"detail\":\"boot\"}'"):
        assert denied(c2.call("Bash", {"command": command})), f"bypass not denied: {command}"
    assert denied(c2.call("mcp__carr__add-loop", {"kind": "idea"}))
    assert denied(c2.call("Bash", {"command": "./run.sh call standing-context '{\"detail\":\"boot\"}'"},
                          cwd="/tmp")), "./run.sh outside a carr-system checkout is not the fetch"


def case_deny_before_allow_after(c):
    c.stub("a", pages=3)
    text = c.arm()
    assert '"detail":"boot"' in text and "3 page(s)" in text, text
    r = c.call(*READ)
    assert denied(r), "an ordinary tool before the boot must be denied"
    reason = r["permissionDecisionReason"]
    assert "1, 2, 3" in reason and RUN_SH in reason and '"page":1' in reason, reason
    c.call(*mcp_fetch(1))
    c.call(*bash_fetch(3))
    r = c.call(*READ)
    assert denied(r) and "2" in r["permissionDecisionReason"], "page 2 still missing"
    c.call(*mcp_fetch(2))
    assert c.call(*READ) is None, "every page fetched: allowed silently"
    assert c.call("Agent", {"prompt": "x"}) is None


def case_digest_change_rearms(c):
    c.stub("a", pages=2)
    c.arm()
    for p in (1, 2):
        c.call(*mcp_fetch(p))
        c.call(*mcp_fetch(p), agent="sub-1")
    assert c.call(*READ) is None and c.call(*READ, agent="sub-1") is None
    c.stub("b", pages=2)
    c.arm("resume")
    assert denied(c.call(*READ)), "main must re-fetch a new digest"
    assert denied(c.call(*READ, agent="sub-1")), "a subagent must re-fetch a new digest"


def case_rearm_on_compact(c):
    c.stub("a", pages=2)
    c.arm()
    c.call(*mcp_fetch(1))
    c.call(*mcp_fetch(2))
    assert c.call(*READ) is None
    c.arm("compact")
    assert denied(c.call(*READ)), "a compacted context has lost the rules: re-fetch required"
    c.call(*mcp_fetch(1))
    c.call(*mcp_fetch(2))
    assert c.call(*READ) is None


def case_subagent_path(c):
    c.stub("a", pages=2)
    c.arm()
    c.call(*mcp_fetch(1))
    c.call(*mcp_fetch(2))
    assert c.call(*READ) is None, "main complete"
    r = c.call(*READ, agent="agent-7")
    assert denied(r), "a subagent is gated on its own fetches, not its parent's"
    reason = r["permissionDecisionReason"]
    assert '"detail":"boot"' in reason and RUN_SH in reason, reason
    c.call(*bash_fetch(1), agent="agent-7")
    c.call(*mcp_fetch(2), agent="agent-7")
    assert c.call(*READ, agent="agent-7") is None


CASES = [case_unreachable_no_deadlock, case_fetch_never_denied, case_deny_before_allow_after,
         case_digest_change_rearms, case_rearm_on_compact, case_subagent_path]


def run_all(tree):
    failures = []
    for case in CASES:
        work = tempfile.mkdtemp(prefix="rule-boot-gate-")
        try:
            case(Case(tree, work))
        except AssertionError as exc:
            failures.append(f"{case.__name__}: {exc}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return failures


# ------------------------------------------------------------------ gate-integrity wiring

def check_gate_integrity_rearms():
    """The real SessionStart hook arms on compact and prints the instructions."""
    work = tempfile.mkdtemp(prefix="rule-boot-gi-")
    try:
        c = Case(REPO, work)
        c.stub("c", pages=4)
        payload = {"hook_event_name": "SessionStart", "session_id": SESSION, "source": "compact"}
        out = subprocess.run([sys.executable, os.path.join(REPO, "hooks", "gate-integrity.py")],
                             input=json.dumps(payload), capture_output=True, text=True,
                             env=c.env, timeout=60).stdout
        assert "RULE BOOT" in out and "4 page(s)" in out, out[-800:]
        with open(os.path.join(c.state, SESSION, "arm.json"), encoding="utf-8") as fh:
            arm = json.load(fh)
        assert arm["status"] == "armed" and arm["source"] == "compact", arm
        # A flagged (CI) run never arms and never reads stdin.
        out = subprocess.run([sys.executable, os.path.join(REPO, "hooks", "gate-integrity.py"), "--strict"],
                             input=json.dumps({**payload, "session_id": "other"}),
                             capture_output=True, text=True, env=c.env, timeout=60).stdout
        assert "RULE BOOT" not in out and not os.path.exists(os.path.join(c.state, "other"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ mutants

MUTANTS = {
    "never-denies": [('    return "deny", fetch_instructions(missing',
                      '    return "allow", fetch_instructions(missing')],
    "denies-the-fetch-itself": [('    if kind == "fetch":\n        if page is not None:',
                                 '    if False:\n        if page is not None:'),
                                ('        if kind == "fetch":\n            record_page',
                                 '        if False:\n            record_page')],
    "no-re-arm-after-compact": [('"epoch": secrets.token_hex(6)}',
                                 '"epoch": (read_arm(session_id) or {}).get("epoch") or secrets.token_hex(6)}')],
    "digest-change-ignored": [('    digest = safe_key(str(arm.get("digest") or "").replace("sha256:", ""), "none")[:24]',
                               '    digest = "same"')],
    "deadlock-after-unreachable-attempt": [('        if fetched_pages(session_id, agent_id, stand_in):\n            return "allow", notice',
                                            '        if False:\n            return "allow", notice')],
}


def mutant_tree(root, replacements):
    tree = os.path.join(root, "tree")
    os.makedirs(os.path.join(tree, "hooks"))
    os.makedirs(os.path.join(tree, "lib"))
    shutil.copy2(os.path.join(REPO, "hooks", "rule-boot-gate.py"), os.path.join(tree, "hooks"))
    with open(os.path.join(REPO, "lib", "rule_boot_gate.py"), encoding="utf-8") as fh:
        source = fh.read()
    for before, after in replacements:
        assert source.count(before) == 1, f"mutant target not unique: {before[:60]!r}"
        source = source.replace(before, after)
    with open(os.path.join(tree, "lib", "rule_boot_gate.py"), "w", encoding="utf-8") as fh:
        fh.write(source)
    # The mutant's own ./run.sh resolution must still see this checkout's run.sh.
    with open(os.path.join(tree, "lib", "rule_boot_gate.py"), "a", encoding="utf-8") as fh:
        fh.write(f"\nREPO = {REPO!r}\n")
    return tree


def main():
    failures = run_all(REPO)
    if failures:
        print("FAIL rule-boot-gate cases:\n  " + "\n  ".join(failures))
        return 1
    check_gate_integrity_rearms()
    survived = []
    for name, replacements in MUTANTS.items():
        root = tempfile.mkdtemp(prefix=f"rule-boot-mutant-{name}-")
        try:
            caught = run_all(mutant_tree(root, replacements))
        finally:
            shutil.rmtree(root, ignore_errors=True)
        if caught:
            print(f"mutant {name}: KILLED by {caught[0].split(':')[0]}")
        else:
            survived.append(name)
    if survived:
        print("FAIL: planted mutants survived: " + ", ".join(survived))
        return 1
    print(f"rule-boot-gate-selftest: {len(CASES)} cases + gate-integrity re-arm passed; "
          f"{len(MUTANTS)} of {len(MUTANTS)} planted mutants killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
