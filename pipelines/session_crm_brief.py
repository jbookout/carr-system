"""Session CRM v0 brief — the morning Chief of Staff view.

Reads session_work from Neon and prints a glanceable summary:
  - 3 doing (most recently seen open items)
  - stale >4h (open items not seen in over 4 hours)
  - promises (all promise-kind rows)
  - kin (grouped items, if any)

Can also run from a JSON file (--from-json) for testing and dry runs.

Usage:
  CARR_DB_JOBS_URL=... .venv/bin/python -m pipelines.session_crm_brief
  .venv/bin/python -m pipelines.session_crm_brief --from-json /path/to/rows.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

STALE_THRESHOLD_HOURS = 4


def _parse_ts(ts: str) -> datetime:
    """Parse an ISO timestamp. Fall back to now() if unparseable."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


def format_brief(rows: list[dict[str, Any]], *, now: datetime | None = None) -> str:
    """Format the brief from a list of session_work rows.

    Pure function: no I/O. This is what the selftest exercises.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    open_rows = [r for r in rows if r.get("open_loop")]
    closed_rows = [r for r in rows if not r.get("open_loop")]

    # 3 doing: most recently seen open items
    doing = sorted(open_rows, key=lambda r: r.get("last_seen", ""), reverse=True)[:3]

    # stale >4h: open items not seen in over 4 hours
    stale: list[dict[str, Any]] = []
    for r in open_rows:
        ts = _parse_ts(r.get("last_seen", ""))
        age = (now - ts).total_seconds() / 3600
        if age > STALE_THRESHOLD_HOURS:
            stale.append(r)
    stale.sort(key=lambda r: r.get("last_seen", ""))

    # promises
    promises = [r for r in rows if r.get("kind") == "promise"]

    # kin: group open rows by kin field (skip None)
    kin_groups: dict[str, list[dict[str, Any]]] = {}
    for r in open_rows:
        k = r.get("kin")
        if k:
            kin_groups.setdefault(k, []).append(r)

    lines: list[str] = []
    lines.append("=== Session CRM Brief ===")
    lines.append("")

    # 3 doing
    lines.append(f"DOING ({len(doing)} shown, {len(open_rows)} open total)")
    if doing:
        for r in doing:
            seat = r.get("next_seat") or "?"
            lines.append(f"  [{r['kind']}] {r['title']}  →  {seat}")
    else:
        lines.append("  (nothing open)")
    lines.append("")

    # stale
    lines.append(f"STALE >{STALE_THRESHOLD_HOURS}h ({len(stale)})")
    if stale:
        for r in stale:
            ts = _parse_ts(r.get("last_seen", ""))
            age_h = int((now - ts).total_seconds() / 3600)
            lines.append(f"  [{r['kind']}] {r['title']}  ({age_h}h ago)")
    else:
        lines.append("  (nothing stale)")
    lines.append("")

    # promises
    lines.append(f"PROMISES ({len(promises)})")
    if promises:
        for r in promises:
            seat = r.get("next_seat") or "?"
            lines.append(f"  {r['promise']}  →  {seat}")
    else:
        lines.append("  (none)")
    lines.append("")

    # kin
    if kin_groups:
        lines.append(f"KIN ({len(kin_groups)} groups)")
        for group_name, group_rows in sorted(kin_groups.items()):
            lines.append(f"  {group_name}: {len(group_rows)} item(s)")
        lines.append("")

    # closed count (just a number)
    lines.append(f"closed: {len(closed_rows)}")

    return "\n".join(lines)


def read_from_neon(dsn: str) -> list[dict[str, Any]]:
    """Read all rows from session_work."""
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            select id, kind, title, last_seen, open_loop, next_seat, sources, promise, kin
            from session_work
            order by last_seen desc
        """)
        desc = cur.description
        if desc is None:
            return []
        cols = [d[0] for d in desc]
        rows = []
        for record in cur.fetchall():
            row = dict(zip(cols, record, strict=True))
            # Convert arrays and timestamps to JSON-serializable types
            if row.get("last_seen"):
                row["last_seen"] = row["last_seen"].isoformat()
            srcs = row.get("sources") or []
            row["sources"] = list(srcs)
            rows.append(row)
        return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Session CRM v0 brief")
    parser.add_argument("--from-json", type=str, default=None,
                        help="Read rows from a JSON file instead of Neon")
    args = parser.parse_args()

    if args.from_json:
        with open(args.from_json) as f:
            rows = json.load(f)
    else:
        dsn = (
            os.environ.get("CARR_DB_JOBS_URL")
            or os.environ.get("CARR_DB_EXPORTER_URL")
            or os.environ.get("DATABASE_URL")
        )
        if not dsn:
            print("brief: NOT CONFIGURED — set CARR_DB_JOBS_URL or use --from-json", file=sys.stderr)
            return 78
        rows = read_from_neon(dsn)

    print(format_brief(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
