#!/usr/bin/env python3
"""bin/session-crm.py — harvest the staff book of SESSION WORK.

WHY THIS EXISTS. Several sessions, worktrees, PRs, and kanban tasks share one
Mac, and nobody can see them all at once. This harvest normalizes them into one
JSON book with a fixed row shape, then prints a brief morning CoS view.

It is deterministic, model-free, and read-only. It writes to
~/.hermes/session-crm/current.json (Hermes-local only; not Drive, not Neon).

USAGE
    python3 bin/session-crm.py harvest     # write current.json
    python3 bin/session-crm.py brief       # print the morning CoS view
    python3 bin/session-crm.py harvest --brief  # both

The selftest (ops/session-crm-selftest.py) tests the pure parsers with fixture
data. The integration is proven by running the harvest on this Mac.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path

# ── constants ──────────────────────────────────────────────────────────────

REPO = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path.home() / ".hermes" / "session-crm"
OUTPUT_FILE = OUTPUT_DIR / "current.json"

REQUIRED_FIELDS = ["id", "kind", "title", "last_seen", "open_loop", "next_seat", "sources"]
VALID_KINDS = {"worktree", "pr", "session", "kanban", "promise"}
STALE_HOURS = 4

# Seed promises — the standing commitments that don't come from any source.
_SEED_PROMISES = [
    {
        "id": "promise:codex-trees-stay",
        "title": "Codex trees stay",
        "open_loop": "Codex control-plane worktrees must not be reaped",
        "next_seat": "Leave them alone",
        "promise": "Codex trees stay",
    },
    {
        "id": "promise:phone-doc-no-claude",
        "title": "Phone Doc does not spawn Claude",
        "open_loop": "The phone-facing Doc bot does not spawn Claude Code sessions",
        "next_seat": "Keep it that way",
        "promise": "Phone Doc does not spawn Claude",
    },
    {
        "id": "promise:loop-455-fable",
        "title": "Loop 455 waits for Fable",
        "open_loop": "Loop 455 is parked waiting on Fable",
        "next_seat": "Wait for Fable",
        "promise": "Loop 455 waits for Fable",
    },
    {
        "id": "promise:bot-mode-parked",
        "title": "Bot Mode parked",
        "open_loop": "Bot Mode is parked, not active",
        "next_seat": "Leave parked",
        "promise": "Bot Mode parked",
    },
]


# ── validation ─────────────────────────────────────────────────────────────

def validate_row(row: dict) -> list[str]:
    """Return a list of error strings. Empty list = valid."""
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in row:
            errors.append(f"missing required field: {field}")
    if "kind" in row and row["kind"] not in VALID_KINDS:
        errors.append(f"invalid kind: {row['kind']!r} (must be one of {VALID_KINDS})")
    if "sources" in row and not isinstance(row.get("sources"), list):
        errors.append("sources must be a list")
    elif "sources" in row and len(row["sources"]) == 0:
        errors.append("sources must not be empty")
    return errors


# ── parsers (pure functions, testable with fixtures) ───────────────────────

def parse_worktrees(porcelain: str) -> list[dict]:
    """Parse `git worktree list --porcelain` output into session-crm rows."""
    rows = []
    blocks = porcelain.strip().split("\n\n")
    for block in blocks:
        if not block.strip():
            continue
        lines = block.strip().split("\n")
        path = None
        branch = None
        for line in lines:
            if line.startswith("worktree "):
                path = line[len("worktree "):]
            elif line.startswith("branch "):
                branch = line[len("branch "):]
                if branch.startswith("refs/heads/"):
                    branch = branch[len("refs/heads/"):]
        if not path:
            continue
        if branch:
            name = branch
            row_id = f"wt:{branch}"
        else:
            name = os.path.basename(path)
            row_id = f"wt:{name}"
        rows.append({
            "id": row_id,
            "kind": "worktree",
            "title": name,
            "last_seen": None,
            "open_loop": None,
            "next_seat": None,
            "sources": ["git"],
        })
    return rows


def parse_prs(pr_list: list[dict]) -> list[dict]:
    """Parse `gh pr list --json` output (already a list of dicts) into rows."""
    rows = []
    for pr in pr_list:
        number = pr.get("number", "?")
        title = pr.get("title", "(untitled)")
        updated = pr.get("updatedAt")
        head = pr.get("headRefName", "")
        rows.append({
            "id": f"pr:{number}",
            "kind": "pr",
            "title": title,
            "last_seen": updated,
            "open_loop": f"PR #{number} open on {head}" if head else f"PR #{number} open",
            "next_seat": None,
            "sources": ["gh"],
        })
    return rows


def parse_sessions(text: str) -> list[dict]:
    """Parse `hermes sessions list` text output into rows."""
    rows = []
    lines = text.strip().split("\n")
    in_data = False
    for line in lines:
        if line.startswith("Title") or line.startswith("\u2500") or not line.strip():
            in_data = True
            continue
        if not in_data:
            continue
        parts = line.rsplit(None, 4)
        if len(parts) < 5:
            continue
        session_id = parts[-1]
        title = parts[0]
        if "_" not in session_id or len(session_id) < 10:
            continue
        last_seen = _session_id_to_iso(session_id)
        rows.append({
            "id": f"session:{session_id}",
            "kind": "session",
            "title": title.strip(),
            "last_seen": last_seen,
            "open_loop": None,
            "next_seat": None,
            "sources": ["hermes"],
        })
    return rows


def _session_id_to_iso(session_id: str) -> str:
    """Convert a session ID like 20260818_225354_71dcb9 to an ISO timestamp."""
    try:
        ts_part = session_id.split("_")[0] + session_id.split("_")[1]
        dt = _dt.datetime.strptime(ts_part, "%Y%m%d%H%M%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (IndexError, ValueError):
        return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_kanban(tasks: list[dict]) -> list[dict]:
    """Parse kanban task dicts (from the SQLite DB) into rows."""
    rows = []
    for task in tasks:
        tid = task.get("id", "?")
        title = task.get("title", "(untitled)")
        status = task.get("status", "?")
        assignee = task.get("assignee", "")
        ts = task.get("last_heartbeat_at") or task.get("started_at") or task.get("created_at")
        if ts:
            last_seen = _epoch_to_iso(ts)
        else:
            last_seen = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        open_loop = None
        if status == "running":
            open_loop = f"running (assigned to {assignee})" if assignee else "running"
        elif status == "blocked":
            open_loop = "blocked"
        elif status == "todo":
            open_loop = "waiting in todo"
        rows.append({
            "id": f"kanban:{tid}",
            "kind": "kanban",
            "title": title,
            "last_seen": last_seen,
            "open_loop": open_loop,
            "next_seat": None,
            "sources": ["kanban"],
        })
    return rows


def _epoch_to_iso(epoch):
    """Convert a Unix epoch timestamp to an ISO 8601 UTC string."""
    if epoch is None:
        return None
    try:
        dt = _dt.datetime.fromtimestamp(float(epoch), tz=_dt.timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


def seeded_promises() -> list[dict]:
    """Return the seeded promises list with all required fields."""
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for p in _SEED_PROMISES:
        rows.append({
            "id": p["id"],
            "kind": "promise",
            "title": p["title"],
            "last_seen": now,
            "open_loop": p["open_loop"],
            "next_seat": p["next_seat"],
            "sources": ["seed"],
            "promise": p["promise"],
        })
    return rows


# ── build_book ─────────────────────────────────────────────────────────────

def build_book(rows: list[dict], harvested_at: str | None = None) -> dict:
    """Assemble the final book dict from a list of rows."""
    if harvested_at is None:
        harvested_at = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for row in rows:
        if row.get("last_seen") is None:
            row["last_seen"] = harvested_at
    return {
        "harvested_at": harvested_at,
        "rows": rows,
    }


# ── brief ──────────────────────────────────────────────────────────────────

def brief(book: dict, now: str | None = None) -> str:
    """Print the morning CoS view: 3 doing, stale >4h, promises, kin if any."""
    if now is None:
        now = book.get("harvested_at",
                       _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    rows = book.get("rows", [])
    now_dt = _parse_iso(now or "")
    lines = []
    lines.append("=== Session CRM \u2014 Morning CoS Brief ===")
    lines.append(f"  harvested: {book.get('harvested_at', '?')}")
    lines.append("")
    promises = [r for r in rows if r.get("kind") == "promise"]
    non_promises = [r for r in rows if r.get("kind") != "promise"]

    def _sort_key(r):
        dt = _parse_iso(r.get("last_seen", ""))
        return dt or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)

    non_promises_sorted = sorted(non_promises, key=_sort_key, reverse=True)
    doing = non_promises_sorted[:3]
    lines.append("DOING (3 most recent):")
    if doing:
        for r in doing:
            loop = r.get("open_loop") or "\u2014"
            seat = r.get("next_seat") or "\u2014"
            lines.append(f"  \u2022 {r['title']}  [{r['kind']}]")
            lines.append(f"      last_seen: {r.get('last_seen', '?')}")
            lines.append(f"      open_loop: {loop}")
            lines.append(f"      next_seat: {seat}")
    else:
        lines.append("  (nothing active)")
    lines.append("")
    stale = []
    for r in non_promises_sorted:
        dt = _parse_iso(r.get("last_seen", ""))
        if dt and now_dt and (now_dt - dt).total_seconds() > STALE_HOURS * 3600:
            stale.append(r)
    lines.append(f"STALE (>{STALE_HOURS}h):")
    if stale:
        for r in stale:
            lines.append(
                f"  \u2022 {r['title']}  [{r['kind']}]  last_seen: {r.get('last_seen', '?')}"
            )
    else:
        lines.append("  (nothing stale)")
    lines.append("")
    lines.append("PROMISES:")
    if promises:
        for r in promises:
            p = r.get("promise", r.get("title", "?"))
            lines.append(f"  \u2022 {p}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("KIN:")
    lines.append("  (not tracked in v0)")
    return "\n".join(lines)


def _parse_iso(s: str) -> _dt.datetime | None:
    """Parse an ISO 8601 timestamp string into a timezone-aware datetime."""
    if not s:
        return None
    try:
        clean = s.rstrip("Z")
        dt = _dt.datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ── live source collectors ────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 30) -> str:
    """Run a command and return stdout. Returns empty string on failure."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def collect_worktrees(repo: str) -> list[dict]:
    """Collect git worktrees from the real repo."""
    out = _run(["git", "worktree", "list", "--porcelain"])
    if not out:
        return []
    rows = parse_worktrees(out)
    plain = _run(["git", "worktree", "list"])
    paths = []
    for line in plain.strip().split("\n"):
        if line.strip():
            parts = line.split(None, 1)
            if parts:
                paths.append(parts[0])
    for i, row in enumerate(rows):
        if i < len(paths):
            commit_date = _run(
                ["git", "-C", paths[i], "log", "-1", "--format=%cI", "--no-patch"],
            )
            if commit_date.strip():
                row["last_seen"] = commit_date.strip()
    return rows


def collect_prs(repo: str) -> list[dict]:
    """Collect open PRs from GitHub CLI."""
    out = _run(["gh", "pr", "list", "--state", "open",
                "--json", "number,title,updatedAt,headRefName"])
    if not out:
        return []
    try:
        pr_list = json.loads(out)
    except json.JSONDecodeError:
        return []
    return parse_prs(pr_list)


def collect_sessions() -> list[dict]:
    """Collect hermes sessions."""
    out = _run(["hermes", "sessions", "list", "--limit", "50"])
    if not out:
        return []
    return parse_sessions(out)


def collect_kanban() -> list[dict]:
    """Collect open kanban tasks from the carr-build board DB."""
    db_path = Path.home() / ".hermes" / "kanban" / "boards" / "carr-build" / "kanban.db"
    if not db_path.exists():
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, status, assignee, started_at, last_heartbeat_at, created_at "
            "FROM tasks WHERE status != 'done' ORDER BY created_at DESC"
        )
        tasks = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception:
        return []
    return parse_kanban(tasks)


# ── main ───────────────────────────────────────────────────────────────────

def do_harvest() -> dict:
    """Run the full harvest and write current.json. Returns the book."""
    rows = []
    rows += collect_worktrees(str(REPO))
    rows += collect_prs(str(REPO))
    rows += collect_sessions()
    rows += collect_kanban()
    rows += seeded_promises()
    book = build_book(rows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(book, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return book


def main():
    args = sys.argv[1:]
    if not args or args[0] == "harvest":
        book = do_harvest()
        print(f"Harvested {len(book['rows'])} rows -> {OUTPUT_FILE}")
        if "--brief" in args:
            print()
            print(brief(book))
    elif args[0] == "brief":
        if OUTPUT_FILE.exists():
            book = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
        else:
            book = do_harvest()
            print(f"Harvested {len(book['rows'])} rows -> {OUTPUT_FILE}")
            print()
        print(brief(book))
    else:
        print(f"Unknown command: {args[0]}", file=sys.stderr)
        print("Usage: session-crm.py [harvest|brief] [--brief]", file=sys.stderr)
        sys.exit(64)


if __name__ == "__main__":
    main()
