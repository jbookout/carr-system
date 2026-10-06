#!/usr/bin/env python3
"""Offline Worker transport regressions; retired prompt gates must stay retired."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

REPO = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, REPO)
NOW = datetime.now(timezone.utc)


def ts(offset_seconds=0):
    return (NOW + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def _write(obj, suffix):
    fh = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
    if suffix == ".jsonl":
        for row in obj:
            fh.write(json.dumps(row) + "\n")
    else:
        json.dump(obj, fh)
    fh.close()
    return fh.name

def report(ok, label):
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    return ok

def _client():
    spec = importlib.util.spec_from_file_location(
        "tsc_server_selftest", os.path.join(REPO, "ops", "typesafe_client.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


for _name in ("CARR_JEV_OFFLINE", "CARR_HOOK_FIXTURE", "CARR_JEV_WORKER"):
    os.environ.pop(_name, None)
os.environ["CARR_JEV_JOB"] = "jev-server-receipts-selftest"

class _Proc:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err

def client_routes_through_the_worker():
    tsc = _client()
    seen = {}

    def runner(argv, **_kw):
        if json.loads(argv[3]).get("transport_mode") == "cache_only":
            return _Proc(1, "", 'TOOL ERROR {"error":"jev_cache_miss","spend_authority":"carr-jev-spend/v1"}')
        seen["verb"] = argv[2]
        seen["args"] = json.loads(argv[3])
        return _Proc(0, json.dumps({
            "ok": True, "spend_authority": "carr-jev-spend/v1", "receipt_id": "srv-1", "recorded_at": ts(0), "purpose": "call",
            "session_id": "s", "model": "jev-1.13.0", "state_sha256": "a" * 64,
            "prompt_sha256": None, "usage": {"input_tokens": 3, "output_tokens": 1},
            "answers": {"diagnosis_q": {"type": "noul", "noul": 0.7}}}))
    log = _write([], ".jsonl")
    try:
        result = tsc.ask({"x": 1}, {"diagnosis_q": tsc.noul("is it?")}, facets=["diagnosis"],
                         calls_log=log, server_runner=runner, cache_ttl_seconds=0)
        with open(log) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    finally:
        os.unlink(log)
    ok = (seen.get("verb") == "ask-jev" and seen["args"]["purpose"] == "call"
          and seen["args"]["facets"] == ["diagnosis"]
          and str(uuid.UUID(seen["args"]["idempotency_key"])) == seen["args"]["idempotency_key"]
          and result["server_receipt"]["receipt_id"] == "srv-1"
          and result["answers"]["diagnosis_q"]["noul"] == 0.7
          and result["usage"] == {"input_tokens": 3, "output_tokens": 1}
          and rows and rows[0]["server_receipt_id"] == "srv-1"
          and rows[0]["ok"] is True and rows[0]["usable"] is True)
    return report(ok, "client: ask() goes through the Worker's ask-jev verb and records the "
                      "server receipt id locally")


def malformed_server_answer_is_not_credited():
    tsc = _client()

    def runner(argv, **_kw):
        if json.loads(argv[3]).get("transport_mode") == "cache_only":
            return _Proc(1, "", 'TOOL ERROR {"error":"jev_cache_miss","spend_authority":"carr-jev-spend/v1"}')
        return _Proc(0, json.dumps({
            "ok": True, "spend_authority": "carr-jev-spend/v1", "receipt_id": "srv-malformed", "model": "jev-1.13.0",
            "usage": {"input_tokens": 3, "output_tokens": 1},
            "answers": {"q": {"type": "noul", "noul": 1.5}}}))

    log = _write([], ".jsonl")
    raised = False
    try:
        try:
            tsc.ask("synthetic", {"q": tsc.noul("Is the fixture valid?")},
                    calls_log=log, server_runner=runner)
        except tsc.TypeSafeError:
            raised = True
        with open(log) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    finally:
        os.unlink(log)
    return report(raised and len(rows) == 1 and rows[0]["ok"] is False
                  and rows[0]["schema_valid"] is False,
                  "malformed server answers retain main's schema validation and no local credit")

def server_cache_answer_preserves_no_spend():
    tsc = _client()

    def runner(argv, **_kw):
        return _Proc(0, json.dumps({
            "ok": True, "spend_authority": "carr-jev-spend/v1", "receipt_id": "srv-cached", "model": "jev-1.13.0",
            "cache_hit": True, "usage": None,
            "answers": {"q": {"type": "noul", "noul": 0.7}}}))

    log = _write([], ".jsonl")
    try:
        result = tsc.ask("synthetic", {"q": tsc.noul("Is the fixture valid?")},
                         calls_log=log, server_runner=runner)
        with open(log) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    finally:
        os.unlink(log)
    return report(result.get("cache_hit") is True and result["usage"] is None
                  and result["server_receipt"]["receipt_id"] == "srv-cached"
                  and rows[0]["cache_hit"] is True and rows[0]["ok"] is False,
                  "Worker cache hits return typed answers and a bound receipt without claiming spend")


class ReviewRegressions(unittest.TestCase):
    def test_retired_stop_and_agent_cannot_load_transcript_facet_enforcement(self):
        # Findings 1-3: malformed IDs, duplicate IDs and ambiguous anchors
        # belonged to the retired prompt-facet checks, not boundary judgments.
        for path in ("hooks/completion-evidence-gate.py", "hooks/executor-tier-gate.py"):
            with self.subTest(path=path):
                source = (Path(REPO) / path).read_text()
                self.assertFalse("from lib.jev_required_actions import" in source, path)
                self.assertFalse("from lib.jev_server_receipts import" in source, path)

    def test_stale_advisories_and_replays_have_no_server_facet_credit_path(self):
        # Findings 4-5: retain main's historical envelope fixtures, remove
        # this PR's unused server enforcement extension rather than repair it.
        source = (Path(REPO) / "lib/jev_required_actions.py").read_text()
        self.assertFalse("def binding_advisory_rows(" in source)
        self.assertFalse("def server_credited_calls(" in source)
        self.assertTrue(server_cache_answer_preserves_no_spend())

    def test_transport_has_no_unused_duplicate_reader(self):
        # Finding 8: after retirement no production caller reads these rows.
        self.assertFalse((Path(REPO) / "lib/jev_server_receipts.py").exists())


    def test_real_server_result_and_no_answer_negative(self):
        # Finding 6: assert the result/receipt, not an allowed hook which may
        # merely have returned unavailable after a fabricated prompt boundary.
        self.assertTrue(client_routes_through_the_worker())
        self.assertTrue(malformed_server_answer_is_not_credited())


if __name__ == "__main__":
    unittest.main()
