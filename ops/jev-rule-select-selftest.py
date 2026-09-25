"""Offline suite for ops/jev_rule_select.py. No credential, no network, no spend.

Every judgment arrives through an injected fake, so this runs on a hosted runner
with nothing configured. The cases that earn their place are the ones that would
have caught a defect this module actually had: the response envelope is nested
one level deeper than a caller expects, and neither sibling module is importable
as a package.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent
MODULE_PATH = OPS / "jev_rule_select.py"
SPEC = importlib.util.spec_from_file_location("jev_rule_select", MODULE_PATH)
assert SPEC and SPEC.loader
sel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sel)


class FakeJudge:
    """Stands in for ops/jev_judge.py, with its real response envelope."""

    JudgeUnavailable = RuntimeError

    def __init__(self, by_gist=None, default=0.1, fail_on=None):
        self.by_gist = by_gist or {}
        self.default = default
        self.fail_on = fail_on or set()
        self.subjects = []

    def judge(self, subject, questions, **kwargs):
        self.subjects.append(subject)
        gist = subject["rule"]
        if gist in self.fail_on:
            raise self.JudgeUnavailable("synthetic outage")
        value = self.by_gist.get(gist, self.default)
        # The real judge returns the decoded response, so the answer sits under
        # "answers". A caller reading one level too shallow is the defect this
        # envelope exists to catch.
        return {"answers": {"binds": {"type": "noul", "noul": value}},
                "usage": {"input_tokens": 1}, "model": "fake", "elapsed_ms": 1}


class FakeClient:
    """Stands in for ops/typesafe_client.py's question builders."""

    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}


RULES = [
    {"id": "aaaaaaaa", "gist": "binds here", "context": ""},
    {"id": "bbbbbbbb", "gist": "also binds", "context": ""},
    {"id": "cccccccc", "gist": "does not bind", "context": ""},
]


class SelectionTests(unittest.TestCase):
    def test_only_rules_over_the_floor_are_surfaced(self):
        judge = FakeJudge({"binds here": 0.95, "also binds": 0.88,
                           "does not bind": 0.40})
        out = sel.select("a moment", RULES, floor=0.85,
                         client=FakeClient, judge=judge)
        self.assertEqual([r["id"] for r in out], ["aaaaaaaa", "bbbbbbbb"])

    def test_the_floor_is_inclusive_and_actually_moves(self):
        judge = FakeJudge({"binds here": 0.85, "also binds": 0.84,
                           "does not bind": 0.10})
        out = sel.select("a moment", RULES, floor=0.85,
                         client=FakeClient, judge=judge)
        self.assertEqual([r["id"] for r in out], ["aaaaaaaa"],
                         "0.84 must fall below a floor of 0.85")

    def test_results_are_ordered_by_probability(self):
        judge = FakeJudge({"binds here": 0.90, "also binds": 0.99,
                           "does not bind": 0.95})
        out = sel.select("a moment", RULES, floor=0.5,
                         client=FakeClient, judge=judge)
        self.assertEqual([r["id"] for r in out],
                         ["bbbbbbbb", "cccccccc", "aaaaaaaa"])

    def test_the_cap_is_enforced(self):
        rules = [{"id": f"{i:08d}", "gist": f"rule {i}", "context": ""}
                 for i in range(20)]
        judge = FakeJudge(default=0.99)
        out = sel.select("a moment", rules, floor=0.5, limit=5,
                         client=FakeClient, judge=judge)
        self.assertEqual(len([r for r in out if r["probability"] is not None]), 5)

    def test_one_request_per_rule_and_no_rule_sees_another(self):
        judge = FakeJudge(default=0.9)
        sel.select("a moment", RULES, floor=0.5, client=FakeClient, judge=judge)
        self.assertEqual(len(judge.subjects), len(RULES))
        for subject in judge.subjects:
            serialized = json.dumps(subject)
            others = [r["gist"] for r in RULES if r["gist"] != subject["rule"]]
            for other in others:
                self.assertNotIn(other, serialized,
                                 "a rule's request must not carry a competitor")

    def test_a_failed_judgment_is_reported_not_dropped(self):
        judge = FakeJudge(default=0.99, fail_on={"also binds"})
        out = sel.select("a moment", RULES, floor=0.5,
                         client=FakeClient, judge=judge)
        failed = [r for r in out if r["probability"] is None]
        self.assertEqual([r["id"] for r in failed], ["bbbbbbbb"],
                         "a rule that could not be judged must stay visible")

    def test_concurrency_does_not_change_the_answer(self):
        """The whole corpus asked serially took over a minute on the first live
        run, so select() asks in parallel. The risk that introduces is order:
        a pool that returns out of sequence would silently reshuffle ties."""
        rules = [{"id": f"{i:08d}", "gist": f"rule {i}", "context": ""}
                 for i in range(30)]
        scores = {f"rule {i}": 0.5 + i / 100 for i in range(30)}
        serial = sel.select("a moment", rules, floor=0.5, limit=30,
                            client=FakeClient, judge=FakeJudge(scores), workers=1)
        parallel = sel.select("a moment", rules, floor=0.5, limit=30,
                              client=FakeClient, judge=FakeJudge(scores), workers=16)
        self.assertEqual([r["id"] for r in serial], [r["id"] for r in parallel])
        self.assertEqual([r["probability"] for r in serial],
                         [r["probability"] for r in parallel])

    def test_an_outage_does_not_raise_at_the_caller(self):
        judge = FakeJudge(default=0.99, fail_on={r["gist"] for r in RULES})
        out = sel.select("a moment", RULES, client=FakeClient, judge=judge)
        self.assertTrue(all(r["probability"] is None for r in out))


class QuestionShapeTests(unittest.TestCase):
    def test_the_false_criterion_excuses_a_sound_but_irrelevant_rule(self):
        """Without this the answer drifts to 'is this a good rule', which every
        active rule passes, which selects everything, which selects nothing."""
        question = sel.binding_question(client=FakeClient)
        false = question["criteria"]["false"].casefold()
        self.assertIn("excellent rule", false)
        self.assertIn("not bind", false)

    def test_the_question_asks_about_binding_not_about_quality(self):
        question = sel.binding_question(client=FakeClient)
        self.assertIn("binds", question["instructions"].casefold())

    def test_already_complying_is_named_as_the_boundary_case(self):
        """The regression that motivates this. The first criterion scored ONE
        rule across twelve different shell commands — 'run the command, do not
        hand the partner a command to paste' — because it matched the rule's
        topic while the session was already complying with it. Naming the
        boundary case is what separates 'this rule is about commands' from
        'this rule's condition is met now'."""
        question = sel.binding_question(client=FakeClient)
        false = question["criteria"]["false"].casefold()
        self.assertIn("already", false)
        self.assertIn("topic overlap is not binding", false)

    def test_the_condition_not_the_subject_is_what_is_asked(self):
        question = sel.binding_question(client=FakeClient)
        self.assertIn("condition", question["instructions"].casefold())


class CorpusTests(unittest.TestCase):
    def test_the_real_rule_corpus_loads_and_is_not_empty(self):
        rules = sel.load_rules()
        self.assertGreater(len(rules), 50)
        self.assertTrue(all(r["id"] and isinstance(r["gist"], str) for r in rules))

    def test_unreachable_rules_are_a_real_and_live_measurement(self):
        """The count must come from the files, never from a number in prose."""
        unreachable = sel.unreachable_rules()
        rules = sel.load_rules()
        reachable = sel.reachable_rule_ids()
        self.assertEqual(len(unreachable), len(rules) - len(
            {r["id"] for r in rules} & reachable))
        self.assertGreater(len(unreachable), 0,
                           "if every rule became reachable, this module's "
                           "premise changed and its docstring must be re-read")

    def test_the_docstring_quotes_no_count(self):
        """A number in prose becomes a dated artifact read as present state —
        the most-logged failure class in this system."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        docstring = source.split('"""')[1]
        import re as _re
        # A provenance date is not a count and is required, so strip ISO dates
        # first. Anything numeric left in the prose is a measurement that moved.
        prose = _re.sub(r"\d{4}-\d{2}-\d{2}", "", docstring)
        # A probability recorded against a date is an observation, not a count:
        # it describes what happened once and does not go stale the way "there
        # are N unreachable rules" does. Counts are the thing being banned.
        prose = _re.sub(r"\d*\.\d+", "", prose)
        found = _re.findall(r"\b(\d{2,})\b", prose)
        self.assertEqual(found, [], f"the docstring quotes {found}; these are "
                                    "counts that move — call the function instead")

    def test_regex_delivery_reproduces_the_git_push_trigger(self):
        delivered = sel.regex_delivery("git push origin HEAD")
        self.assertIn("173119a8", delivered)
        self.assertNotIn("173119a8", sel.regex_delivery("ls -la"))


class ShadowTests(unittest.TestCase):
    def test_shadow_records_both_sets_and_both_differences(self):
        judge = FakeJudge(default=0.99)
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "shadow.jsonl")
            record = sel.shadow_selection(
                "about to push a branch", "git push origin HEAD",
                log_path=log, rules=RULES, floor=0.5,
                client=FakeClient, judge=judge)
            written = json.loads(Path(log).read_text(encoding="utf-8").strip())
        self.assertEqual(record["judged_would_surface"], written["judged_would_surface"])
        for field in ("judged_would_surface", "regex_did_surface",
                      "judged_only", "regex_only"):
            self.assertIn(field, record)
        self.assertIn("173119a8", record["regex_did_surface"])

    def test_shadow_computes_no_accuracy_number(self):
        """Scoring against the existing bundle measures agreement with a
        known-crude mechanism and would be read as a score. See the docstring."""
        judge = FakeJudge(default=0.99)
        with tempfile.TemporaryDirectory() as tmp:
            record = sel.shadow_selection(
                "a moment", "git push", log_path=os.path.join(tmp, "s.jsonl"),
                rules=RULES, floor=0.5, client=FakeClient, judge=judge)
        for banned in ("accuracy", "precision", "recall", "agreement", "score"):
            self.assertNotIn(banned, record,
                             f"{banned} would be read as a verdict on the selector")

    def test_a_broken_log_path_never_reaches_the_caller(self):
        judge = FakeJudge(default=0.99)
        record = sel.shadow_selection(
            "a moment", "git push", log_path="/proc/nonexistent/s.jsonl",
            rules=RULES, floor=0.5, client=FakeClient, judge=judge)
        self.assertIn("judged_would_surface", record)

    def test_shadow_decides_nothing(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("sys.exit", "raise SystemExit", "os._exit"):
            self.assertNotIn(forbidden, source,
                             "shadow mode must never stop a caller")


class AdviceTests(unittest.TestCase):
    def test_no_binding_is_a_successful_empty_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = sel.advise(
                "a moment", log_path=os.path.join(tmp, "live.jsonl"),
                rules=RULES, client=FakeClient, judge=FakeJudge(default=0.1),
                workers=1)
        self.assertEqual(result, [])

    def test_an_unavailable_candidate_is_not_misreported_as_no_binding(self):
        judge = FakeJudge(default=0.1, fail_on={"also binds"})
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "live.jsonl")
            with self.assertRaises(sel.SelectionUnavailable):
                sel.advise("a moment", log_path=log, rules=RULES,
                           client=FakeClient, judge=judge, workers=1)
            written = json.loads(Path(log).read_text(encoding="utf-8").strip())
        self.assertEqual(written["unavailable"], ["bbbbbbbb"])


class EntrypointTests(unittest.TestCase):
    def test_the_module_is_not_a_script_entrypoint(self):
        """Uses the sealed inventory's own detector, not a substring search. A
        shebang or a main guard here would move the frontier and owe a registry
        successor — and the detector is a regex over the WHOLE file, so even an
        example inside this docstring would seal it."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        import re as _re
        self.assertFalse(source.startswith("#!"), "no shebang")
        guard = _re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:")
        self.assertIsNone(guard.search(source), "no main guard")



class RankingPassTests(unittest.TestCase):
    """The cheap Choice that narrows 211 rules before any is judged alone.

    Added after the fact, and the reason is worth recording: the existing
    twenty-one cases all passed unchanged when the ranking pass went in, which
    means none of them covered it. A suite that stays green through a new stage
    is not vouching for that stage.
    """

    class _RankJudge:
        def __init__(self, probabilities, fail=False):
            self.probabilities = probabilities
            self.fail = fail
            self.calls = []

        def judge(self, state, questions, **kwargs):
            self.calls.append((state, questions))
            if self.fail:
                raise RuntimeError("service did not answer")
            return {"answers": {"rank": {"type": "choice",
                                         "probabilities": dict(self.probabilities)}}}

    class _RankClient:
        @staticmethod
        def choice(instructions, options):
            return {"type": "choice", "instructions": instructions,
                    "criteria": dict(options)}

        @staticmethod
        def noul(instructions, true=None, false=None):
            return {"type": "noul", "instructions": instructions,
                    "criteria": {"true": true, "false": false}}

    def _roster(self, count=60):
        return [{"id": f"rule{i:03d}", "gist": f"gist {i}", "context": f"context {i}"}
                for i in range(count)]

    def test_the_whole_roster_is_ranked_in_one_request(self):
        rules = self._roster()
        fake = self._RankJudge({"rule007": 0.6, sel.NONE_BIND: 0.2})
        sel.narrow("a moment", rules, judge=fake, client=self._RankClient())
        self.assertEqual(len(fake.calls), 1)
        options = fake.calls[0][1]["rank"]["criteria"]
        self.assertIn("rule007", options)

    def test_the_shortlist_is_what_the_ranking_put_on_top(self):
        rules = self._roster()
        fake = self._RankJudge({"rule042": 0.5, "rule007": 0.3, sel.NONE_BIND: 0.2})
        got = sel.narrow("a moment", rules, limit=2, judge=fake,
                       client=self._RankClient())
        self.assertEqual([r["id"] for r in got], ["rule042", "rule007"])

    def test_a_roster_already_under_the_limit_is_not_ranked_at_all(self):
        """Paying for a ranking pass to narrow five rules to twenty is waste."""
        fake = self._RankJudge({})
        got = sel.narrow("a moment", self._roster(5), judge=fake,
                       client=self._RankClient())
        self.assertEqual(len(fake.calls), 0)
        self.assertEqual(len(got), 5)

    def test_the_none_binds_option_is_offered(self):
        """Most moments bind no rule, and a Choice with no way to decline must
        return one anyway."""
        fake = self._RankJudge({sel.NONE_BIND: 0.9})
        sel.narrow("a moment", self._roster(), judge=fake, client=self._RankClient())
        options = fake.calls[0][1]["rank"]["criteria"]
        self.assertIn(sel.NONE_BIND, options)
        self.assertIn("ALREADY COMPLYING", options[sel.NONE_BIND])

    def test_the_none_binds_option_never_becomes_a_rule(self):
        fake = self._RankJudge({sel.NONE_BIND: 0.9, "rule001": 0.05})
        got = sel.narrow("a moment", self._roster(), judge=fake,
                       client=self._RankClient())
        self.assertNotIn(sel.NONE_BIND, [r["id"] for r in got])

    def test_rubrics_are_truncated_for_the_ranking_pass(self):
        rules = [{"id": "wordy", "gist": "g" * 900, "context": "c"}] + self._roster()
        fake = self._RankJudge({"wordy": 0.9})
        sel.narrow("a moment", rules, judge=fake, client=self._RankClient())
        rubric = fake.calls[0][1]["rank"]["criteria"]["wordy"]
        self.assertLessEqual(len(rubric), sel.RUBRIC_CHARS)

    def test_the_option_cap_is_respected(self):
        fake = self._RankJudge({"rule001": 0.9})
        sel.narrow("a moment", self._roster(400), judge=fake, client=self._RankClient())
        options = fake.calls[0][1]["rank"]["criteria"]
        self.assertLessEqual(len(options), sel.MAX_OPTIONS + 1)

    def test_a_failed_ranking_falls_back_to_the_whole_roster(self):
        """Judging everything is slower and more expensive, not wrong. It is
        what this module did before the ranking pass existed."""
        rules = self._roster()
        got = sel.narrow("a moment", rules, judge=self._RankJudge({}, fail=True),
                       client=self._RankClient())
        self.assertEqual(len(got), len(rules))

    def test_an_empty_ranking_falls_back_to_the_whole_roster(self):
        rules = self._roster()
        got = sel.narrow("a moment", rules, judge=self._RankJudge({}),
                       client=self._RankClient())
        self.assertEqual(len(got), len(rules))

    def test_a_ranking_naming_nothing_real_falls_back(self):
        rules = self._roster()
        got = sel.narrow("a moment", rules,
                       judge=self._RankJudge({"invented": 0.9, sel.NONE_BIND: 0.1}),
                       client=self._RankClient())
        self.assertEqual(len(got), len(rules))


AGENT_DONE = ("<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>\n"
              "<summary>Agent \"Build X\" finished</summary>\n<result>PR #1 open</result>\n"
              "</task-notification>")
OTHER_AGENT_DONE = AGENT_DONE.replace("a1", "b2").replace("Build X", "Fix Y").replace(
    "PR #1 open", "all green")
COMMAND_FAILED = ("<task-notification>\n<task-id>c3</task-id>\n<status>failed</status>\n"
                  "<summary>Background command \"make\" failed with exit code 2</summary>\n"
                  "</task-notification>")


class VerdictCacheTests(unittest.TestCase):
    """Binding verdicts are kept per session and (rule id, pack, input class)
    for a window. The cache may only ever cost an extra ask or keep a rule
    delivered — never silence a rule a fresh judgment would deliver on a
    first sight of a class."""

    JUDGED = {"binds here": 0.95, "also binds": 0.30, "does not bind": 0.10}

    def _select(self, judge, cache, situation="a moment", now=1000.0, **kw):
        kw.setdefault("session_id", "s1")
        return sel.select(situation, RULES, floor=0.75, client=FakeClient,
                          judge=judge, workers=1, cache_path=cache, now=now, **kw)

    def test_repeated_identical_calls_ask_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            judge = FakeJudge(self.JUDGED)
            first = self._select(judge, cache)
            info = {}
            second = self._select(judge, cache, now=1060.0, cache_info=info)
            third = self._select(judge, cache, now=1200.0)
            self.assertEqual(len(judge.subjects), len(RULES))
            self.assertTrue(info["hit"])
            self.assertEqual(info["verdicts_reused"], len(RULES))
            self.assertEqual([r["id"] for r in first], ["aaaaaaaa"])
            self.assertEqual([r["id"] for r in second], ["aaaaaaaa"])
            self.assertEqual([r["id"] for r in third], ["aaaaaaaa"])

    def test_notifications_of_one_class_share_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            judge = FakeJudge(self.JUDGED)
            self._select(judge, cache, situation=AGENT_DONE)
            out = self._select(judge, cache, situation=OTHER_AGENT_DONE, now=1100.0)
            self.assertEqual(sel.input_class(AGENT_DONE), sel.input_class(OTHER_AGENT_DONE))
            self.assertEqual(len(judge.subjects), len(RULES))
            self.assertEqual([r["id"] for r in out], ["aaaaaaaa"])

    def test_a_changed_signature_asks_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            judge = FakeJudge(self.JUDGED)
            self._select(judge, cache, situation=AGENT_DONE)
            self._select(judge, cache, situation=COMMAND_FAILED, now=1010.0)
            self._select(judge, cache, situation="a partner message", now=1020.0)
            self._select(judge, cache, situation="another partner message", now=1030.0)
            self.assertEqual(len(judge.subjects), 4 * len(RULES))
            # A re-taught rule is a different key; only it is asked again.
            changed = [dict(r) for r in RULES]
            changed[0]["statement"] = "the rule was re-taught"
            sel.select("a partner message", changed, floor=0.75, client=FakeClient,
                       judge=judge, workers=1, cache_path=cache, now=1040.0,
                       session_id="s1")
            self.assertEqual(len(judge.subjects), 4 * len(RULES) + 1)

    def test_sessions_do_not_share_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            judge = FakeJudge(self.JUDGED)
            self._select(judge, cache, situation=AGENT_DONE, session_id="s1")
            self._select(judge, cache, situation=AGENT_DONE, now=1001.0, session_id="s2")
            self.assertEqual(len(judge.subjects), 2 * len(RULES))

    def test_cache_expiry_asks_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            judge = FakeJudge(self.JUDGED)
            self._select(judge, cache)
            self._select(judge, cache, now=1000.0 + sel.CACHE_TTL_SECONDS - 1)
            self.assertEqual(len(judge.subjects), len(RULES))
            self._select(judge, cache, now=1000.0 + sel.CACHE_TTL_SECONDS + 1)
            self.assertEqual(len(judge.subjects), 2 * len(RULES))

    def test_a_binding_verdict_stays_delivered_for_the_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            self._select(FakeJudge(self.JUDGED), cache, situation=AGENT_DONE)
            # Even if a later notification would have judged it lower, the
            # bound verdict is reused: the cache errs toward delivering.
            later = FakeJudge({"binds here": 0.10})
            out = self._select(later, cache, situation=OTHER_AGENT_DONE, now=1100.0)
            self.assertEqual([r["id"] for r in out], ["aaaaaaaa"])
            self.assertEqual(later.subjects, [])

    def test_an_unwritable_cache_still_delivers(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "file")
            Path(blocker).write_text("not a directory", encoding="utf-8")
            cache = os.path.join(blocker, "c.json")
            judge = FakeJudge(self.JUDGED)
            first = self._select(judge, cache)
            second = self._select(judge, cache, now=1001.0)
            self.assertEqual([r["id"] for r in first], ["aaaaaaaa"])
            self.assertEqual(second, first)
            self.assertEqual(len(judge.subjects), 2 * len(RULES))

    def test_a_corrupt_cache_is_a_miss_not_a_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            Path(cache).write_text("{not json", encoding="utf-8")
            judge = FakeJudge(self.JUDGED)
            out = self._select(judge, cache)
            self.assertEqual([r["id"] for r in out], ["aaaaaaaa"])

    def test_a_failed_judgment_is_never_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            outage = FakeJudge(self.JUDGED, fail_on={"also binds"})
            first = self._select(outage, cache)
            self.assertIn(None, [r["probability"] for r in first])
            healthy = FakeJudge(self.JUDGED)
            second = self._select(healthy, cache, now=1001.0)
            self.assertEqual([s["rule"] for s in healthy.subjects], ["also binds"])
            self.assertNotIn(None, [r["probability"] for r in second])

    def test_an_injected_judge_never_touches_the_shared_cache(self):
        judge = FakeJudge(self.JUDGED)
        before = os.path.exists(sel.CACHE_PATH) and os.path.getmtime(sel.CACHE_PATH)
        sel.select("selftest-only moment", RULES, client=FakeClient, judge=judge,
                   workers=1)
        after = os.path.exists(sel.CACHE_PATH) and os.path.getmtime(sel.CACHE_PATH)
        self.assertEqual(before, after)

    def test_the_ranking_is_reused_for_a_class(self):
        roster = [{"id": f"r{i:07d}", "gist": f"rule {i}", "context": ""} for i in range(30)]
        calls = {"rank": 0}

        class RankingJudge(FakeJudge):
            def judge(self, subject, questions, **kwargs):
                if "rank" in questions:
                    calls["rank"] += 1
                    return {"answers": {"rank": {"probabilities": {
                        rule["id"]: 1.0 / (i + 1) for i, rule in enumerate(roster)}}},
                        "model": "fake"}
                return super().judge(subject, questions, **kwargs)

        class RankingClient(FakeClient):
            @staticmethod
            def choice(instructions, options):
                return {"type": "choice", "options": options}

        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            judge = RankingJudge()
            for step, text in enumerate((AGENT_DONE, OTHER_AGENT_DONE)):
                sel.select(text, roster, floor=0.75, client=RankingClient, judge=judge,
                           workers=1, cache_path=cache, now=1000.0 + step,
                           session_id="s1", shortlist=5)
            self.assertEqual(calls["rank"], 1)
            self.assertEqual(len(judge.subjects), 5)

    def test_advise_logs_the_cache_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            log = os.path.join(tmp, "live.jsonl")
            judge = FakeJudge(self.JUDGED)
            for _ in range(2):
                sel.advise("a moment", log_path=log, rules=RULES, client=FakeClient,
                           judge=judge, workers=1, cache_path=cache, session_id="s1")
            rows = [json.loads(line) for line in Path(log).read_text().splitlines()]
            self.assertEqual([r["cache_hit"] for r in rows], [False, True])
            self.assertEqual(rows[1]["cache"]["verdicts_asked"], 0)
            self.assertEqual(rows[0]["surfaced"], rows[1]["surfaced"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
