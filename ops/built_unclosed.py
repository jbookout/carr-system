#!/usr/bin/env python3
"""built_unclosed.py — the missing second number: code on disk, not just closed.

WHY THIS EXISTS. capability-program reports completed=N (confirmed_closed
count). That is attestation truth, not build truth: a Work Request can have
artifacts already merged to main and still sit at state != confirmed_closed
because nobody ran prepare/attest/complete-capability-project. A session that
reads "0/51 completed" as "nothing is built" starts a rebuild of work that
already landed — which happened 24 times, 9 caught by a human.

This module is the local half of the latch. The Cloudflare Worker cannot stat
~/carr-system; this can, because it runs in local hooks (SessionStart,
PreToolUse) that already see the tree and already read the exporter DB.

DEFINITIONS:
  - built_unclosed: state != confirmed_closed AND evidence is a non-empty list
    AND every evidence path exists on disk in this checkout.
  - landed_in_repo: count of rows whose evidence paths all exist, regardless
    of state. (confirmed_closed rows with evidence also count as landed.)

The evidence field lives in each Work Request's project_context JSON:
  project_context.evidence = ["path/to/artifact.py", ...]

Paths are repo-relative. A path that is not a real file (e.g. "MCP input
schemas", "workflow contracts") does not exist on disk and is NOT landed —
do not guess. Empty evidence is NOT landed.
"""
from __future__ import annotations

import json
import os
import os.path
from typing import Any


def _evidence_paths(row: dict[str, Any]) -> list[str]:
    """Extract evidence paths from a row's project_context, or [] if absent."""
    ctx = row.get("project_context") or row.get("context") or {}
    if not isinstance(ctx, dict):
        return []
    ev = ctx.get("evidence")
    if not isinstance(ev, list):
        return []
    return [p for p in ev if isinstance(p, str) and p.strip()]


def _all_exist(paths: list[str], root: str) -> bool:
    """True when paths is non-empty and every path exists under root."""
    if not paths:
        return False
    for p in paths:
        full = os.path.join(root, p)
        if not os.path.exists(full):
            return False
    return True


def detect_built_unclosed(rows: list[dict[str, Any]], root: str) -> list[dict[str, Any]]:
    """Return the subset of rows that are BUILT-UNCLOSED.

    A row is built-unclosed when:
      - state != confirmed_closed
      - evidence is non-empty
      - every evidence path exists on disk under root
    """
    out = []
    for row in rows:
        state = row.get("state", "")
        if state == "confirmed_closed":
            continue
        ev = row.get("evidence")
        if ev is None:
            ev = _evidence_paths(row)
        if not isinstance(ev, list) or not ev:
            continue
        if _all_exist(ev, root):
            out.append(row)
    return out


def landed_in_repo(rows: list[dict[str, Any]], root: str) -> int:
    """Count of rows whose evidence paths all exist on disk, regardless of state."""
    count = 0
    for row in rows:
        ev = row.get("evidence")
        if ev is None:
            ev = _evidence_paths(row)
        if not isinstance(ev, list):
            continue
        if _all_exist(ev, root):
            count += 1
    return count


def load_live_rows() -> list[dict[str, Any]] | None:
    """Load Work Request rows from the exporter DB when CARR_DB_EXPORTER_URL is set.

    Returns None when the env var is absent or the DB is unreachable — the
    caller treats that as "fall back to the seed migration evidence paths."
    Unit tests must never call this; pass fixtures to detect_built_unclosed instead.
    """
    url = os.environ.get("CARR_DB_EXPORTER_URL")
    if not url:
        return None
    # Also check the conventional config location used by session-brief.py
    if not url.startswith(("postgres://", "postgresql://")):
        env_path = os.path.expanduser("~/.config/carr/db.env")
        if os.path.exists(env_path):
            with open(env_path) as fh:
                for line in fh:
                    if line.startswith("CARR_DB_EXPORTER_URL="):
                        url = line.split("=", 1)[1].strip().strip("\"'")
                        break
    if not url or not url.startswith(("postgres://", "postgresql://")):
        return None
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            cur.execute(
                "select ref, state, project_context "
                "from ops.work_request "
                "where program_key = 'carr-ai-engineering-suite-v1' "
                "order by program_ordinal"
            )
            rows = []
            for ref, state, ctx in cur.fetchall():
                row: dict[str, Any] = {"ref": ref, "state": state}
                if isinstance(ctx, dict):
                    row["project_context"] = ctx
                    row["evidence"] = ctx.get("evidence", [])
                rows.append(row)
            return rows
    except Exception:
        return None
