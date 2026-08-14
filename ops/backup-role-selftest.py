#!/usr/bin/env python3
"""Static contract for the dedicated backup role's sequence privileges.

PostgreSQL grants table and sequence privileges independently.  A role that
can SELECT every table can still fail pg_dump when it reads a sequence's last
value.  CI also executes the migration guards against a throwaway database;
this fast check keeps the intended least-privilege SQL from disappearing.
"""

import re
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    raw_sql = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO / "migrations").glob("*.sql"))
    ).lower()
    sql = re.sub(r"\s+", " ", raw_sql)
    required = (
        "grant select on all sequences in schema public to carr_backup;",
        "grant select on all sequences in schema ops to carr_backup;",
        "alter default privileges in schema public grant select on sequences to carr_backup;",
        "alter default privileges in schema ops grant select on sequences to carr_backup;",
    )
    missing = [
        statement
        for statement in required
        if re.sub(r"\s+", " ", statement) not in sql
    ]
    if not re.search(r"has_sequence_privilege\s*\(\s*'carr_backup'", raw_sql):
        missing.append("runtime has_sequence_privilege guard for carr_backup")
    assert not missing, "backup role sequence contract missing: " + ", ".join(missing)

    forbidden = (
        "grant usage on all sequences in schema public to carr_backup;",
        "grant update on all sequences in schema public to carr_backup;",
        "grant usage on all sequences in schema ops to carr_backup;",
        "grant update on all sequences in schema ops to carr_backup;",
    )
    excess = [statement for statement in forbidden if statement in sql]
    assert not excess, "backup role gained unnecessary sequence powers: " + ", ".join(excess)

    print("backup-role-selftest: SELECT-only sequence coverage present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
