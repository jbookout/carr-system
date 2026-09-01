#!/usr/bin/env python3
# ci: runs-outside-ci — needs a disposable PostgreSQL; invoke with CARR_LOCAL_PG_DSN
# doctrine: runbook
"""Disposable-Postgres proof for the WR-000044 backup RLS fix (decision 11376c54).

Proves, against a disposable cluster whose migrations are already applied:

  1. Reproduces the nightly failure: carr_backup reading ops.work_request with
     row_security=off (pg_dump's default) is REFUSED by 0324's RLS.
  2. The fix works: migration 0475's permissive policy + row_security=on lets
     carr_backup read EVERY row of work_request (parity with the true count,
     SIEP-program rows included).
  3. The fix touches no sealed digest: the COMPLETE SCAC v10 census 4th block
     (roles + memberships + ownership, migration 0471's count=52 / sha256:345871
     block) is byte-IDENTICAL before and after the policy — a policy is a schema
     object, hashed by none of the three sets. Positive control on the same
     cluster: flipping carr_backup to BYPASSRLS MOVES that same block digest,
     then restoring NOBYPASSRLS returns it — proving the census is bypass-
     sensitive but policy-insensitive (the two halves are the airtight proof).
  4. Completeness invariant: for EVERY RLS-enabled table in public+ops,
     carr_backup under row_security=on sees the true row count (no silent
     short dump under --enable-row-security).
  5. Joe's guardrails: the grant widened nothing. carr_backup stays SELECT-only
     with NOSUPERUSER / NOCREATEDB / NOCREATEROLE / NOREPLICATION / NOBYPASSRLS,
     no privilege-bundle membership, zero write/DDL grants; and NO '^carr_' role
     gained BYPASSRLS anywhere.

db/schema.sql deliberately omits carr_backup (it is a production-only login), so
this test creates it here — mirroring migration 0119 — exactly as production has
it, then applies migration 0475. That the disposable rebuild lacks carr_backup
was the original false-green: a census/backup check that skips because the role
is absent proves nothing. This test refuses to skip it.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_0475 = ROOT / "migrations" / "0475_backup_role_work_request_rls_read_policy.sql"

PASS = 0
FAIL = 0
FAILED: list[str] = []

# The COMPLETE 4th SCAC v10 census block from migration 0471 (its
# scac_mutation_catalog_v10_current(), the block that returns count=52 and
# sha256:345871...): role_rows UNION ALL membership_rows UNION ALL
# ownership_rows, aggregated and hashed as ONE set. This is the sealed digest
# whose invariance under the policy is the load-bearing safety claim, so the CTE
# is copied VERBATIM from 0471 lines 270-281 (only the final projection is
# widened to also return count, matching 0471's `select count(*),'sha256:'...`).
# Role ATTRIBUTES (incl. bypass_rls) live in role_rows; a table policy appears in
# none of the three sets. Absolute count/digest are environment-specific (a
# disposable cluster holds fewer objects than production's 52); the proof is the
# INVARIANCE (identical across the policy) and the CONTRAST (moves under
# BYPASSRLS), both of which are deterministic.
CENSUS_V10_BLOCK4 = """
  with recursive connected(oid) as (
    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union
    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci'
  ), role_rows as (
    select 'db-role:'||r.rolname ingress_key,jsonb_build_object('ingress_key','db-role:'||r.rolname,'row_kind','role','role',r.rolname,'login',r.rolcanlogin,'inherit',r.rolinherit,'superuser',r.rolsuper,'create_role',r.rolcreaterole,'create_db',r.rolcreatedb,'replication',r.rolreplication,'bypass_rls',r.rolbypassrls) row from pg_roles r where r.oid in(select oid from connected)
  ), membership_rows as (
    select 'db-role-membership:'||role.rolname||':'||member.rolname ingress_key,jsonb_build_object('ingress_key','db-role-membership:'||role.rolname||':'||member.rolname,'row_kind','membership','role',role.rolname,'member',member.rolname,'admin_option',m.admin_option,'inherit_option',m.inherit_option,'set_option',m.set_option) row from pg_auth_members m join pg_roles role on role.oid=m.roleid join pg_roles member on member.oid=m.member where m.roleid in(select oid from connected) and m.member in(select oid from connected)
  ), ownership_rows as (
    select 'db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname ingress_key,jsonb_build_object('ingress_key','db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname,'row_kind','function_owner','signature',n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','owner',owner.rolname) row from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_roles owner on owner.oid=p.proowner where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner' union all
    select 'db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,jsonb_build_object('ingress_key','db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,'row_kind','relation_owner','relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,'owner',owner.rolname) row from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_roles owner on owner.oid=c.relowner where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'
  ), observed as (select * from role_rows union all select * from membership_rows union all select * from ownership_rows)
  select count(*)::int, 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') from observed
"""


def census_block(cur) -> tuple[int, str]:
    """Return (count, digest) of the full SCAC v10 census 4th block."""
    row = cur.execute(CENSUS_V10_BLOCK4).fetchone()
    assert row is not None
    return int(row[0]), str(row[1])

WRITE_PRIVS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
BUNDLE_ROLES = ("carr_writer", "carr_reader", "carr_jobs", "carr_exporter", "carr_authority")


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        FAILED.append(label)
        print(f"  FAIL  {label}  {detail}")


def one(cur, sql: str, args: tuple = ()):
    row = cur.execute(sql, args).fetchone()
    return None if row is None else row[0]


def create_backup_role(conn) -> None:
    """Mirror migration 0119: a plain SELECT-only login, no elevated attributes."""
    with conn.cursor() as cur:
        cur.execute("""
            do $$ begin
              if not exists (select 1 from pg_roles where rolname='carr_backup') then
                execute 'create role carr_backup login password ''placeholder-not-used''';
              end if;
            end $$;
        """)
        cur.execute("grant usage on schema public, ops to carr_backup")
        cur.execute("grant select on all tables in schema public to carr_backup")
        cur.execute("grant select on all tables in schema ops to carr_backup")
    conn.commit()


def apply_0475(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(MIGRATION_0475.read_text(encoding="utf-8"))
    conn.commit()


def count_as_backup(dsn: str, table: str, row_security: str):
    """Return (ok, value_or_error). Fresh connection: SET ROLE + row_security are
    session state, and an RLS refusal aborts the transaction."""
    try:
        with psycopg.connect(dsn) as c, c.cursor() as cur:
            cur.execute(f"set row_security = {row_security}")
            cur.execute("set role carr_backup")
            n = one(cur, f"select count(*) from {table}")
            return True, n
    except psycopg.Error as exc:
        return False, exc


def rls_tables(cur) -> list[str]:
    rows = cur.execute("""
        select n.nspname||'.'||c.relname
          from pg_class c join pg_namespace n on n.oid=c.relnamespace
         where c.relrowsecurity and n.nspname in ('public','ops') and c.relkind in ('r','p')
         order by 1
    """).fetchall()
    return [r[0] for r in rows]


def main() -> int:
    dsn = os.environ.get("CARR_LOCAL_PG_DSN", "").strip()
    if not dsn.startswith(("postgres://", "postgresql://")):
        print("backup-role-rls-coverage gate: CARR_LOCAL_PG_DSN must name a disposable PostgreSQL",
              file=sys.stderr)
        return 2

    with psycopg.connect(dsn) as conn:
        # Mirror production: carr_backup exists.
        create_backup_role(conn)

        with conn.cursor() as cur:
            count_before, digest_before = census_block(cur)
            bypass_before = one(cur, "select rolbypassrls from pg_roles where rolname='carr_backup'")
        print(f"  ..    full v10 census block BEFORE policy: count={count_before} digest={digest_before}")

        # Apply the fix.
        apply_0475(conn)

        with conn.cursor() as cur:
            count_after, digest_after = census_block(cur)
            policy_present = one(cur, """
                select exists(select 1 from pg_policy
                  where polname='carr_backup_full_read' and polrelid='ops.work_request'::regclass)
            """)
        print(f"  ..    full v10 census block AFTER  policy: count={count_after} digest={digest_after}")

        check("migration 0475 creates carr_backup_full_read on ops.work_request", bool(policy_present))
        check("FULL SCAC v10 census block (roles+memberships+ownership) is byte-IDENTICAL across the policy",
              (count_before, digest_before) == (count_after, digest_after),
              f"before=({count_before},{digest_before}) after=({count_after},{digest_after})")
        check("carr_backup did NOT gain BYPASSRLS (fix is a policy, not a role attribute)",
              bypass_before is False and one_bypass(conn) is False)

        # POSITIVE CONTROL: the census IS bypass-sensitive. Flip carr_backup to
        # BYPASSRLS on the same cluster, prove the SAME full census block digest
        # MOVES, then restore. Together with the invariance above this is the
        # airtight proof: the policy path is safe BECAUSE the sealed census
        # reacts to the role attribute the naive fix would have changed, and does
        # NOT react to the schema object this fix adds.
        with conn.cursor() as cur:
            cur.execute("alter role carr_backup bypassrls")
        conn.commit()
        with conn.cursor() as cur:
            count_bypass, digest_bypass = census_block(cur)
        print(f"  ..    full v10 census block WITH BYPASSRLS: count={count_bypass} digest={digest_bypass}")
        check("CONTRAST: flipping carr_backup to BYPASSRLS MOVES the same census block digest",
              digest_bypass != digest_after,
              f"policy_digest={digest_after} bypass_digest={digest_bypass} (must differ)")
        check("CONTRAST: the count is unchanged by BYPASSRLS (only the attribute bit moves the hash)",
              count_bypass == count_after,
              f"count_after={count_after} count_bypass={count_bypass}")
        with conn.cursor() as cur:
            cur.execute("alter role carr_backup nobypassrls")
        conn.commit()
        with conn.cursor() as cur:
            count_restored, digest_restored = census_block(cur)
        check("CONTRAST: restoring NOBYPASSRLS returns the census block to the identical digest",
              (count_restored, digest_restored) == (count_after, digest_after),
              f"restored=({count_restored},{digest_restored}) expected=({count_after},{digest_after})")

        # (1) Reproduce the failure, (2) prove the fix, on the real table.
        off_ok, off_val = count_as_backup(dsn, "ops.work_request", "off")
        print(f"  ..    row_security=off as carr_backup -> {'REFUSED: ' + str(off_val).strip() if not off_ok else 'read ' + str(off_val) + ' rows'}")
        check("row_security=off: carr_backup read of ops.work_request is REFUSED (the nightly failure)",
              (not off_ok) and "row-level security" in str(off_val).lower(),
              f"ok={off_ok} val={off_val}")

        on_ok, on_val = count_as_backup(dsn, "ops.work_request", "on")
        with conn.cursor() as cur:
            true_wr = one(cur, "select count(*) from ops.work_request")
            true_siep = one(cur, "select count(*) from ops.work_request "
                                 "where program_key='carr-system-integrity-elimination-v1'")
        print(f"  ..    row_security=on  as carr_backup -> read {on_val} rows; true count={true_wr}; "
              f"SIEP-keyed rows={true_siep}")
        check("row_security=on: carr_backup reads ops.work_request without error", on_ok, str(on_val))
        check("row_security=on: carr_backup sees EVERY work_request row (parity, no silent omission)",
              on_ok and on_val == true_wr, f"backup={on_val} true={true_wr}")
        check("parity is non-vacuous: work_request has rows to omit", (true_wr or 0) > 0, f"true={true_wr}")
        check("SIEP-program rows are visible to the backup too",
              on_ok and (true_siep or 0) >= 0 and on_val == true_wr,
              f"siep_true={true_siep} backup_total={on_val} true={true_wr}")

        # (4) Completeness invariant across ALL RLS tables in scope.
        with conn.cursor() as cur:
            tables = rls_tables(cur)
        check("scope sanity: ops.work_request is among the RLS tables", "ops.work_request" in tables,
              f"tables={tables}")
        for table in tables:
            t_ok, t_val = count_as_backup(dsn, table, "on")
            with conn.cursor() as cur:
                true_n = one(cur, f"select count(*) from {table}")
            check(f"coverage: carr_backup (row_security on) sees all rows of {table}",
                  t_ok and t_val == true_n,
                  f"backup={t_val} true={true_n} — no carr_backup read-all policy would dump this short")

        # (5) Joe's guardrails: the grant widened nothing.
        with conn.cursor() as cur:
            attrs = cur.execute("""
                select rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin
                  from pg_roles where rolname='carr_backup'
            """).fetchone()
            assert attrs is not None, "carr_backup must exist by now"
            rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin = attrs
            check("carr_backup is NOSUPERUSER", rolsuper is False)
            check("carr_backup is NOCREATEDB", rolcreatedb is False)
            check("carr_backup is NOCREATEROLE", rolcreaterole is False)
            check("carr_backup is NOREPLICATION", rolreplication is False)
            check("carr_backup is NOBYPASSRLS", rolbypassrls is False)
            check("carr_backup can log in (unchanged)", rolcanlogin is True)

            in_bundle = one(cur, """
                select exists(
                  select 1 from pg_auth_members m
                    join pg_roles g on g.oid=m.member
                    join pg_roles r on r.oid=m.roleid
                   where g.rolname='carr_backup' and r.rolname = any(%s))
            """, (list(BUNDLE_ROLES),))
            check("carr_backup is in no privilege bundle", in_bundle is False)

            write_grants = one(cur, """
                select count(*) from information_schema.role_table_grants
                 where grantee='carr_backup' and privilege_type = any(%s)
            """, (list(WRITE_PRIVS),))
            check("carr_backup holds zero write/DDL-adjacent table grants", write_grants == 0,
                  f"count={write_grants}")

            # Mirror the SCAC census's own exclusion of carr_ci (migration 0471:
            # `rolname<>'carr_ci'`): carr_ci is the disposable-cluster bootstrap
            # superuser, an environment artifact absent from production. No REAL
            # app role — carr_backup above all — may carry BYPASSRLS.
            unexpected_bypass = cur.execute("""
                select rolname from pg_roles
                 where rolbypassrls and rolname ~ '^carr_' and rolname <> 'carr_ci'
                 order by rolname
            """).fetchall()
            check("no '^carr_' app role carries BYPASSRLS (fix introduced none anywhere)",
                  unexpected_bypass == [], f"roles={[r[0] for r in unexpected_bypass]}")

    if FAIL:
        print(f"\nbackup-role-rls-coverage: {FAIL} FAILED of {PASS + FAIL}: {FAILED}", file=sys.stderr)
        return 1
    print(f"\nbackup-role-rls-coverage: all {PASS} checks passed")
    return 0


def one_bypass(conn) -> bool:
    with conn.cursor() as cur:
        return one(cur, "select rolbypassrls from pg_roles where rolname='carr_backup'")


if __name__ == "__main__":
    raise SystemExit(main())
