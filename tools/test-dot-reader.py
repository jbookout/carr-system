#!/usr/bin/env python3
"""Dot SQL boundary proof on an owned disposable PostgreSQL cluster.

No production DSN is accepted. Set CARR_DOT_REPO_SCHEMA=1 to repeat against
the committed schema rather than the small behavioral fixture.
"""
import importlib.util
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit, quote
import contextlib
import io
import re
import ast

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/0756_dot_reader.sql"


class DotReader(unittest.TestCase):
    maxDiff = None
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("local_pg", ROOT / "ops/local-pg-ci.py")
        module = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        try:
            cls.bin = module.find_postgres_binaries()
        except module.LocalPGRefusal:
            raise unittest.SkipTest("disposable PostgreSQL binaries unavailable")
        cls.tmp = tempfile.TemporaryDirectory(prefix="dot-reader-test-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.data = Path(cls.tmp.name) / "data"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.run_pg([cls.bin.initdb, "-D", cls.data, "-U", "carr_ci",
                    "--auth-local=trust", "--auth-host=scram-sha-256", "--encoding=UTF8", "--no-locale"])
        cls.run_pg([cls.bin.pg_ctl, "-D", cls.data, "-l", Path(cls.tmp.name) / "pg.log",
                    "-o", f"-h 127.0.0.1 -k {cls.tmp.name} -p {cls.port}", "-w", "start"])
        cls.addClassCleanup(cls.run_pg, [cls.bin.pg_ctl, "-D", cls.data, "-m", "immediate", "-w", "stop"])
        cls.owner_args = dict(host=cls.tmp.name, port=cls.port, user="carr_ci", dbname="postgres")
        with psycopg.connect(**cls.owner_args, autocommit=True) as owner:
            if os.environ.get("CARR_DOT_REPO_SCHEMA") == "1":
                owner.execute("create role neondb_owner")
                # psql understands the snapshot's meta-commands. No credentials
                # are on argv: this owned Unix socket authenticates locally.
                cls.run_pg([cls.bin.psql, "-h", cls.tmp.name, "-p", str(cls.port),
                            "-U", "carr_ci", "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                            "-q", "-f", ROOT / "db/schema.sql"])
            else:
                owner.execute("create schema ops; create role carr_writer; create role neondb_owner")
                # Model the two legacy PUBLIC-executable write doors. The
                # full-schema run exercises their production definitions.
                owner.execute("""
                    create function ops.engineering_register_slice_plan(text,jsonb,text,uuid)
                    returns void language plpgsql security definer as $$
                    begin
                      raise exception 'unguarded writer reached' using errcode='P0001';
                    end
                    $$;
                    create function ops.issue_execution_envelope_v1(text,text,uuid)
                    returns void language plpgsql security definer as $$
                    begin
                      raise exception 'unguarded writer reached' using errcode='P0001';
                    end
                    $$;
                """)
            owner.execute("create role app_reader login")
            if os.environ.get("CARR_DOT_MANAGED_OWNER") == "1":
                owner.execute("alter role neondb_owner createrole createdb")
                owner.execute("alter database postgres owner to neondb_owner")
                owner.execute("alter schema ops owner to neondb_owner")
                owner.execute("alter function ops.engineering_register_slice_plan(text,jsonb,text,uuid) owner to neondb_owner")
                owner.execute("alter function ops.issue_execution_envelope_v1(text,text,uuid) owner to neondb_owner")
                owner.execute("set role neondb_owner")
            owner.execute("""
                create table public.dot_fixture (id int primary key, sponsor text, scope text);
                create table ops.dot_fixture (id int primary key, sponsor text, scope text);
                insert into public.dot_fixture values (1,'sponsor_a','shared'),
                  (2,'sponsor_a','personal'),(3,'sponsor_b','personal');
                insert into ops.dot_fixture select * from public.dot_fixture;
                alter table public.dot_fixture enable row level security;
                alter table public.dot_fixture force row level security;
                alter table ops.dot_fixture enable row level security;
                create policy fixture_sponsor on public.dot_fixture for select
                  using (sponsor = current_setting('test.sponsor',true));
                create view public.dot_fixture_view with (security_invoker=true) as select * from public.dot_fixture;
                create sequence public.dot_fixture_seq;
                create function public.dot_fixture_mutation() returns void language sql
                  security definer as 'insert into ops.dot_fixture values (4, ''sponsor_a'', ''shared'')';
                revoke execute on function public.dot_fixture_mutation() from public;
            """)
            cls.temp_before = owner.execute("select has_database_privilege('app_reader',current_database(),'TEMP')").fetchone()[0]
            owner.execute(MIGRATION.read_text())
            owner.execute("reset role")
            # Only the fixture sets a password. It stays in memory and never
            # reaches a source file, command argument or test output.
            cls.password = secrets.token_urlsafe(32)
            owner.execute(sql.SQL("alter role dot_reader password {}").format(sql.Literal(cls.password)))
            if os.environ.get("CARR_DOT_MANAGED_OWNER") == "1":
                owner.execute("set role neondb_owner")
            owner.execute("create table public.dot_future (id int); insert into public.dot_future values (9)")
            owner.execute("create table ops.dot_future (id int); insert into ops.dot_future values (10)")
            owner.execute("""create function ops.dot_future_mutation() returns void language sql
                security definer as 'insert into ops.dot_fixture values (6, ''sponsor_a'', ''shared'')'""")
            if os.environ.get("CARR_DOT_REPO_SCHEMA") == "1":
                owner.execute("""
                    insert into public.actor(slug,kind,display_name) values
                      ('dot-fixture-a','human','Fixture sponsor A'),
                      ('dot-fixture-b','human','Fixture sponsor B');
                    insert into public.memory_item(organization_tenant_id,kind,statement,scope,owner_actor_id,observed_by_actor_id)
                    select 'dot-fixture','fact','Synthetic review fixture','personal',id,id
                      from public.actor where slug in ('dot-fixture-a','dot-fixture-b');
                """)

    @classmethod
    def run_pg(cls, args):
        result = subprocess.run([str(x) for x in args], capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise RuntimeError("disposable PostgreSQL setup failed")

    def connect(self):
        return psycopg.connect(host="127.0.0.1", port=self.port, user="dot_reader",
                               password=self.password, dbname="postgres", autocommit=True)

    def test_reads_all_sponsors_and_personal_scope(self):
        with self.connect() as dot:
            self.assertEqual(dot.execute("select session_user,current_user").fetchone(), ("dot_reader", "dot_reader"))
            dot.execute("set test.sponsor = 'sponsor_a'")
            for table in ("public.dot_fixture", "ops.dot_fixture", "public.dot_fixture_view"):
                with self.subTest(table=table):
                    rows = dot.execute(sql.SQL("select sponsor,scope from {} order by id").format(sql.Identifier(*table.split(".")))).fetchall()
                    self.assertEqual(rows, [("sponsor_a", "shared"), ("sponsor_a", "personal"), ("sponsor_b", "personal")])
            self.assertEqual(dot.execute("select id from public.dot_future").fetchall(), [(9,)])
            self.assertEqual(dot.execute("select id from ops.dot_future").fetchall(), [(10,)])
            if os.environ.get("CARR_DOT_REPO_SCHEMA") == "1":
                self.assertEqual(dot.execute("""select a.slug from public.memory_item m
                    join public.actor a on a.id=m.owner_actor_id
                    where m.organization_tenant_id='dot-fixture' and m.scope='personal' order by a.slug""").fetchall(),
                    [("dot-fixture-a",), ("dot-fixture-b",)])

    def test_write_and_ddl_denied_even_if_read_only_setting_disabled(self):
        statements = [
            "insert into public.dot_fixture values (5,'sponsor_a','shared')",
            "update public.dot_fixture set scope='shared'",
            "delete from public.dot_fixture", "truncate public.dot_fixture",
            "copy public.dot_fixture from stdin", "create table public.dot_nope (id int)",
            "create table ops.dot_nope (id int)", "create temp table dot_nope (id int)",
            "create schema dot_nope", "create database dot_nope", "create role dot_nope",
            "alter table public.dot_fixture add column nope int", "drop table public.dot_fixture",
            "create function public.dot_nope() returns int language sql as 'select 1'",
            "set role carr_writer", "select nextval('public.dot_fixture_seq')",
            "select public.dot_fixture_mutation()",
            "select ops.dot_future_mutation()",
        ]
        with self.connect() as dot:
            dot.execute("set default_transaction_read_only=off")
            for statement in statements:
                with self.subTest(statement=statement):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        dot.execute(statement)
            for call in (
                "select ops.engineering_register_slice_plan(null,null,null,null)",
                "select * from ops.issue_execution_envelope_v1(null,null,null)",
            ):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    dot.execute(call)
            # PostgreSQL reports a warning for a GRANT without grant option;
            # it must confer no privilege, even when the statement returns.
            dot.execute("grant select on public.dot_fixture to app_reader")
        with psycopg.connect(**self.owner_args) as owner:
            self.assertFalse(owner.execute("select has_table_privilege('app_reader','public.dot_fixture','SELECT')").fetchone()[0])

    def test_limits_isolation_and_full_catalog_coverage(self):
        with self.connect() as dot:
            self.assertEqual(dot.execute("show statement_timeout").fetchone(), ("30s",))
            with self.connect():
                with self.assertRaises(psycopg.OperationalError):
                    self.connect()
        with psycopg.connect(**self.owner_args) as owner:
            self.assertEqual(owner.execute("select rolcanlogin,rolconnlimit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls from pg_roles where rolname='dot_reader'").fetchone(), (True, 2, False, False, False, False, False))
            self.assertEqual(owner.execute("select member::regrole::text,admin_option,inherit_option,set_option from pg_auth_members where roleid='dot_reader'::regrole").fetchall(), [("neondb_owner", True, False, False)])
            self.assertEqual(owner.execute("select count(*) from pg_auth_members where member='dot_reader'::regrole").fetchone(), (0,))
            self.assertEqual(owner.execute("select has_database_privilege('app_reader',current_database(),'TEMP')").fetchone(), (False,))
            self.assertEqual(owner.execute("""select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
                where n.nspname in ('public','ops') and c.relkind in ('r','p','v','m','f')
                  and not has_table_privilege('dot_reader',c.oid,'SELECT')""").fetchone(), (0,))
            self.assertEqual(owner.execute("""select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
                where n.nspname in ('public','ops') and c.relkind in ('r','p') and c.relrowsecurity
                  and not exists(select 1 from pg_policy p where p.polrelid=c.oid and p.polname='dot_reader_full_read'
                    and p.polcmd='*' and p.polpermissive and 'dot_reader'::regrole=any(p.polroles)
                  and pg_get_expr(p.polqual,p.polrelid)='true')""").fetchone(), (0,))

    def test_provision_resume_and_revoke_on_disposable_database(self):
        spec = importlib.util.spec_from_file_location("dot_access", ROOT / "tools/dot-reader-access.py")
        access = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(access)
        owner_password = secrets.token_urlsafe(32)
        with psycopg.connect(**self.owner_args, autocommit=True) as owner:
            owner.execute(sql.SQL("alter role carr_ci password {}").format(sql.Literal(owner_password)))
        owner_uri = urlunsplit(("postgresql", f"carr_ci:{quote(owner_password)}@127.0.0.1:{self.port}", "/postgres", "sslmode=require", ""))
        original_connect = psycopg.connect

        def local_connect(uri, **kwargs):
            # Only this test changes TLS: the disposable cluster has no TLS.
            parts = urlsplit(uri)
            if parts.hostname != "127.0.0.1" or parts.port != self.port:
                raise AssertionError("credential fixture must stay on its owned loopback port")
            return original_connect(uri.replace("sslmode=require", "sslmode=disable"), **kwargs)

        with tempfile.TemporaryDirectory(prefix="dot-credential-test-") as directory:
            destination = Path(directory) / "dot-reader.connection"
            output = io.StringIO()
            with patch.dict(os.environ, {"DATABASE_URL": owner_uri}), \
                 patch.object(access.credential, "ENV_PATH", str(Path(directory) / "db.env")), \
                 patch.object(access, "DOT_CONNECTION_PATH", str(destination)), \
                 patch.object(access, "read_env", return_value={"CARR_DB_EXPORTER_URL": owner_uri}), \
                 patch.object(psycopg, "connect", side_effect=local_connect), \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with patch.object(access, "publish_dot_connection", side_effect=OSError()):
                    self.assertEqual(access.dot_reader_action(revoke=False), 1)
                pending = access.read_dot_pending()
                with patch.object(access, "new_password", side_effect=AssertionError("must resume")):
                    self.assertEqual(access.dot_reader_action(revoke=False), 0)
                self.assertTrue(destination.read_text() == pending + "\n")
                self.assertNotIn(pending, output.getvalue())
                with local_connect(pending, autocommit=True) as dot:
                    self.assertEqual(access.dot_reader_action(revoke=True), 0)
                    with self.assertRaises(psycopg.Error):
                        dot.execute("select 1")
                with self.assertRaises(psycopg.OperationalError):
                    local_connect(pending)
                self.assertEqual(destination.read_text(), "REVOKED\n")
                with self.assertRaises(SystemExit):
                    access.dot_reader_action(revoke=False)
        # Restore the shared fixture login for the remaining read tests.
        with original_connect(**self.owner_args, autocommit=True) as owner:
            owner.execute(sql.SQL("alter role dot_reader login password {}").format(sql.Literal(self.password)))

    def test_future_write_upgrade_needs_only_one_grant(self):
        with psycopg.connect(**self.owner_args, autocommit=True) as owner:
            try:
                owner.execute("grant insert,update,delete on all tables in schema public,ops to dot_reader")
                with self.connect() as dot:
                    dot.execute("begin")
                    try:
                        for schema in ("public", "ops"):
                            dot.execute(sql.SQL("insert into {}.dot_fixture values (99,'sponsor_b','personal')").format(sql.Identifier(schema)))
                            dot.execute(sql.SQL("update {}.dot_fixture set scope='shared' where id=99").format(sql.Identifier(schema)))
                            dot.execute(sql.SQL("delete from {}.dot_fixture where id=99").format(sql.Identifier(schema)))
                    finally:
                        dot.execute("rollback")
            finally:
                owner.execute("revoke insert,update,delete on all tables in schema public,ops from dot_reader")
        with self.connect() as dot:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                dot.execute("delete from public.dot_fixture")


    def test_snapshot_reconstructs_passwordless_role_and_future_read_grants(self):
        exporter = (ROOT / "bin/schema-snapshot.sh").read_text()
        blocks = []
        for marker in ("DOT_READER_ROLES", "DOT_READER_GRANTS"):
            match = re.search(r"cat >> \"\$TMP\" <<'" + marker + r"'\n(.*?)\n" + marker, exporter, re.S)
            self.assertIsNotNone(match, "snapshot must reconstruct the released Dot role")
            blocks.append(match.group(1))
        self.assertIn('if [ "$DOT_READER_APPLIED" = t ]; then', exporter)
        self.assertIn("filename='0756_dot_reader.sql'", exporter)
        with psycopg.connect(**self.owner_args, autocommit=True) as owner:
            owner.execute("alter role dot_reader rename to dot_existing_fixture")
            try:
                for block in blocks:
                    owner.execute(block)
                self.assertEqual(owner.execute("select rolpassword is null,rolcanlogin,rolconnlimit,rolinherit,rolbypassrls from pg_authid where rolname='dot_reader'").fetchone(), (True, True, 2, False, False))
                owner.execute("create table public.dot_snapshot_future (id int); insert into public.dot_snapshot_future values (11)")
                owner.execute("set role dot_reader")
                self.assertEqual(owner.execute("select id from public.dot_snapshot_future").fetchall(), [(11,)])
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    owner.execute("create temp table dot_snapshot_nope (id int)")
            finally:
                owner.execute("reset role")
                owner.execute("drop owned by dot_reader; drop role dot_reader")
                owner.execute("alter role dot_existing_fixture rename to dot_reader")

    @unittest.skipUnless(os.environ.get("CARR_DOT_REPO_SCHEMA") == "1", "requires assurance functions from the full schema")
    def test_assurance_gate_accepts_only_dot_select_exception(self):
        # Execute the gate's SQL itself against adversarial grants. This does
        # not keep a second privilege predicate that could agree with itself.
        tree = ast.parse((ROOT / "ops/assurance-evidence-acceptance-local-pg-gate.py").read_text())
        queries = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                   and isinstance(node.value, str) and "acl.grantee<>c.relowner" in node.value]
        self.assertEqual(len(queries), 1)
        with psycopg.connect(**self.owner_args, autocommit=True) as owner:
            owner.execute("create table ops.assurance_dot_acl_fixture (id int)")
            try:
                self.assertEqual(owner.execute(queries[0]).fetchone(), (True,))
                owner.execute("alter role dot_reader rename to dot_existing_fixture")
                try:
                    self.assertEqual(owner.execute(queries[0]).fetchone(), (False,))
                finally:
                    owner.execute("alter role dot_existing_fixture rename to dot_reader")
                for grant, revoke in (
                    ("grant insert on ops.assurance_dot_acl_fixture to dot_reader", "revoke insert on ops.assurance_dot_acl_fixture from dot_reader"),
                    ("grant select on ops.assurance_dot_acl_fixture to carr_writer", "revoke select on ops.assurance_dot_acl_fixture from carr_writer"),
                    ("grant select on ops.assurance_dot_acl_fixture to dot_reader with grant option", "revoke grant option for select on ops.assurance_dot_acl_fixture from dot_reader"),
                ):
                    try:
                        owner.execute(grant)
                        self.assertEqual(owner.execute(queries[0]).fetchone(), (False,))
                    finally:
                        owner.execute(revoke)
                self.assertEqual(owner.execute(queries[0]).fetchone(), (True,))
            finally:
                owner.execute("drop table ops.assurance_dot_acl_fixture")


if __name__ == "__main__":
    unittest.main()
