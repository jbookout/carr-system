#!/usr/bin/env python3
"""Offline tests for the Jev client.

NOTHING HERE REACHES THE NETWORK. Every request is served by a fake
Worker, so this suite runs on a GitHub runner with no credential, no allowlist
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
import hashlib
import io
import json
import os
import queue
import re
import subprocess
import time
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("typesafe_client.py")
SPEC = importlib.util.spec_from_file_location("typesafe_client", MODULE_PATH)
assert SPEC and SPEC.loader
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)
# The call-site registry, attribution and fixture refusal are exercised by
# ops/jev-call-sites-selftest.py. This suite tests the transport beneath them,
# so every fixture caller is admitted as a budget-free scheduled job. Bound on
# this suite's private module copy to keep independent tests isolated.
_PERMISSIVE_SITE = {"caller": "*", "trigger": "selftest", "runs_in": "selftest",
                    "attribution": "session_or_job", "unattended": "allowed",
                    "hourly_budget": 10**9, "daily_budget": 10**9, "owner": "selftest",
                    "value": "selftest", "sources": ["ops/typesafe_client.py"]}
setattr(client, "load_call_sites", lambda path=None: {"hourly_paid_call_cap": 10**9,
                                                     "sites": {"*": _PERMISSIVE_SITE}})
setattr(client, "call_site", lambda caller, registry: _PERMISSIVE_SITE)
for _name in ("CARR_JEV_OFFLINE", "CARR_HOOK_FIXTURE", "CARR_JEV_WORKER"):
    os.environ.pop(_name, None)
os.environ["CARR_JEV_JOB"] = "typesafe-client-selftest"
_CAP_ROOT = tempfile.TemporaryDirectory()


def setUpModule():
    # Refusal observations are canonical in production; fixtures use only
    # temporary storage and fake Worker/vendor transports.
    unittest.addModuleCleanup(_CAP_ROOT.cleanup)
    patcher = patch.object(client, "JEV_DAILY_CAP_LOG", os.path.join(_CAP_ROOT.name, "calls.jsonl"))
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    real_run = subprocess.run
    def guard_run(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and any(str(a).endswith('local-verb.mjs') for a in argv):
            raise AssertionError('fixture reached real local-verb')
        return real_run(argv, *args, **kwargs)
    guard = patch.object(subprocess, 'run', guard_run)
    guard.start()
    unittest.addModuleCleanup(guard.stop)


_REAL_URLOPEN = urllib.request.urlopen

def _urlopen_worker(state, questions, **options):
    send = urllib.request.urlopen
    if send is _REAL_URLOPEN:
        raise AssertionError("selftest attempted an uninjected transport")
    request = urllib.request.Request("https://fixture.invalid", data=json.dumps({
        "state": state, "questions": questions, "model": options["model"]}).encode())
    with send(request, timeout=options["timeout"]) as response:
        answer = json.load(response)
    return {**answer, "server_receipt": {"receipt_id": "offline-worker"}}, None

_REAL_SERVER_ASK = client.server_ask

def offline_worker(opener):
    def run(argv, **options):
        args = json.loads(argv[3])
        if args.get('transport_mode') == 'cache_only':
            return subprocess.CompletedProcess(argv, 1, '', 'TOOL ERROR '+json.dumps(
                {'error':'jev_cache_miss','spend_authority':client.SPEND_AUTHORITY}))
        request = urllib.request.Request('https://'+'fixture.invalid', method='POST',
            data=json.dumps({'state':args['state']['input'], 'questions':args['questions'], 'model':args['model']}).encode())
        with opener(request, timeout=options['timeout']) as response:
            result = json.load(response)
        if response.status != 200:
            return subprocess.CompletedProcess(argv, 1, '', 'TOOL ERROR {"error":"jev_upstream_failed"}')
        return subprocess.CompletedProcess(argv, 0, json.dumps({**result,'ok':True,'receipt_id':'offline-worker'}), '')
    return run

def fake_worker(state, questions, **options):
    if options.get('runner'):
        return _REAL_SERVER_ASK(state, questions, **options)
    return _urlopen_worker(state, questions, **options)

setattr(client, "server_ask", fake_worker)

class FakeResponse(io.BytesIO):
    """Minimal stand-in for what urlopen hands back as a context manager."""
    status = 200

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


def vendor_refusal(status):
    def opener(request, timeout=None):
        raise urllib.error.HTTPError("https://fixture.invalid", status, "refused",
                                     {}, io.BytesIO(b'{"error":"fixture"}'))
    return opener


def worker_vendor_failure(status, reason):
    payload = json.dumps({"error": "jev_upstream_failed", "status": status, "reason": reason},
                         indent=2)
    return f"local-verb identity -> fixture\nTOOL ERROR {payload}\n"


class UnusableResponseTests(unittest.TestCase):
    """Each failure class behind 2026-09-28..10-04's 30% unusable rate.

    Replayed from out/jev-calls.jsonl: 16,927 non-cached calls came back HTTP
    402 (credits exhausted) in two windows of 22h and 18h. The client logged
    each as schema_valid=false and kept sending. The 1,405
    vendor_failed_at_worker rows were the Worker's single paid_once vendor
    attempt failing (the vendor was throttling: 429), each then paid again
    direct. Zero rows ever had HTTP 200 with an answer that failed validation.
    """
    def setUp(self):
        self.requests = []
        self.worker_calls = []
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.log = root / "calls.jsonl"
        self.options = {"server_runner": offline_worker(responder(ANSWER, self.requests))}

    def ask(self, text):
        return client.ask(text, {"q": client.noul("Fixture")}, **self.options)


    # -- Class 1: HTTP 402, logged as a schema failure and never stopped ----


    def test_builders_copy_criteria_before_returning(self):
        options = {"a": "A", "b": "B"}
        levels = ["a", "b"]
        choice = client.choice("Fixture", options)
        score = client.score("Fixture", levels)
        options.clear()
        levels.clear()
        self.assertEqual(len(choice["criteria"]), 2)
        self.assertEqual(len(score["criteria"]), 2)

    def test_request_contract_matches_worker_and_builders(self):
        cases = [
            ({"type": "choice", "instructions": "Fixture", "criteria": {"a": "A"}}, False),
            ({"type": "choice", "instructions": "Fixture"}, False),
            ({"type": "score", "instructions": "Fixture", "criteria": ["a"]}, False),
            ({"type": "score", "instructions": "Fixture"}, False),
            ({"type": "noul", "instructions": "Fixture", "criteria": []}, False),
            ({"type": "choice", "instructions": "Fixture", "criteria": {"a": "A", "b": "B"}}, True),
            ({"type": "score", "instructions": "Fixture", "criteria": ["a", "b"]}, True),
            ({"type": "noul", "instructions": "Fixture"}, True),
            ({"type": "noul", "instructions": " "}, False),
            ({"type": "unknown", "instructions": "Fixture"}, False),
            ({"type": ["noul"], "instructions": "Fixture"}, False),
        ]
        requests = [{"state": "fixture", "questions": {"q": question},
                     "purpose": "call", "session_id": "fixture"} for question, _ in cases]
        script = """import {validateAskJevArgs} from './mcp-server/src/jev-call-receipt.js';
          let data=''; for await (const chunk of process.stdin) data+=chunk;
          console.log(JSON.stringify(JSON.parse(data).map(args=>{
            try {validateAskJevArgs(args); return true;} catch {return false;}
          })));"""
        result = subprocess.run(["node", "--input-type=module", "-e", script],
                                input=json.dumps(requests), text=True, capture_output=True,
                                cwd=MODULE_PATH.parent.parent, check=True)
        expected = [valid for _, valid in cases]
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual([client.malformed_request("fixture", {"q": q}) is None
                          for q, _ in cases], expected)
        for builder, criteria in [(client.choice, {"a": "A"}), (client.score, ["a"])]:
            with self.assertRaises(client.TypeSafeError):
                builder("Fixture", criteria)

    def test_malformed_question_diagnostics_never_echo_caller_fields(self):
        sentinel = "CONFIDENTIAL_fixture_sentinel"
        for question in [{"type": "noul", "instructions": ""},
                         {"type": sentinel, "instructions": "Fixture"}]:
            with self.assertRaises(client.TypeSafeError) as caught:
                client.ask("fixture", {sentinel: question}, **self.options)
            self.assertNotIn(sentinel, str(caught.exception))
        # Judge logs persist the same exception text; exercise that sink too.
        spec = importlib.util.spec_from_file_location("jev_judge", MODULE_PATH.with_name("jev_judge.py"))
        judge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(judge)
        with self.assertRaises(judge.JudgeUnavailable) as caught:
            judge.judge("fixture", {sentinel: {"type": "noul", "instructions": ""}},
                        client=client, api_key="offline-fixture")
        log = self.log.with_name("judge-errors.jsonl")
        judge.record("fixture", "fixture", None, error=str(caught.exception), log_path=log)
        self.assertNotIn(sentinel, log.read_text())

    # -- Class 2: vendor_failed_at_worker, then a second paid direct attempt -


    def test_worker_error_detail_is_parsed_from_local_verb_stderr(self):
        self.assertEqual(client._worker_upstream(worker_vendor_failure(429, "http_status")),
                         (429, "http_status"))
        self.assertEqual(client._worker_upstream(worker_vendor_failure(None, "network")),
                         (None, "network"))
        self.assertEqual(client._worker_upstream("could not reach the deployed Worker"),
                         (None, None))
        self.assertEqual(client._worker_upstream('TOOL ERROR {"error":"jev_upstream_failed",'
                                                 '"status":"500","reason":"http_status"}'),
                         (None, "http_status"), "a non-integer status is not trusted")

    # -- Class 3: malformed requests were sent and paid for -----------------

    def test_malformed_requests_are_refused_before_any_transport(self):
        cases = {
            "unknown type": {"q": {"type": "rank", "instructions": "x"}},
            "empty instructions": {"q": {"type": "noul", "instructions": "  "}},
            "one-option choice": {"q": {"type": "choice", "instructions": "x", "criteria": {"a": "A"}}},
            "choice without options": {"q": {"type": "choice", "instructions": "x"}},
            "one-level score": {"q": {"type": "score", "instructions": "x", "criteria": ["low"]}},
            "noul criteria not a map": {"q": {"type": "noul", "instructions": "x", "criteria": ["yes"]}},
            "too many questions": {f"q{i}": client.noul("x") for i in range(65)},
            "question not a map": {"q": "is it?"},
        }
        for name, questions in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(client.TypeSafeError, "malformed"):
                    client.ask("state", questions, **self.options)
        with self.assertRaisesRegex(client.TypeSafeError, "malformed"):
            client.ask(["not", "a", "string", "or", "map"], {"q": client.noul("x")}, **self.options)
        self.assertEqual(self.worker_calls, [])
        self.assertEqual(self.requests, [])
        self.assertFalse(self.log.exists())

    # -- The validator is not the cause, and stays strict -------------------

    def test_every_valid_answer_kind_validates(self):
        questions = {"n": client.noul("x", true="yes", false="no"),
                     "c": client.choice("x", {"a": "A", "b": "B"}),
                     "s": client.score("x", ["low", "mid", "high"])}
        answer = {"model": "jev-1.13.0", "usage": {"input_tokens": 9, "output_tokens": 3},
                  "answers": {"n": {"type": "noul", "noul": 0.0},
                              "c": {"type": "choice", "choice": "b", "confidence": 1},
                              "s": {"type": "score", "score": 2, "confidence": 0.4}}}
        for keys in (("n",), ("c",), ("s",), ("n", "c"), ("n", "c", "s")):
            with self.subTest(keys=keys):
                subset = {k: questions[k] for k in keys}
                self.assertTrue(client.usable_judgment(
                    {**answer, "answers": {k: answer["answers"][k] for k in keys}}, subset))

    def test_real_bad_answers_still_fail_validation(self):
        questions = {"c": client.choice("x", {"a": "A", "b": "B"})}
        base = {"model": "jev-1.13.0", "usage": {"input_tokens": 1, "output_tokens": 1}}
        for bad in ({"type": "choice", "choice": "z", "confidence": 0.9},
                    {"type": "choice", "choice": "a"},
                    {"type": "noul", "noul": 0.5}):
            with self.subTest(bad=bad):
                self.assertFalse(client.usable_judgment({**base, "answers": {"c": bad}}, questions))

    # -- Replay of the logged request shapes --------------------------------


SPEND_SPEC = importlib.util.spec_from_file_location(
    "jev_spend_health", MODULE_PATH.with_name("jev_spend_health.py"))


class SpendHealthTests(unittest.TestCase):
    def test_canonical_health_invokes_spend_row(self):
        source = (MODULE_PATH.parent.parent / "tools" / "health-check.py").read_text()
        self.assertIn("worker_usage=jev_spend_health.read_worker_usage", source)

    def test_loaded_nightly_chain_runs_the_spend_alarm_and_preflights_its_sources(self):
        nightly = (MODULE_PATH.parent.parent / "bin" / "nightly.sh").read_text()
        self.assertTrue('step "Jev daily spend alarm"' in nightly
                        and './.venv/bin/python tools/health-check.py --section jev-spend' in nightly,
                        "nightly chain does not invoke the narrow Jev spend alarm")
        preflight = nightly.split('if [ "${1:-}" = "--preflight" ]; then', 1)[1].split('missing=0', 1)[0]
        self.assertIn("tools/health-check.py", preflight)
        self.assertIn("ops/jev_spend_health.py", preflight)

    def test_nightly_alarm_has_a_narrow_exit_status(self):
        health = (MODULE_PATH.parent.parent / "tools" / "health-check.py").read_text()
        self.assertIn('"jev-spend"', health)
        self.assertIn('if CANONICAL_SECTION == "jev-spend":', health)
        self.assertIn('sys.exit(_spend_module.nightly_exit_status(_spend_line))', health)

    def test_nightly_alarm_fails_only_when_reader_or_loop_action_fails(self):
        spend = importlib.util.module_from_spec(SPEND_SPEC)
        SPEND_SPEC.loader.exec_module(spend)
        self.assertEqual(spend.nightly_exit_status("OK jev spend — $0.000"), 0)
        self.assertEqual(spend.nightly_exit_status("WARN jev spend — $0.600"), 0)
        self.assertEqual(spend.nightly_exit_status("UNKNOWN jev spend — missing usage"), 0)
        for line in ("UNKNOWN jev spend — Worker usage unavailable",
                     "UNAVAILABLE jev spend — Worker unreachable",
                     "WARN jev spend — $0.600 · loop action FAILED (RuntimeError)"):
            self.assertEqual(spend.nightly_exit_status(line), 1)

    def test_unsettled_worker_attempt_is_informational_for_nightly(self):
        spend = importlib.util.module_from_spec(SPEND_SPEC)
        SPEND_SPEC.loader.exec_module(spend)
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "absent.jsonl"
            state = Path(d) / "loop.json"
            now = __import__("datetime").datetime(2026, 9, 28,
                tzinfo=__import__("datetime").timezone.utc)
            line = spend.check_spend(log, MODULE_PATH.parent / "config" / "jev-cost-guard.v1.json",
                state, lambda *_: None, now=now,
                worker_usage=lambda _day: {"calls": 0, "input_tokens": 0,
                                            "unknown": 1, "pending_attempts": 1})
            self.assertIn("1 call or attempt missing usage", line)
            self.assertEqual(spend.nightly_exit_status(line), 0)

    def test_abandoned_attempts_are_reported_separately(self):
        spend = importlib.util.module_from_spec(SPEND_SPEC)
        SPEND_SPEC.loader.exec_module(spend)
        with tempfile.TemporaryDirectory() as d:
            line = spend.check_spend(Path(d) / "absent", MODULE_PATH.parent / "config" / "jev-cost-guard.v1.json",
                Path(d) / "loop", lambda *_: None,
                worker_usage=lambda _day: {"calls": 0, "input_tokens": 0, "unknown": 0,
                    "pending_attempts": 1, "abandoned_attempts": 2, "abandon_after_seconds": 3600})
            self.assertIn("2 abandoned attempts", line)
            self.assertIn("older than 3600s", line)
            self.assertIn("owner orchestrator", line)
            self.assertEqual(spend.nightly_exit_status(line), 0)

    def test_unreadable_local_log_fails_with_named_response(self):
        spend = importlib.util.module_from_spec(SPEND_SPEC)
        SPEND_SPEC.loader.exec_module(spend)
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "calls.jsonl"
            log.write_bytes(b"\xff")
            line = spend.check_spend(log, MODULE_PATH.parent / "config" / "jev-cost-guard.v1.json", Path(d) / "loop")
            self.assertIn("UNAVAILABLE", line)
            self.assertIn("owner orchestrator", line)
            self.assertEqual(spend.nightly_exit_status(line), 1)

    def test_narrow_health_cli_exits_before_unrelated_checks(self):
        source_tools = MODULE_PATH.parent.parent / "tools"
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "tools").mkdir()
            (root / "ops").mkdir()
            (root / "tools" / "health-check.py").write_text(
                (source_tools / "health-check.py").read_text())
            (root / "ops" / "jev_spend_health.py").write_text(
                "import os\n"
                "FACTORY_USAGE_LOG = 'factory-log'\n"
                "def read_worker_usage(day): raise AssertionError('not called')\n"
                "def check_spend(*, extra_logs, worker_usage):\n"
                "    assert extra_logs == [FACTORY_USAGE_LOG]\n"
                "    assert worker_usage is read_worker_usage\n"
                "    return os.environ['TEST_SPEND_ROW']\n"
                "def nightly_exit_status(line): return 0 if line.startswith('OK ') else 1\n")
            for line, expected in (("OK jev spend — $0.000", 0),
                                   ("UNKNOWN jev spend — missing usage", 1)):
                result = subprocess.run(
                    [os.sys.executable, str(root / "tools" / "health-check.py"),
                     "--section", "jev-spend"],
                    cwd=root, capture_output=True, text=True,
                    env={**os.environ, "PYTHONPATH": str(source_tools),
                         "TEST_SPEND_ROW": line}, timeout=10)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(result.stdout.strip(), line)
            (root / "ops" / "jev_spend_health.py").unlink()
            missing = subprocess.run(
                [os.sys.executable, str(root / "tools" / "health-check.py"),
                 "--section", "jev-spend"],
                cwd=root, capture_output=True, text=True,
                env={**os.environ, "PYTHONPATH": str(source_tools)}, timeout=10)
            self.assertEqual(missing.returncode, 1)
            for required in ("owner orchestrator", "restore the receipt reader",
                             "verify the next run", "auto-clear after three healthy runs"):
                self.assertIn(required, missing.stdout)

    def test_daily_spend_warns_and_dedups_one_loop_then_auto_clears(self):
        self.assertTrue(SPEND_SPEC and SPEND_SPEC.loader)
        spend = importlib.util.module_from_spec(SPEND_SPEC)
        SPEND_SPEC.loader.exec_module(spend)
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "calls.jsonl"
            state = Path(d) / "loop.json"
            config = MODULE_PATH.parent / "config" / "jev-cost-guard.v1.json"
            events = []

            def verb(name, payload):
                events.append((name, payload))
                if name == "read-loop":
                    return {"loop_id": payload["loop_id"], "version": 1}
                return {"ok": True, "loop_id": "loop-1", "number": "901"}

            def row(ts, tokens, **other):
                return json.dumps({"ts": ts, "ok": True, "usage": {"input_tokens": tokens,
                                   "output_tokens": 5}, **other}) + "\n"

            log.write_text(row("2026-09-28T02:00:00Z", 12_000_000) +
                           row("2026-09-27T02:00:00Z", 90_000_000) +
                           row("2026-09-28T03:00:00Z", 10_000_000, cache_hit=True))
            day = __import__("datetime").datetime(2026, 9, 28, 4, tzinfo=__import__("datetime").timezone.utc)
            first = spend.check_spend(log, config, state, verb, now=day)
            self.assertIn("WARN", first)
            self.assertIn("$0.50", first)
            self.assertIn("owner orchestrator", first)
            self.assertIn("find caller in jev usage log", first)
            self.assertIn("auto-clear", first)
            self.assertEqual([x[0] for x in events], ["add-loop"])
            self.assertEqual(events[0][1]["owner"], "claude")
            spend.check_spend(log, config, state, verb, now=day)
            self.assertEqual(len(events), 1)
            log.write_text(log.read_text() + row("2026-09-28T04:00:00Z", 5_000_000))
            spend.check_spend(log, config, state, verb, now=day)
            self.assertEqual([x[0] for x in events], ["add-loop", "read-loop", "update-loop"])
            self.assertEqual(events[-1][1]["base_version"], 1)
            next_day = day.replace(day=29)
            cleared = spend.check_spend(log, config, state, verb, now=next_day)
            self.assertIn("OK", cleared)
            self.assertEqual([x[0] for x in events], ["add-loop", "read-loop", "update-loop",
                                                  "read-loop", "close-loop"])
            self.assertEqual(events[-1][1]["base_version"], 1)

    def test_success_without_usage_is_unknown_and_preserves_spend_warning(self):
        self.assertTrue(SPEND_SPEC and SPEND_SPEC.loader)
        spend = importlib.util.module_from_spec(SPEND_SPEC)
        SPEND_SPEC.loader.exec_module(spend)
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "calls.jsonl"
            state = Path(d) / "loop.json"
            config = MODULE_PATH.parent / "config" / "jev-cost-guard.v1.json"
            events = []

            def verb(name, payload):
                events.append(name)
                return {"ok": True, "loop_id": "warning-1"}

            day = __import__("datetime").datetime(2026, 9, 28, tzinfo=__import__("datetime").timezone.utc)
            log.write_text(json.dumps({"ts": "2026-09-28T01:00:00Z", "ok": True,
                                       "usage": {"input_tokens": 12_000_000}}) + "\n")
            self.assertIn("WARN", spend.check_spend(log, config, state, verb, now=day))
            self.assertEqual(events, ["add-loop"])
            log.write_text(log.read_text() + json.dumps({
                "ts": "2026-09-29T01:00:00Z", "ok": True, "usage": None,
            }) + "\n")
            unknown = spend.check_spend(log, config, state, verb,
                                        now=day.replace(day=29))
            self.assertIn("UNKNOWN", unknown)
            self.assertIn("missing usage", unknown)
            self.assertNotIn("OK", unknown)
            self.assertIn("$0.000 lower bound", unknown)
            self.assertEqual(events, ["add-loop"])
            self.assertEqual(json.loads(state.read_text())["loop_id"], "warning-1")

    def test_daily_alarm_adds_factory_and_worker_receipts_once(self):
        spend = importlib.util.module_from_spec(SPEND_SPEC)
        SPEND_SPEC.loader.exec_module(spend)
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "local.jsonl"
            factory = Path(d) / "factory.jsonl"
            state = Path(d) / "loop.json"
            events = []
            day = __import__("datetime").datetime(2026, 9, 28, tzinfo=__import__("datetime").timezone.utc)
            local.write_text(json.dumps({"ts": "2026-09-28T02:00:00Z", "ok": True,
                "usage": {"input_tokens": 4_000_000}}) + "\n" +
                json.dumps({"ts": "2026-09-28T02:01:00Z", "ok": False,
                "http_status": 200, "schema_valid": False,
                "usage": {"input_tokens": 1_000_000}}) + "\n")
            factory.write_text(json.dumps({"ts": "2026-09-28T03:00:00Z", "ok": True,
                "usage": {"input_tokens": 2_000_000}}) + "\n" +
                json.dumps({"ts": "2026-09-28T03:01:00Z", "ok": False,
                "cache_hit": True, "usage": None}) + "\n")
            def verb(name, payload):
                events.append(name)
                return {"ok": True, "loop_id": "spend-warning"}
            result = spend.check_spend(local, MODULE_PATH.parent / "config" / "jev-cost-guard.v1.json",
                state, verb, now=day, extra_logs=[factory],
                worker_usage=lambda _day: {"calls": 1, "input_tokens": 6_000_000, "unknown": 0})
            self.assertIn("WARN", result)
            self.assertIn("$0.546", result)
            self.assertIn("4 calls", result)
            self.assertEqual(events, ["add-loop"])

    def test_daily_alarm_works_before_local_log_exists(self):
        spend = importlib.util.module_from_spec(SPEND_SPEC)
        SPEND_SPEC.loader.exec_module(spend)
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "absent.jsonl"
            factory = Path(d) / "factory.jsonl"
            state = Path(d) / "loop.json"
            day = __import__("datetime").datetime(2026, 9, 28, tzinfo=__import__("datetime").timezone.utc)
            factory.write_text(json.dumps({"ts": "2026-09-28T03:00:00Z", "ok": True,
                "usage": {"input_tokens": 7_000_000}}) + "\n")
            events = []
            def verb(name, payload):
                events.append(name)
                return {"ok": True, "loop_id": "spend-warning"}
            result = spend.check_spend(local, MODULE_PATH.parent / "config" / "jev-cost-guard.v1.json",
                state, verb, now=day, extra_logs=[factory],
                worker_usage=lambda _day: {"calls": 1, "input_tokens": 6_000_000, "unknown": 0})
            self.assertIn("WARN", result)
            self.assertIn("$0.546", result)
            self.assertEqual(events, ["add-loop"])


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


class DispatchOwnershipTests(unittest.TestCase):
    def test_tests_never_reserve_against_the_canonical_daily_cap(self):
        canonical = os.path.join(client.CANONICAL_REPO, "out")
        self.assertNotEqual(os.path.commonpath([os.path.abspath(client.JEV_DAILY_CAP_LOG), canonical]),
                            canonical, "a selftest ask would spend the real daily Jev cap")

    def test_runtime_router_preserves_explicit_transcript_owner(self):
        session = 'dispatch-runtime'
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / 'transcript.jsonl'
            transcript.write_text(json.dumps({'type': 'user',
                'timestamp': '2026-09-29T12:00:00Z',
                'message': {'role': 'user', 'content': 'judge this'}}) + '\n')
            log = Path(directory) / 'calls.jsonl'
            with patch.dict(os.environ, {'CODEX_THREAD_ID': session}), patch.object(
                    urllib.request, 'urlopen', responder(ANSWER)):
                result = client.ask('state', {'q': client.noul('judge')},
                     cache_ttl_seconds=0, calls_log=str(log),
                    work_class='app_runtime', transcript_path=str(transcript))
            row = json.loads(log.read_text())
            self.assertEqual(result['answers'], ANSWER['answers'])
            self.assertEqual(row['session'], session)
            self.assertTrue(row['human_turn_id'].startswith('human-turn:v1:'))
            self.assertTrue(row['ok'])

    def test_standalone_ask_captures_owner_outside_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "transcript.jsonl"
            session = "dispatch-standalone"
            records = [{"type": "user", "timestamp": "2026-09-29T12:00:00Z",
                        "message": {"role": "user", "content": "design the seam"}}]
            transcript.write_text(json.dumps(records[0]) + "\n")
            log = Path(directory) / "calls.jsonl"
            code = r"""
import importlib.util, io, json, sys, urllib.request
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('standalone_client', sys.argv[1])
client = importlib.util.module_from_spec(spec); spec.loader.exec_module(client)
client.JEV_DAILY_CAP_LOG = sys.argv[2]
def worker(state, questions, **options):
    return {'model':'jev-test','answers':{'architecture_or_design':{'type':'noul','noul':0.9}},'usage':{'input_tokens':20,'output_tokens':6}}, None
client.server_ask = worker
class Response(io.StringIO):
    status = 200
    def __init__(self):
        super().__init__(json.dumps({'model':'jev-test',
            'answers':{'architecture_or_design':{'type':'noul','noul':0.9}},
            'usage':{'input_tokens':20,'output_tokens':6}}))
with patch.object(client.subprocess, 'run', side_effect=AssertionError('fixture reached real local-verb')), patch.object(urllib.request, 'urlopen', lambda *a, **k: Response()):
    client.ask('state', {'architecture_or_design':client.noul('judge design')},
               cache_ttl_seconds=0, calls_log=sys.argv[2],
               caller='adhoc:standalone-owner-fixture')
"""
            result = subprocess.run([__import__('sys').executable, '-c', code,
                                     str(MODULE_PATH.resolve()), str(log)],
                cwd=directory, env={**os.environ, "CODEX_THREAD_ID": session,
                                    "CARR_JEV_TRANSCRIPT_PATH": str(transcript)},
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(log.read_text())
            self.assertEqual(row['session'], session)
            self.assertIsInstance(row['human_turn_id'], str)
            self.assertTrue(row['human_turn_id'].startswith('human-turn:v1:'))

    def test_ask_discovers_exact_native_session_and_keeps_unknown_owner_unbound(self):
        session = 'dispatch-discovery'
        for runtime in ('codex', 'claude'):
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                if runtime == 'codex':
                    transcript = home / '.codex/sessions/2026/09/29' / f'rollout-2026-09-29T12-00-00-{session}.jsonl'
                    header = {'type':'session_meta', 'payload':{'id':session}}
                    human = {'type':'response_item', 'timestamp':'2026-09-29T12:00:00Z',
                        'payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'design this'}]}}
                else:
                    transcript = home / '.claude/projects/project' / f'{session}.jsonl'
                    header = {'type':'system','sessionId':session}
                    human = {'type':'user','sessionId':session,'timestamp':'2026-09-29T12:00:00Z',
                        'message':{'role':'user','content':'design this'}}
                transcript.parent.mkdir(parents=True)
                transcript.write_text(json.dumps(header)+'\n'+json.dumps(human)+'\n')
                env = {k:v for k,v in os.environ.items() if k not in (
                    'CODEX_THREAD_ID', 'CODEX_HOME', 'CLAUDE_CODE_SESSION_ID',
                    'CLAUDE_CODE_HOST_SESSION_ID', 'CARR_JEV_TRANSCRIPT_PATH')}
                env.update({'CODEX_THREAD_ID':session} if runtime=='codex' else {'CLAUDE_CODE_SESSION_ID':session})
                # A native Codex caller must not inherit the outer Claude owner.
                if runtime=='codex':
                    env['CLAUDE_CODE_SESSION_ID']='parent-claude'
                log = home/'calls.jsonl'
                with patch.dict(os.environ, env, clear=True), patch.object(
                        client.os.path, 'expanduser', lambda path: path.replace('~/', directory+'/', 1)), patch.object(
                        urllib.request, 'urlopen', responder(ANSWER)):
                    client.ask('state', {'q':client.noul('judge')},
                               cache_ttl_seconds=0, calls_log=str(log))
                    transcript.write_text(json.dumps(header)+'\n')
                    client.ask('state', {'q':client.noul('judge')},
                               cache_ttl_seconds=0, calls_log=str(log))
                rows = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(rows[0]['session'], session)
                self.assertIsInstance(rows[0]['human_turn_id'], str)
                self.assertIsNone(rows[1]['human_turn_id'])
                self.assertTrue(all(row['ok'] for row in rows))


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
        answer = {**ANSWER, "answers": {
            "a": {"type": "noul", "noul": 0.91},
            "b": {"type": "noul", "noul": 0.13},
            "c": {"type": "score", "score": 0.7, "confidence": 0.8}}}
        client.ask("state", questions,
                   server_runner=offline_worker(responder(answer, captured)))
        self.assertEqual(len(captured), 1, "batching is the whole point; one call per question is 12x the cost")
        sent = json.loads(captured[0].data)
        self.assertEqual(set(sent["questions"]), {"a", "b", "c"})
        self.assertEqual(captured[0].method, "POST")
        self.assertEqual(captured[0].full_url, 'https://fixture.invalid')

    def test_empty_question_map_is_refused_before_any_request(self):
        captured = []
        with self.assertRaises(client.TypeSafeError):
            client.ask("state", {},  server_runner=offline_worker(responder(ANSWER, captured)))
        self.assertEqual(captured, [])

    def test_oversized_state_fails_locally_and_says_to_narrow_it(self):
        big = "x" * (client.STATE_BUDGET_CHARS + 10)
        captured = []
        with self.assertRaises(client.TypeSafeError) as caught:
            client.ask(big, {"q": client.noul("?")},
                       server_runner=offline_worker(responder(ANSWER, captured)))
        self.assertEqual(captured, [], "an oversized state must never reach the service")
        self.assertIn("Narrow it in code", str(caught.exception))



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


class CallReceiptTests(unittest.TestCase):
    """Round-2 hardening (2026-09-24, PR #1224 second review): a receipt must
    only be written for a REAL production call, must be append-only, and
    must carry the response's usage when present, and (round 3) must carry
    no response id the vendor never supplies."""

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(client, "JEV_DAILY_CAP_LOG", str(root / "quota.jsonl")))

    def test_only_schema_valid_answer_with_usage_is_usable(self):
        questions = {"q": client.noul("?")}
        self.assertTrue(client.usable_judgment(ANSWER, questions))
        for bad in ({}, {**ANSWER, "answers": {}},
                    {**ANSWER, "answers": {"q": {"type": "noul", "noul": 2}}},
                    {**ANSWER, "usage": {}}, {**ANSWER, "usage": None}):
            with self.subTest(bad=bad):
                self.assertFalse(client.usable_judgment(bad, questions))
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "calls.jsonl")
            client._append_call_receipt(questions, [], {"model": "jev", "usage": None}, log)
            row = json.loads(Path(log).read_text())
            self.assertFalse(row["ok"])
            self.assertFalse(row["usable"])


    def test_new_receipts_hash_question_ids_and_preserve_facet_credit(self):
        name = "private_semantic_creation_question"
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "calls.jsonl"
            answer = {**ANSWER, "answers": {
                name: {"type": "noul", "noul": 0.91}}}
            with patch.object(urllib.request, "urlopen", responder(answer)):
                client.ask("state", {name: client.noul("is this relevant?")},
                            calls_log=str(log))
            raw = log.read_text()
            row = json.loads(raw.splitlines()[0])
        self.assertNotIn(name, raw)
        self.assertNotIn("question_ids", row)
        self.assertEqual(row["question_ids_sha256"], [hashlib.sha256(name.encode()).hexdigest()])
        reader_path = MODULE_PATH.parent.parent / "lib" / "jev_required_actions.py"
        spec = importlib.util.spec_from_file_location("jev_required_actions", reader_path)
        assert spec and spec.loader
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
        self.assertTrue(reader._call_covers_facet(row, "semantic_creation"))

    def test_real_call_receipt_includes_usage_and_no_response_id(self):
        # This exercises _append_call_receipt directly with a real-shaped
        # response (the function ask() calls only when opener is None, i.e.
        # a genuine production call — see test_mock_opener_path_writes_no_
        # receipt below for why the mock path can't be used to test this).
        answer = {"model": "jev-1.13.0", "id": "resp-abc123",
                  "answers": {"q": {"type": "noul", "noul": 0.8}},
                  "usage": {"input_tokens": 11, "output_tokens": 3},
                  "usable": True, "schema_valid": True, "http_status": 200}
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "jev-calls.jsonl")
            client._append_call_receipt({"q": 1}, ["semantic_creation"], answer, log)
            rows = [json.loads(line) for line in Path(log).read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("response_id", rows[0])
        self.assertEqual(rows[0]["usage"], {"input_tokens": 11, "output_tokens": 3})
        self.assertEqual(rows[0]["facets"], ["semantic_creation"])

    def test_network_receipt_names_caller_kind_hash_and_tokens_without_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "calls.jsonl")
            with patch.object(urllib.request, "urlopen", responder(ANSWER)):
                client.ask("private prompt", {"q": client.noul("private question")},
                            caller="unit-judge", calls_log=log)
            row = json.loads(Path(log).read_text().splitlines()[0])
        self.assertEqual(row["caller"], "unit-judge")
        self.assertEqual(row["question_kind"], "noul")
        self.assertRegex(row["prompt_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(row["usage"]["input_tokens"], 10)
        self.assertNotIn("private prompt", json.dumps(row))
        self.assertNotIn("private question", json.dumps(row))
        self.assertNotIn("secret", json.dumps(row))


    def test_injected_worker_writes_bound_receipt(self):
        """The fake Worker writes only the explicitly isolated test log."""
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "jev-calls.jsonl")
            client.ask("s", {"q": client.noul("?")},
                       server_runner=offline_worker(responder(ANSWER)), facets=["semantic_creation"],
                       calls_log=log)
            self.assertEqual(json.loads(Path(log).read_text())["server_receipt_id"], "offline-worker")

    def test_receipt_file_is_append_only_across_calls(self):
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "jev-calls.jsonl")
            # Bypass the opener short-circuit above by writing rows directly,
            # the same way a real (opener=None) call would append.
            client._append_call_receipt({"q": 1}, ["evidence_matching"], ANSWER, log)
            client._append_call_receipt({"q2": 1}, ["diagnosis"], ANSWER, log)
            lines = Path(log).read_text().splitlines()
        self.assertEqual(len(lines), 2, "each append must add a row, never rewrite the file")
        first, second = (json.loads(line) for line in lines)
        self.assertEqual(first["facets"], ["evidence_matching"])
        self.assertEqual(second["facets"], ["diagnosis"])


VECTORS_PATH = (MODULE_PATH.parent / "fixtures" / "jev-calibration" /
                "distribution-vectors.v1.json")


class CalibrationRecordTests(unittest.TestCase):
    """Every Choice/Score/Noul answer carries its FULL distribution, entropy,
    the pinned model, and a state digest, so accuracy can later be measured
    per question family rather than argued for."""

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(client, "JEV_DAILY_CAP_LOG", str(root / "quota.jsonl")))

    def test_distribution_vectors_shared_with_the_worker(self):
        vectors = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(vectors["probability_sum_tolerance"], client.PROBABILITY_SUM_TOLERANCE)
        for vector in vectors["vectors"]:
            with self.subTest(vector["name"]):
                got = client.answer_distribution(vector["question"], vector["answer"])
                want = vector["expected"]
                for field in ("type", "distribution", "distribution_complete", "top"):
                    self.assertEqual(got[field], want[field], field)
                for field in ("entropy_bits", "top_probability"):
                    if want[field] is None:
                        self.assertIsNone(got[field], field)
                    else:
                        self.assertAlmostEqual(got[field], want[field], places=12, msg=field)

    def test_distribution_can_be_read_without_the_question(self):
        got = client.answer_distribution(None, {"type": "choice", "choice": "a",
                                                "probabilities": {"a": 0.75, "b": 0.25}})
        self.assertEqual(got["distribution"], {"a": 0.75, "b": 0.25})
        self.assertTrue(got["distribution_complete"])

    def test_state_digest_matches_the_worker_canonical_json(self):
        # Same vector mcp-server/test/jev-call-receipt.test.mjs pins.
        self.assertEqual(client.state_sha256({"plan": "ship it"}),
                         hashlib.sha256(b'{"plan":"ship it"}').hexdigest())
        self.assertEqual(client.state_sha256("plain text state"),
                         hashlib.sha256(b'"plain text state"').hexdigest())

    def test_pinned_means_an_exact_version_not_a_moving_alias(self):
        self.assertFalse(client.model_is_pinned("jev-latest"))
        self.assertFalse(client.model_is_pinned(None))
        self.assertTrue(client.model_is_pinned("jev-1.13.0"))

    def test_ask_returns_a_calibration_block(self):
        questions = {"q": client.noul("?"),
                     "pick": client.choice("which?", {"a": "A", "b": "B"})}
        answer = {"model": "jev-1.13.0", "usage": {"input_tokens": 3, "output_tokens": 1},
                  "answers": {"q": {"type": "noul", "noul": 0.8},
                              "pick": {"type": "choice", "choice": "b", "confidence": 0.9,
                                       "probabilities": {"a": 0.1, "b": 0.9}}}}
        result = client.ask({"plan": "ship it"}, questions,
                            model="jev-1.13.0", server_runner=offline_worker(responder(answer)))
        block = result["calibration"]
        self.assertEqual(block["schema"], "carr.jev-calibration.v1")
        self.assertEqual(block["model_requested"], "jev-1.13.0")
        self.assertEqual(block["model_answered"], "jev-1.13.0")
        self.assertTrue(block["model_pinned"])
        self.assertEqual(block["state_sha256"], client.state_sha256({"plan": "ship it"}))
        self.assertEqual(block["questions"]["pick"]["distribution"], {"a": 0.1, "b": 0.9})
        self.assertAlmostEqual(block["questions"]["q"]["entropy_bits"], 0.7219280948873623)
        # The vendor's own answers are untouched.
        self.assertEqual(result["answers"], answer["answers"])

    def test_receipt_row_carries_entropy_digest_and_model_but_no_option_text(self):
        questions = {"b_pick": client.choice("which?", {"secret-option": "A", "other": "B"}),
                     "a_q": client.noul("?")}
        answer = {"model": "jev-1.13.0", "usage": {"input_tokens": 3, "output_tokens": 1},
                  "answers": {"a_q": {"type": "noul", "noul": 0.5},
                              "b_pick": {"type": "choice", "choice": "other", "confidence": 0.5,
                                         "probabilities": {"secret-option": 0.5, "other": 0.5}}}}
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "calls.jsonl"
            with patch.object(urllib.request, "urlopen", responder(answer)):
                client.ask({"plan": "x"}, questions,  calls_log=str(log),
                           cache_ttl_seconds=0)
            raw = log.read_text()
        row = json.loads(raw.splitlines()[0])
        self.assertNotIn("secret-option", raw)
        self.assertEqual(row["model_requested"], "jev-latest")
        self.assertFalse(row["model_pinned"])
        self.assertEqual(row["state_sha256"], client.state_sha256({"plan": "x"}))
        # Aligned with question_ids_sha256, which is sorted by question id.
        self.assertEqual(row["entropy_bits"], [1.0, 1.0])
        self.assertEqual(row["distribution_complete"], [True, True])

    def test_a_calibration_bug_never_fails_a_usable_call(self):
        with patch.object(client, "calibration_block", side_effect=RuntimeError("boom")):
            result = client.ask("s", {"q": client.noul("?")},
                                server_runner=offline_worker(responder(ANSWER)))
        self.assertIsNone(result["calibration"])
        self.assertEqual(result["answers"], ANSWER["answers"])

    def test_failed_call_receipt_has_null_calibration_fields(self):
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "calls.jsonl")
            client._append_call_receipt({"q": 1}, [], None, log, ok=False, error="network")
            row = json.loads(Path(log).read_text())
        self.assertIsNone(row["entropy_bits"])
        self.assertIsNone(row["state_sha256"])


class CanonicalRepoRootTests(unittest.TestCase):
    """Round-2 fix (2026-09-24): `ask()` used to write its receipt next to
    whichever copy of typesafe_client.py was executing — worktree-relative —
    while hooks/completion-evidence-gate.py always read from the canonical
    checkout's out/. Two different physical files. _canonical_repo_root must
    resolve BOTH sides to the SAME path, proven here from an actual worktree
    boundary (this test file itself may be running from inside a worktree)."""

    def test_resolves_to_the_git_common_dir_parent(self):
        expected = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(MODULE_PATH.parent), capture_output=True, text=True, check=True,
        ).stdout.strip()
        expected_root = str(Path(expected).parent)
        self.assertEqual(client._canonical_repo_root(str(MODULE_PATH.parent)), expected_root)

    def test_matches_regardless_of_which_worktree_file_runs_from(self):
        # client.CANONICAL_REPO was computed at import time from THIS file's
        # own on-disk location (possibly a worktree). Recomputing it fresh
        # from an unrelated cwd inside the same repo (this test file's own
        # directory) must land on the identical canonical root — proving a
        # hook running from the canonical checkout and this client running
        # from any worktree agree on one path.
        here = str(Path(__file__).parent)
        self.assertEqual(client._canonical_repo_root(here), client.CANONICAL_REPO)

    def test_falls_back_to_the_given_path_when_git_is_unavailable(self):
        # A directory with no .git at all (or where git itself fails) must
        # fail open to the fallback rather than raise — matching this
        # module's posture everywhere else.
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(client._canonical_repo_root(d), d)


if __name__ == "__main__":
    unittest.main()
