#!/usr/bin/env python3
"""Ordinary sandbox checks never dispatch a live model implicitly."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "flash_sandbox_suite", Path(__file__).with_name("test-flash-run-sandbox.py"))
assert spec and spec.loader
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


class SuiteSelection(unittest.TestCase):
    def run_cases(self, args, available):
        calls = []

        def sandbox_case():
            calls.append("sandbox")

        def model_case():
            calls.append("model")

        sandbox_case.__module__ = model_case.__module__ = "__main__"
        suite.live(model_case)
        with patch.dict(vars(suite), {"sandbox_case": sandbox_case, "model_case": model_case}), \
                patch.object(suite, "_flash_up", side_effect=available) as probe, \
                patch.object(suite, "FAILURES", []), patch.object(suite, "TEMPS", []), \
                patch.object(sys, "argv", ["test-flash-run-sandbox.py", *args]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = suite.main()
        return code, calls, probe.call_count

    def test_ordinary_suite_does_not_probe_or_dispatch_a_model(self):
        def unexpected_probe():
            raise AssertionError("ordinary CI contacted the model server")

        self.assertEqual(self.run_cases([], unexpected_probe), (0, ["sandbox"], 0))

    def test_explicit_live_check_dispatches_only_when_available(self):
        self.assertEqual(self.run_cases(["--live"], lambda: True),
                         (0, ["sandbox", "model"], 1))

    def test_explicit_live_check_refuses_missing_dependency(self):
        self.assertEqual(self.run_cases(["--live"], lambda: False), (78, [], 1))


if __name__ == "__main__":
    unittest.main()
