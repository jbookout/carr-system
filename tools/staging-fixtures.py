#!/usr/bin/env python3
"""
staging-fixtures.py — the sanitized deterministic fixtures Program 1 asks for.

THE REQUIREMENT: Program 1's bullet list names "sanitized deterministic
fixtures", and the environment contract defines staging's data as "sanitized
representative fixtures or dedicated test data". Staging today holds one row,
named "Throwaway Repro Org", left behind by a reproduction on 2026-08-14. That
is dedicated test data in the sense that nobody meant it to be there.

WHAT SANITIZED MEANS HERE, and it is the whole design: not a scrubbed copy of
production. Every value below is INVENTED. No name, practice, address or phone
in this file corresponds to a real party, and none of it was derived from a
production row by anonymisation. Anonymised data is still data about somebody,
it re-identifies more often than anyone expects, and a rehearsal environment is
exactly where nobody is watching for that. Fixtures are written, not sampled.

WHAT DETERMINISTIC MEANS: every id is a uuid5 of a fixed namespace and a stable
key, so the same fixture always lands with the same id. That gives three things
at once. Loading twice is idempotent rather than doubling the data. A rehearsal
that fails can be re-run and compared against the previous run. And the ids come
from a namespace production never uses, so ops/p1-environment-gate.py's
"no row in staging shares an id with a row in production" assertion cannot be
tripped by a fixture — a collision would mean a genuine leak, which is what that
assertion is for.

IT CANNOT TOUCH PRODUCTION, structurally. The connection comes from
tools/db-tap.py with project="staging" pinned in this file. There is no DSN
argument, no --project flag and no environment variable that redirects it. A
tool that writes fixtures needs exactly one destination, and offering a choice
is how the wrong one eventually gets picked.

  load     insert or update every fixture (idempotent)
  verify   report what is present, and prove the ids are the fixture namespace
  clear    delete every fixture by id, and nothing else

USAGE
    .venv/bin/python tools/staging-fixtures.py load
    .venv/bin/python tools/staging-fixtures.py verify
    .venv/bin/python tools/staging-fixtures.py clear
"""

import argparse
import importlib.util
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.pgrow import fetch_one  # noqa: E402

_spec = importlib.util.spec_from_file_location("db_tap", REPO / "tools" / "db-tap.py")
if _spec is None or _spec.loader is None:
    sys.exit("staging-fixtures: could not load tools/db-tap.py")
db_tap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_tap)

try:
    import psycopg
except ImportError:
    sys.exit("staging-fixtures: psycopg not installed")

# THE FIXTURE NAMESPACE. Every fixture id is uuid5(NAMESPACE, key), so the ids
# are stable across loads and machines, and they are drawn from a space
# production's gen_random_uuid() will not produce in any practical universe.
NAMESPACE = uuid.UUID("f1c70000-0000-5000-a000-000000000001")


def fid(key: str | None) -> uuid.UUID:
    if key is None:
        raise ValueError("a fixture must have a key")
    return uuid.uuid5(NAMESPACE, key)


# INVENTED, not sampled. Two practices in two specialties, one landlord-side
# org, and one vendor — the smallest set that exercises a party graph without
# pretending to be a market.
PARTIES: list[dict[str, str | None]] = [
    {"key": "party:practice:willow-dental",
     "kind": "org",   "name": "Willow Creek Dental (FIXTURE)",
     "city": "Testville", "state": "FL", "specialty": "dental"},
    {"key": "party:practice:harbor-vision",
     "kind": "org",   "name": "Harbor Vision Associates (FIXTURE)",
     "city": "Testville", "state": "AL", "specialty": "vision"},
    {"key": "party:person:dr-avery-stone",
     "kind": "person", "name": "Avery Stone, DMD (FIXTURE)",
     "city": "Testville", "state": "FL", "specialty": "dental"},
    {"key": "party:vendor:fixture-lending",
     "kind": "org",   "name": "Fixture Lending Group (FIXTURE)",
     "city": "Testville", "state": "FL", "specialty": None},
]

FIXTURE_MARK = "(FIXTURE)"


def staging_connection() -> str:
    """The ONE destination. No argument reaches this."""
    return db_tap.dsn(project="staging")


FIXTURE_ACTOR = fid("actor:fixture-loader")


def ensure_actor(cur) -> uuid.UUID:
    """Every record carries its author, and a fixture's author is the loader.

    party.created_by is NOT NULL and references actor, so fixtures need an actor
    to belong to. Borrowing a real one would attribute invented records to Joe or
    Dell in the one environment where nobody is checking attribution, so the
    fixtures get their own actor with its own deterministic id, named so no
    reader mistakes it for a person.
    """
    cur.execute("select 1 from public.actor where id = %s", (FIXTURE_ACTOR,))
    if cur.fetchone() is None:
        cur.execute(
            """insert into public.actor (id, slug, kind, display_name, active)
               values (%s, 'fixture-loader', 'system', 'Staging Fixture Loader (FIXTURE)', true)""",
            (FIXTURE_ACTOR,))
    return FIXTURE_ACTOR


def cmd_load() -> int:
    inserted = updated = 0
    with psycopg.connect(staging_connection(), autocommit=True) as conn, conn.cursor() as cur:
        actor = ensure_actor(cur)
        for p in PARTIES:
            pid = fid(p["key"])
            cur.execute("select 1 from public.party where id = %s", (pid,))
            exists = cur.fetchone() is not None
            if exists:
                cur.execute(
                    """update public.party
                          set kind=%s, name=%s, city=%s, state=%s, specialty=%s,
                              updated_at=now(), updated_by=%s
                        where id=%s""",
                    (p["kind"], p["name"], p["city"], p["state"], p["specialty"],
                     actor, pid))
                updated += 1
            else:
                cur.execute(
                    """insert into public.party
                           (id, kind, name, city, state, specialty, created_by, updated_by)
                       values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pid, p["kind"], p["name"], p["city"], p["state"], p["specialty"],
                     actor, actor))
                inserted += 1
    print(f"staging-fixtures: {inserted} inserted, {updated} updated "
          f"({len(PARTIES)} fixtures total)")
    return 0


def cmd_verify() -> int:
    problems = []
    with psycopg.connect(staging_connection(), autocommit=True) as conn, conn.cursor() as cur:
        for p in PARTIES:
            pid = fid(p["key"])
            cur.execute("select name from public.party where id = %s", (pid,))
            row = cur.fetchone()
            if row is None:
                problems.append(f"missing: {p['key']}")
                continue
            if FIXTURE_MARK not in (row[0] or ""):
                problems.append(f"unmarked: {p['key']} — a fixture must say so in its name")
        # Anything in staging that is NOT a fixture is worth seeing. It is not a
        # failure — rehearsals legitimately create rows — but an unlabelled row
        # nobody remembers is how "Throwaway Repro Org" survived for a day.
        cur.execute("select count(*) from public.party")
        total = fetch_one(cur)[0]
    print(f"staging-fixtures: {len(PARTIES)} fixtures declared, "
          f"{len(PARTIES) - len([p for p in problems if p.startswith('missing')])} present, "
          f"{total} party rows in staging overall")
    for problem in problems:
        print(f"  {problem}")
    return 1 if problems else 0


def cmd_clear() -> int:
    ids = [fid(p["key"]) for p in PARTIES]
    with psycopg.connect(staging_connection(), autocommit=True) as conn, conn.cursor() as cur:
        # BY ID, never by a name pattern. A delete that matches on text will one
        # day match something a person typed.
        cur.execute("delete from public.party where id = any(%s)", (ids,))
        removed = cur.rowcount
    print(f"staging-fixtures: {removed} fixture row(s) removed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("action", choices=["load", "verify", "clear"])
    args = ap.parse_args()
    return {"load": cmd_load, "verify": cmd_verify, "clear": cmd_clear}[args.action]()


if __name__ == "__main__":
    sys.exit(main())
