#!/usr/bin/env python3
"""Exercise the shared fixture through the same subprocess seam as hook wiring."""
import sys
import os
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.selftest_harness import Checker, HookSandbox, load_hook


def main():
    check = Checker()
    check.check("a hook loads by its declared name",
                callable(load_hook("blocker-decider-gate").decide))
    with HookSandbox() as sandbox:
        state_probe = sandbox.fire("fixture", {}, argv=[sys.executable, "-c", """
import json
from lib.selftest_harness import load_hook
completion = load_hook('completion-evidence-gate')
carry = load_hook('chat-lint-carryover')
print(json.dumps({'completion': completion.LOG, 'judgment': completion.JEV_LOG,
                  'carry': carry.carry_path('sandbox-readback')}))
"""])
        state_paths = state_probe.envelopes[0] if state_probe.envelopes else {}
        confined = (state_probe.code == 0 and len(state_paths) == 3
                    and all(Path(path).is_relative_to(sandbox.root)
                            for path in state_paths.values()))
        check.check("the child gate's audit and carryover paths stay inside the fixture",
                    confined, state_probe.stdout or state_probe.stderr)
        if not confined:
            return check.summary()
        result = sandbox.fire("blocker-decider-gate", {
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__carr__add-loop",
            "tool_input": {"blocker": "capability", "title": "blocked"},
        })
        check.check("the subprocess seam returns the gate's denial",
                    result.code == 2 and result.decision == "deny"
                    and "NAME THE DECIDER" in result.stderr, result)
        result = sandbox.fire("completion-evidence-gate", {
            "session_id": "harness-stop", "hook_event_name": "Stop",
        }, turns=[
            ("user", "reconcile the deal"),
            {"type": "assistant", "message": {"content": [{
                "type": "tool_use", "id": "write", "name": "mcp__carr__update-deal",
                "input": {},
            }]}},
            {"type": "user", "message": {"content": [{
                "type": "tool_result", "tool_use_id": "write", "is_error": False,
                "content": "fixture completed successfully",
            }]}},
            ("assistant", "Done."),
        ])
        check.check("simple turns reach the Stop gate and return its block envelope",
                    result.code == 0 and result.decision == "deny"
                    and result.envelopes[0]["decision"] == "block", result.stdout)
        mixed = sandbox.fire("fixture", {}, argv=[sys.executable, "-c",
            'print("banner"); print(\'{"decision":"block","reason":"fixture"}\')'])
        check.check("plain output survives beside a parsed envelope",
                    mixed.stdout == 'banner\n{"decision":"block","reason":"fixture"}\n'
                    and mixed.decision == "deny"
                    and mixed.envelopes == [{"decision": "block", "reason": "fixture"}])
        lint = load_hook("lint-gate")
        vault_tail = Path(lint.VAULT).relative_to(Path.home())
        draft = sandbox.home / vault_tail / "Outreach" / "intro.md"
        draft.parent.mkdir(parents=True)
        draft.write_text("fixture draft\n")
        sandbox.reply("FAIL hard-ban: fixture\n")
        result = sandbox.fire("lint-gate", {
            "tool_name": "Write", "tool_input": {"file_path": str(draft)},
        })
        check.check("the fake home and run.sh reply drive the real lint hook",
                    result.code == 0 and result.stderr == ""
                    and "HARD BAN HIT" in result.stdout
                    and sandbox.calls() == [f"lint {draft} --surface email"])
        result = sandbox.fire("lint-gate", "{not-json")
        check.check("a malformed event keeps the hook's fail-open verdict",
                    result.code == 0 and result.decision == "allow"
                    and result.stdout == result.stderr == "")
        first_root = sandbox.root
    with HookSandbox() as other:
        check.check("fixture homes, replies and latch ledgers are independent",
                    other.root != first_root and other.calls() == []
                    and not Path(other.env["CARR_STOP_LATCH_STATE"]).exists())
    with patch.dict(os.environ, {"GIT_DIR": "/fixture-wrong-repo",
                                "GIT_CONFIG_GLOBAL": "/fixture-wrong-config",
                                "CARR_CONTEXT_STATE": "/fixture-wrong-state"}):
        with HookSandbox() as isolated:
            check.check("git fixtures discard inherited repository and config pointers",
                        isolated.env["GIT_CONFIG_GLOBAL"] == os.devnull
                        and isolated.env["GIT_CONFIG_NOSYSTEM"] == "1"
                        and "GIT_DIR" not in isolated.env)
            check.check("hook state and paid clients cannot inherit the host's CARR settings",
                        "CARR_CONTEXT_STATE" not in isolated.env
                        and isolated.env.get("CARR_JEV_OFFLINE") == "1")
    return check.summary()


if __name__ == "__main__":
    raise SystemExit(main())
