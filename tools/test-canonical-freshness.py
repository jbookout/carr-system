#!/usr/bin/env python3
"""WR-000040 AC-FRESH: the fast-forward refuses rather than destroys, and the
watchdog is independent of it.

Every case builds REAL throwaway git repositories in a temporary directory and
runs the real scripts against them. Nothing here mocks git, because the
properties under test are properties of git's behaviour: that `merge --ff-only`
refuses a diverged branch, and that a modified tracked file survives a run.

The cases that matter are the negatives. A fast-forward job that works on a
clean tree proves very little; one that provably declines to touch a dirty one,
and provably leaves the edit intact afterwards, is the whole safety argument.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FAST_FORWARD = REPO_ROOT / "bin" / "canonical-fast-forward.sh"
WATCHDOG = REPO_ROOT / "bin" / "canonical-dirty-watchdog.sh"

# Exit codes the scripts document. Named here so a changed code fails loudly
# rather than turning an assertion into a tautology.
OK = 0
REFUSED_TRACKED_DIRT = 3
REFUSED_AHEAD = 4
ALARM = 6


def git(cwd, *args, when=None):
    env = dict(os.environ)
    # Every git fixture gets its own scrubbed environment; the ambient one on a
    # developer machine carries GIT_DIR, GIT_WORK_TREE and author identity that
    # would leak into the fixture.
    for key in list(env):
        if key.startswith("GIT_"):
            env.pop(key)
    env.update({
        "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "HOME": str(cwd),
    })
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    return subprocess.run(["git", "-C", str(cwd), *args], env=env,
                          capture_output=True, text=True, check=True)


def run_script(script, repo, *args):
    env = dict(os.environ)
    env["HOME"] = str(repo)
    return subprocess.run(["/bin/zsh", str(script), "--repository", str(repo), *args],
                          env=env, capture_output=True, text=True)


class Fixture:
    """An origin and a clone of it, both real, in a directory that goes away."""

    def __init__(self, stack):
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.origin = self.root / "origin"
        self.origin.mkdir()
        git(self.origin, "init", "--quiet", "--initial-branch=main", ".")
        (self.origin / "tracked.txt").write_text("one\n")
        git(self.origin, "add", "tracked.txt")
        # Dated two days back, so the clone's HEAD is genuinely old and the
        # staleness bar can be tested at its real value rather than at zero.
        self.old_stamp = f"{int(time.time()) - 48 * 3600} +0000"
        git(self.origin, "commit", "--quiet", "-m", "one", when=self.old_stamp)

        self.clone = self.root / "canonical"
        subprocess.run(["git", "clone", "--quiet", str(self.origin), str(self.clone)],
                       check=True, capture_output=True)

    def advance_origin(self, n=1):
        for i in range(n):
            (self.origin / "tracked.txt").write_text(f"origin-{i}\n")
            git(self.origin, "commit", "--quiet", "-am", f"origin {i}")

    def head(self, where):
        return git(where, "rev-parse", "HEAD").stdout.strip()


class CanonicalFreshnessTests(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.fx = Fixture(self.stack)

    # -- the fast-forward -----------------------------------------------------

    def test_current_tree_is_left_alone(self):
        before = self.fx.head(self.fx.clone)
        result = run_script(FAST_FORWARD, self.fx.clone)
        self.assertEqual(result.returncode, OK, result.stderr)
        self.assertIn("already current", result.stdout)
        self.assertEqual(self.fx.head(self.fx.clone), before)

    def test_a_behind_tree_fast_forwards(self):
        self.fx.advance_origin(3)
        result = run_script(FAST_FORWARD, self.fx.clone)
        self.assertEqual(result.returncode, OK, result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), self.fx.head(self.fx.origin))

    def test_tracked_dirt_refuses_AND_the_edit_survives(self):
        """The case the whole script exists for."""
        self.fx.advance_origin(2)
        edit = self.fx.clone / "tracked.txt"
        edit.write_text("somebody was working on this\n")
        before = self.fx.head(self.fx.clone)

        result = run_script(FAST_FORWARD, self.fx.clone)

        self.assertEqual(result.returncode, REFUSED_TRACKED_DIRT, result.stdout + result.stderr)
        self.assertIn("REFUSED", result.stderr)
        # Both halves matter: it did not move the branch, AND it did not eat the
        # edit to be able to move it.
        self.assertEqual(self.fx.head(self.fx.clone), before)
        self.assertEqual(edit.read_text(), "somebody was working on this\n")

    def test_untracked_paths_do_not_block_the_fast_forward(self):
        """AC-CLEAN routes untracked paths to Joe; blocking on them means never running."""
        self.fx.advance_origin(1)
        (self.fx.clone / "debris.txt").write_text("awaiting a ruling\n")
        result = run_script(FAST_FORWARD, self.fx.clone)
        self.assertEqual(result.returncode, OK, result.stdout + result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), self.fx.head(self.fx.origin))
        self.assertTrue((self.fx.clone / "debris.txt").exists())

    def test_a_tree_ahead_of_origin_refuses_rather_than_rewriting(self):
        (self.fx.clone / "local.txt").write_text("local commit\n")
        git(self.fx.clone, "add", "local.txt")
        git(self.fx.clone, "commit", "--quiet", "-m", "local")
        before = self.fx.head(self.fx.clone)

        result = run_script(FAST_FORWARD, self.fx.clone)

        self.assertEqual(result.returncode, REFUSED_AHEAD, result.stdout + result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), before)

    def test_dry_run_moves_nothing(self):
        self.fx.advance_origin(2)
        before = self.fx.head(self.fx.clone)
        result = run_script(FAST_FORWARD, self.fx.clone, "--dry-run")
        self.assertEqual(result.returncode, OK, result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), before)

    # -- the watchdog ---------------------------------------------------------

    def test_watchdog_is_quiet_on_a_clean_current_tree(self):
        """Old HEAD, but NOT behind origin: age alone is never an alarm."""
        result = run_script(WATCHDOG, self.fx.clone, "--no-page", "--max-age-hours", "24")
        self.assertEqual(result.returncode, OK, result.stdout + result.stderr)
        self.assertIn("head_age_hours=48", result.stdout)

    def test_watchdog_alarms_on_tracked_dirt(self):
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")
        result = run_script(WATCHDOG, self.fx.clone, "--no-page")
        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("tracked path(s) modified", result.stderr)

    def test_watchdog_reports_untracked_paths_without_alarming(self):
        """The named distinction: counted and listed, never a page."""
        (self.fx.clone / "debris.txt").write_text("awaiting a ruling\n")
        result = run_script(WATCHDOG, self.fx.clone, "--no-page")
        self.assertEqual(result.returncode, OK, result.stdout + result.stderr)
        self.assertIn("debris.txt", result.stdout)
        self.assertIn("untracked=1", result.stdout)

    def test_watchdog_alarms_when_canonical_is_ahead(self):
        (self.fx.clone / "local.txt").write_text("local\n")
        git(self.fx.clone, "add", "local.txt")
        git(self.fx.clone, "commit", "--quiet", "-m", "local")
        result = run_script(WATCHDOG, self.fx.clone, "--no-page")
        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("ahead of origin/main", result.stderr)

    def test_watchdog_alarms_on_a_stale_head_past_the_bar(self):
        """A 48h-old HEAD that is behind origin is past the one-day bar.

        The bar is exclusive on purpose: a HEAD exactly at the limit is AT it,
        not past it, so a daily job that lands a minute late does not page.
        """
        self.fx.advance_origin(1)
        result = run_script(WATCHDOG, self.fx.clone, "--no-page", "--max-age-hours", "24")
        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("behind origin/main", result.stderr)

    def test_watchdog_does_not_read_the_fast_forward_job(self):
        """Independence, checked as a property of the source rather than a claim.

        If the watchdog ever learns to ask the fast-forward job how it did, it
        stops being able to alarm about that job never running at all.
        """
        source = WATCHDOG.read_text()
        # Comments name the sibling deliberately; code must not invoke it.
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertNotIn("canonical-fast-forward.sh", code)
        self.assertNotIn("com.carr.canonical-fast-forward", code)

    # -- the definitions ------------------------------------------------------

    def test_both_plists_are_definition_only_and_carry_no_cadence(self):
        import plistlib
        config = (REPO_ROOT / "ops" / "config-as-code.py").read_text()
        for name in ("com.carr.canonical-fast-forward.plist",
                     "com.carr.canonical-dirty-watchdog.plist"):
            path = REPO_ROOT / "ops" / "launchd" / name
            self.assertTrue(path.exists(), name)
            body = plistlib.loads(path.read_bytes())
            self.assertIs(body.get("RunAtLoad"), False, f"{name} would fire at load")
            for trigger in ("StartCalendarInterval", "StartInterval", "WatchPaths"):
                self.assertNotIn(trigger, body, f"{name} carries a cadence it must not")
            self.assertIn(name, config, f"{name} is not registered in DEFINITION_ONLY")


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1)
