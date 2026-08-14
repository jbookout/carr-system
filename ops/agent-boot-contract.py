#!/usr/bin/env python3
"""Refuse a Codex boot contract that can misdiagnose its own sandbox as CARR downtime."""

from __future__ import annotations

import sys
from pathlib import Path


DIRECT = "Call `mcp__carr__standing_context` directly FIRST."
DIRECT_TOOL = "mcp__carr__standing_context"
CATALOG = "deferred tool catalog"
FALLBACK = "./run.sh call standing-context '{}'"
SANDBOX_GUARD = "not a store outage"


def check(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []

    for token, description in (
        (DIRECT, "direct-MCP-first instruction"),
        (DIRECT_TOOL, "canonical CARR MCP tool name"),
        (CATALOG, "lazy/deferred tool discovery instruction"),
        (FALLBACK, "local shell fallback"),
        (SANDBOX_GUARD, "sandbox-versus-outage classification guard"),
    ):
        if token not in text:
            problems.append(f"missing {description}: {token!r}")

    direct_at = text.find(DIRECT)
    fallback_at = text.find(FALLBACK)
    if direct_at < 0 or fallback_at < 0 or direct_at >= fallback_at:
        problems.append("direct CARR MCP call must appear before the shell fallback")

    return problems


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1] / "AGENTS.md"
    problems = check(path)
    if problems:
        for problem in problems:
            print(f"agent-boot-contract: {problem}", file=sys.stderr)
        return 1
    print("agent boot contract: direct MCP first; sandbox fallback classified correctly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
