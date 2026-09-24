#!/usr/bin/env python3
"""Offline tests for the Jev client.

NOTHING HERE REACHES THE NETWORK. Every request is served by an injected
opener, so this suite runs on a GitHub runner with no credential, no allowlist
entry and no spend. A test that needed the live service would be a test CI
could not run.

The load-bearing case is test_module_is_a_library_and_must_stay_one. The client
is safe to import from anywhere precisely because it is not a script
entrypoint; the moment someone adds a shebang or a __main__ guard it joins the
sealed source inventory, moves the frontier and owes a forward-only registry
successor. That is a silent, expensive change, so it is asserted here rather
than left to a reviewer noticing.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import unittest
import urllib.error
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("typesafe_client.py")
SPEC = importlib.util.spec_from_file_location("typesafe_client", MODULE_PATH)
assert SPEC and SPEC.loader
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)


class FakeResponse(io.BytesIO):
    """Minimal stand-in for what urlopen hands back as a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def responder(payload, capture=None):
    def opener(request, timeout=None):
        if capture is not None:
            capture.append(request)
        return FakeResponse(json.dumps(payload).encode("utf-8"))
    return opener


ANSWER = {"model": "jev-1.13.0",
          "answers": {"q": {"type": "noul", "noul": 0.91}},
          "usage": {"input_tokens": 10, "output_tokens": 2}}


class LibraryShapeTests(unittest.TestCase):
    # These two patterns are transcribed from isScriptEntrypoint() in
    # ops/scac-mutation-inventory.mjs, which is the detector that actually
    # decides. A looser check here would be worse than none: a plain substring
    # search for the guard also matches the module docstring explaining why
    # there must not be one, so it fails on a correct file and teaches the next
    # reader to delete the test.
    MAIN_GUARD = re.compile(r"""if\s+__name__\s*==\s*["']__main__["']\s*:""")

    def test_module_is_a_library_and_must_stay_one(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(
            source.startswith("#!"),
            "typesafe_client.py gained a shebang, which makes it a registered "
            "script entrypoint in the sealed inventory and owes a registry "
            "successor. Keep it a library and call it from an existing entrypoint.")
        self.assertIsNone(
            self.MAIN_GUARD.search(source),
            "typesafe_client.py gained a __main__ guard, with the same "
            "consequence as a shebang: it becomes a sealed script entrypoint.")

    def test_the_guard_detector_would_catch_a_real_entrypoint(self):
        # Without this, the test above passes on any file that merely lacks the
        # construct, including one where the pattern was quietly broken.
        self.assertIsNotNone(self.MAIN_GUARD.search('if __name__ == "__main__":'))
        self.assertIsNotNone(self.MAIN_GUARD.search("if __name__ == '__main__' :"))
        self.assertIsNone(self.MAIN_GUARD.search("a docstring mentioning __main__"))


class CredentialTests(unittest.TestCase):
    def _write(self, text):
        path = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        target = path / "typesafe.env"
        target.write_text(text, encoding="utf-8")
        return str(target)

    def test_reads_the_value(self):
        path = self._write("# comment\nTYPESAFE_API_KEY=abc123\n")
        self.assertEqual(client.read_api_key(path), "abc123")

    def test_empty_value_is_refused_rather_than_returned(self):
        path = self._write("TYPESAFE_API_KEY=\n")
        with self.assertRaises(client.TypeSafeError):
            client.read_api_key(path)

    def test_missing_line_is_refused(self):
        path = self._write("SOMETHING_ELSE=x\n")
        with self.assertRaises(client.TypeSafeError):
            client.read_api_key(path)

    def test_missing_file_names_the_path(self):
        with self.assertRaises(client.TypeSafeError) as caught:
            client.read_api_key("/nonexistent/typesafe.env")
        self.assertIn("/nonexistent/typesafe.env", str(caught.exception))


class QuestionBuilderTests(unittest.TestCase):
    def test_noul_criteria_are_optional_and_omitted_when_absent(self):
        self.assertNotIn("criteria", client.noul("Is it urgent?"))
        built = client.noul("Is it urgent?", true="time-critical", false="not")
        self.assertEqual(built["criteria"], {"true": "time-critical", "false": "not"})
        self.assertEqual(built["type"], "noul")

    def test_choice_refuses_fewer_than_two_options(self):
        with self.assertRaises(client.TypeSafeError):
            client.choice("Which team?", {"only": "one"})

    def test_score_refuses_fewer_than_two_levels(self):
        with self.assertRaises(client.TypeSafeError):
            client.score("How bad?", ["single"])


class AskTests(unittest.TestCase):
    def test_every_question_travels_in_one_request(self):
        captured = []
        questions = {"a": client.noul("A?"), "b": client.noul("B?"),
                     "c": client.score("C?", ["low", "high"])}
        client.ask("state", questions, api_key="k",
                   opener=responder(ANSWER, captured))
        self.assertEqual(len(captured), 1, "batching is the whole point; one call per question is 12x the cost")
        sent = json.loads(captured[0].data)
        self.assertEqual(set(sent["questions"]), {"a", "b", "c"})
        self.assertEqual(captured[0].method, "POST")
        self.assertEqual(captured[0].full_url, client.ENDPOINT)

    def test_empty_question_map_is_refused_before_any_request(self):
        captured = []
        with self.assertRaises(client.TypeSafeError):
            client.ask("state", {}, api_key="k", opener=responder(ANSWER, captured))
        self.assertEqual(captured, [])

    def test_oversized_state_fails_locally_and_says_to_narrow_it(self):
        big = "x" * (client.STATE_BUDGET_CHARS + 10)
        captured = []
        with self.assertRaises(client.TypeSafeError) as caught:
            client.ask(big, {"q": client.noul("?")}, api_key="k",
                       opener=responder(ANSWER, captured))
        self.assertEqual(captured, [], "an oversized state must never reach the service")
        self.assertIn("Narrow it in code", str(caught.exception))

    def test_http_error_reports_status_and_body_but_never_the_key(self):
        secret = "sk-should-never-appear"

        def failing(request, timeout=None):
            raise urllib.error.HTTPError(
                client.ENDPOINT, 422, "Unprocessable", {},
                io.BytesIO(b"criteria malformed"))

        with self.assertRaises(client.TypeSafeError) as caught:
            client.ask("s", {"q": client.noul("?")}, api_key=secret, opener=failing)
        message = str(caught.exception)
        self.assertIn("422", message)
        self.assertIn("criteria malformed", message)
        self.assertNotIn(secret, message)

    def test_rate_limit_is_retried_honouring_retry_after(self):
        slept = []
        client.time.sleep = lambda seconds: slept.append(seconds)
        calls = {"n": 0}

        def flaky(request, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.HTTPError(
                    client.ENDPOINT, 429, "Too Many Requests",
                    {"retry-after": "7"}, io.BytesIO(b""))
            return FakeResponse(json.dumps(ANSWER).encode("utf-8"))

        result = client.ask("s", {"q": client.noul("?")}, api_key="k", opener=flaky)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(slept, [7.0], "the service's own retry-after must win over our backoff")
        self.assertEqual(result["model"], "jev-1.13.0")

    def test_rate_limit_gives_up_rather_than_retrying_forever(self):
        client.time.sleep = lambda seconds: None

        def always_limited(request, timeout=None):
            raise urllib.error.HTTPError(
                client.ENDPOINT, 429, "Too Many Requests", {}, io.BytesIO(b""))

        with self.assertRaises(client.TypeSafeError):
            client.ask("s", {"q": client.noul("?")}, api_key="k",
                       opener=always_limited, retries=2)


class DecideTests(unittest.TestCase):
    def test_noul_bands_split_yes_no_and_escalate(self):
        high = client.decide({"type": "noul", "noul": 0.95})
        low = client.decide({"type": "noul", "noul": 0.02})
        middle = client.decide({"type": "noul", "noul": 0.5})
        self.assertEqual((high["outcome"], high["escalate"]), ("yes", False))
        self.assertEqual((low["outcome"], low["escalate"]), ("no", False))
        self.assertTrue(middle["escalate"],
                        "a noul near 0.5 means yes and no are equally likely, which is "
                        "exactly the case a person should see")

    def test_band_edges_are_inclusive_so_a_threshold_hit_acts(self):
        self.assertFalse(client.decide({"type": "noul", "noul": 0.8}, yes_at=0.8)["escalate"])
        self.assertFalse(client.decide({"type": "noul", "noul": 0.2}, no_at=0.2)["escalate"])

    def test_low_confidence_choice_escalates_and_still_reports_its_value(self):
        answer = {"type": "choice", "choice": "billing", "confidence": 0.3}
        decided = client.decide(answer, min_confidence=0.6)
        self.assertTrue(decided["escalate"])
        self.assertEqual(decided["value"], "billing")
        self.assertEqual(decided["confidence"], 0.3)

    def test_confident_score_is_acted_on(self):
        decided = client.decide({"type": "score", "score": 1.4, "confidence": 0.9})
        self.assertFalse(decided["escalate"])
        self.assertEqual(decided["value"], 1.4)

    def test_missing_confidence_escalates_rather_than_defaulting_to_act(self):
        decided = client.decide({"type": "choice", "choice": "x"})
        self.assertTrue(decided["escalate"],
                        "absent confidence must fail toward a person, never toward acting")

    def test_unknown_answer_type_is_refused(self):
        with self.assertRaises(client.TypeSafeError):
            client.decide({"type": "something-new", "value": 1})


if __name__ == "__main__":
    unittest.main()
