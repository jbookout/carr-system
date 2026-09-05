#!/usr/bin/env python3
"""Regression tests for migrate-prod refusal escalation readback."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "migrate_prod_support", REPO / "tools" / "migrate-prod-support.py"
)
assert SPEC and SPEC.loader
support: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


def main() -> int:
    calls: list[tuple[str, dict]] = []

    def string_sequence(_door: str, verb: str, payload: dict) -> tuple[int, str, str]:
        calls.append((verb, payload))
        if verb == "add-room-turn":
            return 0, json.dumps({"seq": "42"}), ""
        if verb == "read-room":
            return 0, json.dumps({"turns": [{"seq": "42", "body": "blocked"}]}), ""
        raise AssertionError(verb)

    original = support._call_verb
    try:
        support._call_verb = string_sequence
        assert support._add_room_turn_and_readback("run.sh", "blocked") is True
        assert [verb for verb, _payload in calls] == ["add-room-turn", "read-room"]
        assert calls[0][1]["body"] == "blocked"
        assert calls[1][1] == {"room": support.ROOM, "after_seq": 41}

        calls.clear()

        def malformed(_door: str, verb: str, payload: dict) -> tuple[int, str, str]:
            calls.append((verb, payload))
            return 0, json.dumps({"seq": "not-a-sequence"}), ""

        support._call_verb = malformed
        assert support._add_room_turn_and_readback("run.sh", "blocked") is False
        assert [verb for verb, _payload in calls] == ["add-room-turn"]
    finally:
        support._call_verb = original

    print("migrate-prod support selftest: string sequence readback normalized; malformed refused")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
