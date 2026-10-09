#!/usr/bin/env python3
"""Offline acceptance for the labeled Jev boundary replay."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

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
# Exercise source verification and spend accounting against owned logs. Other
# selftests create out/jev logs without the historical calibration rows.
with tempfile.TemporaryDirectory(prefix="jev-boundary-replay-") as directory:
    root = Path(directory)
    synthetic = json.loads(json.dumps(fixture))
    logs: dict[str, list[str]] = {path: [] for path in synthetic["source_logs"]}
    for case in synthetic["cases"]:
        row = json.dumps({"case_id": case["id"]})
        case["source_row_sha256"] = hashlib.sha256(row.encode()).hexdigest()
        logs[case["source_log"]].append(row)
    for day in ("2026-09-26", "2026-09-27"):
        for question, tokens in (("architecture_or_design", 60), ("failure_class", 40)):
            logs["out/jev-calls.jsonl"].append(json.dumps({
                "ts": day + "T12:00:00Z", "ok": True, "session": "synthetic-session",
                "question_ids": [question], "usage": {"input_tokens": tokens}}))
    for path, lines in logs.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n")
    setattr(module, "REPO", root)
    setattr(module, "FIXTURE", root / "labeled-cases.json")
    module.FIXTURE.write_text(json.dumps(synthetic))
    assert module.labeled_replay(verify_sources=True) == rows
    spend = module.spend_replay()
    assert set(spend) == {"2026-09-26", "2026-09-27"}
    for day in spend.values():
        assert day["before_calls"] == 2
        assert day["after_calls_conservative"] == 1
        assert day["before_input_tokens"] == 100
        assert day["after_input_tokens_conservative"] == 40
    case = next(case for case in synthetic["cases"] if case["family"] != "intake")
    # A valid JSON row with different bytes must still fail the recorded anchor.
    path = root / case["source_log"]
    source_row = json.dumps({"case_id": case["id"]})
    path.write_text(path.read_text().replace(source_row, json.dumps({"case_id": "changed"})))
    try:
        module.labeled_replay(verify_sources=True)
    except AssertionError as error:
        assert str(error) == case["id"], error
    else:
        raise AssertionError("changed source anchor accepted")
assert all(len(case["source_row_sha256"]) == 64 for case in fixture["cases"])
print("jev-boundary-replay-selftest: labeled cases, exact source anchors and spend accounting pass")
