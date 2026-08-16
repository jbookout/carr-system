#!/usr/bin/env python3
"""Validate weekly social-batch candidates before a proposal is created.

Input and output are JSON arrays.  The script has no publishing or database
credentials; it is the deterministic admission seam used before the weekly
cognition proposal is queued.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.client_asset_controls import AssetControlRefusal, reject_weekly_quote_tweets


def main() -> int:
    raw = json.load(sys.stdin)
    if not isinstance(raw, list):
        print("STOP: weekly batch candidates must be a JSON array", file=sys.stderr)
        return 2
    try:
        accepted = reject_weekly_quote_tweets(raw)
    except AssetControlRefusal as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "candidates": accepted}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
