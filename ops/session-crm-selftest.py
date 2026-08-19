#!/usr/bin/env python3
"""Selftest for bin/session-crm.py — the staff session-work harvest.

WHY THIS EXISTS. Several sessions, worktrees, PRs, and kanban tasks share one
Mac. The harvest normalizes them into one JSON book of SESSION WORK with a
fixed row shape. A row missing a field is a silent lie — Doc reads the book and
acts on it, so every row must carry every required field or the selftest
fails. No model, no inference, no guessing: the shape is the contract.

The selftest tests the PURE PARSERS (parse_worktrees, parse_prs, etc.) with
fixture data so it never touches live git, gh, hermes, or the kanban DB. The
integration — calling the real sources — is proven by running the harvest
on this Mac and reading the JSON it writes.

Run: python3 ops/session-crm-selftest.py
"""
from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "bin", "session-crm.py")

spec = importlib.util.spec_from_file_location("session_crm", SCRIPT)
assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
crm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crm)

CASES: list[tuple] = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


REQUIRED = crm.REQUIRED_FIELDS


# ── validate_row ──────────────────────────────────────────────────────────

@case("a complete row passes validation")
def _(assert_):
    row = {
        "id": "wt:main", "kind": "worktree", "title": "main checkout",
        "last_seen": "2026-08-18T22:00:00Z", "open_loop": None,
        "next_seat": None, "sources": ["git"],
    }
    errors = crm.validate_row(row)
    assert_(errors == [], f"a complete row should have no errors, got {errors!r}")


@case("a row missing id fails")
def _(assert_):
    row = {"kind": "worktree", "title": "x", "last_seen": "2026-08-18T22:00:00Z",
           "open_loop": None, "next_seat": None, "sources": ["git"]}
    errors = crm.validate_row(row)
    assert_("id" in " ".join(errors), f"missing id should be flagged: {errors!r}")


@case("a row missing kind fails")
def _(assert_):
    row = {"id": "x", "title": "x", "last_seen": "2026-08-18T22:00:00Z",
           "open_loop": None, "next_seat": None, "sources": ["git"]}
    errors = crm.validate_row(row)
    assert_("kind" in " ".join(errors), f"missing kind should be flagged: {errors!r}")


@case("a row missing title fails")
def _(assert_):
    row = {"id": "x", "kind": "worktree", "last_seen": "2026-08-18T22:00:00Z",
           "open_loop": None, "next_seat": None, "sources": ["git"]}
    errors = crm.validate_row(row)
    assert_("title" in " ".join(errors), f"missing title should be flagged: {errors!r}")


@case("a row missing last_seen fails")
def _(assert_):
    row = {"id": "x", "kind": "worktree", "title": "x",
           "open_loop": None, "next_seat": None, "sources": ["git"]}
    errors = crm.validate_row(row)
    assert_("last_seen" in " ".join(errors), f"missing last_seen should be flagged: {errors!r}")


@case("a row missing open_loop fails")
def _(assert_):
    row = {"id": "x", "kind": "worktree", "title": "x",
           "last_seen": "2026-08-18T22:00:00Z", "next_seat": None, "sources": ["git"]}
    errors = crm.validate_row(row)
    assert_("open_loop" in " ".join(errors), f"missing open_loop should be flagged: {errors!r}")


@case("a row missing next_seat fails")
def _(assert_):
    row = {"id": "x", "kind": "worktree", "title": "x",
           "last_seen": "2026-08-18T22:00:00Z", "open_loop": None, "sources": ["git"]}
    errors = crm.validate_row(row)
    assert_("next_seat" in " ".join(errors), f"missing next_seat should be flagged: {errors!r}")


@case("a row missing sources fails")
def _(assert_):
    row = {"id": "x", "kind": "worktree", "title": "x",
           "last_seen": "2026-08-18T22:00:00Z", "open_loop": None, "next_seat": None}
    errors = crm.validate_row(row)
    assert_("sources" in " ".join(errors), f"missing sources should be flagged: {errors!r}")


@case("a row with an invalid kind fails")
def _(assert_):
    row = {"id": "x", "kind": "bogus", "title": "x",
           "last_seen": "2026-08-18T22:00:00Z", "open_loop": None,
           "next_seat": None, "sources": ["git"]}
    errors = crm.validate_row(row)
    assert_("kind" in " ".join(errors).lower(), f"invalid kind should be flagged: {errors!r}")


@case("a row with empty sources list fails")
def _(assert_):
    row = {"id": "x", "kind": "worktree", "title": "x",
           "last_seen": "2026-08-18T22:00:00Z", "open_loop": None,
           "next_seat": None, "sources": []}
    errors = crm.validate_row(row)
    assert_("sources" in " ".join(errors), f"empty sources should be flagged: {errors!r}")


# ── parse_worktrees ────────────────────────────────────────────────────────

WORKTREE_PORCELAIN = """\
worktree /Users/booko/carr-system
HEAD df6aaf0811a92b63a787dfc8db383a3e673b7c84
branch refs/heads/main

worktree /Users/booko/carr-system/.claude/worktrees/session-crm-v0
HEAD df6aaf0811a92b63a787dfc8db383a3e673b7c84
branch refs/heads/session-crm-v0

worktree /Users/booko/carr-system/.worktrees/t_34f0d25d
HEAD df6aaf0811a92b63a787dfc8db383a3e673b7c84
branch refs/heads/wt/t_34f0d25d
"""


@case("parse_worktrees produces one row per worktree with all required fields")
def _(assert_):
    rows = crm.parse_worktrees(WORKTREE_PORCELAIN)
    assert_(len(rows) == 3, f"expected 3 worktrees, got {len(rows)}")
    for r in rows:
        errors = crm.validate_row(r)
        assert_(errors == [], f"worktree row invalid: {errors!r} — {r!r}")
        assert_(r["kind"] == "worktree", f"kind should be worktree: {r['kind']!r}")


@case("parse_worktrees extracts branch name and path into id and title")
def _(assert_):
    rows = crm.parse_worktrees(WORKTREE_PORCELAIN)
    ids = [r["id"] for r in rows]
    assert_("wt:main" in ids, f"should have wt:main, got {ids!r}")
    assert_("wt:session-crm-v0" in ids, f"should have wt:session-crm-v0, got {ids!r}")


@case("parse_worktrees includes git in sources")
def _(assert_):
    rows = crm.parse_worktrees(WORKTREE_PORCELAIN)
    for r in rows:
        assert_("git" in r["sources"], f"sources should include git: {r['sources']!r}")


# ── parse_prs ──────────────────────────────────────────────────────────────

PR_JSON = [
    {"number": 344, "title": "Number incidents off one clock",
     "headRefName": "claude/relaxed-snyder-ba6f34",
     "updatedAt": "2026-08-19T02:07:21Z"},
    {"number": 335, "title": "Dell's Mac runs its own tasks",
     "headRefName": "fix/dell-tasks-off-drive-t83",
     "updatedAt": "2026-08-19T03:02:45Z"},
]


@case("parse_prs produces one row per open PR with all required fields")
def _(assert_):
    rows = crm.parse_prs(PR_JSON)
    assert_(len(rows) == 2, f"expected 2 PRs, got {len(rows)}")
    for r in rows:
        errors = crm.validate_row(r)
        assert_(errors == [], f"PR row invalid: {errors!r} — {r!r}")
        assert_(r["kind"] == "pr", f"kind should be pr: {r['kind']!r}")


@case("parse_prs uses PR number in id and updatedAt as last_seen")
def _(assert_):
    rows = crm.parse_prs(PR_JSON)
    assert_(rows[0]["id"] == "pr:344", f"id should be pr:344: {rows[0]['id']!r}")
    assert_(rows[0]["last_seen"] == "2026-08-19T02:07:21Z",
            f"last_seen should match updatedAt: {rows[0]['last_seen']!r}")


@case("parse_prs includes gh in sources")
def _(assert_):
    rows = crm.parse_prs(PR_JSON)
    for r in rows:
        assert_("gh" in r["sources"], f"sources should include gh: {r['sources']!r}")


# ── parse_sessions ─────────────────────────────────────────────────────────

SESSIONS_TEXT = """\
Title                        Workspace          Last Active   ID
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
Build Session CRM v0 in Ne   \u2014                  just now      20260818_225354_71dcb9
Work kanban task t_34f0d25   \u2014                  just now      20260818_225302_b64dc7
Report agent_principal_id    carr-system        1h ago        20260818_210114_7e8e3a
"""


@case("parse_sessions produces one row per session with all required fields")
def _(assert_):
    rows = crm.parse_sessions(SESSIONS_TEXT)
    assert_(len(rows) == 3, f"expected 3 sessions, got {len(rows)}")
    for r in rows:
        errors = crm.validate_row(r)
        assert_(errors == [], f"session row invalid: {errors!r} — {r!r}")
        assert_(r["kind"] == "session", f"kind should be session: {r['kind']!r}")


@case("parse_sessions extracts session ID and derives last_seen from it")
def _(assert_):
    rows = crm.parse_sessions(SESSIONS_TEXT)
    ids = [r["id"] for r in rows]
    assert_("session:20260818_225354_71dcb9" in ids, f"should have session id: {ids!r}")
    for r in rows:
        assert_(r["last_seen"] is not None, f"last_seen should not be None: {r!r}")
        assert_("T" in r["last_seen"], f"last_seen should be ISO: {r['last_seen']!r}")


@case("parse_sessions includes hermes in sources")
def _(assert_):
    rows = crm.parse_sessions(SESSIONS_TEXT)
    for r in rows:
        assert_("hermes" in r["sources"], f"sources should include hermes: {r['sources']!r}")


# ── parse_kanban ───────────────────────────────────────────────────────────

KANBAN_TASKS = [
    {"id": "t_34f0d25d", "title": "Implement session-crm v0", "status": "running",
     "assignee": "builder", "started_at": 1787111580, "last_heartbeat_at": 1787111586},
    {"id": "t_3c1b692c", "title": "Session CRM v0: harvest the staff book", "status": "todo",
     "assignee": "default", "started_at": None, "last_heartbeat_at": None},
]


@case("parse_kanban produces one row per task with all required fields")
def _(assert_):
    rows = crm.parse_kanban(KANBAN_TASKS)
    assert_(len(rows) == 2, f"expected 2 kanban tasks, got {len(rows)}")
    for r in rows:
        errors = crm.validate_row(r)
        assert_(errors == [], f"kanban row invalid: {errors!r} — {r!r}")
        assert_(r["kind"] == "kanban", f"kind should be kanban: {r['kind']!r}")


@case("parse_kanban uses task id and converts heartbeat to ISO last_seen")
def _(assert_):
    rows = crm.parse_kanban(KANBAN_TASKS)
    running = [r for r in rows if r["id"] == "kanban:t_34f0d25d"][0]
    assert_(running["last_seen"] is not None, f"running task should have last_seen: {running!r}")
    assert_("T" in running["last_seen"], f"last_seen should be ISO: {running['last_seen']!r}")


@case("parse_kanban includes kanban in sources")
def _(assert_):
    rows = crm.parse_kanban(KANBAN_TASKS)
    for r in rows:
        assert_("kanban" in r["sources"], f"sources should include kanban: {r['sources']!r}")


# ── seeded_promises ────────────────────────────────────────────────────────

@case("seeded_promises returns rows with all required fields")
def _(assert_):
    rows = crm.seeded_promises()
    assert_(len(rows) >= 4, f"expected >=4 seeded promises, got {len(rows)}")
    for r in rows:
        errors = crm.validate_row(r)
        assert_(errors == [], f"promise row invalid: {errors!r} — {r!r}")
        assert_(r["kind"] == "promise", f"kind should be promise: {r['kind']!r}")
        assert_(r.get("promise") is not None, f"promise field should be set: {r!r}")


@case("seeded_promises includes the four known commitments")
def _(assert_):
    rows = crm.seeded_promises()
    ids = [r["id"] for r in rows]
    assert_("promise:codex-trees-stay" in ids, f"missing codex-trees-stay: {ids!r}")
    assert_("promise:phone-doc-no-claude" in ids, f"missing phone-doc-no-claude: {ids!r}")
    assert_("promise:loop-455-fable" in ids, f"missing loop-455-fable: {ids!r}")
    assert_("promise:bot-mode-parked" in ids, f"missing bot-mode-parked: {ids!r}")


# ── brief ──────────────────────────────────────────────────────────────────

def _fixture_book():
    now = "2026-08-19T03:10:00Z"
    stale = "2026-08-18T20:00:00Z"
    return {
        "harvested_at": now,
        "rows": [
            {"id": "wt:main", "kind": "worktree", "title": "main checkout",
             "last_seen": stale, "open_loop": None, "next_seat": None, "sources": ["git"]},
            {"id": "pr:344", "kind": "pr", "title": "Number incidents off one clock",
             "last_seen": "2026-08-19T03:07:21Z", "open_loop": "CI red",
             "next_seat": "fix CI", "sources": ["gh"]},
            {"id": "session:20260818_225354", "kind": "session", "title": "Build Session CRM",
             "last_seen": "2026-08-19T03:05:00Z", "open_loop": None,
             "next_seat": None, "sources": ["hermes"]},
            {"id": "kanban:t_34f0d25d", "kind": "kanban", "title": "Implement session-crm v0",
             "last_seen": "2026-08-19T03:08:00Z", "open_loop": "in progress",
             "next_seat": "finish harvest", "sources": ["kanban"]},
            {"id": "promise:codex-trees-stay", "kind": "promise", "title": "Codex trees stay",
             "last_seen": now, "open_loop": "Codex trees must not be reaped",
             "next_seat": "Leave them alone", "sources": ["seed"],
             "promise": "Codex trees stay"},
            {"id": "promise:bot-mode-parked", "kind": "promise", "title": "Bot Mode parked",
             "last_seen": now, "open_loop": "Bot Mode is parked",
             "next_seat": "Leave parked", "sources": ["seed"],
             "promise": "Bot Mode parked"},
        ],
    }


@case("brief lists the 3 most recently active non-promise rows as DOING")
def _(assert_):
    out = crm.brief(_fixture_book(), now="2026-08-19T03:10:00Z")
    assert_("Implement session-crm v0" in out, f"doing should include kanban task: {out!r}")
    assert_("Number incidents" in out, f"doing should include PR: {out!r}")
    assert_("Build Session CRM" in out, f"doing should include session: {out!r}")


@case("brief flags rows stale >4h")
def _(assert_):
    out = crm.brief(_fixture_book(), now="2026-08-19T03:10:00Z")
    assert_("main checkout" in out, f"stale worktree should be named: {out!r}")


@case("brief lists promises")
def _(assert_):
    out = crm.brief(_fixture_book(), now="2026-08-19T03:10:00Z")
    assert_("Codex trees stay" in out, f"promise should be named: {out!r}")
    assert_("Bot Mode parked" in out, f"promise should be named: {out!r}")


@case("brief with no rows still produces output")
def _(assert_):
    book = {"harvested_at": "2026-08-19T03:10:00Z", "rows": []}
    out = crm.brief(book, now="2026-08-19T03:10:00Z")
    assert_(isinstance(out, str), f"brief should return a string: {type(out)}")
    assert_(len(out) > 0, f"brief should not be empty even with no rows")


# ── harvest output shape ───────────────────────────────────────────────────

@case("a harvested book has harvested_at and rows")
def _(assert_):
    rows = (
        crm.parse_worktrees(WORKTREE_PORCELAIN)
        + crm.parse_prs(PR_JSON)
        + crm.parse_sessions(SESSIONS_TEXT)
        + crm.parse_kanban(KANBAN_TASKS)
        + crm.seeded_promises()
    )
    book = crm.build_book(rows, harvested_at="2026-08-19T03:10:00Z")
    assert_("harvested_at" in book, f"book should have harvested_at: {book.keys()!r}")
    assert_("rows" in book, f"book should have rows: {book.keys()!r}")
    assert_(isinstance(book["rows"], list), f"rows should be a list: {type(book['rows'])}")
    for r in book["rows"]:
        errors = crm.validate_row(r)
        assert_(errors == [], f"row failed validation: {errors!r} — {r!r}")


@case("every row in a merged book has all required fields")
def _(assert_):
    rows = (
        crm.parse_worktrees(WORKTREE_PORCELAIN)
        + crm.parse_prs(PR_JSON)
        + crm.parse_sessions(SESSIONS_TEXT)
        + crm.parse_kanban(KANBAN_TASKS)
        + crm.seeded_promises()
    )
    for r in rows:
        for field in REQUIRED:
            assert_(field in r, f"row {r.get('id', '?')} missing field {field}: {r!r}")


def main():
    failures = []
    for name, fn in CASES:
        errors = []

        def assert_(cond, msg):
            if not cond:
                errors.append(msg)

        try:
            fn(assert_)
        except Exception as exc:
            errors.append(f"raised {type(exc).__name__}: {exc}")
        if errors:
            print(f"[FAIL] {name}")
            for e in errors:
                print(f"    - {e}")
            failures.append(name)
        else:
            print(f"[PASS] {name}")

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
