#!/usr/bin/env python3
"""Offline checks for tools/flash-script.py: a scripted Flash and a fixed Jev drive the real loop, with real script
runs in a real throwaway folder. No Flash server, no Jev, no network. The live runs are recorded in the PR."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("flash_script", os.path.join(HERE, "flash-script.py"))
fs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fs)

FAILURES = []


def check(label, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(label)
        print(f"  FAIL  {label}: {exc}")
    else:
        print(f"  ok    {label}")


class FixedJev:
    def __init__(self, flags=None, pre=None, covers=0.0):
        self.flag_rows, self.pre_row, self.covers, self.errors = list(flags or []), pre or {}, covers, 0

    def pre(self, q, prev):
        return self.pre_row

    def flags(self, q, out, prev):
        return self.flag_rows.pop(0) if self.flag_rows else {}

    def covers_all(self, q, answer, total):
        return self.covers


class ScriptedFlash:
    """Replies in order; records every call's messages, token cap and thinking switch."""
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, msgs, max_tokens=None, think=True):
        self.calls.append({"msgs": [dict(m) for m in msgs], "max_tokens": max_tokens, "think": think})
        r = self.replies.pop(0)
        return (r, "stop", 10, "") if isinstance(r, str) else r


def folder(lines):
    work = tempfile.mkdtemp(prefix="fs-test-")
    with open(os.path.join(work, "rows.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return work


def fence(code):
    return "```python\n" + code + "\n```"


def test_script_runs_in_the_folder_and_a_printed_answer_is_grounded():
    work = folder(["a 3", "b 4", "c 5"])
    flash = ScriptedFlash([fence("print('total', sum(int(l.split()[1]) for l in open('rows.txt')))"), "FINAL: 12"])
    answer, log = fs.solve("Sum the numbers?", work, ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert answer == "12" and log[-1]["support"] == "printed", log
    assert "rows.txt (" in flash.calls[0]["msgs"][1]["content"]  # the preview, never the whole file


def test_every_script_turn_is_capped():
    flash = ScriptedFlash([fence("print('total', 7)"), "FINAL: 7"])
    fs.solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert all(c["max_tokens"] == fs.COMPUTE_TOKENS for c in flash.calls), flash.calls


def test_a_runaway_turn_is_followed_by_one_without_thinking():
    flash = ScriptedFlash([("", "length", 12288, "thinking..."), fence("print('total', 7)"), "FINAL: 7"])
    answer, log = fs.solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert flash.calls[1]["think"] is False and flash.calls[2]["think"] is True, [c["think"] for c in flash.calls]
    assert log[1]["no_think_next"] and answer == "7"


def test_a_made_up_answer_is_marked_invented():
    flash = ScriptedFlash([fence("print('lines 1500')"), 'FINAL: {"a": 300, "b": 300, "c": 300}'])
    answer, log = fs.solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert log[-1]["support"] == "invented", log[-1]


def test_jev_flag_becomes_a_reviewer_note():
    flash = ScriptedFlash([fence("print('matched 1')"), fence("print('matched 900')"), "FINAL: 900"])
    fs.solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev(flags=[{"misparsed": 0.9}]))
    assert "Reviewer note" in flash.calls[1]["msgs"][-1]["content"]


def test_answer_ready_refuses_another_script_once():
    flash = ScriptedFlash([fence("print('total 5')"), fence("print('again')"), "FINAL: 5"])
    answer, log = fs.solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash,
                           jev=FixedJev(flags=[{"answer_ready": 0.9}]))
    assert answer == "5" and any(e.get("focus_enforced") for e in log), log
    assert sum(1 for e in log if "run_s" in e) == 1


def test_final_file_answer_is_read_from_the_working_folder_only():
    flash = ScriptedFlash([fence("import json; json.dump(['A', 'B'], open('final_answer.json', 'w')); print('wrote 2')"),
                           "FINAL: @final_answer.json"])
    answer, log = fs.solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert json.loads(answer) == ["A", "B"], answer


def test_final_file_followed_by_code_runs_the_code_first():
    code = "import json; json.dump([1, 2], open('out.json', 'w'))"
    flash = ScriptedFlash([fence("print('rows 2')"), "FINAL: @out.json\n" + fence(code)])
    answer, log = fs.solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev())
    assert json.loads(answer) == [1, 2] and any(e.get("final_script_run") for e in log), log


def test_count_gap_gets_one_free_fix_when_jev_agrees():
    flash = ScriptedFlash([fence("print('lines: 10')"), 'FINAL: {"a": 4, "b": 4}', fence("print('a 5 b 5')"),
                           'FINAL: {"a": 5, "b": 5}'])
    answer, log = fs.solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev(covers=0.9))
    assert json.loads(answer) == {"a": 5, "b": 5}, answer
    assert any(e.get("count_gap") == [10, 8] or e.get("count_gap") == (10, 8) for e in log), log


def test_repeated_identical_script_stops():
    s = fence("print(1)")
    answer, log = fs.solve("q", folder(["x"]), ["rows.txt"], chat_fn=ScriptedFlash([s, s]), jev=FixedJev())
    assert answer is None and log[-1].get("stuck"), log


def test_semantic_question_gets_the_labelling_hint():
    flash = ScriptedFlash(["FINAL: 1"])
    fs.solve("q", folder(["x"]), ["rows.txt"], chat_fn=flash, jev=FixedJev(pre={"semantic": 0.9}))
    assert "labels depend on the meaning" in flash.calls[0]["msgs"][1]["content"]


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            check(name[5:].replace("_", " "), fn)
    if FAILURES:
        print(f"flash-script: {len(FAILURES)} failed")
        return 1
    print("flash-script: every assertion held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
