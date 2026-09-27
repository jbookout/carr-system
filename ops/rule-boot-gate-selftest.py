#!/usr/bin/env python3
"""Selftest for hooks/rule-boot-gate.py, lib/rule_boot_gate.py and the
SessionStart re-arm in hooks/gate-integrity.py.

Runs the real hook as a subprocess against a throwaway state directory
(CARR_RULE_BOOT_STATE_DIR) with the store answer stubbed
(CARR_RULE_BOOT_FETCH_STUB), so it is offline and deterministic. A fetch is
driven the way Claude Code drives it: PreToolUse, then PostToolUse with the
answer (or PostToolUseFailure with the error).

THE LOCKOUT CASES come first, in the risk order Jev gave on 2026-09-26 for
the PR #1328 review round (out/jev-judge.jsonl, kind
rule-boot-gate-lockout-test-risk-r3, second review round): deny cap 0.77,
outage after a good arm 0.75, out-of-range page is not an outage 0.65,
mid-session digest change 0.62, foreign MCP prefix 0.54, tool-less subagent
0.52, Worker not deployed 0.49, state folder unwritable (armed) 0.48, disk
full (armed) 0.48, a failure is not sticky 0.48, state folder unwritable
(never armed) 0.44, disk full (never armed) 0.40, short repeat notices 0.25. Then the first round's order (kind rule-boot-gate-test-risk):
unreachable-no-deadlock 0.79, fetch-never-denied 0.77, deny-before/allow-after
0.74, digest-change re-arms 0.69, re-arm on compact 0.68, subagent path 0.65.
Pages-complete and no-sponsor-leak are properties of the verb and are proven
in mcp-server/test/rule-boot.test.mjs.

DISK FULL is simulated with a sitecustomize module on the hook's PYTHONPATH
that makes every write under the state directory raise ENOSPC; UNWRITABLE is
a real chmod of the state folder.

PLANTED MUTANTS. The same cases are re-run against copies of the gate with
one defect planted each; every mutant must turn at least one case red (see
MUTANTS below).
"""
import json
import os
import re
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
        self.env.pop("PYTHONPATH", None)
        self.digest, self.pages = None, 0

    def stub(self, digest=None, pages=3):
        if digest in (None, "not_deployed"):
            self.env["CARR_RULE_BOOT_FETCH_STUB"] = digest or "unreachable"
            return
        self.digest, self.pages = digest, pages
        path = os.path.join(self.work, f"stub-{digest}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"ok": True, "rule_boot": self.boot(1)}, fh)
        self.env["CARR_RULE_BOOT_FETCH_STUB"] = path

    def stub_sized(self, digest, pages):
        """A stub whose pages carry real-looking text and the boot's total_chars
        (the sum of every page's text length, as rule-boot.js serves it)."""
        self.sized = True
        self.stub(digest, pages)

    def page_text(self, page):
        return f"page {page} " + "rule text — " * (40 + page)

    def boot(self, page, digest=None, pages=None):
        body = {"schema": "carr-rule-boot/v1", "digest": f"sha256:{(digest or self.digest) * 8}",
                "page": page, "pages_total": pages or self.pages, "text": "x"}
        if getattr(self, "sized", False):
            total = pages or self.pages
            body["text"] = self.page_text(page)
            body["total_chars"] = sum(len(self.page_text(p)) for p in range(1, total + 1))
        return body

    def fetch_cmd(self, command, page, cwd=REPO, agent=None, stdout=None, answer="boot", boot=None):
        """A Bash boot fetch in any shell form: PreToolUse, then PostToolUse whose
        stdout is the page JSON passed through `stdout` (what the pipe printed)."""
        args = {"command": command}
        pre = self.call("Bash", args, agent=agent, cwd=cwd)
        if answer is None:
            return pre, None
        payload = {"hook_event_name": "PostToolUse", "session_id": SESSION, "cwd": cwd,
                   "tool_name": "Bash", "tool_input": args}
        if agent:
            payload["agent_id"] = agent
        if answer == "boot":
            body = json.dumps({"ok": True, "rule_boot": boot or self.boot(page)})
            payload["tool_response"] = {"stdout": stdout(body) if stdout else body, "stderr": "",
                                        "interrupted": False}
        else:
            payload.update(hook_event_name="PostToolUseFailure", error=answer)
        return pre, self.hook(payload)

    def holds(self, agent=None):
        folder = os.path.join(self.state, SESSION, "fetched", agent or "main")
        return [n for _r, _d, files in os.walk(folder) for n in files if re.fullmatch(r"d\d+-.*", n)]

    def disk_full(self):
        """Every write under the state directory raises ENOSPC from now on."""
        folder = os.path.join(self.work, "fault")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "sitecustomize.py"), "w", encoding="utf-8") as fh:
            fh.write(FAULT_SITECUSTOMIZE)
        self.env["PYTHONPATH"] = folder

    def arm(self, source="startup"):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "from lib.rule_boot_gate import arm_session; print(arm_session(sys.argv[2], sys.argv[3]))")
        return subprocess.run([sys.executable, "-c", code, self.tree, SESSION, source],
                              capture_output=True, text=True, env=self.env, timeout=30).stdout

    def hook(self, payload):
        out = subprocess.run([sys.executable, os.path.join(self.tree, "hooks", "rule-boot-gate.py")],
                             input=json.dumps(payload), capture_output=True, text=True,
                             env=self.env, timeout=30).stdout.strip()
        return json.loads(out)["hookSpecificOutput"] if out else None

    def hooks_parallel(self, payloads):
        """Run the hook for every payload at once (a model's parallel batch)."""
        procs = [subprocess.Popen([sys.executable, os.path.join(self.tree, "hooks", "rule-boot-gate.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=self.env) for _ in payloads]
        for proc, payload in zip(procs, payloads):
            proc.stdin.write(json.dumps(payload))
            proc.stdin.close()
        outs = []
        for proc in procs:
            out = proc.stdout.read().strip()
            proc.wait(timeout=30)
            outs.append(json.loads(out)["hookSpecificOutput"] if out else None)
        return outs

    def call(self, tool, tool_input=None, agent=None, cwd=REPO, agent_type=None):
        payload = {"hook_event_name": "PreToolUse", "session_id": SESSION, "cwd": cwd,
                   "tool_name": tool, "tool_input": tool_input or {}}
        if agent:
            payload["agent_id"] = agent
        if agent_type:
            payload["agent_type"] = agent_type
        return self.hook(payload)

    def fetch(self, page, agent=None, form="mcp", answer="boot", digest=None, pages=None):
        """One boot fetch as Claude Code runs it: PreToolUse, the tool, then
        PostToolUse with the answer or PostToolUseFailure with the error.
        Returns (pre verdict, post output)."""
        tool, args = (mcp_fetch if form == "mcp" else bash_fetch)(page)
        pre = self.call(tool, args, agent=agent)
        if answer is None:
            return pre, None
        payload = {"session_id": SESSION, "cwd": REPO, "tool_name": tool, "tool_input": args}
        if agent:
            payload["agent_id"] = agent
        if answer == "boot":
            body = json.dumps({"ok": True, "rule_boot": self.boot(page, digest, pages)})
            payload.update(hook_event_name="PostToolUse", tool_response=(
                [{"type": "text", "text": body}] if form == "mcp"
                else {"stdout": body, "stderr": "", "interrupted": False}))
        elif answer == "out_of_range":
            payload.update(hook_event_name="PostToolUseFailure", error=(
                'TOOL ERROR {"error": "page_out_of_range", "page": %d, "pages_total": %d, '
                '"digest": "sha256:%s"}' % (page, pages or self.pages, (digest or self.digest) * 8)))
        elif answer == "unsupported":
            payload.update(hook_event_name="PostToolUseFailure", error=(
                'TOOL ERROR {"error": "value_not_in_declared_vocabulary", "verb": '
                '"standing-context", "field": "detail", "received": "boot"}'))
        else:
            payload.update(hook_event_name="PostToolUseFailure",
                           error="could not reach the deployed Worker: fetch failed")
        return pre, self.hook(payload)


FAULT_SITECUSTOMIZE = """
import builtins, errno, os
_ROOT = os.path.abspath(os.environ.get("CARR_RULE_BOOT_STATE_DIR") or "/nonexistent-root")
def _hit(p):
    try:
        return os.path.abspath(os.fsdecode(p)).startswith(_ROOT)
    except TypeError:
        return False
def _full(p):
    raise OSError(errno.ENOSPC, "No space left on device", os.fsdecode(p))
_open, _mkdir, _bopen = os.open, os.mkdir, builtins.open
def _os_open(p, flags, *a, **k):
    if _hit(p) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
        _full(p)
    return _open(p, flags, *a, **k)
def _os_mkdir(p, *a, **k):
    if _hit(p):
        _full(p)
    return _mkdir(p, *a, **k)
def _builtin_open(f, mode="r", *a, **k):
    if isinstance(f, (str, bytes, os.PathLike)) and _hit(f) and any(c in mode for c in "wax+"):
        _full(f)
    return _bopen(f, mode, *a, **k)
os.open, os.mkdir, builtins.open = _os_open, _os_mkdir, _builtin_open
"""


def denied(r):
    return bool(r) and r.get("permissionDecision") == "deny"


def mcp_fetch(page):
    return ("mcp__carr__standing-context", {"detail": "boot", "page": page})


def bash_fetch(page):
    return ("Bash", {"command": f"./run.sh call standing-context '{{\"detail\":\"boot\",\"page\":{page}}}'"})


READ = ("Read", {"file_path": "/etc/hosts"})


# ------------------------------------------------------------------ cases

def notice(r):
    return (r or {}).get("additionalContext", "")


def never_denied(c, n=6, **kw):
    r = None
    for i in range(n):
        r = c.call(*READ, **kw)
        assert not denied(r), f"call {i + 1} denied: {r}"
    return r


# --- lockout cases (PR #1328 review round), Jev risk order

def case_deny_cap(c):
    c.stub("a", pages=3)
    c.arm()
    for i in range(3):
        assert denied(c.call(*READ)), f"hold {i + 1} of 3 must deny"
    r = c.call(*READ)
    assert not denied(r) and "RULES UNREAD" in notice(r), f"the 4th call must pass with RULES UNREAD: {r}"
    never_denied(c)
    # Progress resets the count: after a confirmed page the context is held again, up to the cap.
    c.fetch(1)
    assert denied(c.call(*READ)), "a confirmed page resets the cap: held again"
    # A subagent is capped on its own count.
    for _ in range(3):
        assert denied(c.call(*READ, agent="sub-9"))
    assert not denied(c.call(*READ, agent="sub-9"))


def case_outage_after_good_arm(c):
    c.stub("a", pages=7)
    c.arm()
    pre, post = c.fetch(1, answer="error")
    assert not denied(pre) and "RULES UNAVAILABLE" in notice(post), post
    r = c.call(*READ)
    assert not denied(r) and "RULES UNAVAILABLE" in notice(r), f"one failed attempt must unlock: {r}"
    # Same for a subagent over the Bash form.
    c.fetch(1, agent="sub-2", form="bash", answer="error")
    r = c.call(*READ, agent="sub-2")
    assert not denied(r) and "RULES UNAVAILABLE" in notice(r), r
    # A fetch that never answered at all (no Post hook) counts as failed after the grace.
    c2 = Case(c.tree, tempfile.mkdtemp(dir=c.work))
    c2.stub("a", pages=7)
    c2.arm()
    c2.fetch(1, answer=None)
    folder = os.path.join(c2.state, SESSION, "fetched", "main")
    for root, _dirs, files in os.walk(folder):
        for name in files:
            os.utime(os.path.join(root, name), (1, 1))
    r = c2.call(*READ)
    assert not denied(r) and "RULES UNAVAILABLE" in notice(r), f"unanswered fetch past grace: {r}"


def case_mid_session_digest_change(c):
    c.stub("a", pages=3)
    c.arm()
    for p in (1, 2, 3):
        c.fetch(p)
    assert c.call(*READ) is None, "main complete on digest a"
    # The store moves to digest b with 4 pages; a new subagent's fetch reveals it.
    for p in (1, 2, 3):
        c.fetch(p, agent="sub-1", digest="b", pages=4)
    assert denied(c.call(*READ, agent="sub-1")), "3 pages of a 4-page boot must not unlock"
    assert denied(c.call(*READ)), "main must re-read after a mid-session digest change"
    c.fetch(4, agent="sub-1", digest="b", pages=4)
    assert c.call(*READ, agent="sub-1") is None
    for p in (1, 2, 3, 4):
        c.fetch(p, digest="b", pages=4)
    assert c.call(*READ) is None


def case_foreign_mcp_prefix(c):
    c.stub("a", pages=1)
    c.arm()
    for tool in ("mcp__evil__standing-context", "mcp__notcarr__applicable-rules"):
        assert denied(c.call(tool, {"detail": "boot", "page": 1})), f"{tool} is not CARR's"
    c2 = Case(c.tree, tempfile.mkdtemp(dir=c.work))
    c2.stub("a", pages=1)
    c2.arm()
    c2.call("mcp__evil__standing-context", {"detail": "boot", "page": 1})
    assert denied(c2.call(*READ)), "a foreign standing-context must not count as a fetch"


def case_disk_full_armed(c):
    c.stub("a", pages=3)
    c.arm()
    c.disk_full()
    r = never_denied(c)
    assert "UNWRITABLE" in notice(r), r
    pre, _ = c.fetch(1)
    assert not denied(pre), pre
    never_denied(c)
    never_denied(c, agent="sub-3")


def case_toolless_subagent(c):
    c.stub("a", pages=3)
    c.arm()
    r = c.call(*READ, agent="sl-1", agent_type="statusline-setup")
    assert not denied(r) and "RULES UNREAD" in notice(r), r
    never_denied(c, agent="sl-1", agent_type="statusline-setup")
    # A custom Read-only type is not known in advance: the cap unlocks it.
    for _ in range(3):
        c.call(*READ, agent="ro-1", agent_type="reader-only")
    never_denied(c, agent="ro-1", agent_type="reader-only")


def case_not_deployed_distinct(c):
    c.stub("not_deployed")
    text = c.arm()
    assert "NOT DEPLOYED" in text and "UNAVAILABLE" not in text, text
    assert "stop" not in text.lower(), f"the not-deployed notice must not tell a session to stop: {text}"
    r = c.call(*READ)
    assert not denied(r) and "NOT DEPLOYED" in notice(r), f"shown once, nothing held: {r}"
    assert c.call(*READ) is None, "shown once per context"
    never_denied(c, agent="sub-4")
    # Armed, then the fetch is rejected as unsupported (Worker rolled back).
    c2 = Case(c.tree, tempfile.mkdtemp(dir=c.work))
    c2.stub("a", pages=3)
    c2.arm()
    _, post = c2.fetch(1, answer="unsupported")
    assert "NOT DEPLOYED" in notice(post), post
    never_denied(c2)


def case_state_unwritable_armed(c):
    c.stub("a", pages=3)
    c.arm()
    os.chmod(os.path.join(c.state, SESSION), 0o555)
    try:
        r = never_denied(c)
        assert "UNWRITABLE" in notice(r), r
        pre, _ = c.fetch(1)
        assert not denied(pre)
        for _ in range(3):
            c.fetch(2)
        never_denied(c)
        never_denied(c, agent="sub-5")
    finally:
        os.chmod(os.path.join(c.state, SESSION), 0o755)


def case_state_unwritable_never_armed(c):
    os.makedirs(c.state)
    os.chmod(c.state, 0o555)
    try:
        c.fetch(1, answer=None)
        never_denied(c)
        never_denied(c, agent="sub-6")
    finally:
        os.chmod(c.state, 0o755)


def case_disk_full_never_armed(c):
    c.disk_full()
    c.fetch(1, answer=None)
    never_denied(c)
    never_denied(c, agent="sub-7")


def case_out_of_range_not_outage(c):
    c.stub("a", pages=3)
    c.arm()
    pre, post = c.fetch(9, answer="out_of_range")
    assert not denied(pre) and "does not exist" in notice(post), post
    r = c.call(*READ)
    assert denied(r), f"a page past the end must not unlock the context: {r}"
    assert "UNAVAILABLE" not in r["permissionDecisionReason"], r
    # The boot shrinks mid-session: a/4 is armed, the store now serves b/3.
    c2 = Case(c.tree, tempfile.mkdtemp(dir=c.work))
    c2.stub("a", pages=4)
    c2.arm()
    for p in (1, 2, 3):
        c2.fetch(p)
    c2.fetch(4, answer="out_of_range", digest="b", pages=3)
    r = c2.call(*READ)
    assert denied(r) and "3 page(s)" in r["permissionDecisionReason"], f"re-armed on b/3: {r}"
    for p in (1, 2, 3):
        c2.fetch(p, digest="b", pages=3)
    assert c2.call(*READ) is None


def case_failure_not_sticky(c):
    c.stub("a", pages=3)
    c.arm()
    c.fetch(1, answer="error")
    assert "UNAVAILABLE" in notice(c.call(*READ))
    for p in (1, 2, 3):
        c.fetch(p)
    assert c.call(*READ) is None, "every page confirmed after the outage: silent"


def case_notice_short_after_first(c):
    c.stub("a", pages=3)
    c.arm()
    for _ in range(3):
        c.call(*READ)
    first = notice(c.call(*READ))
    second = notice(c.call(*READ))
    assert "RULES UNREAD" in first and len(first) > 200, first
    assert second.startswith("RULES UNREAD") and len(second) < 200, second
    folder = os.path.join(c.state, SESSION, "fetched", "main")
    holds = [n for _r, _d, files in os.walk(folder) for n in files if n.startswith("d")]
    assert len(holds) == 3, f"no marker is written past the cap: {holds}"


# --- first-round cases

def case_unreachable_no_deadlock(c):
    c.stub(None)
    text = c.arm()
    assert "RULES UNAVAILABLE" in text, text
    assert denied(c.call(*READ)), "before any attempt an ordinary tool is held"
    pre, _ = c.fetch(1, form="bash", answer="error")
    assert not denied(pre), f"the fetch attempt itself must run: {pre}"
    r = c.call(*READ)
    assert not denied(r), f"after one attempt the context must be unlocked: {r}"
    assert "RULES UNAVAILABLE" in notice(r), r
    # A subagent is unlocked the same way: one attempt, never a deadlock.
    assert denied(c.call(*READ, agent="sub-1"))
    assert not denied(c.fetch(1, agent="sub-1", answer=None)[0])
    assert not denied(c.call(*READ, agent="sub-1"))
    # Unarmed session: same shape, its own notice.
    shutil.rmtree(c.state)
    assert denied(c.call(*READ))
    assert not denied(c.fetch(1, answer=None)[0])
    r = c.call(*READ)
    assert not denied(r) and "NOT ARMED" in notice(r), r


def case_fetch_never_denied(c):
    c.stub("a", pages=3)
    c.arm()
    for tool, args in (mcp_fetch(2), bash_fetch(3), mcp_fetch(1),
                       ("mcp__b36e17b6-7e3b-4e65-b890-21f21d538440__standing-context", {}),
                       ("mcp__claude_ai_CARR_Record_Layer__standing-context", {"detail": "boot"}),
                       ("mcp__carr__applicable-rules", {"situation": "x"}),
                       ("ToolSearch", {"query": "select:mcp__carr__standing-context"}),
                       ("Bash", {"command": f"{RUN_SH} call standing-context '{{\"detail\":\"boot\",\"page\":1}}'"})):
        r = c.call(tool, args)
        assert not denied(r), f"{tool} {args} must never be denied: {r}"
    # ...and nothing dressed up as one gets through (fresh contexts: the cap is 3).
    bypasses = [("Bash", {"command": cmd}, REPO) for cmd in (
        "./run.sh call standing-context '{\"detail\":\"boot\"}'; touch /tmp/x",
        "./run.sh call standing-context '{\"detail\":\"boot\"}' && echo hi",
        "./run.sh call add-loop '{\"kind\":\"idea\"}'",
        "/tmp/elsewhere/run.sh call standing-context '{\"detail\":\"boot\"}'",
        "./run.sh call --reason x standing-context '{\"detail\":\"boot\"}'")]
    bypasses += [("mcp__carr__add-loop", {"kind": "idea"}, REPO),
                 ("Bash", {"command": "./run.sh call standing-context '{\"detail\":\"boot\"}'"}, "/tmp")]
    for i, (tool, args, cwd) in enumerate(bypasses):
        assert denied(c.call(tool, args, agent=f"bypass-{i}", cwd=cwd)), f"bypass not denied: {tool} {args} {cwd}"


def case_deny_before_allow_after(c):
    c.stub("a", pages=3)
    text = c.arm()
    assert '"detail":"boot"' in text and "3 page(s)" in text, text
    r = c.call(*READ)
    assert denied(r), "an ordinary tool before the boot must be denied"
    reason = r["permissionDecisionReason"]
    assert "1, 2, 3" in reason and RUN_SH in reason and '"page":1' in reason, reason
    c.fetch(1)
    c.fetch(3, form="bash")
    r = c.call(*READ)
    assert denied(r) and "2" in r["permissionDecisionReason"], "page 2 still missing"
    c.fetch(2)
    assert c.call(*READ) is None, "every page confirmed: allowed silently"
    assert c.call("Agent", {"prompt": "x"}) is None


def case_digest_change_rearms(c):
    c.stub("a", pages=2)
    c.arm()
    for p in (1, 2):
        c.fetch(p)
        c.fetch(p, agent="sub-1")
    assert c.call(*READ) is None and c.call(*READ, agent="sub-1") is None
    c.stub("b", pages=2)
    c.arm("resume")
    assert denied(c.call(*READ)), "main must re-fetch a new digest"
    assert denied(c.call(*READ, agent="sub-1")), "a subagent must re-fetch a new digest"


def case_rearm_on_compact(c):
    c.stub("a", pages=2)
    c.arm()
    c.fetch(1)
    c.fetch(2)
    assert c.call(*READ) is None
    c.arm("compact")
    assert denied(c.call(*READ)), "a compacted context has lost the rules: re-fetch required"
    c.fetch(1)
    c.fetch(2)
    assert c.call(*READ) is None


def case_subagent_path(c):
    c.stub("a", pages=2)
    c.arm()
    c.fetch(1)
    c.fetch(2)
    assert c.call(*READ) is None, "main complete"
    r = c.call(*READ, agent="agent-7")
    assert denied(r), "a subagent is gated on its own fetches, not its parent's"
    reason = r["permissionDecisionReason"]
    assert '"detail":"boot"' in reason and RUN_SH in reason, reason
    c.fetch(1, agent="agent-7", form="bash")
    c.fetch(2, agent="agent-7")
    assert c.call(*READ, agent="agent-7") is None


def case_answer_parsing(c):
    """The Worker's real rejection and outage texts classify correctly."""
    code = ("import sys, json; sys.path.insert(0, sys.argv[1]); "
            "from lib.rule_boot_gate import read_answer; "
            "print(json.dumps([read_answer(x)[0] for x in json.loads(sys.argv[2])]))")
    samples = [
        {"stdout": "", "stderr": 'TOOL ERROR {\n  "error": "value_not_in_declared_vocabulary",\n'
                                 '  "verb": "standing-context",\n  "field": "detail",\n  "received": "boot"\n}'},
        {"stdout": "", "stderr": "could not reach the deployed Worker: fetch failed"},
        [{"type": "text", "text": json.dumps({"ok": True, "rule_boot": {
            "schema": "carr-rule-boot/v1", "digest": "sha256:ab", "page": 1, "pages_total": 2}})}],
        "HTTP 502 from the Worker",
        'unexpected server error while rendering "page"',
        {"stdout": "", "stderr": 'TOOL ERROR {"error": "page_out_of_range", "page": 9, '
                                 '"pages_total": 3, "digest": "sha256:abababababab"}'},
    ]
    out = subprocess.run([sys.executable, "-c", code, c.tree, json.dumps(samples)],
                         capture_output=True, text=True, timeout=30).stdout
    assert json.loads(out) == ["unsupported", "failed", "boot", "failed", "failed", "out_of_range"], out


# --- fetch recognition (defect seen live 2026-09-27): a fetch is recognised by
# what it does. The absolute form, `cd <repo> && ./run.sh`, a harmless output
# pipe and a parallel batch are all fetches and never holds; a page counts as
# read only when its answer carries the matching digest, page and text; and once
# every page is read the RULES UNREAD advisory stops.

def boot_arg(page):
    return f"'{{\"detail\":\"boot\",\"page\":{page}}}'"


def abs_cmd(page):
    return f"{RUN_SH} call standing-context {boot_arg(page)}"


PY_FORMAT = "python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), indent=2))'"


def indent2(body):
    return json.dumps(json.loads(body), indent=2)


def case_absolute_form(c):
    c.stub_sized("a", pages=3)
    c.arm()
    for p in (1, 2, 3):
        pre, post = c.fetch_cmd(abs_cmd(p), p, cwd="/tmp")
        assert not denied(pre), f"absolute form from another cwd is a fetch: {pre}"
        assert not notice(post), f"a real page is silent: {post}"
    assert c.call(*READ) is None, "every page read through the absolute form: allowed silently"
    assert not c.holds(), f"no fetch may count as a hold: {c.holds()}"


def case_cd_then_run_sh(c):
    c.stub_sized("a", pages=2)
    c.arm()
    for p in (1, 2):
        pre, _ = c.fetch_cmd(f"cd {REPO} && ./run.sh call standing-context {boot_arg(p)}", p, cwd="/tmp")
        assert not denied(pre), f"cd <repo> && ./run.sh is a fetch: {pre}"
    assert c.call(*READ) is None and not c.holds(), c.holds()
    # Anything chained after the fetch, or a cd that does not reach this repo's run.sh, is not a fetch.
    refused = [f"cd {REPO} && ./run.sh call standing-context {boot_arg(1)} && echo hi",
               f"cd {REPO}; ./run.sh call standing-context {boot_arg(1)}",
               f"cd /tmp && ./run.sh call standing-context {boot_arg(1)}",
               f"cd {REPO} && cd . && ./run.sh call standing-context {boot_arg(1)}",
               f"cd .. && ./run.sh call standing-context {boot_arg(1)}",
               f"cd {REPO} && ./run.sh call standing-context {boot_arg(1)} || true",
               f"cd {REPO} && ./run.sh call standing-context {boot_arg(1)} & echo x",
               f"cd {REPO} && run.sh call standing-context {boot_arg(1)}"]
    for i, cmd in enumerate(refused):
        assert denied(c.call("Bash", {"command": cmd}, agent=f"cd-{i}", cwd="/tmp")), f"not a fetch: {cmd}"


def case_piped_formatter(c):
    c.stub_sized("a", pages=5)
    c.arm()
    pipes = [(f"{abs_cmd(1)} | {PY_FORMAT}", indent2),
             (f"{abs_cmd(2)} 2>&1 | jq .", indent2),
             (f"{abs_cmd(3)} | jq -r .rule_boot", lambda b: json.dumps(json.loads(b)["rule_boot"], indent=2)),
             (f"{abs_cmd(4)} | head -n 4000", None),
             (f"{abs_cmd(5)} </dev/null | python3 -m json.tool", indent2)]
    for p, (cmd, out) in enumerate(pipes, start=1):
        pre, post = c.fetch_cmd(cmd, p, stdout=out)
        assert not denied(pre), f"a harmless pipe keeps it a fetch: {cmd}: {pre}"
        assert not notice(post), f"{cmd}: {post}"
    assert c.call(*READ) is None and not c.holds(), c.holds()
    # A pipe that keeps only the text proves nothing: not read, but not an outage either.
    c2 = Case(c.tree, tempfile.mkdtemp(dir=c.work))
    c2.stub_sized("a", pages=1)
    c2.arm()
    pre, post = c2.fetch_cmd(f"{abs_cmd(1)} | jq -r .rule_boot.text", 1,
                             stdout=lambda b: json.loads(b)["rule_boot"]["text"])
    assert not denied(pre) and "does not count" in notice(post), post
    r = c2.call(*READ)
    assert denied(r) and "UNAVAILABLE" not in r["permissionDecisionReason"], f"held, not unlocked: {r}"
    assert "1 came back without the whole page" in r["permissionDecisionReason"], r
    # It answered, so the never-answered grace does not read it as an outage later.
    for root, _dirs, files in os.walk(os.path.join(c2.state, SESSION, "fetched", "main")):
        for name in files:
            os.utime(os.path.join(root, name), (1, 1))
    r = c2.call(*READ)
    assert denied(r) and "UNAVAILABLE" not in r["permissionDecisionReason"], f"answered is not unanswered: {r}"
    # A formatter failing downstream of the fetch is not proof the store is down.
    c2.fetch_cmd(f"{abs_cmd(1)} | {PY_FORMAT}", 1, answer="Exit code 1\njson.decoder.JSONDecodeError")
    r = c2.call(*READ)
    assert denied(r) and "UNAVAILABLE" not in r["permissionDecisionReason"], r
    # Pipes that run, write or fabricate anything are not fetches.
    refused = [f"{abs_cmd(1)} | sh", f"{abs_cmd(1)} | bash -c 'id'", f"{abs_cmd(1)} | tee /tmp/x",
               f"{abs_cmd(1)} > /tmp/x", f"{abs_cmd(1)} | xargs rm",
               f"{abs_cmd(1)} | python3 -c 'import os; os.system(\"id\")'",
               f"{abs_cmd(1)} | python3 -c 'print(open(\"/etc/hosts\").read())'",
               f"{abs_cmd(1)} | python3 -c '__import__(\"os\")'",
               f"{abs_cmd(1)} | python3 -c 'import json,sys; d=json.load(sys.stdin); d[\"rule_boot\"][\"page\"]=2; print(json.dumps(d))'",
               f"{abs_cmd(1)} | python3 -c 'print(\"{{\\\"rule_boot\\\": 1}}\")'",
               f"{abs_cmd(1)} | python3 /tmp/evil.py",
               # Rewriting the text while keeping the JSON (review of #1343, nit 2).
               f"{abs_cmd(1)} | python3 -c 'import sys; print(\"maybe\".join(sys.stdin.read().split(\"NEVER\")))'",
               f"{abs_cmd(1)} | python3 -c 'import sys; print(sys.stdin.read().lower())'",
               f"{abs_cmd(1)} | jq env", f"{abs_cmd(1)} | jq '{{rule_boot:{{digest:\"sha256:x\"}}}}'",
               f"{abs_cmd(1)} | jq -n '\"x\"'", f"{abs_cmd(1)} | jq . /etc/hosts",
               f"{abs_cmd(1)} | head -n 5 /etc/hosts", f"{abs_cmd(1)} | cat /etc/hosts",
               f"{abs_cmd(1)} | jq . $(id)", f"{abs_cmd(1)} | jq `id`",
               f"{RUN_SH} call standing-context \"$(id)\""]
    for i, cmd in enumerate(refused):
        assert denied(c.call("Bash", {"command": cmd}, agent=f"pipe-{i}")), f"not a harmless pipe: {cmd}"
    # A Python filter imports json from its working directory, so it runs only
    # from a checkout root of this repo (review of #1343, nit 1).
    for i, (cmd, cwd) in enumerate([(f"{abs_cmd(1)} | {PY_FORMAT}", "/tmp"),
                                    (f"cd /tmp && {abs_cmd(1)} | python3 -m json.tool", REPO),
                                    (f"{abs_cmd(1)} | {PY_FORMAT}", os.path.join(REPO, "ops"))]):
        assert denied(c.call("Bash", {"command": cmd}, agent=f"pycwd-{i}", cwd=cwd)), f"python off-root: {cmd} @ {cwd}"
    assert not denied(c.call("Bash", {"command": f"{abs_cmd(1)} | jq ."}, agent="jq-tmp", cwd="/tmp")), \
        "jq imports nothing from the cwd: any cwd"
    assert not denied(c.call("Bash", {"command": f"cd {REPO} && ./run.sh call standing-context {boot_arg(1)} | {PY_FORMAT}"},
                             agent="py-root", cwd="/tmp")), "python after cd to the repo root is fine"


def case_parallel_batch(c):
    """Seven page fetches sent at once, as a model batches them: every PreToolUse
    runs before any tool, then every PostToolUse, each set concurrently."""
    c.stub_sized("a", pages=7)
    c.arm()
    forms = [abs_cmd(1), f"{abs_cmd(2)} | {PY_FORMAT}", f"cd {REPO} && ./run.sh call standing-context {boot_arg(3)}",
             f"./run.sh call standing-context {boot_arg(4)}", f"{abs_cmd(5)} | jq .", abs_cmd(6), abs_cmd(7)]
    pres, posts = [], []
    for p, cmd in enumerate(forms, start=1):
        pres.append({"hook_event_name": "PreToolUse", "session_id": SESSION, "cwd": REPO,
                     "tool_name": "Bash", "tool_input": {"command": cmd}})
        body = json.dumps({"ok": True, "rule_boot": c.boot(p)})
        posts.append({**pres[-1], "hook_event_name": "PostToolUse",
                      "tool_response": {"stdout": indent2(body) if "|" in cmd else body, "stderr": ""}})
    for r in c.hooks_parallel(pres):
        assert not denied(r), f"a batched fetch was held: {r}"
    c.hooks_parallel(posts)
    assert not c.holds(), f"batched fetches counted as holds: {c.holds()}"
    assert c.call(*READ) is None, "all seven pages read in one batch: allowed silently"


def case_all_pages_clear_advisory(c):
    """The live defect: the cap is reached, then every page is read; the
    RULES UNREAD advisory must stop."""
    c.stub_sized("a", pages=7)
    c.arm()
    for _ in range(3):
        assert denied(c.call(*READ))
    assert "RULES UNREAD" in notice(c.call(*READ))
    for p in range(1, 8):
        c.fetch_cmd(f"{abs_cmd(p)} | {PY_FORMAT}", p, stdout=indent2)
    for _ in range(3):
        r = c.call(*READ)
        assert r is None, f"every page read: no advisory any more: {r}"


def case_three_mcp_prefixes(c):
    c.stub_sized("a", pages=2)
    c.arm()
    for i, prefix in enumerate(("mcp__carr__", "mcp__claude_ai_CARR_Record_Layer__",
                                "mcp__b36e17b6-7e3b-4e65-b890-21f21d538440__")):
        agent = f"mcp-{i}"
        for p in (1, 2):
            tool, args = prefix + "standing-context", {"detail": "boot", "page": p}
            assert not denied(c.call(tool, args, agent=agent))
            c.hook({"hook_event_name": "PostToolUse", "session_id": SESSION, "cwd": REPO, "agent_id": agent,
                    "tool_name": tool, "tool_input": args,
                    "tool_response": [{"type": "text", "text": json.dumps({"ok": True, "rule_boot": c.boot(p)})}]})
        assert c.call(*READ, agent=agent) is None, f"{prefix} pages read"


def case_connector_after_compaction(c):
    """Coordinator's report 2026-09-27: connector fetches, including after a
    compaction, and piped Bash fetches. Reads made before a compaction do not
    survive it (the context lost them), and the hold says so; connector
    fetches made after it unlock the context."""
    conn = "mcp__b36e17b6-7e3b-4e65-b890-21f21d538440__standing-context"
    c.stub_sized("a", pages=7)
    c.arm()

    def connector(p):
        args = {"detail": "boot", "page": p}
        assert not denied(c.call(conn, args)), f"connector fetch {p} held"
        c.hook({"hook_event_name": "PostToolUse", "session_id": SESSION, "cwd": REPO, "tool_name": conn,
                "tool_input": args,
                "tool_response": [{"type": "text", "text": json.dumps({"ok": True, "rule_boot": c.boot(p)})}]})

    for p in range(1, 8):
        connector(p)
    assert c.call(*READ) is None, "seven connector pages read: allowed"
    c.arm("compact")
    r = c.call(*READ)
    assert denied(r) and "compact" in r["permissionDecisionReason"], f"held, saying why: {r}"
    for p in range(1, 8):
        connector(p)
    assert c.call(*READ) is None, "connector pages read after the compaction: allowed"
    # Piped Bash fetches after another compaction, including head that keeps the whole page.
    c.arm("compact")
    for p in range(1, 8):
        tail = "| head -n 100000" if p % 2 else "| jq ."
        pre, _ = c.fetch_cmd(f"{abs_cmd(p)} {tail}", p, stdout=indent2 if "jq" in tail else None)
        assert not denied(pre), f"piped fetch {p} held: {pre}"
    assert c.call(*READ) is None, "piped pages read after the compaction: allowed"
    assert len(c.holds()) == 1, f"only the deliberate READ above was a hold, no fetch: {c.holds()}"


def case_confirm_needs_the_real_page(c):
    """Loopholes in what counts as read."""
    # Without total_chars (an older Worker) the page check stands alone.
    c0 = Case(c.tree, tempfile.mkdtemp(dir=c.work))
    c0.stub("a", pages=2)
    c0.arm()
    c0.fetch_cmd(abs_cmd(1), 1)
    c0.fetch_cmd(abs_cmd(2), 2, boot=c0.boot(1))
    assert denied(c0.call(*READ)), "an answer for page 1 does not read page 2"
    no_text = {k: v for k, v in c0.boot(2).items() if k != "text"}
    c0.fetch_cmd(abs_cmd(2), 2, boot=no_text)
    assert denied(c0.call(*READ)), "a page without its text is not read"
    c0.fetch_cmd(abs_cmd(2), 2)
    assert c0.call(*READ) is None
    c.stub_sized("a", pages=2)
    c.arm()
    c.fetch_cmd(abs_cmd(1), 1)
    c.fetch_cmd(abs_cmd(2), 2, boot=c.boot(1))
    assert denied(c.call(*READ)), "an answer for page 1 does not read page 2"
    # Text cut short: every page 'confirmed', but the lengths do not add up to the boot.
    short = {**c.boot(2), "text": c.page_text(2)[:10]}
    c.fetch_cmd(abs_cmd(2), 2, boot=short)
    r = c.call(*READ)
    assert denied(r) and "length" in r["permissionDecisionReason"], f"short text must not complete: {r}"
    c.fetch_cmd(abs_cmd(2), 2)
    assert c.call(*READ) is None, "the whole page read: complete"


def case_same_checkout_worktree(c):
    """run.sh in the main checkout or any worktree of it is this repo's run.sh,
    whichever of them the hook itself was loaded from; a lookalike is not."""
    root = c.work
    main, wt, fake = (os.path.join(root, n) for n in ("main", "wt", "fake"))
    os.makedirs(os.path.join(main, ".git", "worktrees", "wt"))
    for d in (wt, fake):
        os.makedirs(d)
    for d in (main, wt, fake):
        with open(os.path.join(d, "run.sh"), "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n")
    gd = os.path.join(main, ".git", "worktrees", "wt")
    with open(os.path.join(gd, "commondir"), "w", encoding="utf-8") as fh:
        fh.write("../..\n")
    with open(os.path.join(gd, "gitdir"), "w", encoding="utf-8") as fh:
        fh.write(os.path.join(wt, ".git") + "\n")
    with open(os.path.join(wt, ".git"), "w", encoding="utf-8") as fh:
        fh.write(f"gitdir: {gd}\n")
    # A lookalike points into the same .git but git's back-reference names another folder.
    with open(os.path.join(fake, ".git"), "w", encoding="utf-8") as fh:
        fh.write(f"gitdir: {gd}\n")
    code = ("import sys, json; sys.path.insert(0, sys.argv[1]); import lib.rule_boot_gate as g; "
            "out = []\nfor repo, cand in json.loads(sys.argv[2]):\n    g.REPO = repo\n"
            "    out.append(g.classify('Bash', {'command': cand + \" call standing-context '{\\\"detail\\\":\\\"boot\\\",\\\"page\\\":1}'\"}, '/tmp')[0])\n"
            "print(json.dumps(out))")
    pairs = [[main, os.path.join(wt, "run.sh")], [wt, os.path.join(main, "run.sh")],
             [wt, os.path.join(wt, "run.sh")], [main, os.path.join(fake, "run.sh")],
             [wt, os.path.join(fake, "run.sh")]]
    out = subprocess.run([sys.executable, "-c", code, c.tree, json.dumps(pairs)],
                         capture_output=True, text=True, timeout=30)
    assert json.loads(out.stdout or "null") == ["fetch", "fetch", "fetch", "other", "other"], out.stdout + out.stderr


CASES = [case_deny_cap, case_outage_after_good_arm, case_out_of_range_not_outage,
         case_mid_session_digest_change, case_foreign_mcp_prefix, case_toolless_subagent,
         case_not_deployed_distinct, case_state_unwritable_armed, case_disk_full_armed,
         case_failure_not_sticky, case_state_unwritable_never_armed, case_disk_full_never_armed,
         case_notice_short_after_first,
         case_unreachable_no_deadlock, case_fetch_never_denied, case_deny_before_allow_after,
         case_digest_change_rearms, case_rearm_on_compact, case_subagent_path,
         case_answer_parsing,
         case_absolute_form, case_cd_then_run_sh, case_piped_formatter, case_parallel_batch,
         case_all_pages_clear_advisory, case_three_mcp_prefixes, case_connector_after_compaction,
         case_confirm_needs_the_real_page,
         case_same_checkout_worktree]


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


def check_pending_install():
    """Before install the new gate reads PENDING INSTALL, never a failure; once
    it has been seen installed, a missing tuple is a real finding again."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("gate_integrity", os.path.join(REPO, "hooks", "gate-integrity.py"))
    gi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gi)
    work = tempfile.mkdtemp(prefix="rule-boot-pending-")
    try:
        gi.PENDING_INSTALL_STAMP_DIR = work
        errs = ["expected 1 exact PreToolUse/.* hook rule-boot-gate.py; found 0",
                "expected 1 exact PostToolUse/Bash|mcp__.*__standing-context hook rule-boot-gate.py; found 0",
                "expected 1 exact Stop/Stop hook conduct-stop-gate.py; found 0"]
        real, pending = gi.split_pending_install(errs, {})
        assert pending == ["rule-boot-gate.py"] and real == errs[2:], (real, pending)
        installed = {"PreToolUse": [{"matcher": ".*", "hooks": [
            {"type": "command", "command": "/x/.venv/bin/python /x/hooks/hook-meter-run.py /x/hooks/rule-boot-gate.py"}]}]}
        real, pending = gi.split_pending_install(errs[1:2], installed)
        assert pending == [] and real == errs[1:2], "partly installed is drift, not pending"
        real, pending = gi.split_pending_install(errs[:1], {})
        assert pending == [] and real == errs[:1], "seen installed once: missing is a finding"
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ mutants

MUTANTS = {
    # First round (the coordinator's three, plus two).
    "never-denies": [('    return "deny", reason', '    return "allow", reason')],
    "denies-the-fetch-itself": [('    if kind == "fetch":\n        if page is not None:',
                                 '    if False:\n        if page is not None:')],
    "no-re-arm-after-compact": [('"epoch": secrets.token_hex(6)}',
                                 '"epoch": (read_arm(session_id) or {}).get("epoch") or secrets.token_hex(6)}')],
    "digest-change-ignored": [('    digest = safe_key(str(arm.get("digest") or "").replace("sha256:", ""), "none")[:24]',
                               '    digest = "same"')],
    "deadlock-after-unreachable-attempt": [('        if attempted or "failed" in names:\n            return "allow", _notice(',
                                            '        if False:\n            return "allow", _notice(')],
    # PR #1328 review round.
    "deny-on-unwritten-state": [('    if why:\n        return "allow", STATE_UNWRITABLE_NOTICE.format(why=why)\n    return "deny", reason',
                                 '    if False:\n        return "allow", STATE_UNWRITABLE_NOTICE.format(why=why)\n    return "deny", reason')],
    "no-deny-cap": [('    if held >= DENY_CAP:', '    if False:')],
    "out-of-range-read-as-outage": [('        if _OUT_OF_RANGE in text:', '        if False:')],
    "failed-sticky": [('        if not _short_text(folder, arm):\n            return "allow", None',
                       '        if not _short_text(folder, arm) and "failed" not in names:\n            return "allow", None'),
                      ('        for stale in ("failed", "unsupported", f"u{page}"):', '        for stale in ():')],
    "notice-every-call": [('    if f"shown-{name}" in _markers(folder):', '    if False:')],
    "toolless-denied": [('    if _toolless(payload):', '    if False:')],
    "outage-needs-every-page": [('    if "failed" in names:\n        return "allow", _notice(folder, "unavailable"',
                                 '    if False:\n        return "allow", _notice(folder, "unavailable"')],
    "not-deployed-read-as-unreachable": [('    if str(reason or "").startswith("not_deployed"):',
                                          '    if False:')],
    "mid-session-digest-ignored": [('        if total >= 1 and digest and (', '        if False and (')],
    "any-mcp-prefix": [('def _carr_verb(name):\n    for prefix in CARR_MCP_PREFIXES:',
                        'def _carr_verb(name):\n    m = re.match(r"^mcp__.+__([a-z][a-z0-9-]*)$", name)\n'
                        '    return m.group(1) if m else None\n    for prefix in CARR_MCP_PREFIXES:')],
    # Fetch recognition (2026-09-27).
    "pipe-failure-read-as-outage": [('    if not direct and answer != "boot":\n        answer = "inconclusive"',
                                     '    if False:\n        answer = "inconclusive"')],
    "any-answer-confirms-the-page": [('    if answer == "boot" and not _is_page(boot, page):',
                                      '    if False:')],
    "no-length-check": [('    if want < 1:\n        return False', '    if True:\n        return False')],
    "any-filter-harmless": [('def _harmless_filter(stage):\n', 'def _harmless_filter(stage):\n    return True\n')],
    "any-python-code": [('def _python_code_ok(code):\n', 'def _python_code_ok(code):\n    return True\n')],
    "any-jq-filter": [('    if flt is None:\n        return True\n    pos = 0', '    if True:\n        return True\n    pos = 0')],
    "lookalike-worktree": [('    if os.path.realpath(os.path.join(gitdir, back)) != os.path.realpath(dotgit):\n        return None',
                            '    if False:\n        return None')],
    "worktree-refuses-main-checkout": [('        return bool(mine) and _git_common_dir(os.path.dirname(real)) == mine',
                                        '        return False')],
    "unreadable-page-read-as-outage": [('        if p in attempted and f"u{p}" not in names:', '        if p in attempted:')],
    "cd-form-refused": [('        base, tokens = target, tokens[3:]', '        return None')],
    "compaction-hold-silent": [('    if not agent_id and not confirmed and source in ("compact", "resume", "clear"):',
                                '    if False:')],
    "python-any-cwd": [('    if uses_python and not _is_checkout_root(base):', '    if False:')],
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
    check_pending_install()
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
    print(f"rule-boot-gate-selftest: {len(CASES)} cases + gate-integrity re-arm + pending-install passed; "
          f"{len(MUTANTS)} of {len(MUTANTS)} planted mutants killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
