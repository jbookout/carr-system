#!/usr/bin/env python3
"""Fail-closed seven-day eligibility check for rule-delivery enforcement."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LOG = REPO / "out" / "rule-delivery-shadow.jsonl"
WINDOW = timedelta(days=7)
MAX_GAP = timedelta(hours=48)


def stamp(row: dict) -> datetime | None:
    try:
        return datetime.strptime(str(row.get("ts")), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def scoped(row: dict) -> bool:
    return (row.get("mode") == "shadow" and bool(row.get("loaded"))
            and int(row.get("would_omit_count") or 0) > 0 and stamp(row) is not None)


def evaluate(rows: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    observations = sorted(((seen,row) for row in rows
                           if scoped(row) and (seen := stamp(row)) is not None),
                          key=lambda item:item[0])
    reasons: list[str] = []
    if not observations:
        return {"eligible": False, "reasons": ["no actual scoped shadow observations"],
                "qualifying_observations": 0}
    first, last = observations[0][0], observations[-1][0]
    if now-first < WINDOW:
        reasons.append(f"scoped shadow window is {(now-first).total_seconds()/3600:.1f}h, needs 168h")
    if now-last > MAX_GAP:
        reasons.append(f"newest scoped observation is {(now-last).total_seconds()/3600:.1f}h old")
    gaps = [(b[0]-a[0]).total_seconds()/3600
            for a,b in zip(observations,observations[1:])]
    if gaps and max(gaps) > MAX_GAP.total_seconds()/3600:
        reasons.append(f"scoped observation gap is {max(gaps):.1f}h, maximum is 48h")
    window_rows = [row for row in rows
                   if (seen := stamp(row)) is not None and seen >= first]
    misses = [row for row in window_rows if row.get("missed_rules")]
    errors = [row for row in window_rows if row.get("error")]
    if misses:
        reasons.append(f"{len(misses)} shadow observation(s) contain missed rules")
    if errors:
        reasons.append(f"{len(errors)} gate error(s) occurred during the window")
    return {"eligible": not reasons, "reasons": reasons,
            "qualifying_observations": len(observations),
            "window_started": first.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "newest": last.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_hours": round((now-first).total_seconds()/3600, 3),
            "miss_count": len(misses), "gate_errors": len(errors)}


def load(path: Path) -> tuple[list[dict], int]:
    rows: list[dict] = []
    bad = 0
    if not path.exists():
        return rows, bad
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError
            rows.append(row)
        except ValueError:
            bad += 1
    return rows, bad


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows, unreadable = load(args.log)
    result = evaluate(rows)
    if unreadable:
        result["reasons"].append(f"{unreadable} unreadable telemetry line(s)")
        result["eligible"] = False
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["eligible"]:
        print("rule-delivery-shadow-eligibility: ELIGIBLE — "
              f"{result['window_hours']:.1f}h, {result['qualifying_observations']} scoped observations, "
              "zero misses/errors")
    else:
        print("rule-delivery-shadow-eligibility: NOT ELIGIBLE")
        for reason in result["reasons"]:
            print("  " + reason)
    return 0 if result["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
