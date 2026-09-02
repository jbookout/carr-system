#!/usr/bin/env python3
"""Acceptance for the R03 settlement restore-set derivation.

Every test builds a real throwaway git repository. None of them touch the
canonical checkout, and none of them need a network.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r03_settlement_restore_set import (  # noqa: E402
    RestoreSetRefusal, build_restore_set, dirty_paths,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


class RestoreSetCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        git(self.repo, "init", "-q", ".")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "t")
        # A nested path, because the bug this guards truncates the FIRST
        # character of the FIRST reported path and a top-level name would
        # still look plausible after losing it.
        (self.repo / "hooks").mkdir()
        (self.repo / "hooks" / "worktree-self-plumb.py").write_text("original\n")
        (self.repo / "keep.txt").write_text("keep\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.pin = git(self.repo, "rev-parse", "HEAD").strip()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_clean_tree_with_no_allowlist_yields_empty_set(self) -> None:
        self.assertEqual(build_restore_set(self.repo, self.pin, []), [])

    def test_unapproved_dirty_path_refuses_and_names_it(self) -> None:
        (self.repo / "hooks" / "worktree-self-plumb.py").write_text("someone else's work\n")
        with self.assertRaises(RestoreSetRefusal) as caught:
            build_restore_set(self.repo, self.pin, [])
        self.assertIn("hooks/worktree-self-plumb.py", str(caught.exception))

    def test_unapproved_dirty_path_never_silently_enrols(self) -> None:
        """The 2026-09-02 near-miss, as a test.

        The old derivation returned this path as a restore entry, which would
        have reverted another session's live edit. Enrolment is now impossible
        without explicit approval, so the ONLY acceptable outcome is a refusal.
        """
        (self.repo / "hooks" / "worktree-self-plumb.py").write_text("someone else's work\n")
        try:
            result = build_restore_set(self.repo, self.pin, [])
        except RestoreSetRefusal:
            return
        self.fail(f"unapproved dirty path was enrolled instead of refused: {result}")

    def test_approved_dirty_path_is_enrolled_with_its_pinned_blob(self) -> None:
        path = "hooks/worktree-self-plumb.py"
        (self.repo / path).write_text("changed\n")
        expected_blob = git(self.repo, "rev-parse", f"{self.pin}:{path}").strip()
        result = build_restore_set(self.repo, self.pin, [path])
        self.assertEqual(result, [{"path": path, "blob_oid": expected_blob}])

    def test_first_dirty_path_is_not_truncated(self) -> None:
        """Regression for the shipped 'ooks/worktree-self-plumb.py'.

        Exactly ONE dirty file, so it is the FIRST porcelain line — the only
        line the whole-output-strip bug corrupts. A helper that strips the
        captured blob before slicing loses the leading space of this line and
        returns a path one character short.
        """
        (self.repo / "hooks" / "worktree-self-plumb.py").write_text("changed\n")
        found = dirty_paths(self.repo)
        self.assertEqual(found, ["hooks/worktree-self-plumb.py"])
        for path in found:
            self.assertTrue(
                (self.repo / path).exists(),
                f"derived path {path!r} does not exist on disk — it was truncated",
            )

    def test_path_containing_a_space_survives(self) -> None:
        noisy = "hooks/two words.py"
        (self.repo / noisy).write_text("x\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "add spaced path")
        pin = git(self.repo, "rev-parse", "HEAD").strip()
        (self.repo / noisy).write_text("y\n")
        self.assertIn(noisy, dirty_paths(self.repo))
        self.assertEqual(
            build_restore_set(self.repo, pin, [noisy])[0]["path"], noisy)

    def test_untracked_file_neither_refuses_nor_enrols(self) -> None:
        (self.repo / "scratch.tmp").write_text("junk\n")
        self.assertEqual(build_restore_set(self.repo, self.pin, []), [])

    def test_staged_deletion_is_a_candidate_and_refuses(self) -> None:
        git(self.repo, "rm", "-q", "keep.txt")
        with self.assertRaises(RestoreSetRefusal) as caught:
            build_restore_set(self.repo, self.pin, [])
        self.assertIn("keep.txt", str(caught.exception))

    def test_allowlisted_but_clean_path_adds_nothing(self) -> None:
        """A stale allow-list must not invent restore work."""
        self.assertEqual(
            build_restore_set(self.repo, self.pin, ["hooks/worktree-self-plumb.py"]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
