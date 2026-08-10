#!/usr/bin/env python3
"""Executable fixtures for hooks/delegation-gate.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "delegation-gate.py")


def user(text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def assistant_text(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def tool(name):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name, "input": {}}]}}


def run_case(name, recs, want, cwd=REPO, subagent=False):
    with tempfile.TemporaryDirectory(prefix="delegation-gate-") as td:
        base = os.path.join(td, "subagents") if subagent else td
        os.makedirs(base, exist_ok=True)
        transcript = os.path.join(base, "session.jsonl")
        with open(transcript, "w") as fh:
            for rec in recs:
                fh.write(json.dumps(rec) + "\n")
        payload = {
            "session_id": "selftest",
            "cwd": cwd,
            "transcript_path": transcript,
            "tool_name": "Bash",
            "tool_input": {"command": "true"},
        }
        got = subprocess.run(
            [sys.executable, HOOK],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
        )
        ok = got.returncode == want
        print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got.returncode}, want {want}")
        if not ok and got.stderr:
            print(got.stderr.strip())
        return ok


def main():
    cases = [
        ("first mechanical lookup is allowed", [user("audit this" )], 0, REPO, False),
        ("second call without executor blocks", [user("audit this"), tool("Bash")], 2, REPO, False),
        ("explicit delegation is sticky and blocks inline", [user("use the cheapest model that can do this correctly"), tool("Bash")], 2, REPO, False),
        ("Agent spawn satisfies the tripwire", [user("audit this"), tool("Bash"), tool("Agent")], 0, REPO, False),
        ("visible inline executor satisfies ordinary tripwire", [user("audit this"), assistant_text("executor: T3-inline — because this is judgment verification"), tool("Bash")], 0, REPO, False),
        ("inline executor cannot override explicit delegation", [user("use the cheapest qualified model"), assistant_text("executor: T3-inline — because I prefer it"), tool("Bash")], 2, REPO, False),
        ("delegation survives a later phase message", [user("delegate this to the cheapest qualified model"), tool("Agent"), user("I'm in Salesforce now"), tool("Bash")], 2, REPO, False),
        ("visible completion releases the latch", [user("delegate this to the cheapest qualified model"), tool("Agent"), assistant_text("delegation complete: Salesforce extraction"), user("check one more thing"), tool("Bash"), assistant_text("executor: T3-inline — because this is one judgment verification")], 0, REPO, False),
        ("partner can explicitly revoke delegation", [user("do not delegate; keep this inline"), tool("Bash")], 0, REPO, False),
        ("subagent transcripts are exempt", [user("do the assigned sweep"), tool("Bash")], 0, REPO, True),
        ("non-CARR sessions are exempt", [user("audit this"), tool("Bash")], 0, "/private/tmp", False),
    ]
    oks = [run_case(*case) for case in cases]
    print(f"\n{sum(oks)}/{len(oks)} delegation-gate cases passed")
    return 0 if all(oks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
