#!/usr/bin/env python3
"""Regression tests for the Codex standing-context boot contract."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "ops" / "agent-boot-contract.py"
AGENTS = REPO / "AGENTS.md"


def run(text: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as td:
        candidate = Path(td) / "AGENTS.md"
        candidate.write_text(text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(CHECK), str(candidate)],
            text=True,
            capture_output=True,
            check=False,
        )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    source = AGENTS.read_text(encoding="utf-8")

    good = run(source)
    require(good.returncode == 0, f"real AGENTS.md failed: {good.stderr}")

    no_direct = run(source.replace("mcp__carr__standing_context", "standing-context", 1))
    require(no_direct.returncode != 0, "missing direct MCP tool was accepted")

    no_catalog = run(source.replace("deferred tool catalog", "tool list", 1))
    require(no_catalog.returncode != 0, "missing lazy-tool discovery instruction was accepted")

    no_sandbox_guard = run(source.replace("not a store outage", "a store outage", 1))
    require(no_sandbox_guard.returncode != 0, "sandbox failure could still be called an outage")

    fallback_first = source.replace(
        "Call `mcp__carr__standing_context` directly FIRST.",
        "Run `./run.sh call standing-context '{}'` directly FIRST.",
        1,
    )
    require(run(fallback_first).returncode != 0, "shell-first boot order was accepted")

    print("agent boot contract selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
