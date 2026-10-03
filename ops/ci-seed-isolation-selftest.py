#!/usr/bin/env python3
"""Concurrent checker seeds must live in separate disposable repositories."""
import json
import pathlib
import selectors
import subprocess
import sys
import tempfile
import unittest

from git_env import scrubbed_env

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHILD = """
import importlib.util, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent))
spec = importlib.util.spec_from_file_location('checker', sys.argv[1])
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
checker.REPO = pathlib.Path(sys.argv[2])
checker.CI = checker.REPO / 'ops/ci.sh'
with checker.checker_fixture():
    (checker.REPO / 'seed.txt').write_text(sys.argv[3])
    print(json.dumps({'repo': str(checker.REPO)}), flush=True)
    sys.stdin.readline()
"""


class SeedIsolationTests(unittest.TestCase):
    def test_concurrent_seeds_never_modify_the_source_or_each_other(self):
        with tempfile.TemporaryDirectory(prefix="ci-seed-isolation-") as tmp:
            source = pathlib.Path(tmp) / "source"
            source.mkdir()
            env = scrubbed_env()
            def git(*args):
                subprocess.run(["git", *args], cwd=source, env=env, check=True,
                               capture_output=True)
            git("init", "-q")
            (source / "seed.txt").write_text("original")
            git("add", "seed.txt")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-qm", "fixture")
            children = []
            try:
                snapshots = []
                for marker in ("first", "second"):
                    child = subprocess.Popen(
                        [sys.executable, "-c", CHILD, str(ROOT / "ops/ci-selftest.py"),
                         str(source), marker], env=env, stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    children.append(child)
                    with selectors.DefaultSelector() as selector:
                        selector.register(child.stdout, selectors.EVENT_READ)
                        self.assertTrue(selector.select(15), "seed child did not become ready")
                    snapshots.append(pathlib.Path(json.loads(child.stdout.readline())["repo"]))
                self.assertEqual((source / "seed.txt").read_text(), "original")
                self.assertNotEqual(snapshots[0], snapshots[1])
                for snapshot, marker in zip(snapshots, ("first", "second")):
                    self.assertEqual((snapshot / "seed.txt").read_text(), marker)
                for child in children:
                    _, err = child.communicate("done\n", timeout=15)
                    self.assertEqual(child.returncode, 0, err)
                self.assertEqual((source / "seed.txt").read_text(), "original")
                self.assertFalse((source / "_ci_selftest_seed_journal.json").exists())
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    child.communicate(timeout=15)


if __name__ == "__main__":
    unittest.main()
