#!/usr/bin/env python3
"""Contract tests for tools/flash-prompt-rules.py, the UserPromptSubmit hook that gives
interactive Flash sessions the taught rules Jev judges to bind to each message.

It must never block or break a turn: no rules, a slash command, a tiny message or a Jev
outage all mean no output and exit 0.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("flash_prompt_rules",
                                              os.path.join(HERE, "flash-prompt-rules.py"))
if spec is None or spec.loader is None:
    raise ImportError("tools/flash-prompt-rules.py")
fpr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fpr)

FAILURES: list[str] = []


def check(name, fn):
    try:
        fn()
        print(f"  ok    {name}")
    except AssertionError as exc:
        FAILURES.append(name)
        print(f"  FAIL  {name}: {exc!r}")


class FakeSelector:
    def __init__(self, result=None, raises=None):
        self.result, self.raises, self.seen = result or [], raises, []

    def advise(self, situation, **kwargs):
        self.seen.append(situation)
        if self.raises:
            raise self.raises
        return self.result


RULE = {"id": "e65efc68", "gist": "WRITE THE TEST BEFORE THE THING", "probability": 0.91,
        "statement": "The check is written and running before the implementation is finished."}
TASK = "build a pre-push gate that refuses a push when the selftest is missing"


def binding_rules_become_additional_context():
    sel = FakeSelector([RULE])
    out = fpr.build_output(TASK, selector=sel)
    assert out is not None
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "UserPromptSubmit", hso
    assert "e65efc68" in hso["additionalContext"], hso
    assert TASK in sel.seen[0] and "interactive" in sel.seen[0], sel.seen[0]


def nothing_binds_means_no_output():
    assert fpr.build_output(TASK, selector=FakeSelector([])) is None


def slash_commands_and_tiny_messages_are_not_judged():
    sel = FakeSelector([RULE])
    assert fpr.build_output("/clear", selector=sel) is None
    assert fpr.build_output("ok thanks", selector=sel) is None
    assert sel.seen == [], sel.seen


def jev_outage_fails_open():
    assert fpr.build_output(TASK, selector=FakeSelector(raises=RuntimeError("down"))) is None


def main_never_blocks_on_bad_input():
    assert fpr.main(stdin_text="not json", selector=FakeSelector([RULE])) == 0
    assert fpr.main(stdin_text=json.dumps({"prompt": TASK}),
                    selector=FakeSelector(raises=RuntimeError("down"))) == 0


check("binding rules become additionalContext", binding_rules_become_additional_context)
check("nothing binds -> no output", nothing_binds_means_no_output)
check("slash commands and tiny messages are not judged", slash_commands_and_tiny_messages_are_not_judged)
check("Jev outage fails open", jev_outage_fails_open)
check("main never blocks on bad input", main_never_blocks_on_bad_input)

if FAILURES:
    print(f"flash-prompt-rules: {len(FAILURES)} FAILED")
    sys.exit(1)
print("flash-prompt-rules: every assertion held")
