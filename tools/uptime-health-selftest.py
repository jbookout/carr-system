#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import unittest
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location("uptime_health", Path(__file__).with_name("uptime_health.py"))
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NOW = datetime(2026, 10, 5, 19, 0, 0, tzinfo=timezone.utc)


class UptimeHealthTests(unittest.TestCase):
    def test_monitor_failure_has_one_loop_and_verified_recovery_closes_it(self):
        root = Path(__file__).resolve().parents[1]
        state = root / "out/_to_delete" / f"uptime-response-{uuid.uuid4()}.json"
        calls = []

        def verb(name, payload):
            calls.append((name, payload))
            if name == "add-loop":
                return {"ok": True, "loop_id": "fixture-monitor-loop"}
            if name == "read-loop":
                return {"loop_id": "fixture-monitor-loop", "version": 1, "status": "open"}
            return {"ok": True}

        for _ in range(2):
            self.assertEqual(module.reconcile_monitor(True, False, state, verb), "open")
        self.assertEqual([name for name, _ in calls], ["add-loop"])
        self.assertEqual(module.reconcile_monitor(False, False, state, verb), "open")
        self.assertEqual(module.reconcile_monitor(False, True, state, verb), "clear")
        self.assertEqual([name for name, _ in calls], ["add-loop", "read-loop", "close-loop"])
        self.assertIn("first observed", calls[0][1]["body"])
        self.assertIn("recovered", calls[-1][1]["outcome"])

    def test_monitor_loop_replays_after_lost_acknowledgement_and_refuses_corrupt_state(self):
        root = Path(__file__).resolve().parents[1]
        state = root / "out/_to_delete" / f"uptime-response-{uuid.uuid4()}.json"
        calls = []

        def lost_ack(name, payload):
            calls.append(payload)
            if len(calls) == 1:
                raise TimeoutError("fixture credential must never appear")
            self.assertEqual(payload, calls[0])
            return {"ok": True, "loop_id": "fixture-loop"}

        self.assertEqual(module.reconcile_monitor(True, False, state, lost_ack), "error")
        self.assertEqual(module.reconcile_monitor(True, False, state, lost_ack), "open")
        state.write_text("not-json")
        self.assertEqual(module.reconcile_monitor(True, False, state, lost_ack), "error")
        self.assertEqual(len(calls), 2)

    def test_health_cli_reads_fixture_status_and_records_a_bound_finding(self):
        payload = {
            "schema": "carr-uptime.v1", "ok": True,
            "checked_at": datetime.now(timezone.utc).isoformat(), "failures": 0,
            "pending_records": 0, "pending_alerts": 0, "active_incident": None,
            "configuration_missing": [], "alert_error": None, "record_error": None,
            "checks": [{"name": name, "ok": True} for name in (
                "api-release", "app-release", "verb-round-trip")],
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if payload["ok"] else 503)
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        root = Path(__file__).resolve().parents[1]
        destination = root / "out" / "_to_delete" / f"uptime-health-fixture-{uuid.uuid4()}"
        destination.mkdir(parents=True)
        try:
            for healthy in (True, False):
                payload["ok"] = healthy
                payload["failures"] = 0 if healthy else 3
                output = destination / "findings.json"
                result = subprocess.run([
                    sys.executable, str(root / "tools/health-check.py"), "--section", "uptime",
                    "--findings-json", str(output),
                ], env={**os.environ, "CARR_UPTIME_STATUS_URL": f"http://127.0.0.1:{server.server_port}/healthz",
                        "CARR_UPTIME_RESPONSE_STATE": str(destination / "response.json")},
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0 if healthy else 1, result.stderr)
                self.assertIn("on breach:", result.stdout)
                findings = json.loads(output.read_text())["findings"]
                self.assertEqual(len(findings), 0 if healthy else 1)
                if not healthy:
                    self.assertEqual(findings[0]["key"], "production_uptime")
                    self.assertTrue(findings[0]["hard_error"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_output_has_bound_response_for_down_wrong_shape_slow_and_recovered(self):
        good = {
            "schema": "carr-uptime.v1", "ok": True, "checked_at": "2026-10-05T18:59:00Z",
            "failures": 0, "active_incident": None, "pending_records": 0, "pending_alerts": 0,
            "configuration_missing": [], "alert_error": None, "record_error": None,
            "checks": [{"name": name, "ok": True, "reason": "ok"} for name in (
                "api-release", "app-release", "verb-round-trip")],
        }
        cases = [
            (good, False, "OK"),
            ({**good, "ok": False, "failures": 3, "active_incident": "fixture-incident"}, True, "WARN"),
            ({**good, "pending_records": 1}, True, "WARN"),
            ({**good, "checked_at": "2026-10-05T18:55:00Z"}, True, "WARN"),
            ({"ok": True}, True, "WARN"),
            ({**good, "checks": []}, True, "WARN"),
        ]
        for payload, failed, prefix in cases:
            with self.subTest(payload=payload):
                line, observed = module.row(read=lambda: payload, now=NOW, respond=lambda *_: "none")
                self.assertEqual(observed, failed)
                self.assertTrue(line.startswith(prefix), line)
                for clause in ("on breach:", "loop", "owner orchestrator", "fix:", "verify:", "auto-clear:"):
                    self.assertIn(clause, line)

        def slow():
            raise TimeoutError("fixture-secret must never appear")
        line, failed = module.row(read=slow, now=NOW, respond=lambda *_: "none")
        self.assertTrue(failed)
        self.assertNotIn("fixture-secret", line)
        self.assertIn("unreachable", line)


if __name__ == "__main__":
    unittest.main()
