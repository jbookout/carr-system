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
import shutil
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

    policy_text = (REPO / "hooks" / "drift-claim-gate.py").read_text()
    check("drift policy has no ambient Drive-root reader", "CARR_VAULT" not in policy_text)

    # The installed command deliberately routes to the repository interpreter.
    # The fixture below invokes the launcher from /usr/bin/python3 and returns a
    # nonempty canonical v_decision_entry row; no direct-gate monkeypatches are
    # accepted as evidence for this boundary.
    hooks_config = (REPO / "ops" / "config" / "hooks.json").read_text()
    check("installed drift hooks use fixed system bootstrap and repository interpreter",
          hooks_config.count("/usr/bin/python3 {{REPO}}/hooks/run-record-gate.py drift-") == 2
          and "/usr/bin/env python3 {{REPO}}/hooks/run-record-gate.py" not in hooks_config)

    with tempfile.TemporaryDirectory(dir=REPO) as tmp:
        root = Path(tmp)
        for directory in (root / "hooks", root / "lib", root / ".venv" / "bin"):
            directory.mkdir(parents=True, exist_ok=True)
        for name in ("run-record-gate.py", "drift-claim-gate.py", "drift-assertion-gate.py"):
            shutil.copy2(REPO / "hooks" / name, root / "hooks" / name)
        (root / "hooks" / "chat-lint-gate.py").write_text('''
import json
def read_tail(path):
    with open(path) as fh: return [json.loads(line) for line in fh if line.strip()]
def text_of(record, _roles):
    content = record.get("message", {}).get("content", [])
    return "\\n".join(item.get("text", "") for item in content if isinstance(item, dict))
def strip_fences(text): return text
''')
        (root / "lib" / "__init__.py").write_text("")
        (root / "lib" / "record_sources.py").write_text('''
import os
if os.environ.get("PYTHONPATH"):
    raise RuntimeError("launcher failed to scrub PYTHONPATH")
if os.environ.get("FIXTURE_FIXED_INTERPRETER") != "1":
    raise RuntimeError("launcher did not select the fixed interpreter")
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
def _connect(): return Connection()
''')
        interpreter = root / ".venv" / "bin" / "python"
        interpreter.write_text('''#!/bin/sh
if [ "${FIXTURE_PSYCOG_TIMEOUT:-0}" = 1 ] && [ "$1" = -c ]; then sleep 4; fi
if [ "$1" = -c ]; then [ "${FIXTURE_PSYCOG_OK:-1}" = 1 ] && exit 0 || exit 1; fi
export FIXTURE_FIXED_INTERPRETER=1
exec /usr/bin/python3 "$@"
''')
        interpreter.chmod(0o755)
        record_env = {**env, "PYTHONPATH": str(root / "poisoned-pythonpath"), "HOME": str(root)}
        claim = ("The quokka-indexer lane is no longer running. It was supposed to fire nightly "
                 "and the schedule silently reverted, so the index is stale and nothing has been re-pointed.")
        write = subprocess.run(["/usr/bin/python3", str(root / "hooks" / "run-record-gate.py"), "drift-claim-gate.py"],
                               input=json.dumps({"tool_name": "mcp__x__record-defect",
                                                 "tool_input": {"body": claim}}),
                               text=True, capture_output=True, env=record_env)
        transcript = root / "transcript.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": claim}]}}) + "\n")
        assertion = subprocess.run(["/usr/bin/python3", str(root / "hooks" / "run-record-gate.py"), "drift-assertion-gate.py"],
                                   input=json.dumps({"hook_event_name": "Stop", "transcript_path": str(transcript)}),
                                   text=True, capture_output=True,
                                   env={**record_env, "CARR_DRIFT_ASSERTION_STATE": str(root / "state")})
        unknown = subprocess.run(["/usr/bin/python3", str(root / "hooks" / "run-record-gate.py"), "unknown.py"],
                                 text=True, capture_output=True, env=record_env)
        malformed = subprocess.run(["/usr/bin/python3", str(root / "hooks" / "run-record-gate.py"),
                                    "drift-claim-gate.py", "extra"], text=True, capture_output=True, env=record_env)
        missing_dependency = subprocess.run(["/usr/bin/python3", str(root / "hooks" / "run-record-gate.py"), "drift-claim-gate.py"],
                                            text=True, capture_output=True,
                                            env={**record_env, "FIXTURE_PSYCOG_OK": "0"})
        missing_path = root / ".venv" / "bin" / "python-missing"
        interpreter.rename(missing_path)
        try:
            missing_interpreter = subprocess.run(["/usr/bin/python3", str(root / "hooks" / "run-record-gate.py"), "drift-claim-gate.py"],
                                                 text=True, capture_output=True, env=record_env)
        finally:
            missing_path.rename(interpreter)
        timeout = subprocess.run(["/usr/bin/python3", str(root / "hooks" / "run-record-gate.py"), "drift-claim-gate.py"],
                                 text=True, capture_output=True,
                                 env={**record_env, "FIXTURE_PSYCOG_TIMEOUT": "1"})
    check("system bootstrap reaches fixed interpreter, scrubs PYTHONPATH, and blocks on canonical context",
          write.returncode == 0 and "quokka-indexer" in write.stdout
          and assertion.returncode == 2 and "quokka-indexer" in assertion.stderr)
    bootstrap_faults = {"unknown": unknown, "malformed": malformed,
                        "missing dependency": missing_dependency,
                        "missing interpreter": missing_interpreter, "timeout": timeout}
    check("launcher fails closed for unknown/malformed/missing dependency/interpreter/timeout",
          all(item.returncode != 0 for item in bootstrap_faults.values()))
    # A mute exit 2 reads as an objection to the assistant's work rather than as
    # the launcher never reaching the gate, so each fault must name itself and
    # the five names must be distinguishable from one another.
    reasons = {name: item.stderr.strip() for name, item in bootstrap_faults.items()}
    check("every closed-gate bootstrap fault names a distinct reason on stderr",
          all(reason.startswith("run-record-gate: gate not run: ") for reason in reasons.values())
          and len(set(reasons.values())) == len(reasons))

    if failures:
        print(f"FAIL {len(failures)}: {', '.join(failures)}")
        return 1
    print(f"drive runtime hooks selftest: {checks}/{checks} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
