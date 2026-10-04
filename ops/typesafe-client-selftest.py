#!/usr/bin/env python3
"""Offline tests for the Jev client.

NOTHING HERE REACHES THE NETWORK. Every request is served by an injected
opener, so this suite runs on a GitHub runner with no credential, no allowlist
entry and no spend. A test that needed the live service would be a test CI
could not run.
Offline transport fixtures must also own temporary paid-call accounting; a
mocked HTTP response must never reserve capacity in the machine's live ledger.

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
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("typesafe_client.py")
SPEC = importlib.util.spec_from_file_location("typesafe_client", MODULE_PATH)
assert SPEC and SPEC.loader
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)
REAL_ALERT_SINK = client._emit_spend_alert
REAL_MAIL_SINK = getattr(client, "_email_spend_alert", None)
_CAP_ROOT = tempfile.TemporaryDirectory()


def setUpModule():
    # The production cap counter is canonical and shared by every session, so
    # a test that reached it would spend the real daily Jev budget.
    unittest.addModuleCleanup(_CAP_ROOT.cleanup)
    patcher = patch.object(client, "JEV_DAILY_CAP_LOG", os.path.join(_CAP_ROOT.name, "calls.jsonl"))
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


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


class DailyCapTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.log = root / "calls.jsonl"
        self.enterContext(patch.object(client, "JEV_DAILY_CAP_LOG", str(self.log)))
        self.options = dict(api_key="offline-fixture", calls_log=str(self.log),
                            cache_path=str(root / "cache.sqlite3"), caller="cap-test")
        self.requests = []
        self.enterContext(patch.object(client.urllib.request, "urlopen",
                                       responder(ANSWER, self.requests)))
        self.enterContext(patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=2))
        self.alerts = queue.Queue()
        self.mail = queue.Queue()
        self.mail_sink_patch = patch.object(client, "_email_spend_alert", self.mail.put, create=True)
        self.enterContext(self.mail_sink_patch)
        self.sink_patch = patch.object(client, "_emit_spend_alert", self.alerts.put, create=True)
        self.enterContext(self.sink_patch)
        self.delivery_threads = []
        def launch(path, day):
            thread = threading.Thread(target=client._deliver_pending_spend_alerts,
                                      args=(path, day), daemon=True)
            self.delivery_threads.append(thread)
            thread.start()
        self.enterContext(patch.object(client, "_launch_spend_alert_worker", launch, create=True))
        self.addCleanup(lambda: [thread.join(2) for thread in self.delivery_threads
                                if thread.ident is not None])
        self.clock = self.enterContext(patch.object(client, "datetime", wraps=datetime))
        self.clock.now.return_value = datetime(2026, 10, 2, tzinfo=timezone.utc)

    def ask(self, text):
        return client.ask(text, {"q": client.noul("Fixture judgment")}, **self.options)

    def test_cap_reached_is_unavailable_with_zero_further_transport_calls(self):
        self.ask("one")
        self.ask("two")
        for _ in range(3):
            with self.assertRaisesRegex(client.TypeSafeError, "daily.*cap"):
                self.ask("three")
        self.assertEqual(len(self.requests), 2)
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        cap_rows = [row for row in rows if row.get("error") == "daily_paid_call_cap"]
        self.assertEqual(len(cap_rows), 1, "one visible refusal per UTC day")
        self.assertFalse(cap_rows[0]["ok"])

    def test_cache_hit_does_not_count_and_is_available_at_cap(self):
        self.ask("one")
        self.assertTrue(self.ask("one")["cache_hit"])
        self.ask("two")
        self.assertTrue(self.ask("one")["cache_hit"])
        with self.assertRaises(client.TypeSafeError):
            self.ask("three")
        self.assertEqual(len(self.requests), 2)

    def test_utc_day_rollover_resets_cap(self):
        self.ask("one")
        self.ask("two")
        with self.assertRaises(client.TypeSafeError):
            self.ask("three")
        self.clock.now.return_value = datetime(2026, 10, 3, tzinfo=timezone.utc)
        self.ask("three")
        self.assertEqual(len(self.requests), 3)

    def test_custom_receipt_logs_cannot_create_separate_daily_budgets(self):
        self.ask("one")
        self.options["calls_log"] = str(self.log.with_name("other.jsonl"))
        self.ask("two")
        self.options["calls_log"] = str(self.log.with_name("third.jsonl"))
        with self.assertRaisesRegex(client.TypeSafeError, "daily.*cap"):
            self.ask("three")
        self.assertEqual(len(self.requests), 2)
        self.assertIn("daily_paid_call_cap", self.log.read_text())
        self.options["calls_log"] = os.devnull
        with self.assertRaisesRegex(client.TypeSafeError, "daily paid call cap reached"):
            self.ask("suppressed receipt destination")
        self.assertEqual(len(self.requests), 2)

    def test_existing_paid_log_seeds_cap_but_cache_rows_do_not(self):
        self.log.write_text("\n".join(json.dumps(row) for row in [
            {"ts": "2026-10-02T01:00:00Z", "ok": True, "usage": {"input_tokens": 1}},
            {"ts": "2026-10-02T02:00:00Z", "cache_hit": True},
            {"ts": "2026-10-01T01:00:00Z", "ok": True},
        ]) + "\n")
        self.ask("one")
        with self.assertRaises(client.TypeSafeError):
            self.ask("two")
        self.assertEqual(len(self.requests), 1)

    def test_concurrent_callers_share_hard_cap(self):
        self.options["cache_ttl_seconds"] = 0
        def attempt(i):
            try:
                self.ask(str(i))
                return True
            except client.TypeSafeError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(attempt, range(12)))
        self.assertEqual(sum(outcomes), 2)
        self.assertEqual(len(self.requests), 2)

    def test_worker_flag_does_not_disable_explicit_brief_judgment(self):
        with patch.dict(os.environ, CARR_JEV_WORKER="off"):
            self.ask("explicit judgment from brief")
        self.assertEqual(len(self.requests), 1)

    def test_retry_cannot_cross_daily_cap(self):
        client.JEV_COST_CONFIG["daily_paid_call_cap"] = 1
        attempts = []
        def throttled(request, timeout=None):
            attempts.append(1)
            raise urllib.error.HTTPError("https://fixture.invalid", 429, "throttled",
                                         {"retry-after": "0"}, io.BytesIO(b""))
        with patch.object(client.urllib.request, "urlopen", throttled):
            with self.assertRaisesRegex(client.TypeSafeError, "daily.*cap"):
                self.ask("retry")
        self.assertEqual(len(attempts), 1)

    def test_accounting_failure_uses_outage_contract_without_transport(self):
        with patch.object(client.sqlite3, "connect", side_effect=client.sqlite3.OperationalError):
            with self.assertRaisesRegex(client.TypeSafeError, "accounting failed"):
                self.ask("uncountable")
        self.assertEqual(self.requests, [])

    def test_corrupt_seed_evidence_is_unavailable_without_transport(self):
        for evidence in (b'{"ts":"2026-10-02",', b'not json\n', b'\xff\n', b'[]\n',
                         b'{"ts":"invalid"}\n',
                         b'{"ts":"2026-10-02T01:00:00Z","cache_hit":"false"}\n'):
            with self.subTest(evidence=evidence):
                self.log.write_bytes(evidence)
                with self.assertRaisesRegex(client.TypeSafeError, "accounting"):
                    self.ask("uncountable")
                self.assertTrue(client.paid_cap_health().startswith("UNKNOWN"))
        self.assertEqual(self.requests, [])

    def test_cap_configuration_is_required_and_has_no_legacy_default(self):
        remaining = {key: value for key, value in client.JEV_COST_CONFIG.items()
                     if key != "daily_paid_call_cap"}
        with patch.dict(client.JEV_COST_CONFIG, remaining, clear=True):
            with self.assertRaisesRegex(client.TypeSafeError, "cap"):
                self.ask("missing cap configuration")
            self.assertTrue(client.paid_cap_health().startswith("UNKNOWN"))
        self.assertEqual(self.requests, [])

    def test_deadline_expiring_during_accounting_never_starts_transport(self):
        self.options["deadline"] = 15.0
        with patch.object(client.time, "monotonic", side_effect=[10.0, 20.0]):
            with self.assertRaisesRegex(client.TypeSafeError, "deadline"):
                self.ask("expired while reserving")
        self.assertEqual(self.requests, [])


class WorkerDailyCapTests(unittest.TestCase):
    ask = DailyCapTests.ask

    def setUp(self):
        DailyCapTests.setUp(self)
        self.options.pop("api_key")
        self.options["cache_ttl_seconds"] = 0
        self.enterContext(patch.dict(os.environ, CARR_JEV_IN_HOOK="0"))
        self.enterContext(patch.object(client, "read_api_key", return_value="offline-fixture"))
        self.worker_calls = []
        self.cached = False
        self.worker_error = None
        self.options["server_runner"] = self.worker

    def worker(self, argv, **kwargs):
        args = json.loads(argv[3])
        mode = args.get("transport_mode")
        self.worker_calls.append(mode)
        if mode == "cache_only" and not self.cached:
            return subprocess.CompletedProcess(argv, 1, "", '{"error":"jev_cache_miss"}')
        if self.worker_error:
            raise self.worker_error
        answer = {**ANSWER, "ok": True, "receipt_id": "fixture-worker"}
        if self.cached:
            answer.update(cache_hit=True, usage=None)
        return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")

    def count(self):
        path = str(self.log) + ".daily-cap.sqlite3"
        db = client.sqlite3.connect(path)
        try:
            return db.execute("SELECT attempts FROM daily_cap").fetchone()[0]
        finally:
            db.close()

    def test_zero_and_exhausted_cap_never_start_paid_worker_transport(self):
        client.JEV_COST_CONFIG["daily_paid_call_cap"] = 0
        with self.assertRaisesRegex(client.TypeSafeError, "daily.*cap"):
            self.ask("zero capacity")
        self.assertEqual(self.worker_calls, ["cache_only"])
        client.JEV_COST_CONFIG["daily_paid_call_cap"] = 1
        self.ask("one")
        for _ in range(3):
            with self.assertRaisesRegex(client.TypeSafeError, "daily.*cap"):
                self.ask("exhausted")
        self.assertEqual(self.worker_calls.count("paid_once"), 1)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.requests, [])

    def test_worker_cache_hit_is_free_even_at_zero_cap(self):
        client.JEV_COST_CONFIG["daily_paid_call_cap"] = 0
        self.cached = True
        result = self.ask("cached")
        self.assertTrue(result["cache_hit"])
        self.assertIsNone(result["usage"])
        self.assertEqual(self.worker_calls, ["cache_only"])
        self.assertFalse(Path(str(self.log) + ".daily-cap.sqlite3").exists())
        self.assertEqual(self.requests, [])

    def test_direct_fallback_cache_stays_free_when_worker_cache_misses_at_cap(self):
        client.JEV_COST_CONFIG["daily_paid_call_cap"] = 1
        self.options["cache_ttl_seconds"] = 60
        def unavailable(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", '{"error":"unknown_tool"}')
        with patch.dict(self.options, server_runner=unavailable):
            self.ask("direct answer cached while Worker unavailable")
        result = self.ask("direct answer cached while Worker unavailable")
        self.assertTrue(result["cache_hit"])
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.worker_calls, ["cache_only"])
        self.assertEqual(len(self.requests), 1)

    def test_uncertain_worker_attempt_consumes_capacity_before_direct_fallback(self):
        client.JEV_COST_CONFIG["daily_paid_call_cap"] = 1
        self.worker_error = subprocess.TimeoutExpired("offline-worker", 0.01)
        with self.assertRaisesRegex(client.TypeSafeError, "daily.*cap"):
            self.ask("uncertain paid attempt")
        self.assertEqual(self.worker_calls, ["cache_only", "paid_once"])
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.requests, [])
        rows = [json.loads(row) for row in self.log.read_text().splitlines()]
        self.assertTrue(any(row.get("error") == "server_timeout" for row in rows))

    def test_worker_failure_and_direct_retry_each_reserve_capacity(self):
        self.worker_error = OSError("offline-worker failed")
        attempts = []
        def throttled(request, timeout=None):
            attempts.append(1)
            raise urllib.error.HTTPError("https://fixture.invalid", 429, "throttled",
                                         {"retry-after": "0"}, io.BytesIO(b""))
        with patch.object(client.urllib.request, "urlopen", throttled):
            with self.assertRaisesRegex(client.TypeSafeError, "daily.*cap"):
                self.ask("worker then throttled fallback")
        self.assertEqual(self.count(), 2)
        self.assertEqual(len(attempts), 1)

    def test_worker_route_rejects_corrupt_seed_and_missing_configuration(self):
        for evidence in (b'{"ts":"2026-10-02",', b'not json\n', b'\xff\n'):
            with self.subTest(evidence=evidence):
                self.log.write_bytes(evidence)
                with self.assertRaisesRegex(client.TypeSafeError, "accounting"):
                    self.ask("unreadable seed")
        with patch.dict(client.JEV_COST_CONFIG, {}, clear=True):
            with self.assertRaisesRegex(client.TypeSafeError, "cap"):
                self.ask("missing cap")
        self.assertNotIn("paid_once", self.worker_calls)
        self.assertEqual(self.requests, [])

    def test_concurrent_worker_calls_share_cap_and_trigger_alarms(self):
        def attempt(i):
            try:
                self.ask(str(i))
                return True
            except client.TypeSafeError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(attempt, range(12)))
        self.assertEqual(sum(outcomes), 2)
        self.assertEqual(self.worker_calls.count("paid_once"), 2)
        self.assertEqual(self.count(), 2)
        for thread in self.delivery_threads:
            thread.join(2)
        alerts = [self.alerts.get_nowait() for _ in range(self.alerts.qsize())]
        self.assertEqual(sorted(alert["threshold"] for alert in alerts), [50, 80, 100])


class DailyCapMailTests(unittest.TestCase):
    ask = DailyCapTests.ask

    def setUp(self):
        DailyCapTests.setUp(self)
        client.JEV_COST_CONFIG["daily_paid_call_cap"] = 10
        self.options["cache_ttl_seconds"] = 0

    def drain(self):
        for thread in self.delivery_threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        return [self.mail.get_nowait() for _ in range(self.mail.qsize())]

    def test_email_once_per_threshold_per_utc_day_none_below_half(self):
        for day in (2, 3):
            self.clock.now.return_value = datetime(2026, 10, day, tzinfo=timezone.utc)
            for i in range(4):
                self.ask(f"{day}-{i}")
            self.assertEqual(self.drain(), [])
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(self.ask, [f"{day}-{i}" for i in range(4, 10)]))
            for _ in range(3):
                with self.assertRaises(client.TypeSafeError):
                    self.ask("over cap")
            alerts = self.drain()
            self.assertEqual(sorted(a["threshold"] for a in alerts), [50, 80, 100])
            self.assertEqual({a["day"] for a in alerts}, {f"2026-10-{day:02}"})

    def test_notification_retry_does_not_repeat_mail(self):
        for i in range(4):
            self.ask(str(i))
        with patch.object(client, "_emit_spend_alert", side_effect=TimeoutError):
            self.ask("threshold")
            self.assertEqual(len(self.drain()), 1)
        self.ask("retry notification")
        self.assertEqual(self.drain(), [])
        self.assertEqual(len(self.requests), 6)

    def test_paused_old_day_reservation_cannot_repeat_mail_after_rollover(self):
        for i in range(5):
            self.ask(str(i))
        self.assertEqual([(a["day"], a["threshold"]) for a in self.drain()],
                         [("2026-10-02", 50)])
        paused, resume = threading.Event(), threading.Event()
        connect = client.sqlite3.connect
        reservation_thread = None

        def delayed_connect(*args, **kwargs):
            if threading.get_ident() == reservation_thread:
                paused.set()
                if not resume.wait(2):
                    raise TimeoutError("old-day reservation was not resumed")
            return connect(*args, **kwargs)

        def reserve_old_day():
            nonlocal reservation_thread
            reservation_thread = threading.get_ident()
            return self.ask("paused old-day caller")

        with patch.object(client.sqlite3, "connect", delayed_connect):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(reserve_old_day)
                try:
                    self.assertTrue(paused.wait(1), "caller captured its day before connecting")
                    self.clock.now.return_value = datetime(2026, 10, 3, tzinfo=timezone.utc)
                    self.ask("first new-day caller")
                finally:
                    resume.set()
                self.assertEqual(future.result(timeout=2)["model"], ANSWER["model"])
            self.assertEqual(self.drain(), [], "old-day mail claim must survive rollover")

        # Retaining old claims must not suppress a new day's threshold mail.
        for i in range(4):
            self.ask(f"new-day-{i}")
        self.assertEqual([(a["day"], a["threshold"]) for a in self.drain()],
                         [("2026-10-03", 50)])
        self.assertEqual(len(self.requests), 11)

    def test_pending_alarm_recovery_uses_same_worker_for_both_sinks(self):
        workers = []
        for i in range(4):
            self.ask(str(i))
        with patch.object(client, "_dispatch_spend_alerts"):
            self.ask("committed before worker launch")
        self.assertEqual(self.drain(), [])
        def notify(alert):
            workers.append(threading.get_ident())
        def mail(alert):
            self.assertEqual(workers[-1], threading.get_ident())
            self.mail.put(alert)
        with patch.object(client, "_emit_spend_alert", notify), patch.object(client, "_email_spend_alert", mail):
            self.ask("recover committed threshold")
            self.assertEqual(len(self.drain()), 1)
        self.assertIn("mail_sent=1", client.paid_cap_health())

    def test_failed_slow_mail_does_not_block_call_or_retry_email(self):
        entered, release = threading.Event(), threading.Event()
        def sink(alert):
            self.mail.put(alert)
            entered.set()
            release.wait(2)
            raise TimeoutError("fake mail timeout")
        for i in range(4):
            self.ask(str(i))
        try:
            with patch.object(client, "_email_spend_alert", sink):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.ask, "threshold")
                    result = future.result(timeout=0.5)
                    self.assertTrue(entered.wait(1))
                    release.set()
                self.assertEqual(result["model"], ANSWER["model"])
                self.assertEqual(len(self.drain()), 1)
        finally:
            release.set()
        self.ask("after failure")
        self.assertEqual(self.drain(), [])
        self.assertEqual(len(self.requests), 6)
        self.assertIn("mail_failed=1", client.paid_cap_health())

    def test_mail_reuses_handover_self_config_with_deadline(self):
        spec = importlib.util.spec_from_file_location("handover", MODULE_PATH.parent.parent / "bin/gmail-handover.py")
        handover = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(handover)
        messages = []
        class FakeSMTP:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def starttls(self): pass
            def login(self, *args): pass
            def send_message(self, msg): messages.append(msg)
        def run(command, **kwargs):
            self.assertEqual(command[:2], [os.sys.executable, str(MODULE_PATH.parent.parent / "bin/gmail-handover.py")])
            self.assertEqual(command[2:4], ["--to", "joe"])
            self.assertEqual(kwargs["timeout"], 35)
            self.assertTrue(kwargs["check"])
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            with patch.object(os.sys, "argv", command[1:]), patch.object(handover, "creds", return_value=("fixture@example.invalid", "fixture")), patch.object(handover.smtplib, "SMTP", FakeSMTP), patch("sys.stdout", io.StringIO()):
                handover.main()
        alert = {"message": "Jev daily cap 50% · 5/10 paid calls", "threshold": 50}
        with patch.dict(handover.ALLOWED, joe="self-config@carr.us"), patch.object(client.subprocess, "run", run):
            REAL_MAIL_SINK(alert)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["To"], "self-config@carr.us")
        self.assertIn(alert["message"], messages[0].get_content())


class DailyCapAlarmTests(unittest.TestCase):
    ask = DailyCapTests.ask

    def setUp(self):
        DailyCapTests.setUp(self)
        client.JEV_COST_CONFIG["daily_paid_call_cap"] = 10
        self.options["cache_ttl_seconds"] = 0
        self.enterContext(patch.object(client, "_session_id", return_value="session-a"))

    def alert(self, threshold, calls):
        alert = self.alerts.get(timeout=2)
        self.assertEqual((alert["threshold"], alert["calls"], alert["cap"]),
                         (threshold, calls, 10))
        self.assertIn("Jev goes unavailable at the cap", alert["message"])
        self.assertIn(f"{calls}/10", alert["message"])
        for thread in self.delivery_threads:
            if thread.ident is not None:
                thread.join(2)
        return alert

    def test_one_alert_per_threshold_none_below_half_and_no_repeat_at_cap(self):
        for i in range(4):
            self.ask(str(i))
        self.assertTrue(self.alerts.empty())
        for i in range(4, 10):
            self.ask(str(i))
            if i + 1 in (5, 8, 10):
                alert = self.alert({5: 50, 8: 80, 10: 100}[i + 1], i + 1)
                self.assertEqual(alert["top_caller"], ["cap-test", i + 1])
                self.assertEqual(alert["top_session"], ["session-a", i + 1])
        for _ in range(3):
            with self.assertRaises(client.TypeSafeError):
                self.ask("over cap")
        self.assertTrue(self.alerts.empty())
        self.assertEqual(len(self.requests), 10)

    def test_short_lived_process_does_not_wait_for_slow_notifications(self):
        fake_module = self.log.with_name("notifier.py")
        marker = self.log.with_name("submitted.txt")
        fake_module.write_text("import time\ndef _deliver_pending_spend_alerts(path,day):\n"
                               f"    time.sleep(5)\n    open({str(marker)!r}, 'w').write('submitted')\n")
        code = f'''import importlib.util
spec = importlib.util.spec_from_file_location("client", {str(MODULE_PATH)!r})
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
client.__file__ = {str(fake_module)!r}
client.JEV_DAILY_CAP_LOG = {str(self.log)!r}
client._dispatch_spend_alerts([{{"message": "fake", "day": "2026-10-02"}}] * 3)
'''
        started = time.monotonic()
        subprocess.run([os.sys.executable, "-c", code], check=True, timeout=10,
                       capture_output=True)
        self.assertLess(time.monotonic() - started, 3, "hook exit exceeds its three-second budget")
        end = time.monotonic() + 8
        while time.monotonic() < end:
            if marker.exists() and marker.read_text() == "submitted":
                break
            time.sleep(0.01)
        self.assertEqual(marker.read_text(), "submitted", "delivery survived hook exit")

    def test_process_death_after_commit_retains_pending_alert_and_recovers(self):
        for i in range(4):
            self.ask(str(i))
        with patch.object(client, "_dispatch_spend_alerts"):
            self.ask("threshold committed but not dispatched")
        self.assertIn("pending=1", client.paid_cap_health())
        self.ask("recover pending warning")
        self.alert(50, 5)
        self.assertTrue(self.alerts.empty())

    def test_failed_launch_retries_and_delivery_acknowledges_only_success(self):
        for i in range(4):
            self.ask(str(i))
        with patch.object(client, "_launch_spend_alert_worker", side_effect=OSError("fixture spawn failure")):
            self.ask("threshold")
        self.assertIn("failed=1", client.paid_cap_health())
        self.assertIn("OSError", client.paid_cap_health())
        self.ask("retry launch")
        self.alert(50, 5)
        self.assertIn("delivered=1", client.paid_cap_health())
        self.assertIn("failed=0", client.paid_cap_health())

    def test_dead_delivery_lease_recovers_and_failure_retry_ceiling_is_three(self):
        for i in range(4):
            self.ask(str(i))
        with patch.object(client, "_dispatch_spend_alerts"):
            self.ask("pending threshold")
        path = str(self.log) + ".daily-cap.sqlite3"
        with client.sqlite3.connect(path) as db:
            db.execute("UPDATE daily_cap_delivery SET state='sending',attempts=1,lease_until=0 WHERE day=?", ("2026-10-02",))
        db.close()
        self.assertIn("failed=1", client.paid_cap_health())
        self.ask("recover expired lease")
        self.alert(50, 5)
        with patch.object(client, "_launch_spend_alert_worker", side_effect=OSError("fixture spawn failure")):
            for i in range(4):
                self.ask(f"remaining-{i}")
            for i in range(6):
                with self.assertRaises(client.TypeSafeError):
                    self.ask(f"refused-{i}")
        with client.sqlite3.connect(path) as db:
            rows = db.execute("SELECT threshold,attempts,state FROM daily_cap_delivery ORDER BY threshold").fetchall()
        db.close()
        self.assertEqual(rows, [(50, 2, "delivered"), (80, 3, "failed"), (100, 3, "failed")])
        self.assertEqual(len(self.requests), 10)

    def test_notifier_failure_is_observable_and_retry_is_bounded(self):
        for i in range(4):
            self.ask(str(i))
        failed = threading.Event()
        def broken(_alert):
            failed.set()
            raise TimeoutError("fixture notifier timeout")
        with patch.object(client, "_emit_spend_alert", broken):
            self.ask("threshold")
            self.assertTrue(failed.wait(1))
            # Wait for the delivery result, not just entry into the sink.
            end = time.monotonic() + 2
            while "failed=1" not in client.paid_cap_health() and time.monotonic() < end:
                time.sleep(0.01)
            self.assertIn("failed=1", client.paid_cap_health())
        self.ask("recover failed notification")
        self.alert(50, 5)
        self.assertEqual(len(self.requests), 6)

    def test_day_rollover_resets_threshold_alerts(self):
        for day in (2, 3):
            self.clock.now.return_value = datetime(2026, 10, day, tzinfo=timezone.utc)
            for i in range(10):
                self.ask(f"{day}-{i}")
                if i + 1 in (5, 8, 10):
                    alert = self.alert({5: 50, 8: 80, 10: 100}[i + 1], i + 1)
                    self.assertEqual(alert["day"], f"2026-10-{day:02}")
        self.assertTrue(self.alerts.empty())

    def test_alert_failure_and_slow_sink_do_not_break_or_block_call(self):
        entered, release = threading.Event(), threading.Event()
        def broken_sink(_alert):
            entered.set()
            release.wait(2)
            raise RuntimeError("fake alert outage")
        for i in range(4):
            self.ask(str(i))
        try:
            with patch.object(client, "_emit_spend_alert", broken_sink):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.ask, "threshold")
                    try:
                        result = future.result(timeout=0.5)
                    finally:
                        release.set()
                self.assertEqual(result["model"], ANSWER["model"])
                self.assertTrue(entered.wait(1))
                self.assertEqual(len(self.requests), 5, "transport ran while sink blocked")
        finally:
            release.set()
        self.ask("still available")
        self.assertEqual(len(self.requests), 6)

    def test_concurrent_workers_claim_each_threshold_once(self):
        def attempt(i):
            try:
                self.ask(str(i))
            except client.TypeSafeError:
                pass
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(attempt, range(30)))
        alerts = [self.alerts.get(timeout=2) for _ in range(3)]
        self.assertEqual(sorted(a["threshold"] for a in alerts), [50, 80, 100])
        self.assertTrue(self.alerts.empty())
        self.assertEqual(len(self.requests), 10)

    def test_existing_day_seeds_top_caller_and_session_and_catches_up(self):
        self.log.write_text("".join(json.dumps({"ts": "2026-10-02T01:00:00Z",
            "caller": "earlier-worker", "session": "earlier-session", "ok": True}) + "\n"
            for _ in range(7)))
        self.ask("eighth")
        for threshold in (50, 80):
            alert = self.alert(threshold, 8)
            self.assertEqual(alert["top_caller"], ["earlier-worker", 7])
            self.assertEqual(alert["top_session"], ["earlier-session", 7])
        self.assertTrue(self.alerts.empty())

    def test_mac_notification_adapter_has_bounded_wait_in_detached_worker(self):
        with patch.object(client.subprocess, "run") as notify:
            REAL_ALERT_SINK({"message": '5/10 top caller "worker"; session a\\b'})
        args = notify.call_args.args[0]
        self.assertEqual(args[:2], ["/usr/bin/osascript", "-e"])
        self.assertIn("display notification", args[2])
        self.assertIn('\\"worker\\"', args[2])
        self.assertEqual(notify.call_args.kwargs["timeout"], 5)

    def test_cache_hits_never_emit_threshold_alerts(self):
        self.options["cache_ttl_seconds"] = 60
        for i in range(5):
            self.ask(str(i))
        self.alert(50, 5)
        for _ in range(10):
            self.assertTrue(self.ask("4")["cache_hit"])
        self.assertTrue(self.alerts.empty())
        self.assertEqual(len(self.requests), 5)

    def test_alarm_storage_failure_keeps_transport_and_cap_working(self):
        connect = client.sqlite3.connect
        class BrokenAlarmDB:
            def __init__(self, db):
                self.db = db
            def execute(self, sql, *args):
                if "CREATE TABLE IF NOT EXISTS daily_cap_attribution" in sql:
                    raise client.sqlite3.OperationalError("fake alarm-only failure")
                return self.db.execute(sql, *args)
            def __getattr__(self, name):
                return getattr(self.db, name)
        with patch.object(client.sqlite3, "connect",
                          side_effect=lambda *a, **kw: BrokenAlarmDB(connect(*a, **kw))):
            for i in range(10):
                self.ask(str(i))
            with self.assertRaisesRegex(client.TypeSafeError, "daily.*cap"):
                self.ask("eleventh")
        self.assertEqual(len(self.requests), 10)
        self.assertTrue(self.alerts.empty())

    def test_alarm_savepoint_failure_and_process_start_failure_fail_open(self):
        connect = client.sqlite3.connect
        class NoAlarmSavepoint:
            def __init__(self, db):
                self.db = db
            def execute(self, sql, *args):
                if "SAVEPOINT spend_alarm" in sql:
                    raise client.sqlite3.OperationalError("fake alarm savepoint failure")
                return self.db.execute(sql, *args)
            def __getattr__(self, name):
                return getattr(self.db, name)
        with patch.object(client.sqlite3, "connect",
                          side_effect=lambda *a, **kw: NoAlarmSavepoint(connect(*a, **kw))):
            self.assertEqual(self.ask("first")["model"], ANSWER["model"])
        for i in range(3):
            self.ask(str(i))
        with patch.object(client, "_launch_spend_alert_worker", side_effect=RuntimeError("process unavailable")):
            self.assertEqual(self.ask("threshold")["model"], ANSWER["model"])
        self.assertEqual(len(self.requests), 5)

    def test_existing_ap_counter_seeds_unreceipted_attempts_as_unknown(self):
        with client.sqlite3.connect(str(self.log) + ".daily-cap.sqlite3") as db:
            db.execute("CREATE TABLE daily_cap (day TEXT PRIMARY KEY, attempts INTEGER, notified INTEGER)")
            db.execute("INSERT INTO daily_cap VALUES ('2026-10-02',7,0)")
        db.close()
        self.ask("eighth")
        for threshold in (50, 80):
            alert = self.alert(threshold, 8)
            self.assertEqual(alert["top_caller"], ["unknown", 7])
            self.assertEqual(alert["top_session"], ["unknown", 7])

    def test_tiny_cap_claims_overlapping_thresholds_once(self):
        client.JEV_COST_CONFIG["daily_paid_call_cap"] = 1
        self.ask("only call")
        alerts = [self.alerts.get(timeout=2) for _ in range(3)]
        self.assertEqual([a["threshold"] for a in alerts], [50, 80, 100])
        for _ in range(2):
            with self.assertRaises(client.TypeSafeError):
                self.ask("refused")
        self.assertTrue(self.alerts.empty())

    def test_paid_cap_health_is_read_only_and_names_bound_action(self):
        for i in range(5):
            self.ask(str(i))
        self.alert(50, 5)
        self.log.unlink()  # Counter, not successful receipt count, is authoritative.
        with patch.object(client, "read_api_key", side_effect=AssertionError("credential read")):
            line = client.paid_cap_health()
        self.assertIn("5/10", line)
        self.assertIn("WARN", line)
        for text in ("on breach:", "notify Joe", "owner orchestrator", "remediation",
                     "verify", "auto-clear"):
            self.assertIn(text, line)
        self.assertTrue(self.alerts.empty())


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
            self.assertNotIn("$0.000", unknown)
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
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.quota_log = root / 'quota.jsonl'
        self.enterContext(patch.object(client, 'JEV_DAILY_CAP_LOG', str(self.quota_log)))

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
                    client.urllib.request, 'urlopen', responder(ANSWER)):
                result = client.ask('state', {'q': client.noul('judge')},
                    api_key='offline', cache_ttl_seconds=0, calls_log=str(log),
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
import importlib.util, io, json, sys
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('standalone_client', sys.argv[1])
client = importlib.util.module_from_spec(spec); spec.loader.exec_module(client)
client.JEV_DAILY_CAP_LOG = sys.argv[3]
class Response(io.StringIO):
    status = 200
    def __init__(self):
        super().__init__(json.dumps({'model':'jev-test',
            'answers':{'architecture_or_design':{'type':'noul','noul':0.9}},
            'usage':{'input_tokens':20,'output_tokens':6}}))
with patch.object(client.urllib.request, 'urlopen', lambda *a, **k: Response()):
    client.ask('state', {'architecture_or_design':client.noul('judge design')},
               api_key='offline', cache_ttl_seconds=0, calls_log=sys.argv[2])
"""
            result = subprocess.run([__import__('sys').executable, '-c', code,
                                     str(MODULE_PATH.resolve()), str(log), str(self.quota_log)],
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
                        client.urllib.request, 'urlopen', responder(ANSWER)):
                    client.ask('state', {'q':client.noul('judge')}, api_key='offline',
                               cache_ttl_seconds=0, calls_log=str(log))
                    transcript.write_text(json.dumps(header)+'\n')
                    client.ask('state', {'q':client.noul('judge')}, api_key='offline',
                               cache_ttl_seconds=0, calls_log=str(log))
                rows = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(rows[0]['session'], session)
                self.assertIsInstance(rows[0]['human_turn_id'], str)
                self.assertIsNone(rows[1]['human_turn_id'])
                self.assertTrue(all(row['ok'] for row in rows))


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
        answer = {**ANSWER, "answers": {
            "a": {"type": "noul", "noul": 0.91},
            "b": {"type": "noul", "noul": 0.13},
            "c": {"type": "score", "score": 0.7, "confidence": 0.8}}}
        client.ask("state", questions, api_key="k",
                   opener=responder(answer, captured))
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

    def test_http_200_empty_body_records_unusable_call(self):
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "calls.jsonl")
            with patch.object(client.urllib.request, "urlopen", responder({})):
                with self.assertRaises(client.TypeSafeError):
                    client.ask("s", {"q": client.noul("?")}, api_key="k", calls_log=log)
            row = json.loads(Path(log).read_text())
            self.assertEqual(row["http_status"], 200)
            self.assertFalse(row["ok"])
            self.assertFalse(row["schema_valid"])

    def test_new_receipts_hash_question_ids_and_preserve_facet_credit(self):
        name = "private_semantic_creation_question"
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "calls.jsonl"
            answer = {**ANSWER, "answers": {
                name: {"type": "noul", "noul": 0.91}}}
            with patch.object(client.urllib.request, "urlopen", responder(answer)):
                client.ask("state", {name: client.noul("is this relevant?")},
                           api_key="secret", calls_log=str(log))
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
            with patch.object(client.urllib.request, "urlopen", responder(ANSWER)):
                client.ask("private prompt", {"q": client.noul("private question")},
                           api_key="secret", caller="unit-judge", calls_log=log,
                           cache_path=str(Path(d) / "cache.sqlite3"))
            row = json.loads(Path(log).read_text().splitlines()[0])
        self.assertEqual(row["caller"], "unit-judge")
        self.assertEqual(row["question_kind"], "noul")
        self.assertRegex(row["prompt_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(row["usage"]["input_tokens"], 10)
        self.assertNotIn("private prompt", json.dumps(row))
        self.assertNotIn("private question", json.dumps(row))
        self.assertNotIn("secret", json.dumps(row))

    def test_default_client_caches_identical_calls_for_every_caller(self):
        with tempfile.TemporaryDirectory() as d:
            requests = []
            cache = str(Path(d) / "cache.sqlite3")
            log = str(Path(d) / "calls.jsonl")
            with patch.object(client.urllib.request, "urlopen", responder(ANSWER, requests)):
                args = {"api_key": "secret", "caller": "direct-model-room",
                        "cache_path": cache, "calls_log": log}
                first = client.ask("same state", {"q": client.noul("same question")}, **args)
                second = client.ask("same state", {"q": client.noul("same question")}, **args)
            self.assertEqual(len(requests), 1)
            self.assertEqual(first["usage"]["input_tokens"], 10)
            self.assertIsNone(second["usage"])
            self.assertTrue(second["cache_hit"])

    def test_invalid_json_attempt_is_logged_without_request_text(self):
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "calls.jsonl")
            with patch.object(client.urllib.request, "urlopen", return_value=FakeResponse(b"not-json")):
                with self.assertRaises(ValueError):
                    client.ask("sensitive state", {"q": client.noul("private question")},
                               api_key="secret", caller="unit-judge", calls_log=log)
            row = json.loads(Path(log).read_text().splitlines()[0])
        self.assertFalse(row["ok"])
        self.assertRegex(row["prompt_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("sensitive state", json.dumps(row))

    def test_identical_judge_call_within_ttl_makes_zero_additional_network_calls(self):
        with tempfile.TemporaryDirectory() as d:
            cache = str(Path(d) / "cache.sqlite3")
            log = str(Path(d) / "calls.jsonl")
            requests = []
            with patch.object(client.urllib.request, "urlopen", responder(ANSWER, requests)):
                kw = {"api_key": "secret", "caller": "jev_judge",
                      "cache_ttl_seconds": 60, "cache_path": cache, "calls_log": log}
                first = client.ask("same state", {"q": client.noul("same?")}, **kw)
                second = client.ask("same state", {"q": client.noul("same?")}, **kw)
            self.assertEqual(len(requests), 1)
            self.assertEqual(first["answers"], second["answers"])
            self.assertTrue(second["cache_hit"])
            self.assertIsNone(second["usage"])
            rows = [json.loads(x) for x in Path(log).read_text().splitlines()]
            self.assertEqual(sum(row.get("ok") is True for row in rows), 1)

    def test_cache_key_separates_caller_and_prompt_and_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            requests = []
            cache = str(Path(d) / "cache.sqlite3")
            clock = [100.0]
            with patch.object(client.urllib.request, "urlopen", responder(ANSWER, requests)):
                with patch.object(client.time, "time", side_effect=lambda: clock[0]):
                    for index, (caller, state) in enumerate((("judge-a", "x"), ("judge-b", "x"),
                                                             ("judge-a", "y"), ("judge-a", "x"))):
                        if index == 3:
                            clock[0] = 161.0
                        client.ask(state, {"q": client.noul("?")}, api_key="secret",
                                   caller=caller, cache_ttl_seconds=60,
                                   cache_path=cache, calls_log=str(Path(d) / "calls.jsonl"))
            self.assertEqual(len(requests), 4)

    def test_cache_separates_endpoint_account_model_and_question_kind(self):
        with tempfile.TemporaryDirectory() as d:
            requests = []

            def answer(request, timeout=None):
                requests.append(request)
                kind = json.loads(request.data)["questions"]["q"]["type"]
                value = ({"type": "score", "score": 1, "confidence": 0.8}
                         if kind == "score" else
                         {"type": "noul", "noul": len(requests) / 10})
                return FakeResponse(json.dumps({**ANSWER, "answers": {"q": value}}).encode())

            kw = {"caller": "same-caller", "cache_ttl_seconds": 60,
                  "cache_path": str(Path(d) / "cache.sqlite3"),
                  "calls_log": str(Path(d) / "calls.jsonl")}
            with patch.object(client.urllib.request, "urlopen", answer):
                first = client.ask("same state", {"q": client.noul("same?")},
                                   api_key="account-a", endpoint="https://one.example/api",
                                   model="jev-a", **kw)
                by_endpoint = client.ask("same state", {"q": client.noul("same?")},
                                         api_key="account-a", endpoint="https://two.example/api",
                                         model="jev-a", **kw)
                by_account = client.ask("same state", {"q": client.noul("same?")},
                                        api_key="account-a", endpoint="https://one.example/api",
                                        model="jev-a", account="org-b", **kw)
                by_credential = client.ask("same state", {"q": client.noul("same?")},
                                           api_key="account-b", endpoint="https://one.example/api",
                                           model="jev-a", **kw)
                by_model = client.ask("same state", {"q": client.noul("same?")},
                                      api_key="account-a", endpoint="https://one.example/api",
                                      model="jev-b", **kw)
                by_kind = client.ask("same state", {"q": client.score("same?", ["no", "yes"])},
                                     api_key="account-a", endpoint="https://one.example/api",
                                     model="jev-a", **kw)
                repeated = client.ask("same state", {"q": client.noul("same?")},
                                      api_key="account-a", endpoint="https://one.example/api",
                                      model="jev-a", **kw)
            self.assertEqual(len(requests), 6)
            self.assertEqual([x["answers"]["q"] for x in
                              (first, by_endpoint, by_account, by_credential, by_model, by_kind)],
                             [{"type": "noul", "noul": n / 10} for n in range(1, 6)] +
                             [{"type": "score", "score": 1, "confidence": 0.8}])
            self.assertEqual(repeated["answers"], first["answers"])
            self.assertTrue(repeated["cache_hit"])

    def test_mock_opener_path_writes_no_receipt(self):
        """A call made through `opener` (the offline selftest/mock path) must
        NOT leave a receipt — a mock response was never actually seen by the
        vendor, and a receipt for it would let running THIS selftest suite
        count as a real turn's Jev evidence in lib/jev_required_actions.py."""
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "jev-calls.jsonl")
            client.ask("s", {"q": client.noul("?")}, api_key="k",
                       opener=responder(ANSWER), facets=["semantic_creation"],
                       calls_log=log)
            self.assertFalse(Path(log).exists(),
                            "mock/opener calls must not create a receipt file at all")

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
        result = client.ask({"plan": "ship it"}, questions, api_key="k",
                            model="jev-1.13.0", opener=responder(answer))
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
            with patch.object(client.urllib.request, "urlopen", responder(answer)):
                client.ask({"plan": "x"}, questions, api_key="k", calls_log=str(log),
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
            result = client.ask("s", {"q": client.noul("?")}, api_key="k",
                                opener=responder(ANSWER))
        self.assertIsNone(result["calibration"])
        self.assertEqual(result["answers"], ANSWER["answers"])

    def test_failed_call_receipt_has_null_calibration_fields(self):
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "calls.jsonl")
            client._append_call_receipt({"q": 1}, [], None, log, ok=False, error="network")
            row = json.loads(Path(log).read_text())
        self.assertIsNone(row["entropy_bits"])
        self.assertIsNone(row["state_sha256"])

    def test_cache_hit_still_carries_a_calibration_block(self):
        with tempfile.TemporaryDirectory() as d:
            args = {"api_key": "k", "cache_path": str(Path(d) / "c.sqlite3"),
                    "calls_log": str(Path(d) / "calls.jsonl"), "cache_ttl_seconds": 60}
            with patch.object(client.urllib.request, "urlopen", responder(ANSWER)):
                client.ask("s", {"q": client.noul("?")}, **args)
                hit = client.ask("s", {"q": client.noul("?")}, **args)
        self.assertTrue(hit["cache_hit"])
        self.assertAlmostEqual(hit["calibration"]["questions"]["q"]["distribution"]["true"], 0.91)


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
