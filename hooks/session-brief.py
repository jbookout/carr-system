#!/usr/bin/env python3
"""session-brief.py — the ZERO-FILE local bootstrap (Joe, 2026-08-08:
"Couldn't that claude Md file just become the project instructions? And then
we truly don't have any Md files").

SessionStart hook for sessions rooted in the CARR vault: stdout injects the
briefing directive that CLAUDE.md used to carry, built FRESH from the store
each session — so unlike the file, it cannot go stale (the file lagged its
own rule counts by 4 within a day of the last refresh; measured 2026-08-08).

Three layers of the same directive, by surface:
  - MCP server `instructions` field  → every connector-holding session, all
    surfaces, Dell included (mcp.js)
  - Cowork project-instructions FIELD → the rev-7 pointer stub (no file)
  - THIS HOOK                         → local Claude Code, code not markdown

FAIL-SOFT IS A STOP RAIL, NOT SILENCE: if the store is unreachable the hook
still injects the identity line and the stop-and-say-so instruction — a blank
session that improvises files is the failure this whole build exists to end.
"""
import json
import os
import sys

STATIC_RAIL = (
    "CARR AI (Joe Bookout's healthcare-CRE system, partner Dell McCraney; "
    "business only — personal is Life AI). OPENING ACT: call standing-context "
    "FIRST and recite its rule counts in your first response. Doctrine and "
    "records live in the store (doctrine-index / search-doctrine / "
    "read-doctrine / catch-me-up); there are NO doctrine files. WRITE LAW "
    "(rule 14181e60): verbs only, never a .md file — a hard gate enforces it. "
    "Code lives in ~/carr-system, nowhere else."
)


def dynamic_counts():
    env = os.path.expanduser("~/.config/carr/db.env")
    url = None
    with open(env) as fh:
        for line in fh:
            if line.startswith("CARR_DB_EXPORTER_URL="):
                url = line.split("=", 1)[1].strip().strip("\"'")
    if not url:
        raise RuntimeError("no exporter url")
    import psycopg
    with psycopg.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
        cur.execute(
            "select coalesce(personal_to,'shared'), count(*) from v_compiled_rules "
            "where coalesce(scope->>'kind','') <> 'intro_politics' "
            "group by 1")
        counts = dict(cur.fetchall())
        cur.execute(
            "select count(*) from loop_item where kind='action_required' "
            "and status='open'")
        ar = cur.fetchone()[0]
    shared = counts.pop("shared", 0)
    personal = ", ".join(f"{v} {k}-personal" for k, v in sorted(counts.items()))
    return (f" LIVE COUNTS to verify against standing-context: {shared} shared"
            + (f", {personal}" if personal else "")
            + (f" · {ar} action-required item(s) open" if ar else ""))


def main():
    try:
        json.load(sys.stdin)          # hook payload; nothing needed from it
    except Exception:
        pass
    extra = ""
    try:
        sys.path.insert(0, os.path.expanduser("~/carr-system/.venv/lib"))
        extra = dynamic_counts()
    except Exception:
        extra = (" (store unreachable at session start — STOP AND SAY SO "
                 "before doing anything that would normally need it)")
    print(STATIC_RAIL + extra)
    sys.exit(0)


if __name__ == "__main__":
    main()
