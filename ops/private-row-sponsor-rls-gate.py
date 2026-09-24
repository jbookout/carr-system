#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Personal rows are fenced by the database, per partner (migration 0572).

Decisions 04101316 and 443fe82a (audit ruling 5, 2026-09-24). Two proofs, one
rolled-back transaction on the disposable migration-lane database:

  ENUMERATION. Every public/ops table whose CHECK constraints mention the
  'personal' scope is a table that can hold a partner's private rows. Each one
  must either have row security enabled with a policy that reads the
  server-set carr.sponsoring_human_slug, be named in ACCEPTED_SHARED with the
  reason its "personal" marker is not privacy, or be named in PENDING with the
  follow-up that will fence it. A new table that grows a personal scope without
  one of the three fails here, before it can leak.

  BEHAVIOUR on public.memory_item, as the real runtime role carr_writer: Joe's
  session sees shared rows and Joe's personal rows, never Dell's; Dell's the
  mirror; a shared-only machine session sees shared rows only; a session
  cannot insert a personal row owned by another partner; carr_backup, where
  the role exists, still reads every row so the nightly dump stays complete.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role

GUC = "carr.sponsoring_human_slug"

# A table here carries a 'personal' marker that is NOT privacy. Each entry
# names why, and changes only with a logged decision.
ACCEPTED_SHARED = {
    "public.loop_item": (
        "decision 443fe82a: add-loop stamps every open loop and idea 'personal' "
        "to whoever filed it, automation included; the loop board is the shared "
        "work queue both partners and the system drain"),
}

# A table here IS private and is NOT yet fenced by the database; the server's
# own filter is its only guard until the named follow-up lands. Listing it is an
# admission, not an exemption: the entry must be removed when its policy ships.
PENDING = {
    "public.doctrine_document": (
        "visibility 'personal' is real privacy (doctrine.js filters it), but the "
        "text lives in doctrine_section rows with no personal marker, so a "
        "document-level policy alone would fence titles, not content; the "
        "section-level policy through the parent document is the next slice "
        "(0 personal documents live on 2026-09-24)"),
}


def fail(message: str) -> int:
    print(f"private-row sponsor RLS gate: {message}", file=sys.stderr)
    return 1


def main() -> int:
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("private-row sponsor RLS gate requires DATABASE_URL (the disposable migration-lane database)",
              file=sys.stderr)
        return 78
    with rollback_only_connection(dsn) as conn:
        with conn.cursor() as cur:
            personal_tables = sorted({row[0] for row in cur.execute("""
                select c.oid::regclass::text
                  from pg_constraint k
                  join pg_class c on c.oid = k.conrelid
                  join pg_namespace n on n.oid = c.relnamespace
                 where k.contype = 'c'
                   and n.nspname in ('public', 'ops')
                   and pg_get_constraintdef(k.oid) ilike '%''personal''%'""").fetchall()})
            personal_tables = [t if "." in t else f"public.{t}" for t in personal_tables]
            if "public.memory_item" not in personal_tables:
                return fail(f"enumeration is vacuous: memory_item not found among {personal_tables}")
            uncovered = []
            for table in personal_tables:
                if table in ACCEPTED_SHARED or table in PENDING:
                    continue
                rls, policy_reads_sponsor = cur.execute("""
                    select c.relrowsecurity,
                           exists (select 1 from pg_policy p
                                    where p.polrelid = c.oid and p.polcmd in ('r', '*')
                                      and pg_get_expr(p.polqual, p.polrelid) like %s)
                      from pg_class c where c.oid = %s::regclass""",
                    (f"%{GUC}%", table)).fetchone()
                if not (rls and policy_reads_sponsor):
                    uncovered.append(table)
            stale = sorted((set(ACCEPTED_SHARED) | set(PENDING)) - set(personal_tables))
            if stale:
                return fail(f"ACCEPTED_SHARED or PENDING names tables with no personal scope any more: {stale}")
            for table in PENDING:
                fenced = cur.execute("select relrowsecurity from pg_class where oid=%s::regclass",
                                     (table,)).fetchone()[0]
                if fenced:
                    return fail(f"{table} now has row security; move it out of PENDING")
            if uncovered:
                return fail("tables that can hold a partner's personal rows have no sponsor policy "
                            f"and no accepted-shared reason: {uncovered}")

            # Behaviour. Fixtures are written as the migration owner, which
            # bypasses row security, then read back as carr_writer.
            actors = {}
            for slug in ("joe", "dell"):
                row = cur.execute("select id from public.actor where slug=%s and kind='human'", (slug,)).fetchone()
                if row is None:
                    row = cur.execute(
                        "insert into public.actor (slug, kind, display_name) values (%s,'human',%s) returning id",
                        (slug, slug.title())).fetchone()
                actors[slug] = row[0]
            observer = actors["joe"]
            tag = f"rls-gate-{uuid.uuid4()}"
            for scope, owner in (("shared", None), ("personal", actors["joe"]), ("personal", actors["dell"])):
                cur.execute(
                    "insert into public.memory_item (organization_tenant_id, kind, statement, scope, "
                    "owner_actor_id, observed_by_actor_id) values ('carr-internal','fact',%s,%s,%s,%s)",
                    (f"{tag} {scope} {owner}", scope, owner, observer))

            def visible() -> dict[str, int]:
                rows = cur.execute(
                    "select scope, owner_actor_id from public.memory_item where statement like %s",
                    (f"{tag}%",)).fetchall()
                seen = {"shared": 0, "joe": 0, "dell": 0}
                for scope, owner in rows:
                    key = "shared" if scope == "shared" else ("joe" if owner == actors["joe"] else "dell")
                    seen[key] += 1
                return seen

            grant_settable_runtime_roles(cur, "carr_writer")
            set_local_role(cur, "carr_writer")
            expected = {
                "joe": {"shared": 1, "joe": 1, "dell": 0},
                "dell": {"shared": 1, "joe": 0, "dell": 1},
                "": {"shared": 1, "joe": 0, "dell": 0},
            }
            observed = {}
            for sponsor, want in expected.items():
                cur.execute("select set_config(%s, %s, true)", (GUC, sponsor))
                observed[sponsor or "machine"] = got = visible()
                if got != want:
                    return fail(f"sponsor {sponsor or '<none>'!r} saw {got}, expected {want}")

            cur.execute("select set_config(%s, 'dell', true)", (GUC,))
            cur.execute("savepoint cross_owner")
            try:
                cur.execute(
                    "insert into public.memory_item (organization_tenant_id, kind, statement, scope, "
                    "owner_actor_id, observed_by_actor_id) values ('carr-internal','fact',%s,'personal',%s,%s)",
                    (f"{tag} forged", actors["joe"], actors["dell"]))
            except psycopg.errors.InsufficientPrivilege:
                cur.execute("rollback to savepoint cross_owner")
            else:
                return fail("Dell's session inserted a personal memory owned by Joe")

            cur.execute("reset role")
            backup = None
            if cur.execute("select 1 from pg_roles where rolname='carr_backup'").fetchone():
                grant_settable_runtime_roles(cur, "carr_backup")
                set_local_role(cur, "carr_backup")
                cur.execute("select set_config(%s, '', true)", (GUC,))
                backup = visible()
                if backup != {"shared": 1, "joe": 1, "dell": 1}:
                    return fail(f"carr_backup saw {backup}; the nightly dump would be short")
                cur.execute("reset role")

    print(json.dumps({
        "contract": "private-row-sponsor-rls.v1",
        "personal_scope_tables": personal_tables,
        "accepted_shared": sorted(ACCEPTED_SHARED),
        "pending_not_yet_fenced": sorted(PENDING),
        "visible_by_session": observed,
        "carr_backup": backup if backup is not None else "role absent on this database",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
