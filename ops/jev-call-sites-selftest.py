#!/usr/bin/env python3
"""Offline tests for the Jev call-site registry, budgets and quiet cap notices.

NOTHING HERE REACHES THE NETWORK OR SENDS MAIL. Paid transports are an
injected urlopen, the cap counter lives in a temporary directory, and the
alarm sinks run through the CARR_JEV_ALERT_SINK dry-run seam.

What this suite holds (2026-10-04 system-wide Jev audit):
  * a paid call needs a registered site, that site's attribution, and room in
    its own and the global hourly budget; every refusal happens before any
    transport, is logged once, and never counts as a paid attempt;
  * the hourly cap is real and resets on the hour;
  * a reached cap surfaces ONE line per session per cap window, with the reset;
  * the alarm mail names its failure (mail_unconfigured) instead of a bare
    CalledProcessError, and tests never send mail;
  * every source file that can make a paid call is in the registry, and every
    registry entry names files that exist (the CI guard against a new burner).
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("typesafe_client_sites", REPO / "ops" / "typesafe_client.py")
assert SPEC and SPEC.loader
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)
REGISTRY_PATH = REPO / "ops" / "config" / "jev-call-sites.v1.json"


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


ANSWER = {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.9}},
          "usage": {"input_tokens": 10, "output_tokens": 2}}


def site(caller, **overrides):
    entry = {"caller": caller, "trigger": "fixture", "runs_in": "fixture",
             "attribution": "session", "unattended": "off", "hourly_budget": 50,
             "daily_budget": 500, "owner": "orchestrator", "value": "fixture",
             "sources": ["ops/typesafe_client.py"]}
    entry.update(overrides)
    return entry


class Harness(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = root
        self.log = root / "calls.jsonl"
        self.registry = root / "sites.json"
        self.write_registry([site("hook_site"), site("job_site", attribution="session_or_job"),
                             site("brief:*", unattended="allowed")], hourly=100)
        self.enterContext(patch.object(client, "JEV_DAILY_CAP_LOG", str(self.log)))
        self.enterContext(patch.object(client, "JEV_CALL_SITES_PATH", str(self.registry)))
        self.enterContext(patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=1000))
        self.requests = []

        def opener(request, timeout=None):
            self.requests.append(request)
            return FakeResponse(json.dumps(ANSWER).encode())
        self.enterContext(patch.object(client.urllib.request, "urlopen", opener))
        self.enterContext(patch.object(client, "_launch_spend_alert_worker", lambda *a: None))
        env = {k: v for k, v in os.environ.items()
               if k not in client.SESSION_ID_ENV_KEYS + ("CARR_JEV_JOB", "XPC_SERVICE_NAME", "CARR_JEV_WORKER",
                                                         "CARR_JEV_OFFLINE", "CARR_HOOK_FIXTURE")}
        self.enterContext(patch.dict(os.environ, env, clear=True))
        self.clock = self.enterContext(patch.object(client, "datetime", wraps=datetime))
        self.now(2026, 10, 4, 3, 10)

    def now(self, *parts):
        self.clock.now.return_value = datetime(*parts, tzinfo=timezone.utc)

    def write_registry(self, sites, hourly=100):
        self.registry.write_text(json.dumps({"schema": "carr-jev-call-sites/v1",
                                             "hourly_paid_call_cap": hourly, "sites": sites}))

    def ask(self, text, caller="hook_site", session="sess-1"):
        return client.ask(text, {"q": client.noul("Fixture judgment")}, api_key="offline-fixture",
                          calls_log=str(self.log), cache_ttl_seconds=0, caller=caller,
                          session_id=session)

    def rows(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]


class RegistryAdmissionTests(Harness):
    def test_unregistered_caller_is_refused_before_transport_and_logged_once(self):
        for _ in range(3):
            with self.assertRaises(client.JevCallRefused) as caught:
                self.ask("x", caller="brand_new_burner")
            self.assertEqual(caught.exception.code, "unregistered_caller")
        self.assertEqual(self.requests, [])
        refusals = [r for r in self.rows() if r.get("error") == "unregistered_caller"]
        self.assertEqual(len(refusals), 1, "a refusal is visible once per window, not per call")
        self.assertFalse(refusals[0]["ok"])

    def test_refusals_never_count_toward_the_daily_cap_seed(self):
        with self.assertRaises(client.JevCallRefused):
            self.ask("x", caller="brand_new_burner")
        day = "2026-10-04"
        self.assertEqual(client._logged_attempts(str(self.log), day), 0)

    def test_session_site_without_session_is_refused(self):
        with self.assertRaises(client.JevCallRefused) as caught:
            self.ask("x", session=None)
        self.assertEqual(caught.exception.code, "unattributed_call")
        self.assertEqual(self.requests, [])
        self.ask("x", session="sess-1")
        self.assertEqual(len(self.requests), 1)

    def test_job_attribution_comes_from_carr_job_or_launchd_label(self):
        with self.assertRaises(client.JevCallRefused):
            self.ask("x", caller="job_site", session=None)
        with patch.dict(os.environ, XPC_SERVICE_NAME="application.com.apple.Terminal"):
            with self.assertRaises(client.JevCallRefused):
                self.ask("y", caller="job_site", session=None)
        with patch.dict(os.environ, XPC_SERVICE_NAME="com.carr.deal-read"):
            self.ask("z", caller="job_site", session=None)
        with patch.dict(os.environ, CARR_JEV_JOB="release-pipeline"):
            self.ask("w", caller="job_site", session=None)
        self.assertEqual(len(self.requests), 2)
        jobs = [r.get("job") for r in self.rows() if r.get("ok")]
        self.assertEqual(jobs, ["com.carr.deal-read", "release-pipeline"])

    def test_unattended_worker_runs_only_sites_that_allow_it(self):
        with patch.dict(os.environ, CARR_JEV_WORKER="off"):
            with self.assertRaises(client.JevCallRefused) as caught:
                self.ask("x")
            self.assertEqual(caught.exception.code, "unattended_worker_off")
            self.ask("explicit brief judgment", caller="brief:w3_builder")
        self.assertEqual(len(self.requests), 1)

    def test_fixture_and_ci_runs_never_pay_and_never_log(self):
        for flag in ("CARR_JEV_OFFLINE", "CARR_HOOK_FIXTURE"):
            with self.subTest(flag=flag), patch.dict(os.environ, {flag: "1"}):
                with self.assertRaises(client.JevCallRefused) as caught:
                    self.ask("x")
                self.assertEqual(caught.exception.code, "fixture_offline")
        self.assertEqual(self.requests, [])
        self.assertEqual(self.rows(), [], "fixture traffic stays out of the real call log")

    def test_prefix_entry_needs_a_suffix(self):
        with self.assertRaises(client.JevCallRefused):
            self.ask("x", caller="brief:")

    def test_unreadable_registry_admits_nothing(self):
        self.registry.write_text("{")
        with self.assertRaises(client.TypeSafeError):
            self.ask("x")
        self.assertEqual(self.requests, [])

    def test_offline_injected_opener_is_not_policed(self):
        result = client.ask("x", {"q": client.noul("Fixture")}, api_key="k", caller="anything",
                            opener=lambda request, timeout=None: FakeResponse(json.dumps(ANSWER).encode()),
                            calls_log=str(self.log), cache_ttl_seconds=0)
        self.assertEqual(result["model"], "jev-1.13.0")


class BudgetTests(Harness):
    def test_global_hourly_cap_holds_and_resets_on_the_hour(self):
        self.write_registry([site("hook_site", hourly_budget=50, daily_budget=500)], hourly=3)
        for i in range(3):
            self.ask(f"a{i}")
        with self.assertRaises(client.JevCallRefused) as caught:
            self.ask("over")
        self.assertEqual(caught.exception.code, "hourly_paid_call_cap")
        self.assertEqual(caught.exception.resets_at, "2026-10-04T04:00:00Z")
        self.assertEqual(len(self.requests), 3)
        self.now(2026, 10, 4, 4, 0)
        self.ask("next hour")
        self.assertEqual(len(self.requests), 4)

    def test_site_hourly_and_daily_budgets_hold(self):
        self.write_registry([site("hook_site", hourly_budget=2, daily_budget=3),
                             site("job_site", attribution="session_or_job")], hourly=100)
        self.ask("1")
        self.ask("2")
        with self.assertRaises(client.JevCallRefused) as caught:
            self.ask("3")
        self.assertEqual(caught.exception.code, "site_hourly_budget")
        self.ask("other site keeps working", caller="job_site")
        self.now(2026, 10, 4, 5, 0)
        self.ask("3")
        with self.assertRaises(client.JevCallRefused) as caught:
            self.ask("4")
        self.assertEqual(caught.exception.code, "site_daily_budget")
        self.assertEqual(caught.exception.resets_at, "2026-10-05T00:00:00Z")
        self.assertEqual(len(self.requests), 4)

    def test_budget_refusal_is_logged_once_per_window(self):
        self.write_registry([site("hook_site", hourly_budget=1, daily_budget=10)], hourly=100)
        self.ask("1")
        for _ in range(4):
            with self.assertRaises(client.JevCallRefused):
                self.ask("2")
        self.assertEqual(len([r for r in self.rows() if r.get("error") == "site_hourly_budget"]), 1)

    def test_daily_cap_still_applies_above_site_budgets(self):
        with patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=1):
            self.ask("1")
            with self.assertRaisesRegex(client.TypeSafeError, "daily paid call cap"):
                self.ask("2")


class QuietNoticeTests(Harness):
    def test_cap_reached_surfaces_one_line_per_session_per_window(self):
        self.write_registry([site("hook_site")], hourly=1)
        self.ask("1")
        with self.assertRaises(client.JevCallRefused):
            self.ask("2")
        pause = client.active_pause()
        self.assertEqual(pause["scope"], "hourly_paid_call_cap")
        first = client.pause_notice("sess-A")
        self.assertIn("04:00", first)
        self.assertIn("paused", first.lower())
        self.assertIsNone(client.pause_notice("sess-A"))
        self.assertIsNotNone(client.pause_notice("sess-B"), "each session hears it once")
        self.now(2026, 10, 4, 4, 1)
        self.assertIsNone(client.active_pause())
        self.assertIsNone(client.pause_notice("sess-A"))

    def test_daily_cap_pause_names_utc_midnight(self):
        with patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=1):
            self.ask("1")
            with self.assertRaises(client.TypeSafeError):
                self.ask("2")
            pause = client.active_pause()
        self.assertEqual(pause, {"scope": "daily_paid_call_cap", "resets_at": "2026-10-05T00:00:00Z"})

    def test_site_budget_pause_is_scoped_to_the_named_sites(self):
        self.write_registry([site("hook_site", hourly_budget=1, daily_budget=10),
                             site("job_site", attribution="session_or_job")], hourly=100)
        self.ask("1")
        with self.assertRaises(client.JevCallRefused):
            self.ask("2")
        self.assertIsNone(client.active_pause())
        self.assertEqual(client.active_pause(sites=["hook_site"])["scope"], "site_hourly_budget")
        self.assertIsNone(client.active_pause(sites=["job_site"]))


class SpendHealthTests(Harness):
    def test_health_line_reports_spend_by_site_with_bound_action(self):
        self.write_registry([site("hook_site", daily_budget=2, hourly_budget=2),
                             site("job_site", attribution="session_or_job")], hourly=100)
        self.ask("1")
        self.ask("2")
        self.ask("3", caller="job_site")
        line = client.spend_by_site_health()
        self.assertTrue(line.startswith("WARN jev spend by site"), line)
        self.assertIn("hook_site=2/2", line)
        self.assertIn("job_site=1/500", line)
        self.assertIn("owner orchestrator", line)
        self.assertIn("ops/config/jev-call-sites.v1.json", line)

    def test_canonical_health_prints_the_site_spend_row(self):
        source = (REPO / "tools" / "health-check.py").read_text(encoding="utf-8")
        self.assertIn("client.spend_by_site_health()", source)
        self.assertIn('_red("jev_site_budget"', source)

    def test_health_line_is_ok_when_every_site_is_inside_budget(self):
        self.ask("1")
        self.assertTrue(client.spend_by_site_health().startswith("OK jev spend by site"))


class AlarmMailTests(unittest.TestCase):
    def test_missing_mail_credential_is_named_not_called_process_error(self):
        with patch.object(client, "GMAIL_ENV_PATH", "/nonexistent/gmail.env"), \
                patch.dict(os.environ, {"CARR_GMAIL_USER": "", "CARR_GMAIL_APP_PASSWORD": "",
                                        "CARR_JEV_ALERT_SINK": ""}), \
                patch.object(client.subprocess, "run") as run:
            with self.assertRaises(client.AlarmSinkError) as caught:
                client._email_spend_alert({"message": "m", "threshold": 50})
        self.assertEqual(str(caught.exception), "mail_unconfigured")
        run.assert_not_called()

    def test_failed_handover_reports_a_category_from_its_stderr(self):
        failure = subprocess.CalledProcessError(1, ["gmail-handover"], stderr="login refused: (535)")
        with tempfile.NamedTemporaryFile() as env_file, \
                patch.object(client, "GMAIL_ENV_PATH", env_file.name), \
                patch.dict(os.environ, {"CARR_JEV_ALERT_SINK": ""}), \
                patch.object(client.subprocess, "run", side_effect=failure):
            with self.assertRaises(client.AlarmSinkError) as caught:
                client._email_spend_alert({"message": "m", "threshold": 50})
        self.assertEqual(str(caught.exception), "mail_auth_refused")

    def test_dry_run_seam_records_both_sinks_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            sink = Path(root) / "alerts.jsonl"
            with patch.dict(os.environ, CARR_JEV_ALERT_SINK=f"dry-run:{sink}"), \
                    patch.object(client.subprocess, "run") as run:
                client._emit_spend_alert({"message": "m", "threshold": 80})
                client._email_spend_alert({"message": "m", "threshold": 80})
            run.assert_not_called()
            rows = [json.loads(line) for line in sink.read_text().splitlines()]
        self.assertEqual([r["sink"] for r in rows], ["notification", "mail"])


class ChangeTollsDedupeTests(unittest.TestCase):
    """pre-push asked the same diff twice per push (owed, then verify) and again
    on every re-push. One paid question per distinct diff per day now."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("tolls_under_test", REPO / "ops" / "jev_change_tolls.py")
        self.tolls = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tolls)
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(self.tolls, "CACHE_PATH", str(root / "tolls-cache.json")))
        self.asked = []
        tolls = self.tolls

        class Client:
            noul = staticmethod(client.noul)

            @staticmethod
            def ask(state, questions, **kwargs):
                self_ref.asked.append(state)
                return {"answers": {name: {"type": "noul", "noul": 0.9 if name == "gate_rebless" else 0.1}
                                    for name in tolls.TOLLS}}
        self_ref = self
        self.enterContext(patch.object(self.tolls, "_client", lambda: Client))

    def test_same_diff_is_asked_once_and_a_new_diff_is_asked_again(self):
        state = {"files": ["hooks/x.py"], "behind_main": 0}
        first = self.tolls.owed(state)
        second = self.tolls.owed(dict(state))
        self.assertEqual(first, second)
        self.assertEqual(len(self.asked), 1)
        self.tolls.owed({"files": ["hooks/y.py"], "behind_main": 0})
        self.assertEqual(len(self.asked), 2)

    def test_cache_expires_after_a_day(self):
        state = {"files": ["hooks/x.py"]}
        self.tolls.owed(state)
        with patch.object(self.tolls.time, "time", return_value=self.tolls.time.time() + 86_401):
            self.tolls.owed(state)
        self.assertEqual(len(self.asked), 2)

    def test_an_injected_client_bypasses_the_cache(self):
        state = {"files": ["hooks/x.py"]}
        self.tolls.owed(state)
        self.tolls.owed(state, client=self.tolls._client())
        self.assertEqual(len(self.asked), 2)


class WorkerBreakerTests(Harness):
    def worker(self, failures):
        calls = []

        def runner(argv, **kwargs):
            mode = json.loads(argv[3]).get("transport_mode")
            calls.append(mode)
            if mode == "cache_only":
                return subprocess.CompletedProcess(argv, 1, "", '{"error":"jev_cache_miss"}')
            if failures:
                return subprocess.CompletedProcess(argv, 1, "", '{"error":"jev_upstream_failed"}')
            return subprocess.CompletedProcess(argv, 0, json.dumps({**ANSWER, "ok": True,
                                                                    "receipt_id": "w"}), "")
        return calls, runner

    def ask_worker(self, text, runner):
        with patch.object(client, "read_api_key", return_value="offline-fixture"), \
                patch.dict(os.environ, CARR_JEV_IN_HOOK="0"):
            return client.ask(text, {"q": client.noul("Fixture")}, calls_log=str(self.log),
                              cache_ttl_seconds=0, caller="hook_site", session_id="sess-1",
                              server_runner=runner)

    def test_a_failing_worker_is_paid_once_then_skipped_for_the_breaker_window(self):
        calls, runner = self.worker(failures=True)
        self.ask_worker("one", runner)
        self.assertEqual(calls.count("paid_once"), 1)
        self.ask_worker("two", runner)
        self.ask_worker("three", runner)
        self.assertEqual(calls.count("paid_once"), 1, "breaker open: no doomed Worker attempts")
        day = "2026-10-04"
        db = client.sqlite3.connect(str(self.log) + ".daily-cap.sqlite3")
        attempts = db.execute("SELECT attempts FROM daily_cap WHERE day=?", (day,)).fetchone()[0]
        db.close()
        self.assertEqual(attempts, 4, "one doomed Worker try, then one reservation per call")


# Files that make up the transport itself, or call it only through a site
# that is registered. Anything else that reaches a paid call must be listed.
INFRASTRUCTURE = {"ops/typesafe_client.py", "ops/jev_judge.py", "tools/judge/interface.py"}


def paid_call_sources():
    """Every tracked non-test Python file that invokes the Jev client or judge()."""
    tracked = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True,
                             text=True, check=True).stdout.split()
    found = set()
    for rel in tracked:
        if (rel in INFRASTRUCTURE or "selftest" in rel or "/fixtures/" in rel or rel.startswith("evals/")
                or "/tests/" in rel or rel.startswith("tests/") or Path(rel).name.startswith("test_")):
            continue
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if not re.search(r"typesafe|jev_judge", text):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # Over-inclusive on purpose: any .ask()/.judge()/.server_ask() call in
            # a file that loads the client or the judge counts as a paid path.
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("ask", "_ask_jev", "judge", "server_ask")):
                found.add(rel)
                break
    return found


class RegistryCoverageTests(unittest.TestCase):
    def test_production_registry_is_valid(self):
        registry = client.load_call_sites(REGISTRY_PATH)
        self.assertLessEqual(registry["hourly_paid_call_cap"] * 24,
                             client.JEV_COST_CONFIG["daily_paid_call_cap"] * 24)
        total = sum(e["daily_budget"] for e in registry["sites"].values())
        self.assertLessEqual(total, client.JEV_COST_CONFIG["daily_paid_call_cap"],
                             "site daily budgets must fit inside the global daily cap")

    def test_every_paid_call_source_is_registered(self):
        registry = client.load_call_sites(REGISTRY_PATH)
        registered = {s for e in registry["sites"].values() for s in e["sources"]}
        missing = sorted(paid_call_sources() - registered)
        self.assertEqual(missing, [], "a new paid Jev call path needs an entry in "
                         "ops/config/jev-call-sites.v1.json (caller, trigger, budgets, owner, value)")

    def test_every_registered_source_exists(self):
        registry = client.load_call_sites(REGISTRY_PATH)
        for entry in registry["sites"].values():
            for rel in entry["sources"]:
                self.assertTrue((REPO / rel).is_file(), f"{entry['caller']}: {rel} does not exist")

    def test_registered_caller_names_match_their_source_module(self):
        registry = client.load_call_sites(REGISTRY_PATH)
        for caller, entry in registry["sites"].items():
            if caller.endswith("*"):
                continue
            stems = {Path(s).stem for s in entry["sources"]}
            named = caller in stems or any(
                f'"{caller}"' in (REPO / s).read_text(encoding="utf-8", errors="replace")
                for s in entry["sources"])
            self.assertTrue(named, f"{caller}: the caller is the calling module's file name, "
                                   "or an explicit caller= string in one of its sources")


if __name__ == "__main__":
    unittest.main()
