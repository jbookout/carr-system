#!/usr/bin/env python3
"""File-level acceptance for the sanctioned append-only shadow ledger door."""
from __future__ import annotations

import importlib.util
import json
import tempfile
from argparse import Namespace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "ledger", REPO / "ops/rule-delivery-shadow-ledger.py")
assert spec and spec.loader
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)

identity = {"policy_digest": "1" * 64, "map_digest": "2" * 64,
            "source_digest": "3" * 64}
observation = {"ts": "2026-08-26T01:00:00Z", "hook": "rule-pack-drift-gate",
               "session": "s1", "mode": "shadow", "loaded": [],
               "would_omit_count": 4, "missed_rules": ["deadbeef"]}
raw = json.dumps(observation, separators=(",", ":"))
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "shadow.jsonl"
    path.write_text(raw + "\n", encoding="utf-8")
    event_id = ledger.list_findings(path)[0]["event_id"]
    args = Namespace(event_id=event_id, disposition="explained", owner="owner-a",
                     remedy_ref="WR-000007", evidence_ref="INC-1",
                     rollback_ref="retain raw")
    receipt = ledger.add_disposition(path, args)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == raw, "raw observation was rewritten"
    assert receipt["event_id"] == event_id and receipt["owner"] == "owner-a"
    assert len(lines) == 2, "disposition was not one append"
    try:
        ledger.add_disposition(path, args)
    except RuntimeError as exc:
        assert "already has a disposition" in str(exc)
    else:
        raise AssertionError("duplicate disposition was accepted")

    def add_epoch(rows):
        allowed, reason = ledger.can_start_epoch(rows, identity)
        assert allowed, reason
        return ledger.make_epoch(identity, owner="owner-a", reason="initial epoch",
                                 remedy_ref="WR-000007", rollback_ref="retain prior")

    epoch = ledger.append(path, add_epoch)
    assert epoch["record_type"] == "epoch"
    assert path.read_text(encoding="utf-8").splitlines()[0] == raw

wrapper = (REPO / "bin/rule-delivery-shadow-ledger-prod.sh").read_text()
assert "carr_load_routine_db_env CARR_DB_JOBS_URL" in wrapper
assert "CARR_DB_JOBS_URL=\"$CARR_DB_JOBS_URL\"" in wrapper

print("rule-delivery-shadow-ledger-selftest: 9 cases passed")
