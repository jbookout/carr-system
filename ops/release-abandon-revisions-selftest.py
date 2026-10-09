#!/usr/bin/env python3
"""Release fixtures use delivered history even when topic ancestors cannot ship."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git_env import fixture_env

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release_abandon", REPO / "ops/release-abandon-selftest.py")
assert spec and spec.loader
abandon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(abandon)


class FixtureRevisions(unittest.TestCase):
    def test_topic_ancestors_do_not_supply_release_fixtures(self):
        with tempfile.TemporaryDirectory(prefix="release-revisions-") as directory:
            repo = Path(directory)

            def git(*args, input=None):
                return subprocess.run(["git", *args], cwd=repo, env=fixture_env(),
                                      input=input, text=True, capture_output=True,
                                      check=True).stdout.strip()

            git("init", "--bare", "-q")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            tree = git("mktree", input="")
            delivered = []
            for index in range(4):
                parents = ["-p", delivered[0]] if delivered else []
                delivered.insert(0, git("commit-tree", tree, *parents,
                                        input=f"delivered {index}\n"))
            git("update-ref", "refs/remotes/origin/main", delivered[0])
            topic = git("commit-tree", tree, "-p", delivered[-1], input="unshippable topic\n")
            git("update-ref", "refs/heads/topic", topic)
            git("symbolic-ref", "HEAD", "refs/heads/topic")
            with patch.object(abandon, "REPO", repo), patch.dict("os.environ", fixture_env(), clear=True):
                self.assertEqual(abandon.staging_fixture_revisions(4), delivered)
                with self.assertRaisesRegex(RuntimeError, "delivered main history"):
                    abandon.staging_fixture_revisions(5)
            self.assertEqual(git("rev-parse", "HEAD"), topic)


if __name__ == "__main__":
    unittest.main()
