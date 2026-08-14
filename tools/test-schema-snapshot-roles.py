#!/usr/bin/env python3
"""test-schema-snapshot-roles.py — db/schema.sql must carry the roles its
grants depend on.

WHY THIS EXISTS. db/schema.sql is written by `pg_dump --schema-only --no-owner
--no-acl`, and those flags deliberately drop every OWNER TO and GRANT — an
embedded grant names roles a fresh database has never heard of, and the first
such statement aborts the load. Roles are also CLUSTER-level, so no schema dump
would carry them anyway.

Migration 0115 knew this and created carr_reader, carr_writer and carr_jobs
NOLOGIN for exactly this reason, in its own words: "so that privileges have
somewhere to attach in a rebuilt environment ... which carries no ACLs".

THE TRAP, walked into on 2026-08-14. That only holds while 0115 is PENDING. The
snapshot refresh moved 0115 into the ledger as already-applied, so CI stopped
running it against the rebuilt database, the roles were never created, and the
next migration to grant anything — 0117 — died with `role "carr_jobs" does not
exist`. The failure surfaced one migration later than its cause, on a branch
that had not touched either migration.

The snapshot is supposed to be the way a fresh environment gets built. A build
declaration that omits the roles its own later migrations grant to is not one,
and the gap is invisible until a role-creating migration ages into it — which is
to say, it would have come back.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT = os.path.join(REPO, "db", "schema.sql")

# The roles the system's own migrations grant to. neondb_owner is excluded on
# purpose: it is Neon's, exists on every Neon project and on no vanilla
# Postgres, and .github/workflows/ci.yml creates it for that reason.
EXPECTED_ROLES = ["carr_reader", "carr_writer", "carr_jobs"]

failures: list[str] = []
checked = 0


def check(name, cond, detail=""):
    global checked
    checked += 1
    if cond:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def main():
    if not os.path.exists(SNAPSHOT):
        print(f"FAIL: {SNAPSHOT} not present")
        return 1
    sql = open(SNAPSHOT).read()

    head = sql[:20000]
    for role in EXPECTED_ROLES:
        check(f"the snapshot creates {role}",
              re.search(rf"create role .*{role}|'{role}'", head, re.I) is not None)

    check("the roles are created NOLOGIN — a role that cannot log in cannot "
          "become an unnoticed door",
          re.search(r"nologin", head, re.I) is not None)

    check("creating them is idempotent, so loading the snapshot onto a cluster "
          "that already has them is not an error",
          re.search(r"pg_roles", head, re.I) is not None)

    # Ordering is the whole point: a grant that runs before its role exists
    # fails, and pg_dump puts the schema body after whatever we prepend.
    # Compared by LINE NUMBER over statement lines only — the first cut of this
    # test compared character offsets across the whole file and matched the word
    # GRANT inside the bootstrap's own comment, failing on a correct file.
    lines = sql.split("\n")
    role_line = next((i for i, ln in enumerate(lines)
                      if not ln.lstrip().startswith("--")
                      and any(f"'{r}'" in ln for r in EXPECTED_ROLES)), None)
    grant_line = next((i for i, ln in enumerate(lines)
                       if re.match(r"\s*grant\s", ln, re.I)), None)
    check("the role bootstrap precedes any grant statement in the file",
          role_line is not None and (grant_line is None or role_line < grant_line),
          f"role at line {role_line}, first grant at line {grant_line}")

    # The snapshot must still be exactly what it claims: structure, the ledger,
    # and the reference vocabulary. A role bootstrap is a build declaration, not
    # business data, but the guard against widening belongs here too.
    check("no business tables were swept in with it",
          not re.search(r"^COPY public\.(party|deal|client|lead|loop_item|event)\b",
                        sql, re.M))

    print(f"\npassed {checked - len(failures)} · failed {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
