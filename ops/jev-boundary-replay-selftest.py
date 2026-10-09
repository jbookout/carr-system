#!/usr/bin/env python3
"""Offline acceptance for the labeled Jev boundary replay."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

repo = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("jev_boundary_replay", repo / "ops/jev-boundary-replay.py")
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fixture = json.loads(module.FIXTURE.read_text())
rows = module.labeled_replay(verify_sources=False)
by_id = {row["id"]: row for row in rows}
assert len(rows) >= 5
for row in rows:
    assert row["after"]["detected"] >= row["before"]["detected"], row
    assert row["after"]["false_positives"] <= row["before"]["false_positives"], row
# Triage is deterministic here and #1528 retired the injection screen: no paid call.
assert by_id["failed_test_injection"]["after"]["calls"] == 0
assert set(by_id["failed_test_injection"]["after"]["found"]) == {"failure", "ci_failed"}
for case_id in ("unsupported_stop", "ordinary_diff_stop"):
    assert "done_review" in by_id[case_id]["after"]["found"], by_id[case_id]
    assert "done_unsupported" not in by_id[case_id]["after"]["found"], by_id[case_id]
assert by_id["ordinary_diff_stop"]["before"]["calls"] == 0  # legacy dispatch uses current predicates
assert by_id["ordinary_diff_stop"]["after"]["calls"] == 0
assert by_id["repeated_stop"]["after"]["calls"] == 0
with tempfile.TemporaryDirectory(prefix="jev-replay-source-") as directory:
    root = Path(directory)
    source = root / "source.jsonl"
    line = json.dumps({"fixture": "source evidence"})
    source.write_text(line + "\n")
    case = dict(next(case for case in fixture["cases"] if case["family"] == "tool"))
    case.update(source_log=source.name,
                source_row_sha256=hashlib.sha256(line.encode()).hexdigest())
    anchored = root / "cases.json"
    anchored.write_text(json.dumps({**fixture, "source_logs": [source.name], "cases": [case]}))
    with patch.object(module, "REPO", root), patch.object(module, "FIXTURE", anchored):
        assert module.labeled_replay()[0]["id"] == case["id"]
        (root / "out").mkdir()
        calls = [{"ts": day, "ok": True, "session": "synthetic",
                  "question_ids": [question], "usage": {"input_tokens": tokens}}
                 for day in ("2026-09-26", "2026-09-27")
                 for question, tokens in (("architecture_or_design", 100), ("failure_class", 50))]
        (root / "out/jev-calls.jsonl").write_text("\n".join(map(json.dumps, calls)) + "\n")
        spend = module.spend_replay()
        assert set(spend) == {"2026-09-26", "2026-09-27"}
        assert all(day["before_input_tokens"] == 150 and
                   day["after_input_tokens_conservative"] == 50 for day in spend.values())
        source.write_text(json.dumps({"fixture": "altered evidence"}) + "\n")
        try:
            module.labeled_replay()
        except AssertionError as error:
            assert error.args == (case["id"],)
        else:
            raise AssertionError("altered source evidence was accepted")
assert all(len(case["source_row_sha256"]) == 64 for case in fixture["cases"])
print("jev-boundary-replay-selftest: labeled cases and source-anchor refusals pass")
