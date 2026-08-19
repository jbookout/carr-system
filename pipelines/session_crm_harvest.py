"""Session CRM v0 harvest — deterministic, model-free, read-only.

Collects work items from five sources and upserts them into the session_work
table in Neon. No model in the harvest: every collector shells out to a CLI
or reads a static list, parses the output, and returns rows with the same shape.

SOURCES (all read-only):
  1. git worktree list --porcelain
  2. gh pr list --json (open PRs only)
  3. hermes sessions list (recent Hermes sessions)
  4. hermes kanban list --json (carr-build board)
  5. SEEDED_PROMISES (static list below)

ROW SHAPE (every row, no exceptions):
  id          — stable: kind ':' source_key
  kind        — worktree | pr | hermes_session | kanban | promise
  title       — human-readable label
  last_seen   — ISO timestamp (when the source last saw this)
  open_loop   — bool: is this item still active?
  next_seat   — who/what owns the next step (nullable)
  sources     — list of source names that mentioned this item
  promise     — promise text (nullable, non-null only for promise rows)
  kin         — grouping hint (nullable, null in v0)

SAFETY. The harvest is read-only on git remotes (no push, no fetch), read-only
on gh (list only, no create/merge), and does not delete any worktree. It writes
only to the session_work table in Neon through CARR_DB_JOBS_URL.

Usage:
  CARR_DB_JOBS_URL=... .venv/bin/python -m pipelines.session_crm_harvest [--dry-run]
  CARR_DB_JOBS_URL=... .venv/bin/python -m pipelines.session_crm_harvest --json-out PATH
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

# ── seeded promises ──────────────────────────────────────────────────────────
# Static, human-authored. These are standing commitments that the harvest cannot
# derive from any CLI. Add or remove here; they land as promise-kind rows.

SEEDED_PROMISES: list[dict[str, Any]] = [
    {
        "id": "promise:codex-trees-stay",
        "kind": "promise",
        "title": "Codex control-plane trees stay",
        "last_seen": None,  # filled at runtime
        "open_loop": True,
        "next_seat": "joe",
        "sources": ["seeded_promises"],
        "promise": "Codex control-plane trees stay — do not delete them.",
        "kin": None,
    },
    {
        "id": "promise:phone-doc-no-claude",
        "kind": "promise",
        "title": "Phone Doc does not spawn Claude",
        "last_seen": None,
        "open_loop": True,
        "next_seat": "joe",
        "sources": ["seeded_promises"],
        "promise": "Phone Doc does not spawn Claude.",
        "kin": None,
    },
    {
        "id": "promise:loop-455-waits-fable",
        "kind": "promise",
        "title": "Loop 455 waits for Fable",
        "last_seen": None,
        "open_loop": True,
        "next_seat": "fable",
        "sources": ["seeded_promises"],
        "promise": "Loop 455 waits for Fable.",
        "kin": None,
    },
    {
        "id": "promise:bot-mode-parked",
        "kind": "promise",
        "title": "Bot Mode parked",
        "last_seen": None,
        "open_loop": True,
        "next_seat": "joe",
        "sources": ["seeded_promises"],
        "promise": "Bot Mode is parked.",
        "kin": None,
    },
]

KNOWN_KINDS = {"worktree", "pr", "hermes_session", "kanban", "promise"}

# ── helpers ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: list[str], *, timeout: int = 15) -> tuple[int, str, str]:
    """Run a command, return (exit_code, stdout, stderr). Never raises."""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s: {' '.join(cmd)}"


# ── collectors ─────────────────────────────────────────────────────────────────


def collect_worktrees() -> list[dict[str, Any]]:
    """Parse `git worktree list --porcelain` into session_work rows.

    Read-only: git worktree list does not mutate the working tree or remotes.
    The canonical tree (main) is included; worktrees under .claude/worktrees/
    and .worktrees/ are Codex control-plane or session trees.
    """
    ts = _now_iso()
    rc, out, _ = _run(["git", "worktree", "list", "--porcelain"])
    if rc != 0:
        return []

    rows: list[dict[str, Any]] = []
    current: dict[str, str] = {}

    for line in out.splitlines():
        if line == "":
            if current:
                rows.append(_worktree_row(current, ts))
                current = {}
            continue
        parts = line.split(" ", 1)
        key = parts[0]
        val = parts[1] if len(parts) > 1 else ""
        current[key] = val

    if current:
        rows.append(_worktree_row(current, ts))

    return rows


def _worktree_row(d: dict[str, str], ts: str) -> dict[str, Any]:
    path = d.get("worktree", "")
    branch = d.get("branch", "").replace("refs/heads/", "")
    head = d.get("HEAD", "")[:8]

    # Title: branch name if available, else the path basename
    if branch:
        title = branch
    else:
        title = Path(path).name if path else "(detached)"

    # kin="main" ONLY for the canonical integration tree (branch main). A
    # worktree that happens to share main's HEAD but sits on a different
    # branch is NOT main — it is a session tree on its own lane.
    is_main = branch == "main"

    source_key = path or head or title
    row_id = f"worktree:{source_key}"

    return {
        "id": row_id,
        "kind": "worktree",
        "title": title,
        "last_seen": ts,
        "open_loop": True,
        "next_seat": None,
        "sources": ["git_worktree_list"],
        "promise": None,
        "kin": "main" if is_main else None,
    }


def collect_prs() -> list[dict[str, Any]]:
    """Parse `gh pr list --json` into session_work rows.

    Read-only: gh pr list does not create, merge, or push.
    """
    ts = _now_iso()
    rc, out, _ = _run([
        "gh", "pr", "list", "--json",
        "number,title,isDraft,state,headRefName,updatedAt",
        "--limit", "50",
    ])
    if rc != 0:
        return []

    try:
        prs = json.loads(out)
    except json.JSONDecodeError:
        return []

    rows: list[dict[str, Any]] = []
    for pr in prs:
        num = pr.get("number", 0)
        title = pr.get("title", f"PR #{num}")
        is_draft = pr.get("isDraft", False)
        updated = pr.get("updatedAt", ts)

        row_id = f"pr:{num}"
        rows.append({
            "id": row_id,
            "kind": "pr",
            "title": f"#{num} {title}" + (" (draft)" if is_draft else ""),
            "last_seen": updated,
            "open_loop": pr.get("state", "OPEN") == "OPEN",
            "next_seat": "reviewer" if is_draft else None,
            "sources": ["gh_pr_list"],
            "promise": None,
            "kin": None,
        })

    return rows


def collect_hermes_sessions() -> list[dict[str, Any]]:
    """Parse `hermes sessions list` output into session_work rows.

    The CLI prints a fixed-width table with columns:
    Title  Workspace  Last Active  ID
    We parse the last two whitespace-delimited tokens as Last Active and ID.
    """
    ts = _now_iso()
    rc, out, _ = _run(["hermes", "sessions", "list", "--limit", "20"])
    if rc != 0:
        return []

    rows: list[dict[str, Any]] = []
    lines = out.strip().splitlines()

    for line in lines:
        # Skip the separator line and header
        if line.startswith("\u2500") or line.startswith("Title"):
            continue
        if not line.strip():
            continue

        # The ID is the last token (e.g. 20260818_225354_71dcb9)
        parts = line.rsplit(None, 1)
        if len(parts) < 2:
            continue
        title_part, sid = parts

        # Last Active is the second-to-last token (e.g. "just now", "1h ago")
        sub_parts = title_part.rsplit(None, 1)
        if len(sub_parts) == 2:
            title = sub_parts[0]
        else:
            title = title_part

        row_id = f"hermes_session:{sid}"
        rows.append({
            "id": row_id,
            "kind": "hermes_session",
            "title": title.strip(),
            "last_seen": ts,  # hermes sessions list does not emit ISO timestamps
            "open_loop": True,
            "next_seat": None,
            "sources": ["hermes_sessions_list"],
            "promise": None,
            "kin": None,
        })

    return rows


def collect_kanban() -> list[dict[str, Any]]:
    """Parse `hermes kanban list --json` into session_work rows."""
    ts = _now_iso()
    rc, out, _ = _run(["hermes", "kanban", "list", "--json"])
    if rc != 0:
        return []

    try:
        tasks = json.loads(out)
    except json.JSONDecodeError:
        return []

    rows: list[dict[str, Any]] = []
    for t in tasks:
        tid = t.get("id", "")
        title = t.get("title", tid)
        status = t.get("status", "todo")
        assignee = t.get("assignee")

        # open_loop: not done, not archived
        is_open = status not in ("done", "archived")

        # next_seat: the assignee, or "unassigned"
        next_seat = assignee if assignee else "unassigned"

        row_id = f"kanban:{tid}"
        rows.append({
            "id": row_id,
            "kind": "kanban",
            "title": title,
            "last_seen": ts,
            "open_loop": is_open,
            "next_seat": next_seat,
            "sources": ["hermes_kanban_list"],
            "promise": None,
            "kin": None,
        })

    return rows


def collect_promises() -> list[dict[str, Any]]:
    """Return the seeded promises as session_work rows."""
    ts = _now_iso()
    rows: list[dict[str, Any]] = []
    for p in SEEDED_PROMISES:
        row = dict(p)
        row["last_seen"] = ts
        rows.append(row)
    return rows


# ── harvest ────────────────────────────────────────────────────────────────────

COLLECTORS = [
    ("git_worktree_list", collect_worktrees),
    ("gh_pr_list", collect_prs),
    ("hermes_sessions_list", collect_hermes_sessions),
    ("hermes_kanban_list", collect_kanban),
    ("seeded_promises", collect_promises),
]


def harvest() -> list[dict[str, Any]]:
    """Run all collectors, merge, and return the unified row list.

    If multiple sources mention the same id, their sources lists are merged.
    The first collector to see an id wins the non-sources fields; subsequent
    collectors only append their source name.
    """
    merged: dict[str, dict[str, Any]] = {}

    for source_name, collector in COLLECTORS:
        rows = collector()
        for row in rows:
            rid = row["id"]
            if rid in merged:
                # Merge sources
                existing = merged[rid]
                if source_name not in existing["sources"]:
                    existing["sources"].append(source_name)
                # Refresh last_seen if this source saw it more recently
                if row["last_seen"] > existing["last_seen"]:
                    existing["last_seen"] = row["last_seen"]
            else:
                row = dict(row)
                if source_name not in row["sources"]:
                    row["sources"] = row["sources"] + [source_name] if source_name not in row.get("sources", []) else row["sources"]
                merged[rid] = row

    return list(merged.values())


def validate_rows(rows: list[dict[str, Any]]) -> list[str]:
    """Return a list of validation errors. Empty list = all rows valid."""
    errors: list[str] = []
    required_fields = {"id", "kind", "title", "last_seen", "open_loop", "next_seat", "sources", "promise"}

    for i, row in enumerate(rows):
        missing = required_fields - set(row.keys())
        if missing:
            errors.append(f"row {i} ({row.get('id', '?')}): missing fields {sorted(missing)}")
            continue

        if row["kind"] not in KNOWN_KINDS:
            errors.append(f"row {i} ({row['id']}): unknown kind '{row['kind']}'")

        if not row["id"]:
            errors.append(f"row {i}: empty id")

        if not row["title"]:
            errors.append(f"row {i} ({row['id']}): empty title")

        if row["kind"] == "promise" and not row["promise"]:
            errors.append(f"row {i} ({row['id']}): promise-kind row with null promise text")

    return errors


def upsert_to_neon(rows: list[dict[str, Any]], dsn: str) -> tuple[int, int]:
    """Upsert rows into session_work. Returns (inserted, updated).

    Uses ON CONFLICT (id) DO UPDATE — idempotent. last_seen is refreshed
    on every run so staleness is queryable.

    The session_work table has a version column (int not null default 1)
    so trg_touch_row() — which does `new.version := old.version + 1` —
    works on the UPDATE path. Without the version column the trigger
    would crash on the first upsert.
    """
    import psycopg

    inserted = 0
    updated = 0

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                insert into session_work
                    (id, kind, title, last_seen, open_loop, next_seat, sources, promise, kin)
                values
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (id) do update set
                    kind      = excluded.kind,
                    title     = excluded.title,
                    last_seen = excluded.last_seen,
                    open_loop = excluded.open_loop,
                    next_seat = excluded.next_seat,
                    sources   = excluded.sources,
                    promise   = excluded.promise,
                    kin       = excluded.kin
                returning (xmax = 0) as was_insert
                """,
                (
                    row["id"],
                    row["kind"],
                    row["title"],
                    row["last_seen"],
                    row["open_loop"],
                    row["next_seat"],
                    row["sources"],
                    row["promise"],
                    row["kin"],
                ),
            )
            result = cur.fetchone()
            if result and result[0]:
                inserted += 1
            else:
                updated += 1
        conn.commit()

    return inserted, updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Session CRM v0 harvest")
    parser.add_argument("--dry-run", action="store_true",
                        help="Collect and validate without writing to Neon")
    parser.add_argument("--json-out", type=str, default=None,
                        help="Write the harvested rows to a JSON file")
    parser.add_argument("--print", action="store_true",
                        help="Print rows as JSON to stdout")
    args = parser.parse_args()

    rows = harvest()
    errors = validate_rows(rows)

    if errors:
        for e in errors:
            print(f"VALIDATION ERROR: {e}", file=sys.stderr)
        return 1

    print(f"harvest: {len(rows)} rows collected, 0 validation errors", file=sys.stderr)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rows, indent=2, default=str) + "\n")
        print(f"harvest: wrote {len(rows)} rows to {out_path}", file=sys.stderr)

    if args.print:
        print(json.dumps(rows, indent=2, default=str))

    if args.dry_run:
        print("harvest: --dry-run, skipping Neon upsert", file=sys.stderr)
        return 0

    dsn = (
        os.environ.get("CARR_DB_JOBS_URL")
        or os.environ.get("CARR_IMPORT_DB_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not dsn:
        print("harvest: NOT CONFIGURED — set CARR_DB_JOBS_URL to upsert to Neon", file=sys.stderr)
        print("harvest: use --dry-run or --json-out to collect without a database", file=sys.stderr)
        return 78  # NOT CONFIGURED, same convention as cadence_engine

    inserted, updated = upsert_to_neon(rows, dsn)
    print(f"harvest: {inserted} inserted, {updated} updated in session_work", file=sys.stderr)

    return 0
