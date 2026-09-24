#!/usr/bin/env python3
"""Contract tests for flash-run's per-task rule delivery (pick_rules / rules_block).

Jev (ops/jev_rule_select.advise) picks the taught rules that bind to ONE Flash task;
flash-run appends them to each attempt's system prompt. Delivery must fail open:
a Jev outage leaves the attempt without rules, never without an attempt.
"""

from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("flash_run", os.path.join(HERE, "flash-run.py"))
fr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fr)

FAILURES = []


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
        "statement": "On any non-trivial build, the check is written and running first."}


def picks_rules_and_describes_the_real_situation():
    sel = FakeSelector([RULE])
    rules, note = fr.pick_rules("write a gate hook", selector=sel)
    assert [r["id"] for r in rules] == ["e65efc68"], rules
    assert note is None, note
    situation = sel.seen[0]
    # The situation must say Flash does no git or delivery, or session-level rules
    # (worktree-per-session, own-the-merge) get picked for a disposable copy.
    assert "no git" in situation and "write a gate hook" in situation, situation


def fails_open_when_jev_is_down():
    rules, note = fr.pick_rules("fix a bug", selector=FakeSelector(raises=RuntimeError("down")))
    assert rules == [], rules
    assert note and "down" in note, note


def block_is_empty_without_rules():
    assert fr.rules_block([]) is None


def block_carries_statement_and_caps_length():
    long_rule = dict(RULE, statement="x" * 5000)
    block = fr.rules_block([long_rule] * 9)
    assert block.count("e65efc68") == fr.MAX_TASK_RULES, block.count("e65efc68")
    assert len(block) < 700 * fr.MAX_TASK_RULES + 400, len(block)
    assert "WRITE THE TEST BEFORE THE THING" in block


check("picks rules and describes Flash's real situation", picks_rules_and_describes_the_real_situation)
check("fails open when Jev is down", fails_open_when_jev_is_down)
check("no rules -> no block", block_is_empty_without_rules)
check("block carries rule text and caps its size", block_carries_statement_and_caps_length)

if FAILURES:
    print(f"flash-run rules: {len(FAILURES)} FAILED")
    sys.exit(1)
print("flash-run rules: every assertion held")
