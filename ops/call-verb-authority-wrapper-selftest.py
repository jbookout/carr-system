#!/usr/bin/env python3
"""Prove the receipted local wrapper preserves Joe's narrow authority DSN."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("call_verb", ROOT / "tools" / "call-verb.py")
assert SPEC and SPEC.loader
call_verb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(call_verb)


class Tap:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def dsn(self, target: str) -> str:
        self.calls.append(target)
        return f"owner:{target}"


def main() -> int:
    old = os.environ.get("CARR_DB_AUTHORITY_JOE_URL")
    try:
        os.environ["CARR_DB_AUTHORITY_JOE_URL"] = "authority:joe"
        tap = Tap()
        assert call_verb.break_glass_dsn(tap, None) == "authority:joe"
        assert tap.calls == []
        assert call_verb.break_glass_dsn(tap, "rehearse-0306") == "owner:rehearse-0306"
        assert tap.calls == ["rehearse-0306"]
        os.environ.pop("CARR_DB_AUTHORITY_JOE_URL", None)
        assert call_verb.break_glass_dsn(tap, None) == "owner:production"
        assert tap.calls == ["rehearse-0306", "production"]
    finally:
        if old is None:
            os.environ.pop("CARR_DB_AUTHORITY_JOE_URL", None)
        else:
            os.environ["CARR_DB_AUTHORITY_JOE_URL"] = old
    print("call-verb authority wrapper selftest: authority production path and branch fallback pinned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
