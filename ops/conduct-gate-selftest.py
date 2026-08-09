#!/usr/bin/env python3
"""conduct-gate-selftest.py — fixtures for hooks/conduct-stop-gate.py.

Spawns the REAL hook as a subprocess with a REAL transcript file and reads its
actual stdout, the same way ops/guard-selftest.py exercises the egress guard.
It does not import the module and call scan() directly, because that would test
a function rather than the thing settings.json actually runs.

Two halves, and BOTH matter:
  MUST BLOCK  — the conduct this gate exists to stop.
  MUST ALLOW  — ordinary work, and the narrow exemptions. A gate that blocks
                legitimate turns gets switched off within a week, which is the
                same outcome as never having built it.

Run: python3 ops/conduct-gate-selftest.py
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "conduct-stop-gate.py")

# (name, human_turn, assistant_turn, expect_block)
CASES = [
    # ── MUST BLOCK: command handoff (rule e313a3ca) ─────────────────────────
    ("fenced-bash", "update the repo",
     "I've made the changes. Now run:\n\n```bash\ngit pull\n```", True),
    ("fenced-unlabelled-cmd", "update the repo",
     "Ready to go.\n\n```\nnpm install\n```", True),
    ("fenced-sh", "fix it", "Done.\n\n```sh\n./run.sh health\n```", True),
    ("run-this-prose", "fix it", "All set. Run this when you get a chance.", True),
    ("paste-terminal", "fix it",
     "Paste this into your terminal and it will finish the job.", True),
    ("you-will-need-run", "deploy it",
     "The code is ready. You'll need to run the migration yourself.", True),
    ("go-ahead-and-run", "ship it", "Go ahead and run the deploy script.", True),

    # ── MUST BLOCK: decision offload (rules 14e0408b + aa411351) ────────────
    ("should-i", "clean up the schema",
     "I can do this two ways. Should I drop the column or keep it nullable?", True),
    ("want-me-to", "clean up the schema",
     "The index is redundant. Do you want me to remove it?", True),
    ("would-you-like", "refactor the exporter",
     "Would you like me to split this into two modules?", True),
    ("which-prefer", "sort the loops",
     "Which do you prefer for the ordering, created date or severity?", True),
    ("let-me-know", "tidy the renders",
     "I've staged the changes. Let me know if you want the old format kept.", True),
    ("your-call", "pick a name for the table",
     "Either works fine. Your call.", True),
    ("option-menu", "improve the nightly job",
     "Two paths:\n\nA) Rewrite the runner\nB) Patch the existing script\n", True),
    ("shall-i", "clean the cache", "Shall I clear the derived index too?", True),

    # ── MUST BLOCK: soft wait (the council's #1 predicted bypass) ───────────
    ("hold-until", "restructure the folders",
     "I've mapped it out. I'll hold here until you weigh in.", True),
    ("pending-your", "restructure the folders",
     "Work is staged, pending your decision on the naming.", True),
    ("confirm-before", "migrate the table",
     "Please confirm the approach before I proceed.", True),
    ("not-proceeding", "rebuild the index",
     "I won't continue without your go-ahead on the ordering.", True),
    ("say-the-word", "clean up dead code",
     "Everything is ready. Just say the word.", True),

    # ── MUST BLOCK: near-miss vocabulary that must NOT buy an exemption ─────
    # The PROTECTED list exempts genuine money/client questions. These are
    # internal offloads that merely CONTAIN a word close to that list; if any
    # of them flips to allow, the exemption has been widened too far and the
    # gate leaks. Added after "plan|rate|tier" briefly did exactly that.
    ("near-miss-plan", "improve the nightly job",
     "I have a plan for the refactor. Should I do it in one pass or two?", True),
    ("near-miss-rate", "tune the poller",
     "The rate limit is conservative. Do you want me to raise it?", True),
    ("near-miss-tier", "reorganise the cache",
     "There are two tiers here. Which do you prefer I collapse?", True),
    ("near-miss-post", "fix the render",
     "The PostToolUse hook is noisy. Should I quiet it?", True),
    ("near-miss-costly", "speed up the export",
     "That join is costly. Would you like me to add an index?", True),

    # ── MUST ALLOW: ordinary completed work ─────────────────────────────────
    ("plain-report", "fix the exporter",
     "Fixed. The exporter was writing to staging because CARR_EXPORT_LIVE was "
     "unset; refresh-rules.sh sets it. Verified: rule counts now match.", False),
    ("ran-it-himself", "check system health",
     "Ran `run.sh health`. All rows green except rules-live, which is stale by "
     "one because the hourly job has not fired yet. It will self-clear at 22:00.", False),
    ("decided-and-reported", "clean up the schema",
     "Dropped the redundant index. It duplicated the primary key and cost writes "
     "with no reader. Reversible: the migration is additive-down.", False),
    ("question-in-quoted-draft", "draft the client email",
     "Draft ready:\n\n> Would you like me to schedule the tour for Thursday?\n\n"
     "That is the client-facing copy; it is staged, not sent.", False),
    ("code-in-fence-not-shell", "show me the parser",
     "Here is the function:\n\n```python\ndef parse(x):\n    return x\n```", False),

    # ── MUST ALLOW: the human explicitly asked (exemptions) ─────────────────
    ("human-asked-command", "show me the command to rebuild",
     "Here it is:\n\n```bash\n./run.sh export --only compiled-rules\n```", False),
    ("human-asked-howto", "how do I install the dictation rig?",
     "Run:\n\n```bash\nbin/build-quill.sh\n```", False),
    ("human-asked-options", "lay out the options for the folder structure",
     "Two paths:\n\nA) Flat by domain\nB) Nested by lifecycle\n\n"
     "I recommend A.", False),
    ("human-asked-recommend", "which would you recommend?",
     "A. It is shallower and matches the 3-4 level rule.", False),
    ("human-said-dont-run", "don't run it, just tell me the command",
     "```bash\ngit push origin main\n```", False),

    # ── MUST ALLOW: protected class genuinely belongs to Joe ────────────────
    ("client-facing-question", "handle the Musicologie file",
     "The LOI is drafted. Should I send it to the listing agent, or do you want "
     "to review the rent abatement first?", False),
    ("publish-question", "do the social batch",
     "Three posts are drafted. Do you want me to publish them to LinkedIn today?", False),
    ("spend-question", "sort out the subscription",
     "The plan renews Friday. Should I let it renew at $240?", False),
    ("delete-question", "clean the archive",
     "These 40 rows are superseded. Do you want me to delete them?", False),
]


def run_case(human, assistant):
    """Write a real JSONL transcript, spawn the real hook, return its stdout."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({
                "type": "user",
                "origin": {"kind": "user"},
                "message": {"content": [{"type": "text", "text": human}]},
            }) + "\n")
            fh.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": assistant}]},
            }) + "\n")
        payload = {"transcript_path": path, "stop_hook_active": False,
                   "session_id": "selftest"}
        p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=30)
        out = (p.stdout or "").strip()
        if not out:
            return False, ""
        try:
            d = json.loads(out)
        except Exception:
            return False, out
        return d.get("decision") == "block", d.get("reason", "")
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}")
        return 1
    passed = failed = 0
    bad = []
    for name, human, assistant, expect in CASES:
        got, _ = run_case(human, assistant)
        ok = (got == expect)
        if ok:
            passed += 1
        else:
            failed += 1
            bad.append((name, expect, got))
        mark = "ok  " if ok else "FAIL"
        want = "BLOCK" if expect else "allow"
        real = "BLOCK" if got else "allow"
        print(f"  {mark} {name:28} want={want:5} got={real}")

    print()
    print(f"conduct-gate-selftest: {passed}/{passed + failed} passed")
    if bad:
        print("\nFAILURES:")
        for name, expect, got in bad:
            print(f"  {name}: wanted {'BLOCK' if expect else 'allow'}, "
                  f"got {'BLOCK' if got else 'allow'}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
