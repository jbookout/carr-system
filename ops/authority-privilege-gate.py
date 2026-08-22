#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only acceptance gate: a human authority principal can read what it must.

THE DEFECT THIS EXISTS FOR, found 2026-08-22 the expensive way. approve-rule
worked with a full 36-character rule id and failed with the 8-character short id
that standing-context prints — the form every session actually holds. The cause
was not id formatting: resolving a short id does a prefix read of public.rule on
the AUTHORITY connection, that connection logs in as carr_authority_joe, and
migration 0161 deliberately puts every authority privilege on a separate NOLOGIN
bundle called carr_authority that the login roles had never been made members of.
So the prefix read raised permission denied while the full-uuid path, which skips
the read entirely, worked. It surfaced as a bare "internal error", so finding it
took a live rule activation.

WHAT THIS GATE ASSERTS, and why it is not the migration's proof:

  1. carr_authority exists and is NOLOGIN. If it ever gains LOGIN, the bundle has
     become a credential rather than a privilege set, which is a different and
     larger change than anyone will have intended.
  2. Every authority login role present on this database is a member of the
     bundle. Membership, not a direct grant — 0161's design keeps the two human
     roles carrying no privileges of their own so they cannot drift apart.
  3. Those roles can actually SELECT the tables the authority verbs read. Today
     that is public.rule, read by approve-rule's short-id resolution. A new
     authority verb that reads a new table belongs on this list, and the point of
     the list is that the next missing privilege fails here on a push rather than
     in front of a partner mid-approval.

A database with NO authority login role provisioned is reported, not failed: the
roles are created in the provider console, and a throwaway CI database
legitimately has neither. What must never pass silently is a role that exists and
cannot do its job.

Writes nothing; the connection is rolled back.
"""

from __future__ import annotations

import os
import sys

import psycopg

BUNDLE = "carr_authority"
LOGIN_ROLES = ("carr_authority_joe", "carr_authority_dell")

# Tables an authority-connection verb reads. approve-rule's resolveRuleId reads
# public.rule to turn the short id a session holds into a full one.
AUTHORITY_READS = (
    ("public.rule", "approve-rule resolves the short rule id by prefix"),
)


def scalar(cur, query: str, params: tuple, label: str):
    """cursor.execute().fetchone() is Optional; these queries always return a row,
    and saying so once beats an inline assertion at every call site."""
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError(f"{label} returned no row")
    return row[0]


def fail(message: str) -> int:
    print(f"authority-privilege-gate: FAIL — {message}", file=sys.stderr)
    return 1


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")

    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            row = cur.execute(
                "select rolcanlogin from pg_roles where rolname = %s", (BUNDLE,)
            ).fetchone()
            if row is None:
                return fail(
                    f"the {BUNDLE} privilege bundle does not exist; migration 0161 "
                    "has not been applied to this database")
            if row[0]:
                return fail(
                    f"{BUNDLE} can log in. It is meant to be a NOLOGIN privilege bundle that "
                    "human login roles join; a bundle that can authenticate is a shared "
                    "credential for partner-binding authority, which is exactly what "
                    "migration 0161 separated.")

            present = [
                r for r in LOGIN_ROLES
                if cur.execute("select 1 from pg_roles where rolname = %s", (r,)).fetchone()
            ]
            if not present:
                print("authority-privilege-gate: no authority login role is provisioned on this "
                      "database — reported, not failed; they are created in the provider console "
                      "and a throwaway CI database legitimately has neither")
                return 0

            problems = []
            for role in present:
                is_member = scalar(
                    cur, "select pg_has_role(%s, %s, 'USAGE')", (role, BUNDLE),
                    f"{role} membership of {BUNDLE}")
                if not is_member:
                    problems.append(
                        f"{role} is not a member of {BUNDLE}, so it holds none of the authority "
                        "privileges — this is the defect that made short rule ids fail while full "
                        "uuids worked")
                for table, why in AUTHORITY_READS:
                    can_read = scalar(
                        cur, "select has_table_privilege(%s, %s, 'SELECT')", (role, table),
                        f"{role} select privilege on {table}")
                    if not can_read:
                        problems.append(f"{role} cannot select from {table} — {why}")

            if problems:
                return fail(f"{len(problems)} problem(s):\n  " + "\n  ".join(problems))

            checked = ", ".join(present)
    except psycopg.Error as exc:
        return fail(str(exc))
    finally:
        conn.rollback()
        conn.close()

    print(f"authority-privilege-gate passed: {BUNDLE} is a NOLOGIN bundle; {checked} "
          f"{'is a member' if len(present) == 1 else 'are members'} and can read "
          f"{len(AUTHORITY_READS)} authority table(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
