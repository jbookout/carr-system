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

    if failures:
        print(f"FAIL {len(failures)}: {', '.join(failures)}")
        return 1
    print(f"drive runtime hooks selftest: {checks}/{checks} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
