"""Local fail-closed mirror of the durable doctrine-cutoff state.

The database remains authoritative.  This sentinel exists only so file gates
and exporters stay closed if the database cannot be read during a staged or
finalized cutover.  It contains no doctrine or business data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BLOCKING_PHASES = {"preparing", "staged", "finalizing", "finalized", "rolling_back"}


def sentinel_path(repo: Path | None = None) -> Path:
    override = os.environ.get("CARR_CUTOFF_SENTINEL")
    if override:
        return Path(override)
    root = repo or Path(__file__).resolve().parents[1]
    return root / "out" / "doctrine-cutoff-state.json"


def read_local_state(repo: Path | None = None) -> dict:
    path = sentinel_path(repo)
    if not path.exists():
        return {"phase": "not_staged"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("phase") not in BLOCKING_PHASES:
            return {"phase": "invalid", "fail_closed": True}
        return value
    except Exception:
        return {"phase": "invalid", "fail_closed": True}


def markdown_writes_blocked(repo: Path | None = None) -> bool:
    state = read_local_state(repo)
    return state.get("phase") in BLOCKING_PHASES or state.get("fail_closed") is True
