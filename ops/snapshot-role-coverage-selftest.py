#!/usr/bin/env python3
"""Every role a migration creates must also be created by the schema snapshot.

WHY THIS EXISTS. db/schema.sql is the base every fresh database is built from,
and the migrations govern changes going forward. Those two facts have a sharp
edge where roles are concerned: a role arrives in a `create role` inside some
migration, and that works right up until a snapshot refresh carries the ledger
PAST that migration. From that moment the migration is applied, so it never
replays, so nothing creates the role — and the snapshot has to carry it instead.

THE TRAP HAS SPRUNG FOUR TIMES, every one caught by a rebuild failing rather
than by the change that made the role:

    0115  carr_reader, carr_writer   CI died on migration 0117
    0006  carr_exporter              2026-08-14
    0161  carr_authority             2026-08-19, five db-gates at once
    0163  carr_device_evidence       2026-08-20, and this one did not even fail
                                     cleanly: has_function_privilege() RAISES on
                                     a missing role, so the gate crashed with a
                                     traceback instead of reporting a finding

Each time the fix was one line in the preamble. Each time it was found days or
weeks later, in a failure that named a symptom rather than the cause. This check
is the whole feedback loop moved forward: it runs in CI, needs no database, and
names the missing role and the migration that created it, on the commit that
adds it.

DELIBERATE EXCLUSION, and the reason it is safe to have one. A role may be
created by a migration and deliberately NOT carried by the snapshot — carr_backup
is, because it is the backup credential, bin/backup-dump.sh supplies it, no gate
asks for it, and minting a second login role with a placeholder password to
satisfy nothing is a cost with no buyer. An exclusion has to be written down
HERE, with its reason, so that skipping a role is a decision somebody made and
not an oversight nobody noticed.

Run: python3 ops/snapshot-role-coverage-selftest.py
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO / "migrations"
SNAPSHOT_SCRIPT = REPO / "bin" / "schema-snapshot.sh"
GRANTS_TEST = REPO / "tools" / "test-schema-snapshot-grants.py"

# role -> why the snapshot deliberately does not create it
EXCLUDED = {
    "carr_backup": (
        "the backup credential; bin/backup-dump.sh supplies it, no gate asks "
        "for it, and a second login role with a placeholder password would "
        "satisfy nothing"
    ),
}

CREATE_ROLE = re.compile(r"\bcreate\s+role\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok and detail:
        for line in detail.splitlines():
            print("       " + line)
    return ok


def main() -> int:
    if not MIGRATIONS.is_dir():
        print("snapshot-role-coverage: no migrations directory")
        return 0

    created: dict[str, str] = {}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        for role in CREATE_ROLE.findall(path.read_text(encoding="utf-8", errors="ignore")):
            created.setdefault(role.lower(), path.name)

    print(f"snapshot role coverage — {len(created)} role(s) created across migrations")
    if not created:
        return 0

    script = SNAPSHOT_SCRIPT.read_text(encoding="utf-8")
    grants = GRANTS_TEST.read_text(encoding="utf-8") if GRANTS_TEST.exists() else ""
    results: list[bool] = []

    for role, migration in sorted(created.items()):
        if role in EXCLUDED:
            results.append(check(
                f"{role} is deliberately excluded ({migration})", True))
            continue
        results.append(check(
            f"{role} is created by the snapshot ({migration})",
            role in script,
            f"{migration} runs `create role {role}`, but bin/schema-snapshot.sh\n"
            f"never names it. The day that migration's ledger entry lands, it\n"
            f"stops creating the role and nothing else does. Add it to the role\n"
            f"preamble, or record it in EXCLUDED here with the reason."))

    # The grants test holds the same role set independently, and a role present
    # in one and missing from the other reports its own grants as strays — which
    # is exactly how carr_authority and carr_device_evidence each failed twice.
    if grants:
        for role, migration in sorted(created.items()):
            if role in EXCLUDED or role not in script:
                continue
            results.append(check(
                f"{role} is also known to the grants test",
                role in grants,
                f"bin/schema-snapshot.sh creates {role} but\n"
                f"tools/test-schema-snapshot-grants.py does not list it, so the\n"
                f"snapshot's own grants to it will report as strays."))

    passed = sum(results)
    print(f"passed {passed} · failed {len(results) - passed}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
