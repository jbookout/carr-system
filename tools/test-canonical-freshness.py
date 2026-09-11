#!/usr/bin/env python3
"""WR-000040 AC-FRESH: the fast-forward refuses rather than destroys, the
watchdog is independent of it, and the page it sends is a call the verb accepts.

Every git case builds REAL throwaway repositories in a temporary directory and
drives the real command against them. Nothing here mocks git, because the
properties under test are properties of git's behaviour: that `merge --ff-only`
refuses a diverged branch, and that a modified tracked file survives a run.

The cases that matter are the negatives. A fast-forward job that works on a
clean tree proves very little; one that provably declines to touch a dirty one,
and provably leaves the edit intact afterwards, is the whole safety argument.

THE PAGING PATH IS DRIVEN END TO END against a stand-in `run.sh` planted in the
fixture. That is the half the first version of this file could not see: it
disabled paging in every case, so a `report-problem` call missing four of its
five registered fields passed every test and would have been refused by the
verb at the only moment it mattered. The stand-in records the argv it was
handed, so the assertions are about the ACTUAL payload rather than about the
code that builds it.
"""
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
JANITOR = REPO_ROOT / "tools" / "repo-hygiene-janitor.py"
LIBRARY = REPO_ROOT / "lib" / "canonical_freshness.py"

sys.path.insert(0, str(REPO_ROOT / "lib"))
import canonical_freshness as freshness  # noqa: E402

OK = freshness.OK
REFUSED_TRACKED_DIRT = freshness.REFUSED_TRACKED_DIRT
REFUSED_AHEAD = freshness.REFUSED_AHEAD
ALARM = freshness.ALARM

# The verb's registered contract, restated here from
# mcp-server/src/work-request-intake.js so this file fails when the payload
# drifts from it rather than when someone edits the payload builder.
REPORT_PROBLEM_FIELDS = {"idempotency_key", "situation", "title", "desired_outcome",
                         "acceptance_criteria"}
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
CRITERION_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{1,63}$")


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


def run_command(repo, *args):
    """The real entrypoint, not the library: the plist will invoke this path."""
    env = dict(os.environ)
    env["HOME"] = str(repo)
    return subprocess.run([sys.executable, str(JANITOR), "--repository", str(repo), *args],
                          env=env, capture_output=True, text=True)


# A stand-in run.sh. It records the argv it was handed to a file OUTSIDE the
# repository — writing inside would make the fixture dirty and destroy the very
# property the paging tests check — and exits the way the caller asked.
FAKE_RUN_SH = """#!/bin/sh
printf '%s\\n' "$3" > "$CARR_FAKE_PAGER_LOG"
printf '%s' "$CARR_FAKE_PAGER_STDOUT"
exit ${CARR_FAKE_PAGER_EXIT:-0}
"""


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
        self.pager_log = self.root / "pager-argv.txt"

    def advance_origin(self, n=1):
        for i in range(n):
            (self.origin / "tracked.txt").write_text(f"origin-{i}\n")
            git(self.origin, "commit", "--quiet", "-am", f"origin {i}")

    def head(self, where):
        return git(where, "rev-parse", "HEAD").stdout.strip()

    def index_state(self):
        """Everything a page could plausibly disturb: the index, the worktree,
        and the commit the branch points at."""
        return (git(self.clone, "status", "--porcelain", "--untracked-files=all").stdout,
                git(self.clone, "rev-parse", "HEAD").stdout,
                git(self.clone, "diff", "--cached", "--name-status").stdout)

    def install_fake_run_sh(self):
        """Left UNTRACKED on purpose. Committing the stand-in would put the
        clone one commit ahead of origin, which is itself an alarm condition —
        the fixture would then manufacture the very finding under test."""
        script = self.clone / "run.sh"
        script.write_text(FAKE_RUN_SH)
        script.chmod(0o755)
        return script

    def paged_payload(self):
        return json.loads(self.pager_log.read_text())


def page_env(fixture, *, exit_code=0, stdout='{"ok":true}'):
    env = dict(os.environ)
    env.update({"HOME": str(fixture.clone),
                "CARR_FAKE_PAGER_LOG": str(fixture.pager_log),
                "CARR_FAKE_PAGER_EXIT": str(exit_code),
                "CARR_FAKE_PAGER_STDOUT": stdout})
    return env


def run_watchdog_with_pager(fixture, *args, exit_code=0, stdout='{"ok":true}'):
    return subprocess.run([sys.executable, str(JANITOR), "--repository", str(fixture.clone),
                           "--canonical-freshness", "watchdog", *args],
                          env=page_env(fixture, exit_code=exit_code, stdout=stdout),
                          capture_output=True, text=True)


class CanonicalFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.fx = Fixture(self.stack)

    # -- the fast-forward -----------------------------------------------------

    def test_current_tree_is_left_alone(self):
        before = self.fx.head(self.fx.clone)
        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward")
        self.assertEqual(result.returncode, OK, result.stderr)
        self.assertIn("already current", result.stdout)
        self.assertEqual(self.fx.head(self.fx.clone), before)

    def test_a_behind_tree_fast_forwards(self):
        self.fx.advance_origin(3)
        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward")
        self.assertEqual(result.returncode, OK, result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), self.fx.head(self.fx.origin))

    def test_tracked_dirt_refuses_AND_the_edit_survives(self):
        """The case the whole command exists for."""
        self.fx.advance_origin(2)
        edit = self.fx.clone / "tracked.txt"
        edit.write_text("somebody was working on this\n")
        before = self.fx.head(self.fx.clone)

        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward")

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
        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward")
        self.assertEqual(result.returncode, OK, result.stdout + result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), self.fx.head(self.fx.origin))
        self.assertTrue((self.fx.clone / "debris.txt").exists())

    def test_a_tree_ahead_of_origin_refuses_rather_than_rewriting(self):
        (self.fx.clone / "local.txt").write_text("local commit\n")
        git(self.fx.clone, "add", "local.txt")
        git(self.fx.clone, "commit", "--quiet", "-m", "local")
        before = self.fx.head(self.fx.clone)

        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward")

        self.assertEqual(result.returncode, REFUSED_AHEAD, result.stdout + result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), before)

    def test_dry_run_moves_nothing(self):
        self.fx.advance_origin(2)
        before = self.fx.head(self.fx.clone)
        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward", "--dry-run")
        self.assertEqual(result.returncode, OK, result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), before)

    # -- the watchdog ---------------------------------------------------------

    def test_watchdog_is_quiet_on_a_clean_current_tree(self):
        """Old HEAD, but NOT behind origin: age alone is never an alarm."""
        result = run_command(self.fx.clone, "--canonical-freshness", "watchdog",
                             "--no-page", "--max-age-hours", "24")
        self.assertEqual(result.returncode, OK, result.stdout + result.stderr)
        self.assertIn("head_age_hours=48", result.stdout)

    def test_watchdog_alarms_on_tracked_dirt(self):
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")
        result = run_command(self.fx.clone, "--canonical-freshness", "watchdog", "--no-page")
        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("tracked path(s) modified", result.stderr)

    def test_watchdog_reports_untracked_paths_without_alarming(self):
        """The named distinction: counted and listed, never a page."""
        (self.fx.clone / "debris.txt").write_text("awaiting a ruling\n")
        result = run_command(self.fx.clone, "--canonical-freshness", "watchdog", "--no-page")
        self.assertEqual(result.returncode, OK, result.stdout + result.stderr)
        self.assertIn("debris.txt", result.stdout)
        self.assertIn("untracked=1", result.stdout)

    def test_watchdog_alarms_when_canonical_is_ahead(self):
        (self.fx.clone / "local.txt").write_text("local\n")
        git(self.fx.clone, "add", "local.txt")
        git(self.fx.clone, "commit", "--quiet", "-m", "local")
        result = run_command(self.fx.clone, "--canonical-freshness", "watchdog", "--no-page")
        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("ahead of origin/main", result.stderr)

    def test_watchdog_alarms_on_a_stale_head_past_the_bar(self):
        """A 48h-old HEAD that is behind origin is past the one-day bar.

        The bar is exclusive on purpose: a HEAD exactly at the limit is AT it,
        not past it, so a daily job that lands a minute late does not page.
        """
        self.fx.advance_origin(1)
        result = run_command(self.fx.clone, "--canonical-freshness", "watchdog",
                             "--no-page", "--max-age-hours", "24")
        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("behind origin/main", result.stderr)

    def test_watchdog_does_not_read_the_fast_forward_job(self):
        """Independence, checked as a property of the source rather than a claim.

        Sharing a module with the job it watches is fine; CALLING it is not. If
        the watchdog ever learns to ask the fast-forward how it did, it stops
        being able to alarm about that job never having run at all.
        """
        import inspect
        body = "\n".join(line for line in inspect.getsource(freshness.watchdog).splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertNotIn("fast_forward", body)
        self.assertNotIn("canonical-fast-forward.log", body)

    # -- the page -------------------------------------------------------------

    def test_the_page_sends_the_complete_registered_payload(self):
        """The defect this file previously could not see.

        `report-problem` validates an EXACT key set — idempotency_key,
        situation, title, desired_outcome, acceptance_criteria — and refuses
        anything short of it with invalid_report_problem. A page that sends only
        `situation` is not a durable record, it is a rejected call, and the
        watchdog would report it as having paged.
        """
        self.fx.install_fake_run_sh()
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")

        result = run_watchdog_with_pager(self.fx)

        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        payload = self.fx.paged_payload()
        self.assertEqual(set(payload), REPORT_PROBLEM_FIELDS)
        self.assertRegex(payload["idempotency_key"], UUID_RE)
        self.assertTrue(0 < len(payload["situation"]) <= 1000)
        self.assertTrue(0 < len(payload["title"]) <= 200)
        self.assertTrue(0 < len(payload["desired_outcome"]) <= 2000)
        self.assertTrue(1 <= len(payload["acceptance_criteria"]) <= 12)
        ids = set()
        for criterion in payload["acceptance_criteria"]:
            self.assertEqual(set(criterion), {"id", "text"})
            self.assertRegex(criterion["id"], CRITERION_ID_RE)
            self.assertTrue(0 < len(criterion["text"]) <= 500)
            ids.add(criterion["id"])
        self.assertEqual(len(ids), len(payload["acceptance_criteria"]))
        # The measured facts reach the record, not just the word "alarm".
        self.assertIn("tracked_modified=1", payload["situation"])
        self.assertIn("tracked-dirt", payload["title"])

    def test_each_run_mints_its_own_idempotency_key(self):
        """Two runs that see the same dirty tree are two observations. Reusing a
        key would silently collapse the second into the first."""
        self.fx.install_fake_run_sh()
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")

        run_watchdog_with_pager(self.fx)
        first = self.fx.paged_payload()["idempotency_key"]
        run_watchdog_with_pager(self.fx)
        second = self.fx.paged_payload()["idempotency_key"]

        self.assertNotEqual(first, second)

    def test_a_rejected_page_is_reported_and_never_silences_the_exit(self):
        """Route 1 failing must not suppress route 2."""
        self.fx.install_fake_run_sh()
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")

        result = run_watchdog_with_pager(self.fx, exit_code=1,
                                         stdout='{"error":"invalid_report_problem"}')

        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("did not land", result.stderr)
        self.assertNotIn("paged report-problem", result.stdout)

    def test_a_zero_exit_carrying_an_error_body_is_a_rejection(self):
        """The verb reports a refusal inside its payload, so reading only the
        exit status would call a rejection a successful page."""
        self.fx.install_fake_run_sh()
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")

        result = run_watchdog_with_pager(self.fx, exit_code=0,
                                         stdout='{"error":"invalid_report_problem"}')

        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("verb refused: invalid_report_problem", result.stderr)

    def test_paging_leaves_the_worktree_and_the_index_untouched(self):
        """An alarm channel that dirties the tree it is alarming about would
        manufacture the next alarm."""
        self.fx.install_fake_run_sh()
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")
        before = self.fx.index_state()

        result = run_watchdog_with_pager(self.fx)

        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertEqual(self.fx.index_state(), before)

    def test_the_page_never_fires_when_there_is_nothing_to_page_about(self):
        self.fx.install_fake_run_sh()
        result = run_watchdog_with_pager(self.fx)
        self.assertEqual(result.returncode, OK, result.stdout + result.stderr)
        self.assertFalse(self.fx.pager_log.exists())

    # -- the shape ------------------------------------------------------------

    def test_the_library_is_not_a_scac_ingress(self):
        """The reason the machinery lives in lib/ at all.

        ops/scac-mutation-inventory.mjs counts a tracked .py as a script
        entrypoint when it carries a shebang or an `if __name__ == "__main__":`
        block, and the sealed inventory's review overlay cannot add a row. This
        module must stay reachable only through an entrypoint that already holds
        one.
        """
        source = LIBRARY.read_text()
        self.assertFalse(source.startswith("#!"), "a shebang would register a new ingress")
        # The classifier's own regex, copied from isScriptEntrypoint. It is
        # UNANCHORED, so a guard quoted inside a docstring registers the file
        # just as surely as a real one — which is exactly how this assertion
        # first fired.
        guard = re.compile(r"""if\s+__name__\s*==\s*["']__main__["']\s*:""")
        self.assertIsNone(guard.search(source),
                          "a __main__ guard anywhere in the source registers a new ingress")
        mode = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "--stage",
                               "lib/canonical_freshness.py"],
                              capture_output=True, text=True, check=True).stdout.split()
        self.assertTrue(mode, "lib/canonical_freshness.py is not tracked")
        self.assertEqual(mode[0], "100644", "an executable bit would register a new ingress")

    def test_no_launchagent_definition_ships_ahead_of_its_activation_ruling(self):
        """The plists were withdrawn from this change on purpose.

        A new ops/launchd/*.plist is a new launchd-workflow row in the same
        sealed inventory, and `current_source_review` cannot add one. The
        definitions belong to the activation change that carries the successor
        and Joe's AC-FRESH cadence ruling; until then nothing in this repository
        may name them.
        """
        for name in ("com.carr.canonical-fast-forward.plist",
                     "com.carr.canonical-dirty-watchdog.plist"):
            self.assertFalse((REPO_ROOT / "ops" / "launchd" / name).exists(), name)
            self.assertNotIn(name, (REPO_ROOT / "ops" / "config-as-code.py").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1)
