#!/usr/bin/env python3
"""Offline acceptance for the labeled Jev boundary replay."""
import importlib.util
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("jev_boundary_replay", repo / "ops/jev-boundary-replay.py")
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
rows = module.labeled_replay()
by_id = {row["id"]: row for row in rows}
assert len(rows) >= 8
for row in rows:
    assert row["after"]["detected"] >= row["before"]["detected"], row
    assert row["after"]["false_positives"] <= row["before"]["false_positives"], row
assert by_id["unrelated_prompt"]["after"]["calls"] == 0
assert by_id["unrelated_prompt"]["after"]["false_positives"] == 0
assert by_id["failed_test_injection"]["after"]["calls"] == 1
assert set(by_id["failed_test_injection"]["after"]["found"]) == {"security", "failure", "ci_failed"}
assert by_id["ordinary_diff_stop"]["before"]["calls"] == 2
assert by_id["ordinary_diff_stop"]["after"]["calls"] == 1
assert by_id["repeated_stop"]["after"]["calls"] == 0
spend = module.spend_replay()
assert set(spend) == {"2026-09-26", "2026-09-27"}
assert all(day["before_input_tokens"] > day["after_input_tokens_conservative"] for day in spend.values())
print("jev-boundary-replay-selftest: labeled cases and recorded spend pass")
