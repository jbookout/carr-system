"""Every judgment library has a caller, or it is declared inert on purpose.

WHY THIS FILE EXISTS. On 2026-09-18 four judgment libraries were built in one
session. Two of them — the command pre-check and the stale-claim judge — run on
every shell call and every Stop, because each is dispatched from a hook that
already fires. The other two ran exactly once each, from a scratch script, by
hand. Nothing in the repository invoked them and nothing in the repository
complained. They were finished, tested, reviewed, merged and inert.

THAT IS NOT AN ACCIDENT OF THAT DAY, it is this system's most repeated failure
and three separate measurements of it landed within an hour:

  · 132 of 211 active rules have no detector in rule-jit-triggers.v1.json and
    can never reach a session.
  · The doctrine search's concept axis returned zero across eight probes and 67
    rows, resting on 18 hand-authored concepts against 2,251 sections. Its
    ranking policy is a two-axis formula running on one axis, and its
    dual-evidence bonus can never fire at all.
  · Two of four judgment libraries had no caller.

Every one is capability built, wired to nothing, noticed by nobody. Prose does
not close this: the record-defect verb's own schema text asks a session to
reuse an existing class, and the ledger holds 304 singletons across 320
classes. A rule that asks someone to remember is a rule that fires sometimes.

SO THIS IS A CHECK, NOT A RULE, and it is modelled on one this repository
already has. ops/ci-selftest.py's test_every_test_file_in_the_tree_is_collected
reads ci.sh's own globs back out and fails when any test-shaped file matches
none of them, because a suite nobody runs looks exactly like coverage. Same
idea, one level up: a judgment nobody calls looks exactly like capability.

DECLARING SOMETHING INERT IS ALLOWED AND IS THE POINT. A module may sit in
DECLARED_INERT with a reason and the loop that will wire it. That turns an
invisible gap into a written one, which is the whole difference. What is not
allowed is silence.
"""

from __future__ import annotations

import ast
import re
import subprocess
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent
REPO = OPS.parent

# A module is a JUDGMENT when it reaches the model, directly or through the
# judging layer. Detected from the source rather than from the filename,
# because a naming convention is the thing that quietly stops being followed.
REACHES_THE_MODEL = ("typesafe_client", "jev_judge")

# Where a caller may live. These are the surfaces that actually run: a hook the
# harness fires, a script a person or a job invokes, the MCP server that serves
# the verbs. A library calling another library is not wiring — it just moves
# the question one file along, so ops/ is deliberately absent here.
DOORS = ("hooks", "bin", "tools", "pipelines", "mcp-server/src", "evals",
         # git runs these itself on every commit and push, which makes
         # them a door even though they sit under ops/ -- the one place
         # the blanket exclusion of ops/ below would get a real caller
         # wrong.
         "ops/githooks")

# Modules that reach the model and are KNOWINGLY not wired yet. Each needs the
# reason and the loop, in the open. An entry here is a debt, not an excuse, and
# the list is meant to shrink.
DECLARED_INERT: dict[str, str] = {
    "jev_rule_select.py":
        "DECIDED 2026-09-18: it REPLACES the keyword table rather than backing "
        "it up, on Joe's steer that a rule Jev cannot detect should be "
        "re-engineered rather than kept on a regex crutch. Still listed here "
        "because the wiring into hooks/rule-pack-preuse-reselection.py is not "
        "done, and an inert module must stay declared until it has a caller. "
        "THE EVIDENCE, and it is worth reading before anyone reopens this. "
        "Three separate times this module read as weaker than the regexes and "
        "all three were the measuring instrument. The last one: load_rules() "
        "was feeding the model title_gist, which is a HEADLINE -- median 87 "
        "characters, all 211 ending without terminal punctuation because a "
        "title has no sentence to end -- plus `reason`, which is triage "
        "metadata about where a rule is delivered. The rule's actual statement "
        "was never sent. Feeding the real text moved the origin-not-HEAD rule "
        "from 0.39 to 0.96 on a moment its condition covers, and 0.15 on the "
        "near-miss. Nine rules that a 12-moment sweep called undetectable all "
        "separated cleanly once given a situation that met their condition "
        "(positives 0.75-0.95, every negative below the 0.75 floor), including "
        "two -- end with one next action, and show the shape rather than "
        "narrating it -- that carry no token a regex could ever match. Across "
        "211 rules by 12 moments, 2532 judgments in 45 seconds, ZERO rules "
        "bound to nine or more moments: nothing is over-broad. "
        "THE LATENCY BUDGET IS ANSWERED and is not a blocker. The selector "
        "costs ~1.3s (one ranking Choice over the roster, then parallel nouls "
        "over a shortlist of 20, the two stages serial so the cost is two "
        "round trips rather than twenty). Its home, hooks/rule-pack-preuse-"
        "reselection.py, is already allocated a 20-SECOND timeout by the "
        "harness and already shells out to the standing-context door on every "
        "matched call. Better still, the corpus this change adds carries the "
        "rule statements locally, so a judged selector can deliver the text "
        "itself and DROP that round trip -- net latency flat or lower, not "
        "additive. WHAT REMAINS is only the wiring. Loop 620.",
}


def judgment_modules():
    """Every ops/*.py that reaches the model, read from its imports."""
    found = []
    for path in sorted(OPS.glob("*.py")):
        if path.name.endswith("-selftest.py") or path.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        # An import reaches the model; so does a spec_from_file_location naming
        # one of those modules, which is how ops/ libraries load each other
        # because ops/ holds no __init__.py and is not a package.
        source = path.read_text(encoding="utf-8")
        body = source.split('"""', 2)[-1] if source.lstrip().startswith('"""') else source
        for name in REACHES_THE_MODEL:
            if re.search(r"[\"']%s[\"']" % re.escape(name), body) or _imports(tree, name):
                found.append(path.name)
                break
    return found


def _imports(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == name for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] == name:
                return True
    return False


def door_files():
    """Every tracked file under a door. One git call, not one per module."""
    try:
        out = subprocess.run(["git", "ls-files", "--"] + list(DOORS),
                             capture_output=True, text=True, cwd=REPO, timeout=60)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return []
    return [REPO / line.strip() for line in out.stdout.splitlines() if line.strip()]


def wired_modules():
    """Every judgment a door reaches, following ops -> ops references.

    Reachability rather than a direct-caller check, because the judging layer
    ops/jev_judge.py is called only by the judgments sitting on top of it and
    would otherwise read as inert while in fact running on every shell command.
    A door reaching a judgment reaches everything that judgment uses.

    Transitive THROUGH ops/, never FROM ops/: the seeds are door files only, so
    two inert modules that call each other still reach nothing and cannot vouch
    for one another. Tracked files only, so an untracked scratch script cannot
    make an inert module look wired — which is exactly how the two inert ones
    looked fine on the day they were written.
    """
    ops_stems = {p.stem for p in OPS.glob("*.py")}
    text_of = {}
    for path in OPS.glob("*.py"):
        try:
            text_of[path.stem] = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover
            text_of[path.stem] = ""

    # One alternation pass per file rather than one pass per module. With ~100
    # modules against every tracked file under every door, the naive form spent
    # two minutes where this spends a second, and a check slow enough to be
    # resented is a check somebody eventually skips.
    any_stem = re.compile(
        r"\b(%s)\b" % "|".join(re.escape(s) for s in sorted(ops_stems, key=len, reverse=True)))

    def names_in(text):
        return set(any_stem.findall(text))

    frontier = set()
    for path in door_files():
        try:
            frontier |= names_in(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:  # pragma: no cover
            continue

    reached = set()
    while frontier:
        stem = frontier.pop()
        if stem in reached:
            continue
        reached.add(stem)
        frontier |= names_in(text_of.get(stem, "")) - reached

    judgments = {m[:-3]: m for m in judgment_modules()}
    return {judgments[s] for s in reached if s in judgments}


class JudgmentWiringTests(unittest.TestCase):

    def test_every_judgment_module_is_wired_or_declared_inert(self):
        """The whole file in one assertion.

        The failure message has to teach, because whoever trips this will be
        someone who has just finished building something and believes it is
        done — which is precisely the state this catches.
        """
        wired = wired_modules()
        unwired = [m for m in judgment_modules()
                   if m not in wired and m not in DECLARED_INERT]
        self.assertEqual(
            unwired, [],
            "These judgment libraries reach the model and NOTHING CALLS THEM. "
            "A library with no caller is not a capability, it is a file: it "
            "will never run, and no test will ever say so. Wire each one to a "
            "door that already fires — a hook under hooks/, a script under "
            "bin/ or tools/, or the MCP server — attaching it to the MOMENT it "
            "is about rather than to a session's memory of it. If it genuinely "
            "should not be wired yet, add it to DECLARED_INERT in this file "
            "with the reason and the loop that will wire it, so the gap is "
            "written down instead of silent. Unwired: " + ", ".join(unwired))

    def test_declared_inert_entries_carry_a_reason_and_a_loop(self):
        """An escape hatch with no cost is just a way of turning the check off."""
        for module, reason in DECLARED_INERT.items():
            self.assertTrue(
                re.search(r"loop\s*#?\s*\d+", reason, re.I),
                f"{module} is declared inert without naming the loop that will "
                f"wire it; a debt with no ticket is a debt nobody pays")
            self.assertGreater(
                len(reason), 60,
                f"{module} is declared inert without a real reason")

    def test_declared_inert_names_a_module_that_still_exists(self):
        """A stale entry silently exempts nothing and hides a real gap the day
        someone reuses that filename."""
        for module in DECLARED_INERT:
            self.assertTrue((OPS / module).exists(),
                            f"{module} is declared inert but no longer exists")

    def test_the_detector_finds_the_modules_we_know_about(self):
        """A detector that finds nothing passes this whole file trivially.

        Names the two that are wired today rather than a count, because a count
        moves every time a module is added and then gets edited to match rather
        than investigated.
        """
        modules = judgment_modules()
        for known in ("command_precheck.py", "jev_defect_class.py"):
            self.assertIn(known, modules,
                          "the judgment detector stopped seeing a known judgment")

    def test_a_library_calling_a_library_is_not_wiring(self):
        """ops/ is deliberately not a door. Otherwise two inert modules that
        call each other vouch for one another and the check means nothing."""
        self.assertNotIn("ops", DOORS)


if __name__ == "__main__":
    unittest.main(verbosity=1)
