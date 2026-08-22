#!/usr/bin/env python3
"""test-schema-snapshot-roles.py — db/schema.sql must carry the roles its
grants depend on.

WHY THIS EXISTS. db/schema.sql is written by `pg_dump --schema-only --no-owner
--no-acl`, and those flags deliberately drop every OWNER TO and GRANT — an
embedded grant names roles a fresh database has never heard of, and the first
such statement aborts the load. Roles are also CLUSTER-level, so no schema dump
would carry them anyway.

The privilege bundles carr_reader, carr_writer and carr_exporter must remain
NOLOGIN for exactly this reason. carr_jobs is different: it is the unattended
runtime identity, so a rebuilt environment must create it as LOGIN (or convert
the old NOLOGIN placeholder) with a random password that is never printed.

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
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT = os.path.join(REPO, "db", "schema.sql")
GENERATOR = os.path.join(REPO, "bin", "schema-snapshot.sh")

# The roles the system's own migrations grant to. carr_exporter joined on
# 2026-08-14: 0006 created it, the ledger absorbed 0006, and the grants
# section (test-schema-snapshot-grants.py) carries its privileges — which
# need a role to attach to. neondb_owner is excluded on purpose: it is
# Neon's, exists on every Neon project and on no vanilla Postgres, and
# .github/workflows/ci.yml creates it for that reason.
NOLOGIN_BUNDLES = [
    "carr_reader", "carr_writer", "carr_exporter", "carr_authority",
    "carr_device_evidence", "carr_calendar_prebrief_jobs",
    "carr_calendar_prebrief_canary_jobs", "carr_calendar_prebrief_attestors",
    "carr_calendar_prebrief_email_resolver",
]
EXPECTED_ROLES = [*NOLOGIN_BUNDLES, "carr_jobs"]

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
    generator = open(GENERATOR).read() if os.path.exists(GENERATOR) else ""

    head = sql[:20000]
    for role in EXPECTED_ROLES:
        check(f"the snapshot creates {role}",
              re.search(rf"create role .*{role}|'{role}'", head, re.I) is not None)

    for role in NOLOGIN_BUNDLES:
        check(f"{role} remains a NOLOGIN privilege bundle",
              re.search(rf"create role %I nologin.*?{role}|{role}.*?create role %I nologin",
                        head, re.I | re.S) is not None)

    check("carr_jobs is created LOGIN with an unprinted random placeholder",
          re.search(r"create role %I login password %L", head, re.I) is not None
          and re.search(r"replace\(gen_random_uuid\(\)::text \|\| gen_random_uuid\(\)::text, '-', ''\)",
                        head, re.I) is not None)
    check("an old NOLOGIN carr_jobs is converted to LOGIN with a fresh placeholder",
          re.search(r"alter role %I login password %L", head, re.I) is not None
          and re.search(r"rolcanlogin", head, re.I) is not None)
    check("an already-login carr_jobs leaves its password unchanged",
          re.search(r"elsif not jobs_can_login", head, re.I) is not None)

    generated = re.search(r"cat > \"\$TMP\" <<'ROLES'\n(.*?)\nROLES", generator, re.S)
    preamble_end = sql.find("--\n-- PostgreSQL database dump")
    check("the snapshot generator carries the exact checked-in role preamble",
          generated is not None and preamble_end > 0
          and generated.group(1).strip() == sql[:preamble_end].strip())

    normalizer = re.search(r"EOF_NORMALIZER='\n(.*?)\n'", generator, re.S)
    if normalizer is None:
        eof_shape_ok = False
    else:
        normalized = subprocess.run(
            ["awk", normalizer.group(1)],
            input="first\n\nsecond\n  \n\n",
            text=True,
            capture_output=True,
            check=False,
        )
        eof_shape_ok = (
            normalized.returncode == 0
            and normalized.stdout == "first\n\nsecond\n"
        )
    check("the snapshot EOF normalizer preserves interior blanks and emits one LF",
          eof_shape_ok)

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
