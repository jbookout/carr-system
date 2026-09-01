#!/usr/bin/env python3
"""Source contract: the nightly backup must read every RLS-guarded row.

bin/backup-dump.sh dumps with --enable-row-security (row_security=on) so the
carr_backup role's read of ops.work_request is not refused by 0324's RLS
(WR-000044, decision 11376c54).  That flag trades a fail-CLOSED behavior for a
fail-OPEN one: with row_security on, a table whose RLS hides rows from
carr_backup is dumped SHORT and SILENT rather than erroring.

So every table in public+ops that has RLS enabled must carry a permissive
carr_backup SELECT policy with no row filter (USING (true)).  Today
ops.work_request is the only one; migration 0475 adds its policy.  This
selftest fails loudly the moment a future migration enables RLS on a public/ops
table without also granting carr_backup an unfiltered read — before that
migration can silently shrink the backup.

Pure source analysis over migrations/ and bin/backup-dump.sh; no database.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
BACKUP_SCRIPT = ROOT / "bin" / "backup-dump.sh"

FAIL: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}  {detail}")


def normalized(sql: str) -> str:
    """Lowercase, strip line comments, collapse whitespace — for coarse matching."""
    no_comments = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"\s+", " ", no_comments.lower()).strip()


ENABLE_RE = re.compile(r"alter table (?:only )?([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*) enable row level security")
DISABLE_RE = re.compile(r"alter table (?:only )?([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*) disable row level security")


def backup_policy_re(table: str) -> re.Pattern[str]:
    # create policy <name> on <table> for select to carr_backup using (true)
    t = re.escape(table)
    return re.compile(
        r"create policy [a-z_][a-z0-9_]* on " + t
        + r" for select to carr_backup using \(\s*true\s*\)"
    )


def main() -> int:
    corpus = "\n".join(
        normalized(p.read_text(encoding="utf-8"))
        for p in sorted(MIGRATIONS.glob("*.sql"))
    )

    enabled = set(ENABLE_RE.findall(corpus))
    disabled = set(DISABLE_RE.findall(corpus))
    rls_tables = sorted(enabled - disabled)

    check(
        "at least one public/ops table has RLS (sanity: ops.work_request)",
        "ops.work_request" in rls_tables,
        f"rls_tables={rls_tables}",
    )

    # Only public+ops are in the backup's schema scope (--schema=public --schema=ops).
    scoped = [t for t in rls_tables if t.split(".", 1)[0] in ("public", "ops")]
    for table in scoped:
        covered = bool(backup_policy_re(table).search(corpus))
        check(
            f"RLS table {table} has a permissive carr_backup read-all policy in migrations",
            covered,
            "no `create policy ... on {t} for select to carr_backup using (true)` found — "
            "with --enable-row-security this table would dump SHORT and SILENT".format(t=table),
        )

    script = BACKUP_SCRIPT.read_text(encoding="utf-8")
    check(
        "bin/backup-dump.sh dumps with --enable-row-security",
        "--enable-row-security" in script,
        "row_security must be on so the carr_backup policy applies instead of the dump erroring",
    )
    # Guard the exact invocation so a refactor cannot drop the flag unnoticed.
    check(
        "the pg_dump invocation itself carries --enable-row-security",
        re.search(
            r'"\$PG_DUMP_BIN"[^\n]*--enable-row-security[^\n]*"\$URL"', script
        )
        is not None,
        "the flag must be on the pg_dump command line, not merely mentioned in a comment",
    )

    if FAIL:
        print(f"\nbackup-role-rls-coverage-selftest: {len(FAIL)} FAILED: {FAIL}", file=sys.stderr)
        return 1
    print(
        f"backup-role-rls-coverage-selftest: {len(scoped)} RLS table(s) covered, "
        "backup reads under row_security=on"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
