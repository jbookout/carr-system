#!/usr/bin/env python3
"""Manifest determinism checks keep one source revision when HEAD moves."""
import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import patch


class BuildsCaptured(Exception):
    pass


class SourceBindingTests(unittest.TestCase):
    def test_determinism_builds_pin_head_before_either_build(self):
        path = Path(__file__).with_name("release-manifest-selftest.py")
        spec = importlib.util.spec_from_file_location("manifest_selftest", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        initial_sha = "a" * 40
        advanced_sha = "b" * 40
        observed = []

        def build(*args):
            observed.append(args[args.index("--sha") + 1])
            if len(observed) == 2:
                raise BuildsCaptured
            return {}

        # HEAD may advance after the first build. No ref may be re-resolved to
        # choose the revision for the second determinism build.
        with patch.object(module, "git", side_effect=[initial_sha, advanced_sha]) as git, \
                patch.object(module, "build", side_effect=build), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(BuildsCaptured):
                module.main()
        self.assertEqual(observed, [initial_sha, initial_sha])
        git.assert_called_once_with("rev-parse", "HEAD")


if __name__ == "__main__":
    unittest.main()
