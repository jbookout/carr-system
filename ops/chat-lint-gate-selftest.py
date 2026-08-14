#!/usr/bin/env python3
"""chat-lint-gate-selftest.py — fixtures for hooks/chat-lint-gate.py, written
before the hook (rule e65efc68, enforcing rules 5be2f462 and 3a9dbafd).

TWO RULES, ONE MOMENT. Both bind the assistant's own outgoing chat text, and
the only place that text exists before Joe reads it is the Stop hook:

  5be2f462  the contrast-reframe and banned-vocab HARD regexes that already
            guard every prospect-visible surface (tools/writing-lint.py,
            enforced by lint-gate.py on file writes) finally reach the surface
            they were actually taught about: internal dialogue to Joe.
  3a9dbafd  never make a partner decode an ID. A bare 8-hex rule id or a bare
            'loop #250' with no plain words around it forces Joe to go look up
            what the thing is; the id must ride WITH its gloss.

SCOPE IS DELIBERATELY NARROW. Only the HARD ids the audit row names (vocab,
contrast-reframe, contrast-reframe-split) point at chat. contrast-compressed
stays off this surface: 'X, not Y' is REVIEW severity precisely because a
genuine correction of fact takes that shape, and honest technical chat is
full of genuine corrections. The em-dash ban stays prospect-only — chat and
this repo's own house style use em dashes on purpose. A lint that flags
half of every honest report gets muted in a day, and then it catches nothing.

FALSE-POSITIVE GUARDS THE CASES BELOW PIN DOWN: fenced code is exempt (git
output is full of hex), all-digit tokens are exempt (20260814 is a date, not
an id), and an id WITH plain words in its sentence is a gloss, not a bare id.

Spawns the REAL hook with REAL Stop payloads + transcript fixtures.
Exit 2 = blocked, 0 = allowed.
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "chat-lint-gate.py")

# (name, assistant_text, expect_block)
CASES = [
    ("vocab-banned", "This will seamlessly unlock a transformative workflow "
                     "for the whole pipeline.", True),
    ("contrast-reframe", "It's not about speed, it's about correctness of the "
                         "render path.", True),
    ("contrast-split", "This isn't a bug. It's a feature of the exporter that "
                       "was chosen on purpose.", True),
    ("bare-id-list", "Done today:\n- c0b38d80\n- 24e10ee8\nBoth landed.", True),
    ("bare-loop-ref", "Close loop #250 and loop #251.", True),
    ("glossed-id", "Rule c0b38d80 (re-bless the baseline in the same commit "
                   "as any gate change) is now enforced by the pre-commit "
                   "hook, so the next unblessed gate change cannot commit.",
     False),
    ("glossed-loop", "Loop #250 (the living-orb panel visual spec) still "
                     "waits on the origination conversation nobody recovered.",
     False),
    ("hex-in-fence", "All green now:\n```\nc0b38d80 gates: re-bless landed\n"
                     "24e10ee8 commit-msg check\n```\nEvery selftest passes.",
     False),
    ("date-not-id", "The audit file 20260814 rows were classified today and "
                    "the queue is ranked by leverage.", False),
    ("plain-chat", "The rebase finished cleanly and every selftest passes. "
                   "The branch is ready to push once the checkout reconciles.",
     False),
]

passed = 0
bad: list[str] = []


def run_stop(assistant_text, event="Stop", stop_active=False):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type": "user", "origin": {"kind": "user"},
                "message": {"content": [{"type": "text", "text": "status?"}]}}) + "\n")
            fh.write(json.dumps({"type": "assistant",
                "message": {"content": [{"type": "text", "text": assistant_text}]}}) + "\n")
        payload = {"hook_event_name": event, "transcript_path": path,
                   "session_id": "selftest", "stop_hook_active": stop_active}
        p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=30)
        return p.returncode == 2, (p.stdout or "") + (p.stderr or "")
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def main():
    global passed
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}")
        return 1
    for name, text, expect in CASES:
        got, out = run_stop(text)
        ok = got == expect
        if ok:
            passed += 1
        else:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:20} "
              f"want={'BLOCK' if expect else 'allow'} got={'BLOCK' if got else 'allow'}")

    got, _ = run_stop(CASES[0][1], stop_active=True)
    if not got:
        passed += 1
    else:
        bad.append("stop-active-loops")
    print(f"  {'ok  ' if not got else 'FAIL'} {'stop-active-never-loops':20} "
          f"want=allow got={'BLOCK' if got else 'allow'}")

    got, out = run_stop(CASES[0][1])
    if "writing" in out.lower() or "vocab" in out.lower():
        passed += 1
        print("  ok   the block names what it caught")
    else:
        bad.append("block-text")
        print(f"  FAIL the block names what it caught — {out[:100]}")

    print(f"\nchat-lint-gate-selftest: {passed}/{passed + len(bad)} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
