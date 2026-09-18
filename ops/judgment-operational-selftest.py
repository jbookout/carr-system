"""Every live judgment still ANSWERS, proven against answers known in advance.

WHY A SECOND CHECK, WHEN ONE ALREADY ASSERTS EVERY JUDGMENT HAS A CALLER.
Because having a caller is not the bar, and this session proved it twice in
one day. ops/jev_change_tolls.py was wired into the push hook with 2>/dev/null
on the invocation while writing its findings to stderr: called on every push,
printing to nowhere, and the wiring check was green the whole time. Earlier the
codebase scan read a response key that does not exist, scored 426 regions at
0.0, and reported a clean codebase -- again with every caller in place.

BOTH FAILURES LOOK EXACTLY LIKE SUCCESS, and that is the point. A judgment that
returns nothing is indistinguishable from a judgment that correctly found
nothing. You cannot tell them apart by watching. You can only tell them apart
by asking something whose answer you already know.

SO EVERY PROBE COMES IN A PAIR. A POSITIVE whose answer must be non-empty, and
a NEGATIVE whose answer must be empty. One alone is useless: a judgment stuck
at "nothing" passes every negative, and a judgment stuck at "everything" passes
every positive. Only the pair pins it.

WHAT THIS CATCHES that no other check in this repository does: a credential
that expired, a service that changed its response shape, output written to a
stream somebody silenced, a question edited until it stopped discriminating,
and a judgment quietly degrading while its tests -- which use recorded
fixtures, never the live service -- stay green.

LOCAL-ONLY, and for the same reason ops/config-as-code-selftest.py is. It needs
a live credential and a live service. A hosted runner has neither, so there it
would assert the absence of a thing that is absent by construction. The
credential is read from the environment and never printed.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import pathlib
import unittest

OPS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(OPS)


def load(module_name):
    spec = importlib.util.spec_from_file_location(
        module_name, os.path.join(OPS, f"{module_name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def recent_commits(limit=40):
    out = subprocess.run(
        ["git", "log", "--oneline", f"-{limit}", "--format=%h %s"],
        capture_output=True, text=True, cwd=REPO, timeout=60).stdout
    return [(line.split(" ", 1)[0], line.split(" ", 1)[1])
            for line in out.splitlines() if " " in line]


def has_credential():
    if os.environ.get("TYPESAFE_API_KEY"):
        return True
    path = os.path.expanduser("~/.config/carr/typesafe.env")
    return os.path.exists(path)


SKIP = "LOCAL-ONLY: needs the live credential and the live service"


@unittest.skipUnless(has_credential(), SKIP)
class ShellPreCheckTests(unittest.TestCase):
    """Fires on every Bash call, from the delegation gate."""

    def setUp(self):
        self.module = load("command_precheck")

    def test_it_still_flags_a_command_that_cannot_work(self):
        probability, _facts, _reasons = self.module.check(
            "./.venv/bin/python ops/there-is-no-such-file-as-this.py --nope",
            repo=REPO)
        self.assertGreater(probability, 0.5,
                           "a command naming a path that does not exist should "
                           "read as likely to fail; near-zero here means the "
                           "judgment stopped answering, not that the command is fine")

    def test_it_still_leaves_an_ordinary_command_alone(self):
        probability, _facts, _reasons = self.module.check("git status", repo=REPO)
        # None is the quiet answer, not a missing one: nothing was flagged, so
        # there is no maximum to report. Treating it as a number was this
        # test's own bug on the first run.
        self.assertTrue(probability is None or probability < 0.5,
                        f"a plain command must not be flagged (got {probability}); "
                        "a high score here means the check now warns about "
                        "everything, which is the same as warning about nothing")


@unittest.skipUnless(has_credential(), SKIP)
class StaleClaimJudgeTests(unittest.TestCase):
    """Fires at Stop, on any claim that something is missing or unbuilt."""

    def setUp(self):
        self.module = load("stale_claim_judge")
        self.commits = recent_commits()

    def test_it_still_finds_the_commit_that_answers_a_claim(self):
        # The claim is chosen to be answered by a commit SUBJECT, because
        # subjects are all this judgment is given. A squash merge retitles
        # everything under one subject, so a claim answered only by a commit
        # BODY is a bad probe rather than a failure -- that mistake cost a
        # false alarm on 2026-09-18.
        hits = self.module.refuting_commits(
            "no session warns before a shell command runs", self.commits)
        self.assertIsNotNone(hits, "the judgment was unavailable")
        self.assertTrue(hits, "a commit subject in the last 40 answers this "
                              "claim outright; finding none means the judgment "
                              "has gone silent")

    def test_it_still_returns_nothing_for_a_claim_no_commit_answers(self):
        hits = self.module.refuting_commits(
            "the moon is made of green cheese", self.commits)
        self.assertIsNotNone(hits, "the judgment was unavailable")
        self.assertEqual(hits, [], "nothing in this repository speaks to this; "
                                   "matching anything means it now matches "
                                   "everything")


@unittest.skipUnless(has_credential(), SKIP)
class DefectClassAdvisoryTests(unittest.TestCase):
    """Fires when a session is about to file a defect."""

    def setUp(self):
        self.module = load("jev_defect_class")

    def test_it_still_returns_a_ranked_shortlist(self):
        note = self.module.advise({
            "claimed": "the library was finished and working",
            "actual": "nothing in the repository ever called it, so it never ran"})
        self.assertTrue(note, "no shortlist came back at all")
        self.assertIn("0.", note, "a shortlist with no probabilities in it means "
                                  "the scores are being read from the wrong key "
                                  "-- the failure that scored 426 regions at zero")


class TollsHoldThePushTests(unittest.TestCase):
    """The advisory stopped being advice on 2026-09-18.

    It had scored inventory_reseal at 0.94 on a push, named the exact remedy,
    and been pushed past; hosted CI failed 25 minutes later on that same toll.
    These hold the shape of the fix rather than the score: the model picks
    which deterministic check to run, the check decides, and anything that
    cannot run lets the push through.
    """

    def setUp(self):
        self.module = load("jev_change_tolls")

    def test_a_probability_never_refuses_a_push_on_its_own(self):
        """Every toll that can hold a push names a real command to run."""
        for name, entry in self.module.VERIFIERS.items():
            argv, reason = entry
            self.assertIsInstance(argv, list)
            self.assertTrue(argv and all(isinstance(a, str) for a in argv),
                            f"{name} has no runnable command, so its score "
                            f"would be the thing refusing the push")
            self.assertIn(name, self.module.TOLLS,
                          f"{name} verifies a toll that does not exist")
            self.assertTrue(reason.strip(), f"{name} fails without saying why")

    def test_a_verifier_that_cannot_run_does_not_hold_the_push(self):
        """The fail-closed trap this must never become.

        A missing interpreter, an unreachable service, a renamed script: none
        of those are evidence the change owes anything. Blocking on them would
        turn an advisory into an outage.
        """
        module = self.module
        original = dict(module.VERIFIERS)
        try:
            module.VERIFIERS.clear()
            module.VERIFIERS["inventory_reseal"] = (
                ["/nonexistent/interpreter/that/is/not/here"], "cannot run")
            state = {"files": {"added": [], "edited": ["hooks/delegation-gate.py"]},
                     "added_files_with_a_shebang_or_main_guard": [],
                     "edited_files_that_are_script_entrypoints": ["hooks/delegation-gate.py"],
                     "this_branch_merged_another_branch": False}
            failures = module.verify(state)
            for name, _p, reason, _o in failures:
                self.assertIn("could not run", reason,
                              "a verifier that failed to LAUNCH was reported as "
                              "a failed check, which would block a correct push")
        finally:
            module.VERIFIERS.clear()
            module.VERIFIERS.update(original)

    def test_the_push_hook_actually_stops_on_a_failed_check(self):
        """Wiring, not behaviour. The function can be perfect and the hook can
        still `|| true` it into silence -- which is exactly what happened to
        this same advisory's stderr in the commit that added it."""
        hook = (pathlib.Path(self.module.REPO) / "ops" / "githooks" / "pre-push"
                ).read_text(encoding="utf-8")
        self.assertIn("module.verify(", hook,
                      "the push hook does not run the verifiers at all")
        self.assertIn("sys.exit(3)", hook,
                      "the hook prints failures without exiting nonzero")
        block = hook[hook.index("module.verify("):]
        self.assertNotIn("|| true", block.split("TOLLS")[0],
                         "`|| true` on the toll block swallows the refusal, "
                         "which is how this check got wired to nothing once already")
        self.assertIn('if [ "$status" -eq 3 ]', hook,
                      "the shell never translates the refusal into a failed push")

    def test_the_skip_door_still_exists(self):
        """A door with no handle gets taken off its hinges."""
        hook = (pathlib.Path(self.module.REPO) / "ops" / "githooks" / "pre-push"
                ).read_text(encoding="utf-8")
        self.assertIn("CARR_SKIP_TOLL_ADVISORY", hook)


class ChangeCollectorTests(unittest.TestCase):
    """What the advisory SEES, which needs no credential and so runs in CI too.

    Separated from the judgment tests on purpose: the questions need the model,
    but the file list handed to them is ordinary git plumbing, and both bugs
    found on 2026-09-18 were in the plumbing rather than in the judgment. A
    perfect answer about the wrong files is still the wrong answer.
    """

    def setUp(self):
        self.module = load("jev_change_tolls")

    def test_every_path_the_advisory_names_is_a_file_that_exists(self):
        """A path that does not resolve means the list was mis-parsed.

        `git status --porcelain` puts the status in two COLUMNS, so an
        unstaged edit reads " M path" with a leading space. Stripping the
        whole blob before splitting it ate that space on the FIRST line only
        and shifted that one path by a character -- the advisory reported an
        edit to "cp-server/src/tools.js", which does not exist, while every
        other row came through clean. One silently wrong row per run is the
        hardest kind to notice, so this asserts the property rather than the
        parse.
        """
        state = self.module.change()
        named = state["files"]["added"] + state["files"]["edited"]
        missing = [rel for rel in named
                   if not (pathlib.Path(self.module.REPO) / rel).exists()]
        self.assertEqual(
            missing, [],
            "the change advisory named paths that do not exist, so its file "
            "list is being mis-parsed and every judgment it makes is about "
            "the wrong change: " + ", ".join(missing))

    def test_it_sees_uncommitted_work(self):
        """The advisory is most useful mid-edit, which is when the first
        version was blind: it diffed origin/main...HEAD only."""
        source = (pathlib.Path(self.module.__file__).read_text(encoding="utf-8")
                  if getattr(self.module, "__file__", None) else "")
        self.assertIn(
            "status", source,
            "jev_change_tolls.change() no longer consults git status, so it "
            "cannot see uncommitted work -- the exact moment a session most "
            "needs to be told what its change owes")


@unittest.skipUnless(has_credential(), SKIP)
class ChangeTollAdvisoryTests(unittest.TestCase):
    """Fires on every push, from ops/githooks/pre-push."""

    def setUp(self):
        self.module = load("jev_change_tolls")

    def test_it_still_names_the_tolls_an_obvious_change_owes(self):
        owed = self.module.owed({
            "files": {"added": ["hooks/new-gate.py"],
                      "edited": ["hooks/delegation-gate.py", "ops/ci.sh"]},
            "added_files_with_a_shebang_or_main_guard": ["hooks/new-gate.py"],
            "edited_files_that_are_script_entrypoints":
                ["hooks/delegation-gate.py", "ops/ci.sh"],
            "this_branch_merged_another_branch": False})
        named = {name for _probability, name, _fix in owed}
        for expected in ("new_ingress_admitted", "inventory_reseal", "gate_rebless"):
            self.assertIn(expected, named,
                          f"a new hook with a shebang plus two edited entrypoints "
                          f"plainly owes {expected}; missing it means the judgment "
                          f"stopped discriminating")

    def test_it_still_stays_quiet_on_a_change_that_owes_nothing(self):
        owed = self.module.owed({
            "files": {"added": [], "edited": ["README.md"]},
            "added_files_with_a_shebang_or_main_guard": [],
            "edited_files_that_are_script_entrypoints": [],
            "this_branch_merged_another_branch": False})
        self.assertEqual(owed, [], "editing one markdown file owes no toll; "
                                   "naming any means it now warns on everything")


@unittest.skipUnless(has_credential(), SKIP)
class PostWriteCodeReviewTests(unittest.TestCase):
    """Fires after every Write or Edit, from hooks/lint-gate.py."""

    def setUp(self):
        self.module = load("jev_code_review")

    def test_it_still_reads_a_swallowed_write_as_wrong(self):
        scores = self.module.review_one({
            "path": "probe.py", "line": 1, "kind": "probe",
            "code": "def save_invoice(row):\n"
                    "    try:\n"
                    "        db.execute('insert into invoice values (%s)', row)\n"
                    "        db.commit()\n"
                    "    except Exception:\n"
                    "        pass\n"
                    "    return {'ok': True, 'saved': True}"})
        self.assertGreater(scores["swallow_is_wrong_here"], 0.6,
                           "a database write swallowed and then reported as "
                           "succeeded is the clearest case there is; a low score "
                           "means the judgment stopped discriminating")

    def test_it_still_leaves_correct_code_alone(self):
        scores = self.module.review_one({
            "path": "probe.py", "line": 1, "kind": "probe",
            "code": "def normalize_rate(amount, basis):\n"
                    "    if basis == 'usd_sf_mo':\n"
                    "        return amount * 12\n"
                    "    if basis == 'usd_sf_yr':\n"
                    "        return amount\n"
                    "    raise ValueError(f'unknown rate basis: {basis}')"})
        loud = [name for name, value in scores.items()
                if not name.startswith("_") and value >= 0.70]
        self.assertEqual(loud, [], "plain correct code must draw nothing; "
                                   "flagging it means the review now flags "
                                   "everything, which reads as noise and gets "
                                   "ignored")


@unittest.skipUnless(has_credential(), SKIP)
class EveryPathIsOutputNotJustReturnValue(unittest.TestCase):
    """The hook must SHOW what the judgment said.

    A judgment can be correct, called, and still silent, because the thing that
    invokes it discards its output. That is not hypothetical: the push hook
    carried 2>/dev/null over a block writing to stderr, and every other check
    in this repository was green.
    """

    def test_the_push_hook_does_not_silence_the_advisory(self):
        with open(os.path.join(REPO, "ops", "githooks", "pre-push"),
                  encoding="utf-8") as handle:
            body = handle.read()
        block = body[body.find("WHAT THIS CHANGE OWES"):]
        self.assertIn("jev_change_tolls", block, "the advisory is not in pre-push")
        invocation = [line for line in block.splitlines()
                      if ".venv/bin/python" in line and "TOLLS" in line]
        self.assertTrue(invocation, "could not find the advisory's invocation line")
        self.assertNotIn("2>/dev/null", invocation[0],
                         "the advisory writes to stderr; silencing stderr makes it "
                         "run on every push and print to nowhere")

    def test_the_post_write_review_prints_its_context(self):
        with open(os.path.join(REPO, "hooks", "lint-gate.py"),
                  encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("jev_code_review", body,
                      "the post-write review is not wired into the write door")
        self.assertIn("additionalContext", body,
                      "the review runs and its answer is never handed to the "
                      "session, which is indistinguishable from not running")

    def test_the_defect_advisory_prints_its_context(self):
        with open(os.path.join(REPO, "hooks", "blocker-decider-gate.py"),
                  encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("additionalContext", body,
                      "the shortlist is computed and never handed to the session")


if __name__ == "__main__":
    if not has_credential():
        print("judgment-operational-selftest: " + SKIP)
        sys.exit(0)
    unittest.main(verbosity=2)
