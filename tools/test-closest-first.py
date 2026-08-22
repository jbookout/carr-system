#!/usr/bin/env python3
"""Regression tests for the read-only closest-first command."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import unittest
from unittest.mock import patch


MODULE_PATH = os.path.join(os.path.dirname(__file__), "closest-first.py")
SPEC = importlib.util.spec_from_file_location("closest_first", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
closest_first = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(closest_first)


class FakeCursor:
    def __init__(self) -> None:
        self.statement = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, params=None) -> None:
        self.statement = statement

    def fetchone(self):
        return (1, 1, 0, 100)

    def fetchall(self):
        if "from v_loop_proximity where not unscored" in self.statement:
            return [(297, None, "joe", 1, "one action", 12, "none", False,
                     "needs a domain", "Classify this old loop")]
        return []


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return FakeCursor()


class UnscoredFakeCursor(FakeCursor):
    def fetchone(self):
        return (1, 0, 1, 0)

    def fetchall(self):
        if "where unscored" in self.statement:
            return [(298, None, 30, "Classify this older loop")]
        return []


class UnscoredFakeConnection(FakeConnection):
    def cursor(self):
        return UnscoredFakeCursor()


class ClosestFirstTest(unittest.TestCase):
    def test_unclassified_domain_renders_without_crashing(self) -> None:
        output = io.StringIO()
        with (patch.object(closest_first.psycopg, "connect", return_value=FakeConnection()),
              patch.dict(os.environ, {"DATABASE_URL": "postgresql://synthetic"}),
              patch.object(sys, "argv", ["closest-first"]),
              contextlib.redirect_stdout(output)):
            self.assertEqual(closest_first.main(), 0)

        self.assertIn("#297   (unclassified)", output.getvalue())

    def test_all_unscored_unclassified_domain_renders_without_crashing(self) -> None:
        output = io.StringIO()
        with (patch.object(closest_first.psycopg, "connect",
                           return_value=UnscoredFakeConnection()),
              patch.dict(os.environ, {"DATABASE_URL": "postgresql://synthetic"}),
              patch.object(sys, "argv", ["closest-first", "--all"]),
              contextlib.redirect_stdout(output)):
            self.assertEqual(closest_first.main(), 0)

        self.assertIn("#298   (unclassified)", output.getvalue())


if __name__ == "__main__":
    unittest.main()
