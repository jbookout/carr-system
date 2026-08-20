#!/usr/bin/env python3
"""Behavioral boundary checks for normal runtime Drive retirement.

Each normal path is run with a poisoned CARR_VAULT.  Success means it either
uses the repository/record contract or ignores that ambient value; none may
discover or open the poisoned tree.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
POISON = "/definitely-not-a-carr-source"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    failures: list[str] = []
    checks = 0

    def check(name: str, value: bool) -> None:
        nonlocal checks
        checks += 1
        print(f"  {'ok  ' if value else 'FAIL'} {name}")
        if not value:
            failures.append(name)

    env = {**os.environ, "CARR_VAULT": POISON}
    sync = subprocess.run([str(REPO / "bin" / "sync-settings.sh")], text=True,
                          capture_output=True, env=env)
    check("settings normal mode ignores poisoned ambient root",
          sync.returncode == 0 and "repository config is canonical" in sync.stdout
          and POISON not in sync.stdout + sync.stderr)

    control = load("drive_runtime_control", REPO / "tools" / "control-plane.py")
    captured: list[dict[str, str]] = []
    original_run = control.subprocess.run
    original_env = os.environ.copy()
    os.environ["CARR_VAULT"] = POISON
    try:
        def fake_run(_argv, **kwargs):
            captured.append(kwargs["env"])
            return subprocess.CompletedProcess([], 0, "", "")
        control.subprocess.run = fake_run
        control._execute_deterministic(
            {"execution": {"entrypoint": "bin/nightly.sh", "args": [], "shadow_args": []}},
            {}, 30, "shadow")
    finally:
        control.subprocess.run = original_run
        os.environ.clear()
        os.environ.update(original_env)
    check("deterministic children exclude poisoned ambient root",
          bool(captured) and "CARR_VAULT" not in captured[0])

    executor = subprocess.run(
        [sys.executable, str(REPO / "hooks" / "executor-tier-gate.py")],
        input=json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": "poisoned-agent"}}),
        text=True, capture_output=True, env=env)
    check("executor ignores a poisoned synced agent definition",
          executor.returncode == 0 and '"permissionDecision": "deny"' in executor.stdout)

    sources = load("drive_runtime_sources", REPO / "lib" / "record_sources.py")
    check("record identities require a declared root, not an ambient mount",
          sources._strip_source_root("/repo/DNA/example.md", "/repo") == "DNA/example.md"
          and sources._strip_source_root(POISON + "/DNA/example.md", "/repo")
          == POISON + "/DNA/example.md")

    policy = load("drive_runtime_drift", REPO / "hooks" / "drift-claim-gate.py")
    original_connect = None
    try:
        import lib.record_sources as record_sources
        original_connect = record_sources._connect
        record_sources._connect = lambda: (_ for _ in ()).throw(AssertionError("record call"))
        old = os.environ.get("CARR_VAULT")
        os.environ["CARR_VAULT"] = POISON
        lines = policy._record_decision_lines()
    finally:
        if original_connect is not None:
            record_sources._connect = original_connect
        if old is None:
            os.environ.pop("CARR_VAULT", None)
        else:
            os.environ["CARR_VAULT"] = old
    check("drift policy does not turn poisoned ambient root into a file read", lines == [])

    # The installed command deliberately routes to the repository interpreter:
    # system Python has no psycopg on this Mac.  The canonical-path fixture below
    # is non-empty and reaches v_decision_entry through the same query boundary;
    # it is not a monkeypatched empty-reader acceptance test.
    hooks_config = (REPO / "ops" / "config" / "hooks.json").read_text()
    check("installed drift hooks use the fixed repository record interpreter",
          hooks_config.count("run-record-gate.py drift-") == 2)

    with tempfile.TemporaryDirectory(dir=REPO) as tmp:
        root = Path(tmp)
        (root / "psycopg.py").write_text('''
class Cursor:
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, *_): pass
    def fetchall(self):
        return [("2026-08-20", "The quokka-indexer lane stays disabled", "Joe approved it", "the cost exceeded value", "chosen state")]
class Connection:
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def cursor(self): return Cursor()
def connect(_url): return Connection()
''')
        record_env = {**env, "PYTHONPATH": str(root), "CARR_DB_EXPORTER_URL": "synthetic://record",
                      "HOME": str(root)}
        record_env.pop("CARR_NONCANONICAL_DECISIONS_PATH", None)
        claim = ("The quokka-indexer lane is no longer running. It was supposed to fire nightly "
                 "and the schedule silently reverted, so the index is stale and nothing has been re-pointed.")
        write = subprocess.run(["/usr/bin/python3", str(REPO / "hooks" / "drift-claim-gate.py")],
                               input=json.dumps({"tool_name": "mcp__x__record-defect",
                                                 "tool_input": {"body": claim}}),
                               text=True, capture_output=True, env=record_env)
        transcript = root / "transcript.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": claim}]}}) + "\n")
        assertion = subprocess.run(["/usr/bin/python3", str(REPO / "hooks" / "drift-assertion-gate.py")],
                                   input=json.dumps({"hook_event_name": "Stop", "transcript_path": str(transcript)}),
                                   text=True, capture_output=True,
                                   env={**record_env, "CARR_DRIFT_ASSERTION_STATE": str(root / "state")})
    check("system Python loads nonempty canonical record context and assertion blocks",
          write.returncode == 0 and "quokka-indexer" in write.stdout
          and assertion.returncode == 2 and "quokka-indexer" in assertion.stderr)

    if failures:
        print(f"FAIL {len(failures)}: {', '.join(failures)}")
        return 1
    print(f"drive runtime hooks selftest: {checks}/{checks} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
