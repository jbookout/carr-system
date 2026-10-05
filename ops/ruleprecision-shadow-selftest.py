#!/usr/bin/env python3
"""Shadow comparison must preserve delivery and expose no prompt content."""
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


@contextmanager
def environment(values):
    before = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def paths(root):
    return {"CARR_RULEPRECISION_SHADOW": "1",
            "CARR_RULEPRECISION_CONFIG": str(root / "config.json"),
            "CARR_RULEPRECISION_LOG": str(root / "shadow.jsonl"),
            "CARR_RULEPRECISION_STATE": str(root / "state.json")}


def config(root):
    row = {"schema": "ruleprecision/v1", "candidate": "selftest",
           "active_ids": ["4a53ff82", "7e9739f2", "ffffffff"],
           "boot_ids": ["7e9739f2"], "corpus_digest": "sha256:" + "a" * 64,
           "boot_digest": "sha256:" + "b" * 64,
           "selector": {"refine": True, "add_actions": True, "add_ids": ["4a53ff82"]}}
    (root / "config.json").write_text(json.dumps(row), encoding="utf-8")
    return row


def payload():
    return {"hook_event_name": "PreToolUse", "session_id": "private-session-marker",
            "tool_use_id": "private-tool-marker", "tool_name": "Edit",
            "tool_input": {"file_path": "private-client-marker/file.py",
                           "old_string": "private-secret-marker", "new_string": "next"}}


def observe_parallel(argument):
    root, index = argument
    from lib.ruleprecision_shadow import observe
    data = payload()
    data["tool_use_id"] = str(index)
    with environment(paths(Path(root))):
        observe(REPO, data, None)


def rows(root):
    return [json.loads(line) for line in (root / "shadow.jsonl").read_text().splitlines()]


def main():
    spec = importlib.util.spec_from_file_location(
        "precision_hook_selftest", REPO / "hooks/rule-pack-preuse-reselection.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    sentinel = {"hookSpecificOutput": {"additionalContext": "unchanged baseline"}}
    calls = []

    def today(data, **kwargs):
        calls.append(data)
        return sentinel

    hook._process_today = today
    with tempfile.TemporaryDirectory(prefix="carr-shadow-test-") as directory:
        root = Path(directory)
        config(root)
        settings = paths(root)
        with environment(dict(settings, CARR_RULEPRECISION_SHADOW=None)):
            assert hook.process(payload()) is sentinel
        assert len(calls) == 1, "today selection must execute exactly once"
        assert not (root / "shadow.jsonl").exists(), "shadow is off by default"
        with environment(settings):
            assert hook.process(payload()) is sentinel
        assert len(calls) == 2
        log = rows(root)
        assert log[0]["candidate_ids"] == ["4a53ff82"]
        assert log[0]["proposed_new_ids"] == ["4a53ff82"]
        assert log[0]["proposal_only"] is True
        assert log[0]["today_full_ids"] == []
        assert log[0]["boot_basis"] == "configured_snapshot"
        with environment(settings):
            assert hook.process(payload()) is sentinel
        assert rows(root)[1]["candidate_ids"] == ["4a53ff82"]
        assert rows(root)[1]["proposed_new_ids"] == [], "dedupe proposals within one session"
        combined = (root / "shadow.jsonl").read_text() + (root / "state.json").read_text()
        for private in ("private-session-marker", "private-tool-marker", "private-client-marker",
                        "private-secret-marker", "unchanged baseline"):
            assert private not in combined, "raw hook input or output leaked"

        from lib.ruleprecision_shadow import observe
        receipt = {"hookSpecificOutput": {"additionalContext": json.dumps({
            "rule_ids": ["ffffffff", "7e9739f2"],
            "rules": [{"id": "ffffffff", "statement": "private-rule-text-marker"}],
            "overflow": [{"id": "7e9739f2", "summary": "private-pointer-marker"}]})}}
        other = payload()
        other["session_id"] = "another session"
        with environment(settings):
            observe(REPO, other, receipt)
        latest = rows(root)[-1]
        assert latest["today_full_ids"] == ["ffffffff"]
        assert latest["today_pointer_ids"] == ["7e9739f2"]
        assert latest["today_declared_ids"] == ["7e9739f2", "ffffffff"]
        assert "private-rule-text-marker" not in (root / "shadow.jsonl").read_text()
        assert "private-pointer-marker" not in (root / "shadow.jsonl").read_text()

        hook._process_today = lambda *args, **kwargs: None
        with environment(settings):
            assert hook.process(other) is None, "a shadow addition cannot change no delivery"
        assert rows(root)[-1]["candidate_ids"] == ["4a53ff82"]
        before_digest = rows(root)[-1]["selector_digest"]
        from lib import rule_delivery_precision
        routes_source = root / "rule_routes.py"
        routes_source.write_bytes(Path(rule_delivery_precision.rule_routes.__file__).read_bytes() + b"\n")
        with environment(settings), patch.object(rule_delivery_precision.rule_routes, "__file__", str(routes_source)):
            observe(REPO, other, None)
        assert rows(root)[-1]["selector_digest"] != before_digest, "imported action parser must bind selector generation"
        (root / "config.json").write_text('{"schema":"bad","secret":"private-error-marker"}')
        with environment(settings):
            assert hook.process(other) is None
        assert rows(root)[-1]["status"] == "config_invalid"
        assert "private-error-marker" not in (root / "shadow.jsonl").read_text()
        config(root)
        with environment(dict(settings, CARR_RULEPRECISION_LOG=str(root))):
            assert hook.process(other) is None, "logger failure must not affect delivery"

    with tempfile.TemporaryDirectory(prefix="carr-shadow-concurrent-") as directory:
        root = Path(directory)
        config(root)
        with ProcessPoolExecutor(max_workers=4) as pool:
            list(pool.map(observe_parallel, [(directory, number) for number in range(12)]))
        log = rows(root)
        assert len(log) == 12
        assert sum("4a53ff82" in row["proposed_new_ids"] for row in log) == 1
        assert all(row["candidate_ids"] == ["4a53ff82"] for row in log)
    print("ruleprecision-shadow-selftest: unchanged output, privacy, fixed errors, concurrency passed")


if __name__ == "__main__":
    main()
