#!/usr/bin/env python3
"""Deterministic contract tests for the Report Card v2 runner.

The rubric is deliberately still DRAFT. These tests do not grade the system or
schedule an audit; they protect the rails that keep an eventual dry run from
silently grading malformed or incomplete evidence.
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import ModuleType
from unittest import mock


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(REPO, "tools", "report-card.py")
SPEC = importlib.util.spec_from_file_location("report_card", MODULE_PATH)
report_card = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report_card)

RULES_METRIC_PATH = os.path.join(REPO, "ops", "report-card", "rules-live-metric.py")
RULES_METRIC_SPEC = importlib.util.spec_from_file_location("rules_live_metric", RULES_METRIC_PATH)
rules_live_metric = importlib.util.module_from_spec(RULES_METRIC_SPEC)
RULES_METRIC_SPEC.loader.exec_module(rules_live_metric)

EVIDENCE_METRIC_PATH = os.path.join(REPO, "ops", "report-card", "evidence-metric.py")
EVIDENCE_METRIC_SPEC = importlib.util.spec_from_file_location(
    "evidence_metric", EVIDENCE_METRIC_PATH)
evidence_metric = importlib.util.module_from_spec(EVIDENCE_METRIC_SPEC)
EVIDENCE_METRIC_SPEC.loader.exec_module(evidence_metric)


def minimal_spec(metric=None):
    metric = metric or {
        "key": "healthy_metric",
        "category": "core",
        "dimension": "performance",
        "question": "Does the core report a healthy value?",
        "source_command": "printf 1",
        "measured_from": "test fixture",
        "threshold": {"polarity": "higher_is_worse", "warn": 2, "fail": 3},
        "bound_action": "Investigate the core evidence source.",
        "added_on": "2026-08-11",
    }
    return {
        "dimension": [{"key": "performance", "sourced_from": "code"}],
        "lane": [{"key": "core"}],
        "category": [{
            "key": "core", "label": "Core", "lanes": ["core"],
            "kind": "structural", "dimensions": ["performance"],
            "trend_carries": False, "added_on": "2026-08-11",
        }],
        "metric": [metric],
    }


class ReportCardContractTest(unittest.TestCase):
    def test_checked_rubric_has_no_structural_errors(self):
        errors, _ = report_card.validate(report_card.load())
        self.assertEqual(errors, [])

    def test_collected_metric_requires_threshold_table(self):
        for replacement in (None, {}):
            with self.subTest(threshold=replacement):
                spec = minimal_spec()
                if replacement is None:
                    del spec["metric"][0]["threshold"]
                else:
                    spec["metric"][0]["threshold"] = replacement
                errors, _ = report_card.validate(spec)
                self.assertTrue(any("needs a non-empty threshold" in error
                                    for error in errors), errors)

    def test_collected_metric_threshold_requires_polarity(self):
        spec = minimal_spec()
        del spec["metric"][0]["threshold"]["polarity"]
        errors, _ = report_card.validate(spec)
        self.assertTrue(any("needs polarity" in error for error in errors), errors)

    def test_declared_gap_may_omit_threshold(self):
        spec = minimal_spec()
        spec["metric"][0]["source_command"] = ""
        del spec["metric"][0]["threshold"]
        errors, warnings = report_card.validate(spec)
        self.assertFalse(any("threshold" in error for error in errors), errors)
        self.assertTrue(any("declared gap" in warning for warning in warnings), warnings)

    def test_blocking_gap_requires_reason_and_cannot_be_collected(self):
        spec = minimal_spec()
        spec["metric"][0]["blocking_gap"] = True
        errors, _ = report_card.validate(spec)
        self.assertTrue(any("cannot also be a blocking gap" in error for error in errors), errors)
        self.assertTrue(any("needs gap_reason" in error for error in errors), errors)

    def test_blocking_declared_gap_prevents_green_run(self):
        metric = minimal_spec()["metric"][0]
        metric["source_command"] = ""
        metric.pop("threshold")
        metric["blocking_gap"] = True
        metric["gap_reason"] = "connector parity identity is not provisioned"
        spec = minimal_spec(metric)
        with tempfile.TemporaryDirectory() as root:
            scratch = os.path.join(root, "out", "report-card")
            os.makedirs(scratch)
            for label, name in (("health", "health.txt"), ("check", "check.txt")):
                with open(os.path.join(scratch, name), "w") as fh:
                    fh.write("evidence\n" + report_card.EVIDENCE_MARKERS[label] + "\n")
            with mock.patch.object(report_card, "REPO", root):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(report_card.run(spec, skip_evidence=True), 1)

    def test_run_mode_validates_before_executing_a_metric(self):
        spec = minimal_spec()
        del spec["metric"][0]["threshold"]["polarity"]
        with mock.patch.object(report_card, "load", return_value=spec), \
             mock.patch.object(report_card, "run") as run, \
             mock.patch.object(sys, "argv", ["report-card.py", "--run"]):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(report_card.main(), 1)
            run.assert_not_called()

    def test_threshold_direction_does_not_invert_a_success_metric(self):
        higher_is_worse = {"polarity": "higher_is_worse", "warn": 3, "fail": 5}
        higher_is_better = {"polarity": "higher_is_better", "warn": 80, "fail": 50}
        self.assertEqual(report_card._grade("5", higher_is_worse), "FAIL")
        self.assertEqual(report_card._grade("3", higher_is_worse), "WARN")
        self.assertEqual(report_card._grade("1", higher_is_worse), "OK")
        self.assertEqual(report_card._grade("50", higher_is_better), "FAIL")
        self.assertEqual(report_card._grade("80", higher_is_better), "WARN")
        self.assertEqual(report_card._grade("100", higher_is_better), "OK")

    def test_evidence_bootstrap_failure_stops_before_metrics(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(report_card, "REPO", root), \
                 mock.patch.object(report_card, "capture", return_value=(127, "", "not found")) as capture:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(report_card.run(minimal_spec()), 1)
                # Both evidence producers are captured for a useful failure
                # report; no metric source is allowed to run afterwards.
                self.assertEqual(capture.call_count, 2)

    def test_rc1_fatal_stdout_is_not_completed_evidence(self):
        fatal = "Traceback (most recent call last):\nRuntimeError: bootstrap failed\n"
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(report_card, "REPO", root), \
                 mock.patch.object(report_card, "capture", return_value=(1, fatal, "")) as capture:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(report_card.run(minimal_spec()), 1)
                self.assertEqual(capture.call_count, 2)

    def test_evidence_capture_exception_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(report_card, "REPO", root), \
                 mock.patch.object(report_card.subprocess, "run",
                                   side_effect=RuntimeError("subprocess bootstrap failed")):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(report_card.run(minimal_spec()), 1)

    def test_rc1_recognized_findings_with_terminal_markers_are_evidence(self):
        captures = [
            (1, "health finding\n" + report_card.EVIDENCE_MARKERS["health"] + "\n", ""),
            (1, "drift finding\n" + report_card.EVIDENCE_MARKERS["check"] + "\n", ""),
            (0, "1\n", ""),
        ]
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(report_card, "REPO", root), \
                 mock.patch.object(report_card, "capture", side_effect=captures):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(report_card.run(minimal_spec()), 0)

    def test_cache_at_exactly_26_hours_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            scratch = os.path.join(root, "out", "report-card")
            os.makedirs(scratch)
            health = os.path.join(scratch, "health.txt")
            check = os.path.join(scratch, "check.txt")
            with open(health, "w") as fh:
                fh.write("health evidence\n" + report_card.EVIDENCE_MARKERS["health"] + "\n")
            with open(check, "w") as fh:
                fh.write("check evidence\n" + report_card.EVIDENCE_MARKERS["check"] + "\n")
            now = 1_800_000_000.0
            old = now - 26 * 3600
            os.utime(health, (now, now))
            os.utime(check, (old, old))
            with mock.patch.object(report_card, "REPO", root), \
                 mock.patch.object(report_card.time, "time", return_value=now):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(report_card.run(minimal_spec(), skip_evidence=True), 1)

    def test_invalid_integrity_sample_blocks_the_run(self):
        metric = minimal_spec()["metric"][0]
        metric["independent_command"] = "printf 1"
        spec = minimal_spec(metric)
        with tempfile.TemporaryDirectory() as root:
            scratch = os.path.join(root, "out", "report-card")
            os.makedirs(scratch)
            for label, name in (("health", "health.txt"), ("check", "check.txt")):
                with open(os.path.join(scratch, name), "w") as fh:
                    fh.write("evidence\n" + report_card.EVIDENCE_MARKERS[label] + "\n")
            calls = [
                (0, "1\n", ""),       # metric primary
                (0, "1\n", ""),       # sampled primary
                (127, "", "not found"), # sampled independent path
            ]
            with mock.patch.object(report_card, "REPO", root), \
                 mock.patch.object(report_card, "capture", side_effect=calls):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(report_card.run(spec, skip_evidence=True), 1)

    def test_rules_context_connector_missing_token_is_unknown(self):
        with tempfile.TemporaryDirectory() as root, \
             mock.patch.dict(os.environ, {
                 "CARR_MCP_PROBE_TOKEN": "",
                 "CARR_MCP_ENV": os.path.join(root, "missing.env"),
             }, clear=False):
            with self.assertRaisesRegex(RuntimeError, "PROBE_TOKEN unavailable"):
                rules_live_metric.connector_shared_count()

    def test_rules_context_connector_parses_authenticated_shared_count(self):
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps({
                "ok": True,
                "identity": {"agent_principal_id": "smoke-probe"},
                "shared_rules": [{"id": "one"}, {"id": "two"}],
                "personal_rules": [],
            })}]},
        })
        completed = ModuleType("completed")
        completed.returncode = 0
        completed.stdout = payload + "\nCARR_HTTP_STATUS:200\n"
        completed.stderr = ""
        with mock.patch.dict(os.environ, {"CARR_MCP_PROBE_TOKEN": "fixture-token"}), \
             mock.patch.object(rules_live_metric.subprocess, "run", return_value=completed):
            self.assertEqual(rules_live_metric.connector_shared_count(), 2)

    def test_rules_context_bearer_never_enters_argv_or_diagnostics(self):
        sentinel = "SENTINEL-BEARER-MUST-NOT-LEAK"
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps({
                "ok": True, "shared_rules": [], "personal_rules": [],
            })}]},
        })
        completed = ModuleType("completed")
        completed.returncode = 0
        completed.stdout = payload + "\nCARR_HTTP_STATUS:200\n"
        completed.stderr = ""

        def fake_run(argv, **kwargs):
            self.assertNotIn(sentinel, " ".join(argv))
            self.assertIn(sentinel, kwargs["input"])
            self.assertIn("\nsilent\nshow-error\nfail-with-body\n", "\n" + kwargs["input"])
            self.assertNotIn(" = true", kwargs["input"])
            return completed

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"CARR_MCP_PROBE_TOKEN": sentinel}), \
             mock.patch.object(rules_live_metric.subprocess, "run", side_effect=fake_run), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(rules_live_metric.connector_shared_count(), 0)
        self.assertNotIn(sentinel, completed.stdout)
        self.assertNotIn(sentinel, completed.stderr)
        self.assertNotIn(sentinel, stdout.getvalue())
        self.assertNotIn(sentinel, stderr.getvalue())

    def test_rules_context_timeout_diagnostic_redacts_process_fields(self):
        sentinel = "SENTINEL-TIMEOUT-SECRET"
        timeout = subprocess.TimeoutExpired(
            ["curl", "--config", "-"], 40,
            output=f"response echoed {sentinel}", stderr=f"failure echoed {sentinel}")
        with mock.patch.dict(os.environ, {"CARR_MCP_PROBE_TOKEN": sentinel}), \
             mock.patch.object(rules_live_metric.subprocess, "run", side_effect=timeout):
            with self.assertRaisesRegex(RuntimeError, "timed out") as caught:
                rules_live_metric.connector_shared_count()
        self.assertNotIn(sentinel, str(caught.exception))

    def _assert_rules_connector_unknown(self, completed, expected):
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"CARR_MCP_PROBE_TOKEN": "fixture-token"}), \
             mock.patch.object(rules_live_metric.subprocess, "run", return_value=completed), \
             mock.patch.object(sys, "argv", ["rules-live-metric.py", "--primary"]), \
             redirect_stderr(stderr):
            self.assertEqual(rules_live_metric.main(), 2)
        self.assertIn("UNKNOWN rules-context parity", stderr.getvalue())
        self.assertIn(expected, stderr.getvalue())

    def test_rules_context_http_401_and_403_are_distinct_unknowns(self):
        for status, classification in ((401, "authentication"), (403, "authorization")):
            with self.subTest(status=status):
                completed = ModuleType("completed")
                completed.returncode = 22
                completed.stdout = (json.dumps({"error": "denied"})
                                    + f"\nCARR_HTTP_STATUS:{status}\n")
                completed.stderr = f"curl: (22) HTTP {status}"
                self._assert_rules_connector_unknown(completed, classification)

    def test_rules_context_http_000_is_transport_unknown(self):
        completed = ModuleType("completed")
        completed.returncode = 7
        completed.stdout = "\nCARR_HTTP_STATUS:000\n"
        completed.stderr = "curl: (7) connection refused"
        self._assert_rules_connector_unknown(completed, "transport failed (rc=7)")

    def test_rules_context_jsonrpc_error_is_unknown(self):
        completed = ModuleType("completed")
        completed.returncode = 0
        completed.stdout = (json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32601, "message": "unknown tool"},
        }) + "\nCARR_HTTP_STATUS:200\n")
        completed.stderr = ""
        self._assert_rules_connector_unknown(completed, "JSON-RPC error")

    def test_rules_context_error_output_redacts_bearer_sentinel(self):
        sentinel = "SENTINEL-ERROR-MUST-NOT-LEAK"
        completed = ModuleType("completed")
        completed.returncode = 0
        completed.stdout = (json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32000, "message": f"echo {sentinel}"},
        }) + "\nCARR_HTTP_STATUS:200\n")
        completed.stderr = f"server stderr echoed {sentinel}"
        stdout = io.StringIO()
        stderr = io.StringIO()

        def fake_run(argv, **kwargs):
            self.assertNotIn(sentinel, " ".join(argv))
            return completed

        with mock.patch.dict(os.environ, {"CARR_MCP_PROBE_TOKEN": sentinel}), \
             mock.patch.object(rules_live_metric.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(sys, "argv", ["rules-live-metric.py", "--primary"]), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(rules_live_metric.main(), 2)
        self.assertNotIn(sentinel, stdout.getvalue())
        self.assertNotIn(sentinel, stderr.getvalue())
        self.assertIn("[REDACTED]", stderr.getvalue())

    def test_rules_context_mcp_iserror_is_unknown(self):
        completed = ModuleType("completed")
        completed.returncode = 0
        completed.stdout = (json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "result": {"isError": True, "content": [
                {"type": "text", "text": "standing-context unavailable"},
            ]},
        }) + "\nCARR_HTTP_STATUS:200\n")
        completed.stderr = ""
        self._assert_rules_connector_unknown(completed, "MCP tool error")

    def test_rules_context_direct_store_unavailable_is_unknown(self):
        common = ModuleType("exporters.common")
        common.connect = mock.Mock(side_effect=SystemExit("DATABASE_URL missing"))
        targets = ModuleType("exporters.targets")
        targets._fetch_rules = mock.Mock()
        with mock.patch.dict(sys.modules, {
            "exporters.common": common,
            "exporters.targets": targets,
        }):
            with self.assertRaisesRegex(RuntimeError, "rule store unavailable"):
                rules_live_metric.direct_store_count_primary()

    def test_rules_context_parity_uses_connector_and_store_counts(self):
        with mock.patch.object(rules_live_metric, "connector_shared_count", return_value=144), \
             mock.patch.object(rules_live_metric, "direct_store_count_primary", return_value=143):
            self.assertEqual(rules_live_metric.measure(independent=False), 1)
        with mock.patch.object(rules_live_metric, "connector_shared_count", return_value=144), \
             mock.patch.object(rules_live_metric, "direct_store_count_independent", return_value=144):
            self.assertEqual(rules_live_metric.measure(independent=True), 0)

    def test_export_deadman_unreadable_is_unknown_not_zero(self):
        health = (
            "Export register — a target nobody registered is not a target that failed\n"
            "  ⚠︎ register UNREADABLE — cannot classify any export target\n"
            "  OK R2 archive         healthy\n")
        with self.assertRaisesRegex(RuntimeError, "unreadable or unknown"):
            evidence_metric.export_deadman_failures(health)

    def test_export_deadman_counts_actual_failure_rows(self):
        health = (
            "Export register — a target nobody registered is not a target that failed\n"
            "  ⚠︎ STALE alpha (last ok yesterday)\n"
            "  ⚠︎ NEVER RAN beta\n"
            "  -- NOT A TARGET old-key\n"
            "  OK R2 archive         healthy\n")
        self.assertEqual(evidence_metric.export_deadman_failures(health), 2)

    def test_export_deadman_missing_section_is_unknown(self):
        with self.assertRaisesRegex(RuntimeError, "missing or unterminated"):
            evidence_metric.export_deadman_failures("CARR_EVIDENCE_COMPLETE health-check/v1\n")

    def test_doctrine_unreadable_is_unknown_not_zero(self):
        health = (
            "doctrine store\n"
            "  ⚠︎ store UNREADABLE — cannot say whether doctrine is healthy\n"
            "CARR_EVIDENCE_COMPLETE health-check/v1\n")
        with self.assertRaisesRegex(RuntimeError, "unreadable"):
            evidence_metric.doctrine_stale_sections(health)

    def test_doctrine_stale_zero_requires_explicit_row(self):
        health = (
            "doctrine store\n"
            "  OK stale-sections       0 past review_after\n"
            "CARR_EVIDENCE_COMPLETE health-check/v1\n")
        self.assertEqual(evidence_metric.doctrine_stale_sections(health), 0)

    def test_code_drift_counts_drift_and_missing_in_code_section_only(self):
        check = (
            "== Code drift (repo vs live vault copy, per manifest.tsv) ==\n"
            "  OK      one.py\n"
            "  DRIFT   two.py\n"
            "  MISSING three.py\n"
            "== Video pipeline drift (repo vs runtime) ==\n"
            "  MISSING video.py\n")
        self.assertEqual(evidence_metric.code_drift_rows(check), 2)

    def test_code_drift_missing_section_is_unknown(self):
        with self.assertRaisesRegex(RuntimeError, "missing evidence section"):
            evidence_metric.code_drift_rows("CARR_EVIDENCE_COMPLETE check/v1\n")

    def test_facade_parsers_are_section_scoped_and_failure_sensitive(self):
        health = (
            "Façade check (rule 28) — now — outputs, not schedules\n"
            "  OK healthy             0.0d old\n"
            "  MISSING absent         no file\n"
            "  ⚠︎ stale               STALE 10.0d old\n"
            "  ⚠︎ behind              BEHIND inputs: source.json\n"
            "  -- GATED gated         not runnable\n"
            "Schedule drift — did the job run WHEN scheduled\n"
            "  ⚠︎ unrelated           STALE 99.0d old\n")
        self.assertEqual(evidence_metric.facade_findings(health), 3)
        self.assertEqual(evidence_metric.facade_findings_independent(health), 3)

    def test_facade_independent_rejects_unknown_warning_state(self):
        health = (
            "Façade check (rule 28) — now — outputs, not schedules\n"
            "  ⚠︎ odd                 NOVEL STATE\n"
            "Schedule drift — did the job run WHEN scheduled\n")
        with self.assertRaisesRegex(RuntimeError, "unknown status"):
            evidence_metric.facade_findings_independent(health)

    def test_facade_missing_section_is_unknown(self):
        with self.assertRaisesRegex(RuntimeError, "missing evidence section"):
            evidence_metric.facade_findings("CARR_EVIDENCE_COMPLETE health-check/v1\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
