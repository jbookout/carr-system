#!/usr/bin/env python3
"""Proves guard-unattended.py blocks real destructive SQL and stops blocking English
prose that happens to quote a SQL keyword (loop #240).

WHY THIS EXISTS. The destructive-SQL patterns scan the whole command string, so
`\\btruncate\\s+(table\\s+)?\\w` matched the phrase "truncate honestly" inside a loop's
closing outcome and the session could not file it. No database was involved anywhere
in that command. It is the SAME defect as the egress one fixed earlier the same day,
in the other rule set: a rule keying on a word rather than on what the command does.

THE PATTERNS ARE NOT LOOSENED. A real TRUNCATE, DROP, unqualified DELETE or
unqualified UPDATE stays blocked. What changed is that they are consulted only when
the command plausibly executes SQL. This file is the standing proof of both halves,
because loosening a destructive-operation guard needs a regression test rather than a
one-time eyeball.

A NOTE ON RUNNING IT. The cases below contain real DROP and TRUNCATE statements on
purpose. That is exactly why they live in a file: putting them on a Bash command line
makes the LIVE guard scan them as the command under test, which is the bug this file
is about — it blocked its own first test run on 2026-08-13, twice.

    .venv/bin/python ops/sql-scope-selftest.py
"""
import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(REPO, "hooks", "guard-unattended.py")

spec = importlib.util.spec_from_file_location("guard_unattended", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

# Prose. Every one of these quotes a destructive SQL keyword and none of them touches
# a database. The first is the exact shape that blocked a loop close.
MUST_ALLOW = [
    'echo "shorten honestly rather than truncate the list" >> notes.md',
    'git commit -m "drop the stale row and truncate the label"',
    'echo "the migration will drop table party_old next week" >> plan.md',
    'grep -n "delete from" migrations/README.md',
    'echo "update deal set is what the verb does under the hood" >> notes.md',
]

# Real operations against a real database. Every one must stay blocked.
MUST_BLOCK = [
    'psql -c "truncate table client"',
    'psql "$DATABASE_URL" -c "drop table party"',
    'psql -c "delete from lead;"',
    'psql -c "update deal set phase = 1"',
    'psql -c "drop schema public cascade"',
    '.venv/bin/python tools/db-tap.py sql wipe.sql  # truncate table event',
]


def main():
    failures = []
    for cmd in MUST_ALLOW:
        reason = guard.check(cmd)
        if reason:
            failures.append(("should ALLOW", cmd, reason))
    for cmd in MUST_BLOCK:
        reason = guard.check(cmd)
        if not reason:
            failures.append(("should BLOCK", cmd, "allowed"))

    for kind, cmd, reason in failures:
        print(f"FAIL ({kind}): {' '.join(cmd.split())[:90]}\n        -> {str(reason)[:90]}")

    total = len(MUST_ALLOW) + len(MUST_BLOCK)
    if failures:
        print(f"sql scope: {len(failures)} of {total} FAILED")
        return 1
    print(f"sql scope: {len(MUST_ALLOW)} prose commands allowed, "
          f"{len(MUST_BLOCK)} real destructive statements blocked — {total}/{total} pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
