#!/usr/bin/env python3
"""Contract tests for Flash's per-task rule delivery (pick_rules / rules_block).

ops/rule_trigger_delivery.judge_budgeted ranks the whole active corpus and judges the
shortlist for ONE Flash task; flash-run appends the picks to each attempt's system
prompt. Delivery must fail open but visibly: an unavailable or partial judgment leaves
the attempt with whatever was judged, and the note names the gap for the run log.

These drive the real selector module; only the two paid requests (rank, bind) are
injected, so the roster, the status report and the hydration are the production ones.
"""

from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fr = _load("flash_run", os.path.join(HERE, "flash-run.py"))
# The same module pick_rules loads, read here only to compare its roster and text.
rtd = _load("rule_trigger_delivery_flash_test", os.path.join(REPO, "ops", "rule_trigger_delivery.py"))
rtc = _load("rule_trigger_compile_flash_test", os.path.join(REPO, "ops", "rule_trigger_compile.py"))

FAILURES: list[str] = []
# Verification rules: built a check must compare against the known-good shape, and a
# changed system needs a before/after comparison. Neither is a pack-layer rule, so the
# partner-message roster (pack_rules) cannot offer them; Flash has no boot load and no
# enforcing hooks to deliver them any other way.
VERIFY_RULES = ("a9ecd5b4", "a6e6ab4e")


def check(name, fn):
    try:
        fn()
        print(f"  ok    {name}")
    except AssertionError as exc:
        FAILURES.append(name)
        print(f"  FAIL  {name}: {exc!r}")


class Paid:
    """The two paid requests, scripted. Records the pool the ranker was offered."""

    def __init__(self, ranked=(), noul=0.9, rank_raises=None, bind_raises=None, drop=()):
        self.ranked, self.noul = list(ranked), noul
        self.rank_raises, self.bind_raises, self.drop = rank_raises, bind_raises, set(drop)
        self.pool, self.situations = [], []

    def rank(self, text, pool, limit, client):
        self.situations.append(text)
        self.pool = [rule["id"] for rule in pool]
        if self.rank_raises:
            raise self.rank_raises
        return [rule_id for rule_id in self.ranked if rule_id in self.pool][:limit], 1, "jev"

    def ask(self, state, questions):
        if self.bind_raises:
            raise self.bind_raises
        return {"model": "jev", "answers": {
            key: {"noul": self.noul} for key in questions
            if key.removeprefix("bind_") not in self.drop}}

    def judge(self):
        return {"rank": self.rank, "ask": self.ask, "client": FakeClient(), "titles": {}}


class FakeClient:
    @staticmethod
    def noul(instructions, true="", false=""):
        return {"instructions": instructions, "criteria": {"true": true, "false": false}}


def roster_is_the_whole_active_corpus():
    paid = Paid(ranked=VERIFY_RULES)
    rules, note = fr.pick_rules("build a CI check for the export", **paid.judge())
    assert note is None, note
    corpus = {rule["id"] for rule in rtd.load_rules()}
    pack = {rule["id"] for rule in rtc.pack_rules()}
    assert set(paid.pool) == corpus, (len(paid.pool), len(corpus))
    assert len(corpus) > len(pack), (len(corpus), len(pack))
    for rule_id in VERIFY_RULES:
        assert rule_id not in pack, rule_id
        assert rule_id in paid.pool, rule_id
    assert {rule["id"] for rule in rules} == set(VERIFY_RULES), rules


def describes_the_real_situation_and_hydrates_text():
    paid = Paid(ranked=["a9ecd5b4"])
    rules, _ = fr.pick_rules("write a gate hook", **paid.judge())
    # The situation must say Flash does no git or delivery, or session-level rules
    # (worktree-per-session, own-the-merge) get picked for a disposable copy.
    assert "no git" in paid.situations[0] and "write a gate hook" in paid.situations[0]
    statement = {rule["id"]: rule["statement"] for rule in rtd.load_rules()}["a9ecd5b4"]
    assert rules[0]["statement"] == statement, rules
    assert statement[:80] in fr.rules_block(rules)


def bind_outage_is_reported_not_an_empty_success():
    paid = Paid(ranked=VERIFY_RULES, bind_raises=TimeoutError("jev timed out"))
    rules, note = fr.pick_rules("build a CI check", **paid.judge())
    assert rules == [], rules
    assert note and "unavailable" in note, note


def partial_bind_keeps_matches_and_names_the_gap():
    paid = Paid(ranked=VERIFY_RULES, drop=["a6e6ab4e"])
    rules, note = fr.pick_rules("build a CI check", **paid.judge())
    assert [rule["id"] for rule in rules] == ["a9ecd5b4"], rules
    assert note and "partial" in note, note


def rank_outage_falls_back_and_says_so():
    paid = Paid(rank_raises=RuntimeError("ranker down"))
    rules, note = fr.pick_rules("compare the known-good backup artifact before and after",
                                **paid.judge())
    assert note and "unavailable_overlap_fallback" in note, note
    assert rules, "the overlap shortlist is still judged"


def deadline_is_reported():
    paid = Paid(ranked=VERIFY_RULES)
    rules, note = fr.pick_rules("build a CI check", deadline=0.0, **paid.judge())
    assert rules == [], rules
    assert note and "deadline" in note, note


def fails_open_when_the_selector_breaks():
    rules, note = fr.pick_rules("fix a bug", titles=object(),
                                rank=Paid(ranked=VERIFY_RULES).rank, ask=Paid().ask,
                                client=FakeClient())
    assert rules == [], rules
    assert note and "AttributeError" in note, note


def block_is_empty_without_rules():
    assert fr.rules_block([]) is None


def block_carries_statement_and_caps_length():
    rule = {"id": "e65efc68", "gist": "WRITE THE TEST BEFORE THE THING", "statement": "x" * 5000}
    block = fr.rules_block([rule] * 9)
    assert block.count("e65efc68") == fr.MAX_TASK_RULES, block.count("e65efc68")
    assert len(block) < 700 * fr.MAX_TASK_RULES + 400, len(block)
    assert "WRITE THE TEST BEFORE THE THING" in block


check("roster is the whole active corpus, not the pack layer", roster_is_the_whole_active_corpus)
check("describes Flash's real situation and hydrates rule text", describes_the_real_situation_and_hydrates_text)
check("a binding outage is reported, not an empty success", bind_outage_is_reported_not_an_empty_success)
check("a partial binding keeps matches and names the gap", partial_bind_keeps_matches_and_names_the_gap)
check("a ranking outage falls back and says so", rank_outage_falls_back_and_says_so)
check("a deadline is reported", deadline_is_reported)
check("fails open when the selector breaks", fails_open_when_the_selector_breaks)
check("no rules -> no block", block_is_empty_without_rules)
check("block carries rule text and caps its size", block_carries_statement_and_caps_length)

if FAILURES:
    print(f"flash-run rules: {len(FAILURES)} FAILED")
    sys.exit(1)
print("flash-run rules: every assertion held")
