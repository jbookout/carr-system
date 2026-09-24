#!/usr/bin/env python3
"""Paired selftest for hooks/jev-supervisor.py — the Jev supervision dispatcher.

What must hold, because the hook sits on nearly every tool call of every
session: it exits 0 on every input including garbage, it prints nothing in
shadow mode, it routes each event to the checks that event owns, a library that
raises never reaches the session, and in advise mode it prints exactly one JSON
object carrying only the notable results.

The check libraries are replaced with fakes, so nothing here touches the
network or the vendor credential.

Run:  python3 ops/jev-supervisor-selftest.py
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "jev-supervisor.py")


def load(mode):
    os.environ["CARR_JEV_SUPERVISOR"] = mode
    spec = importlib.util.spec_from_file_location("jev_supervisor_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_default():
    """Load the hook with CARR_JEV_SUPERVISOR unset, so MODE picks its own
    default rather than an explicit override -- the case `load()` can't reach."""
    os.environ.pop("CARR_JEV_SUPERVISOR", None)
    spec = importlib.util.spec_from_file_location("jev_supervisor_default_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result(check, verdict, advice=""):
    return {"check": check, "verdict": verdict, "confidence": 0.9, "escalate": False,
            "detail": {"advice": advice} if advice else {}}


class FakeLibs:
    """Stands in for every ops/ check library; records which checks ran."""

    def __init__(self, verdicts=None, explode=()):
        self.calls = []
        self.verdicts = verdicts or {}
        self.explode = set(explode)

    def _fn(self, name):
        def fn(*args, **kwargs):
            self.calls.append(name)
            if name in self.explode:
                raise RuntimeError("library bug")
            return result(name, self.verdicts.get(name, "ok"), f"advice from {name}")
        fn.__name__ = name
        return fn

    def __call__(self, lib_name):
        if lib_name == "jev_code_review":
            return SimpleNamespace(latest_task=lambda path: "fix add in calc.py")
        names = ["screen_tool_output", "triage_failure", "locate_bug", "repair_path",
                 "check_existing", "pick_tests", "watch_progress", "check_thinking",
                 "last_assistant_text", "check_test_quality", "check_done_claim", "triage_review"]
        ns = SimpleNamespace(**{n: self._fn(n) for n in names})
        ns.last_assistant_text = lambda path: "done"
        return ns


def run_main(module, payload):
    buf = io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    try:
        with redirect_stdout(buf):
            code = module.main()
    finally:
        sys.stdin = old_stdin
    return code, buf.getvalue()


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        with open(os.path.join(self.dir, "calc.py"), "w") as fh:
            fh.write("def add(a, b):\n    return a - b\n\nassert add(2, 3) == 5\n")

    def tearDown(self):
        self.tmp.cleanup()

    def failing_bash(self):
        return {"hook_event_name": "PostToolUse", "session_id": "t", "cwd": self.dir,
                "tool_name": "Bash", "tool_input": {"command": "python3 calc.py"},
                "tool_response": {"stdout": "", "exit_code": 1, "stderr":
                                  'Traceback (most recent call last):\n  File "calc.py", line 4, '
                                  'in <module>\nAssertionError'}}

    def test_garbage_stdin_exits_zero_silently(self):
        m = load("advise")
        self.assertEqual(run_main(m, "{not json"), (0, ""))

    def test_off_mode_runs_nothing(self):
        m = load("off")
        fake = FakeLibs()
        m._lib = fake
        self.assertEqual(run_main(m, self.failing_bash()), (0, ""))
        self.assertEqual(fake.calls, [])

    def test_shadow_mode_runs_checks_but_prints_nothing(self):
        # Explicit override only, now that advise is the default (Joe,
        # 2026-09-24, decision 5ec806a4): CARR_JEV_SUPERVISOR=shadow must
        # still record without ever printing.
        m = load("shadow")
        fake = FakeLibs(verdicts={"triage_failure": "code_bug"})
        m._lib = fake
        code, out = run_main(m, self.failing_bash())
        self.assertEqual((code, out), (0, ""))
        self.assertIn("triage_failure", fake.calls)

    def test_default_mode_is_advise_when_unset(self):
        m = load_default()
        self.assertEqual(m.MODE, "advise")
        fake = FakeLibs(verdicts={"triage_failure": "code_bug", "locate_bug": "line_located"})
        m._lib = fake
        code, out = run_main(m, self.failing_bash())
        self.assertEqual(code, 0)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("advice from triage_failure", ctx)
        self.assertIn("advice from locate_bug", ctx)

    def test_failed_bash_routes_to_triage_and_bug_locator(self):
        m = load("advise")
        fake = FakeLibs(verdicts={"triage_failure": "code_bug", "locate_bug": "line_located"})
        m._lib = fake
        code, out = run_main(m, self.failing_bash())
        self.assertEqual(code, 0)
        self.assertIn("triage_failure", fake.calls)
        self.assertIn("locate_bug", fake.calls)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("advice from triage_failure", ctx)
        self.assertIn("advice from locate_bug", ctx)

    def test_quiet_verdicts_print_nothing_in_advise_mode(self):
        m = load("advise")
        m._lib = FakeLibs()  # every check says "ok"
        self.assertEqual(run_main(m, self.failing_bash()), (0, ""))

    def test_library_exception_never_reaches_the_session(self):
        m = load("advise")
        fake = FakeLibs(explode={"triage_failure", "screen_tool_output"})
        m._lib = fake
        code, out = run_main(m, self.failing_bash())
        self.assertEqual((code, out), (0, ""))

    def test_missing_read_path_routes_to_repair(self):
        m = load("advise")
        fake = FakeLibs(verdicts={"repair_path": "path_found"})
        m._lib = fake
        payload = {"hook_event_name": "PostToolUse", "session_id": "t", "cwd": self.dir,
                   "tool_name": "Read", "tool_input": {"file_path": os.path.join(self.dir, "calk.py")},
                   "tool_response": "File does not exist."}
        code, out = run_main(m, payload)
        self.assertIn("repair_path", fake.calls)
        self.assertIn("advice from repair_path", out)

    def test_edit_to_test_file_asks_test_quality_not_test_picker(self):
        m = load("shadow")
        fake = FakeLibs()
        m._lib = fake
        payload = {"hook_event_name": "PostToolUse", "session_id": "t", "cwd": self.dir,
                   "tool_name": "Write", "tool_input": {"file_path": os.path.join(self.dir, "test_calc.py"),
                                                        "content": "def test_add():\n    assert 1\n"},
                   "tool_response": {"success": True}}
        run_main(m, payload)
        self.assertIn("check_test_quality", fake.calls)
        self.assertNotIn("pick_tests", fake.calls)

    def test_edit_adding_a_function_asks_already_exists(self):
        m = load("shadow")
        fake = FakeLibs()
        m._lib = fake
        payload = {"hook_event_name": "PostToolUse", "session_id": "t", "cwd": self.dir,
                   "tool_name": "Edit", "tool_input": {"file_path": os.path.join(self.dir, "calc.py"),
                                                       "new_string": "def subtract(a, b):\n    return a - b\n"},
                   "tool_response": {"success": True}}
        run_main(m, payload)
        self.assertIn("check_existing", fake.calls)
        self.assertIn("pick_tests", fake.calls)

    def test_stop_runs_done_claim_and_uses_systemmessage(self):
        m = load("advise")
        fake = FakeLibs(verdicts={"check_done_claim": "unsupported"})
        m._lib = fake
        payload = {"hook_event_name": "Stop", "session_id": "t", "cwd": self.dir,
                   "last_assistant_message": "All tests pass."}
        code, out = run_main(m, payload)
        self.assertEqual(code, 0)
        self.assertIn("check_done_claim", fake.calls)
        self.assertIn("advice from check_done_claim", json.loads(out)["systemMessage"])

    def test_stop_hook_active_does_nothing(self):
        m = load("advise")
        fake = FakeLibs(verdicts={"check_done_claim": "unsupported"})
        m._lib = fake
        payload = {"hook_event_name": "Stop", "session_id": "t", "cwd": self.dir,
                   "stop_hook_active": True, "last_assistant_message": "All tests pass."}
        self.assertEqual(run_main(m, payload), (0, ""))
        self.assertEqual(fake.calls, [])

    def test_budget_stops_starting_new_checks(self):
        m = load("shadow")
        run = m.Run()
        run.started -= m.BUDGET_SECONDS  # already spent
        self.assertIsNone(run.do(lambda: result("x", "stuck")))
        self.assertEqual(run.results, [])

    def test_subprocess_exit_is_zero_on_real_invocation(self):
        env = dict(os.environ, CARR_JEV_SUPERVISOR="off")
        done = subprocess.run([sys.executable, HOOK], input="{}", capture_output=True, text=True,
                              env=env, timeout=30)
        self.assertEqual((done.returncode, done.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
