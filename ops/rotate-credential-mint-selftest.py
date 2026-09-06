#!/usr/bin/env python3
"""Tests for the carr_backup rotation primitives and its privilege contract.

Most of this file is hermetic: it covers the fail-closed entrypoints, the URL
parser and the durable-write guard, touching no provider and no database.

The LAST suite is different and deliberately so.  verify_backup_least_privilege
is thirteen SQL assertions about a live role, and four of them were rewritten on
2026-09-03 after they refused a real provisioning run for reasons that were
defects in the assertions rather than over-privilege in the role.  Assertions of
that shape cannot be tested by asserting a fake row: the previous versions were
never once executed against a database, which is exactly how they came to be
wrong.  So that suite builds a disposable PostgreSQL cluster, reproduces the
production shapes, and MUTATES THE DATABASE STATE the assertions read -- never
the SQL text -- to prove each one refuses the over-privilege it claims to catch.
It skips, without failing, when no local PostgreSQL is installed.
"""
from __future__ import annotations

import contextlib
import ast
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rotate_credential", REPO / "tools" / "rotate-credential.py")
if SPEC is None or SPEC.loader is None:
    raise SystemExit("rotate-credential-mint-selftest: cannot load credential tool")
rc: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rc)

FAILURES: list[str] = []
OWNER = "postgresql://owner:ownerpw@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require"  # ci-secret-scan: allow — hermetic fixture
PEER = "postgresql://carr_jobs:oldpw@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"  # ci-secret-scan: allow — hermetic fixture


def check(label: str, ok: bool) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        FAILURES.append(label)


def refused(call) -> str:
    try:
        call()
    except SystemExit as exc:
        return str(exc)
    return ""


@contextlib.contextmanager
def isolated_state():
    with tempfile.TemporaryDirectory() as raw:
        previous = rc.ENV_PATH
        rc.ENV_PATH = str(Path(raw) / "db.env")
        try:
            yield Path(raw)
        finally:
            rc.ENV_PATH = previous


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, row):
        self.row = row
        self.query = ""

    def execute(self, query):
        self.query = str(query)
        return Result(self.row)


# --------------------------------------------------------------------------
# The least-privilege contract, mutation-tested against a real cluster.
#
# WHY A REAL CLUSTER.  Every assertion here reads a PostgreSQL system catalog,
# and the four rewritten on 2026-09-03 were wrong precisely because nobody had
# ever run them against one: two tested PUBLIC defaults (TEMPORARY on the
# database, EXECUTE on functions) and blamed the role for a server default that
# no rotation could clear; one omitted views from its allowed-kind list and so
# flagged correct read-only grants as over-privilege; and one banned row
# security outright when row security UNDER-privileges a backup reader -- a
# reader with no policy silently dumps fewer rows than the table holds, which is
# a quietly incomplete backup and worse than a failed one.  A fake row cannot
# catch any of that.
#
# WHAT IS MUTATED.  The database state, never the SQL.  Mutating the text would
# only prove the text is load-bearing; mutating the state proves the assertion
# catches the real defect.  Two mutations (M1b, M1c) exercise clauses the
# rewrite DELETED, because dropping a clause is only safe if a neighbouring
# check really covers what it covered -- both land on the datacl check, which is
# what makes the deletion honest rather than a silently removed control.

LEAST_PRIVILEGE_FIXTURE = """
create role carr_backup login password 'fixturepw';
create schema ops;
create table public.plain (id int primary key, body text);
create table ops.work_request (id int primary key, body text);
create table ops.other (id int primary key);
create table public.parted (id int) partition by range (id);
create table public.parted_1 partition of public.parted for values from (0) to (10);
create view ops.v_summary as select id from ops.other;
create materialized view ops.m_summary as select id from ops.other;
create sequence ops.seq_thing;
create function ops.f_thing() returns int language sql as 'select 1';
create extension file_fdw;
create server fileserver foreign data wrapper file_fdw;
create foreign table ops.ft_thing (id text) server fileserver
  options (filename '/dev/null', format 'csv');
create schema elsewhere;
create table elsewhere.hidden (id int);

-- row security on exactly one table, mirroring production's ops.work_request
alter table ops.work_request enable row level security;
create policy carr_backup_full_read on ops.work_request
  for select to carr_backup using (true);

-- the least-privilege grant set the contract is supposed to accept
grant usage on schema ops to carr_backup;
grant select on ops.work_request, ops.other, ops.v_summary, ops.m_summary to carr_backup;
grant select on public.plain, public.parted, public.parted_1 to carr_backup;
grant select on sequence ops.seq_thing to carr_backup;
"""

# (label, expectation, break, restore)
LEAST_PRIVILEGE_MUTATIONS = (
    ("check 5 refuses when CONNECT is revoked from the role and from PUBLIC", "REFUSE",
     "revoke connect on database carr_ci from public;"
     " revoke connect on database carr_ci from carr_backup;",
     "grant connect on database carr_ci to public;"),
    ("the DELETED CREATE clause is covered: CREATE on the database still refuses", "REFUSE",
     "grant create on database carr_ci to carr_backup;",
     "revoke create on database carr_ci from carr_backup;"),
    ("the DELETED TEMPORARY clause is covered: TEMPORARY still refuses", "REFUSE",
     "grant temporary on database carr_ci to carr_backup;",
     "revoke temporary on database carr_ci from carr_backup;"),
    ("check 4 refuses CONNECT granted WITH GRANT OPTION", "REFUSE",
     "grant connect on database carr_ci to carr_backup with grant option;",
     "revoke grant option for connect on database carr_ci from carr_backup;"),

    ("check 8 refuses a grantable SELECT on a view", "REFUSE",
     "grant select on ops.v_summary to carr_backup with grant option;",
     "revoke grant option for select on ops.v_summary from carr_backup;"),
    ("check 8 refuses INSERT on a view", "REFUSE",
     "grant insert on ops.v_summary to carr_backup;",
     "revoke insert on ops.v_summary from carr_backup;"),
    ("check 8 refuses UPDATE on a materialised view", "REFUSE",
     "grant update on ops.m_summary to carr_backup;",
     "revoke update on ops.m_summary from carr_backup;"),
    ("check 8 refuses any grant outside public and ops", "REFUSE",
     "grant usage on schema elsewhere to carr_backup;"
     " grant select on elsewhere.hidden to carr_backup;",
     "revoke all on elsewhere.hidden from carr_backup;"
     " revoke all on schema elsewhere from carr_backup;"),
    ("adding v and m did not open the kind list: a foreign table still refuses", "REFUSE",
     "grant select on ops.ft_thing to carr_backup;",
     "revoke select on ops.ft_thing from carr_backup;"),

    ("check 11 refuses row security with NO policy at all", "REFUSE",
     "alter table ops.other enable row level security;",
     "alter table ops.other disable row level security;"),
    ("check 11 refuses a policy that names a different role", "REFUSE",
     "drop policy carr_backup_full_read on ops.work_request;"
     " create policy p on ops.work_request for select to carr_ci using (true);",
     "drop policy p on ops.work_request;"
     " create policy carr_backup_full_read on ops.work_request"
     " for select to carr_backup using (true);"),
    ("check 11 refuses a RESTRICTIVE-only policy, which grants no read", "REFUSE",
     "drop policy carr_backup_full_read on ops.work_request;"
     " create policy p on ops.work_request as restrictive"
     " for select to carr_backup using (true);",
     "drop policy p on ops.work_request;"
     " create policy carr_backup_full_read on ops.work_request"
     " for select to carr_backup using (true);"),
    ("check 11 refuses a policy that is not a read command", "REFUSE",
     "drop policy carr_backup_full_read on ops.work_request;"
     " create policy p on ops.work_request for insert to carr_backup with check (true);",
     "drop policy p on ops.work_request;"
     " create policy carr_backup_full_read on ops.work_request"
     " for select to carr_backup using (true);"),
    ("check 11 ACCEPTS forced row security while the read policy stands", "PASS",
     "alter table ops.work_request force row level security;",
     "alter table ops.work_request no force row level security;"),

    ("check 13 refuses EXECUTE granted on a function", "REFUSE",
     "grant execute on function ops.f_thing() to carr_backup;",
     "revoke execute on function ops.f_thing() from carr_backup;"),

    # Controls on assertions the rewrite did NOT touch.  A fixture that passed
    # everything for the wrong reason would show up here first.
    ("check 1 refuses a role granted BYPASSRLS", "REFUSE",
     "alter role carr_backup bypassrls;", "alter role carr_backup nobypassrls;"),
    ("check 10 refuses INSERT on a table", "REFUSE",
     "grant insert on ops.other to carr_backup;",
     "revoke insert on ops.other from carr_backup;"),
    ("check 12 refuses USAGE on a sequence", "REFUSE",
     "grant usage on sequence ops.seq_thing to carr_backup;",
     "revoke usage on sequence ops.seq_thing from carr_backup;"),
)


def _free_port(start: int = 55600) -> int:
    import socket
    for port in range(start, start + 400):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port for the disposable cluster")


def _contract_refuses(conn) -> bool:
    try:
        rc.verify_backup_least_privilege(conn)
    except SystemExit:
        return True
    return False


def least_privilege_mutation_suite() -> None:
    """Prove each assertion refuses the over-privilege it claims to catch."""
    missing = [name for name in ("initdb", "pg_ctl", "createdb") if not shutil.which(name)]
    if missing:
        print(f"  skip  least-privilege mutations: no local PostgreSQL ({', '.join(missing)})")
        return
    try:
        import psycopg
    except ImportError:
        print("  skip  least-privilege mutations: psycopg is not installed")
        return

    port = _free_port()
    workdir = tempfile.mkdtemp(prefix="carr-lp-mutation-")
    data = Path(workdir) / "data"
    env = dict(os.environ)
    env["LC_ALL"] = "C"          # initdb refuses some inherited locales on this Mac
    started = False
    try:
        subprocess.run(["initdb", "-D", str(data), "-U", "carr_ci", "--auth=trust",
                        "-E", "UTF8", "--no-sync"],
                       check=True, env=env, stdout=subprocess.DEVNULL)
        subprocess.run(["pg_ctl", "-D", str(data), "-w", "-o",
                        f"-h 127.0.0.1 -p {port} -c fsync=off",
                        "-l", str(Path(workdir) / "log"), "start"],
                       check=True, env=env, stdout=subprocess.DEVNULL)
        started = True
        subprocess.run(["createdb", "-h", "127.0.0.1", "-p", str(port),
                        "-U", "carr_ci", "carr_ci"],
                       check=True, env=env, stdout=subprocess.DEVNULL)

        with psycopg.connect(f"postgres://carr_ci@127.0.0.1:{port}/carr_ci",
                             autocommit=True) as conn:
            conn.execute(LEAST_PRIVILEGE_FIXTURE)

            # The control.  A fixture the contract already refuses would make
            # every mutation below vacuously "fail correctly".
            check("a correctly least-privileged carr_backup satisfies the contract",
                  not _contract_refuses(conn))
            if _contract_refuses(conn):
                return

            for label, expectation, break_sql, restore_sql in LEAST_PRIVILEGE_MUTATIONS:
                conn.execute(break_sql)
                observed = "REFUSE" if _contract_refuses(conn) else "PASS"
                check(label, observed == expectation)
                conn.execute(restore_sql)
                # A restore that does not return the fixture to a passing state
                # would silently poison every later mutation.
                check(f"fixture restored after: {label}", not _contract_refuses(conn))
    finally:
        if started:
            subprocess.run(["pg_ctl", "-D", str(data), "-m", "immediate", "stop"],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    source = (REPO / "tools" / "rotate-credential.py").read_text(encoding="utf-8")
    workflow = (REPO / ".github" / "workflows" / "backup-nightly.yml").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    # SUPERSEDED 2026-09-03.  This assertion used to require that NO callable
    # carr_backup ALTER ROLE path existed at all, because rotation was dormant
    # behind an unconditional refusal.  That refusal named a receipt seam that
    # was implemented nowhere, so it could never be satisfied by anyone -- and
    # the local nightly backup went unprovisioned for weeks behind it.  The
    # refusal is now satisfiable by the repo's real break-glass envelope, so a
    # callable path is the intended state and asserting its absence would be
    # asserting the outage.  What replaces it is the property that still
    # matters: the path exists, and it is reachable ONLY behind that receipt.
    check("the carr_backup ALTER ROLE path exists and is receipt-gated",
          {"rotate_role", "rotate_backup_role",
           "_require_backup_mutation_receipt"}.issubset(function_names)
          and 'sql.Identifier("carr_backup")' in source
          and "CARR_BREAK_GLASS" in source
          and rc.MINTABLE == set())
    check("workflow documentation does not sanction direct backup credential mutation",
          "PROVISIONING IS DISABLED" in workflow
          and "alter role carr_backup password" not in workflow.lower()
          and "gh secret set BACKUP_DATABASE_URL" not in workflow
          and "Provisioning steps are in this workflow" not in workflow
          and "run it manually (workflow_dispatch)" not in workflow)
    _, owner_target = rc._postgres_parts(OWNER, "owner")
    minted = rc.mint_url("carr_backup", {"CARR_DB_JOBS_URL": PEER}, "A" * 40, owner_target)
    check("canonical TLS-only peer mints a pinned backup URL",
          minted.endswith("?sslmode=require") and "oldpw" not in minted)
    exact = (
        "sslmode=require&host=evil", "sslmode=require&hostaddr=127.0.0.1",
        "sslmode=require&port=5433", "sslmode=require&dbname=other",
        "sslmode=require&user=other", "sslmode=require&service=other",
        "sslmode=require&options=-csearch_path=evil", "sslmode=require&sslmode=require",
        "channel_binding=require&sslmode=require", "sslmode=verify-full",
    )
    for query in exact:
        dsn = f"postgresql://x:y@host/db?{query}"
        check(f"libpq override refuses {query.split('=', 1)[0]}",
              "ambiguous libpq query override" in refused(lambda dsn=dsn: rc._postgres_parts(dsn, "test")))
    check("owner, peer, existing, and pending share strict parser",
          all("ambiguous libpq query override" in refused(lambda value=value: rc._postgres_parts(value, "test"))
              for value in ("postgresql://x:y@host/db?sslmode=require&user=x",) * 4))

    # EVERY backup entrypoint must stop before every local or provider primitive
    # when no break-glass receipt exists.  The replacements below throw if any
    # one is reached, so a missing gate surfaces as a crash rather than a pass.
    #
    # THIS IS FOUR ENTRYPOINTS ON PURPOSE, not one plus decoration.  On
    # 2026-09-03 implementing rotate_backup_role dropped the receipt check from
    # that function alone: the CLI stayed gated (main goes through rotate_role)
    # while the public, importable entrypoint reached ALTER ROLE on production
    # with no receipt written.  Only the per-entrypoint form catches that; a
    # test of the CLI path would have passed.
    real_lock, real_read, real_run = rc.credential_env_lock, rc.read_env, rc.subprocess.run
    real_break_glass = os.environ.pop("CARR_BREAK_GLASS", None)
    rc.credential_env_lock = lambda: (_ for _ in ()).throw(AssertionError("lock reached"))
    rc.read_env = lambda: (_ for _ in ()).throw(AssertionError("env reached"))
    rc.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess reached"))
    try:
        check("rotate_role refuses carr_backup without a receipt, before any primitive",
              "break-glass receipt" in refused(lambda: rc.rotate_role("carr_backup", True, True)))
        check("the public backup entrypoint refuses on its own, before any primitive",
              "break-glass receipt" in refused(lambda: rc.rotate_backup_role(True)))
        check("generic internal helper cannot bypass carr_backup refusal",
              "permitted only" in refused(lambda: rc._rotate_existing_role("carr_backup", True)))
        check("deepest ALTER ROLE helper cannot bypass carr_backup refusal",
              "permitted only" in refused(lambda: rc._rotate_existing_role_locked("carr_backup", True)))
    finally:
        rc.credential_env_lock, rc.read_env, rc.subprocess.run = real_lock, real_read, real_run
        if real_break_glass is not None:
            os.environ["CARR_BREAK_GLASS"] = real_break_glass

    with isolated_state() as raw:
        pending = "postgresql://carr_backup:Z@host/db?sslmode=require"
        rc._write_pending_backup_url(pending)
        path = Path(rc._private_pending_path())
        check("pending canonical is private regular 0600 with one link",
              path.read_text(encoding="utf-8") == pending + "\n"
              and stat.S_IMODE(path.stat().st_mode) == 0o600 and path.stat().st_nlink == 1)
        check("pending canonical publication never overwrites",
              "already exists" in refused(lambda: rc._write_pending_backup_url(pending)))
        prefix = rc._prepublication_prefix(str(path))
        fd, twin = tempfile.mkstemp(dir=raw, prefix=prefix)
        os.write(fd, (pending + "\n").encode())
        os.fsync(fd)
        os.close(fd)
        os.chmod(twin, 0o600)
        os.unlink(path)
        os.link(twin, path)
        rc._clean_prepublication_temps(str(path))
        check("only a validated same-inode publication twin is cleaned", path.stat().st_nlink == 1)
        rc._clear_pending_backup_url()
        real_link = rc.os.link
        rc.os.link = lambda *unused: (_ for _ in ()).throw(RuntimeError("injected prepublish failure"))
        try:
            try:
                rc._write_pending_backup_url(pending)
            except RuntimeError:
                pass
            check("prepublication crash leaves canonical pending state absent", not path.exists())
            check("prepublication temp is safely cleaned on next attempt",
                  not list(raw.glob(path.name + ".prepublish.*")))
        finally:
            rc.os.link = real_link

        lock = Path(rc.ENV_PATH + ".rotate-credential.lock")
        target = raw / "not-a-lock"
        target.write_text("x", encoding="utf-8")
        lock.symlink_to(target)
        check("symlink lock is refused", "cannot be opened safely" in refused(lambda: rc.credential_env_lock().__enter__()))
        lock.unlink()
        lock.write_text("x", encoding="utf-8")
        os.chmod(lock, 0o644)
        check("non-0600 lock is refused", "not a private regular" in refused(lambda: rc.credential_env_lock().__enter__()))
        lock.unlink()
        lock.write_text("x", encoding="utf-8")
        os.chmod(lock, 0o600)
        sibling = raw / "lock-hardlink"
        os.link(lock, sibling)
        check("multi-link lock is refused", "not a private regular" in refused(lambda: rc.credential_env_lock().__enter__()))
        sibling.unlink()
        lock.unlink()
        with rc.credential_env_lock():
            check("private regular lock can be acquired", True)

    recorded: dict[str, object] = {}
    real_run, old_host = rc.subprocess.run, os.environ.get("GH_HOST")
    rc.subprocess.run = lambda argv, **kw: (recorded.update(argv=argv, **kw) or types.SimpleNamespace(returncode=0))
    try:
        rc.set_github_secret("postgresql://carr_backup:NEVERPRINT@host/db?sslmode=require")  # ci-secret-scan: allow — hermetic fixture
        gh_environment = cast(dict[str, str], recorded["env"])
        check("GitHub call pins repository, host, and a bounded timeout",
              recorded["argv"] == ["gh", "secret", "set", "BACKUP_DATABASE_URL", "--repo", "jbookout/carr-system"]
              and gh_environment["GH_HOST"] == "github.com" and recorded["timeout"] == 30)
        rc.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("gh", 30))
        check("GitHub timeout is secret-free and resumable", "timed out" in refused(
            lambda: rc.set_github_secret("postgresql://carr_backup:NEVERPRINT@host/db?sslmode=require")))  # ci-secret-scan: allow — hermetic fixture
        os.environ["GH_HOST"] = "evil.example"
        check("ambient GitHub host redirect is refused", "non-github.com" in refused(
            lambda: rc.set_github_secret("postgresql://carr_backup:NEVERPRINT@host/db?sslmode=require")))  # ci-secret-scan: allow — hermetic fixture
    finally:
        rc.subprocess.run = real_run
        if old_host is None:
            os.environ.pop("GH_HOST", None)
        else:
            os.environ["GH_HOST"] = old_host

    identity = Connection(("carr_backup", "carr_backup"))
    rc.verify_backup_connection(identity)
    check("credential identity requires session_user and current_user", "session_user" in identity.query)
    check("credential identity rejects SET ROLE/proxy mismatch",
          "session identity" in refused(lambda: rc.verify_backup_connection(Connection(("owner", "carr_backup")))))
    owner_ok = Connection((True,) * 9)
    rc.verify_backup_least_privilege(owner_ok)
    check("owner-derived verification covers powerful attrs, reachability, ownership, ACLs, tables and sequences",
          all(token in owner_ok.query for token in ("rolbypassrls", "reachable", "pg_database",
              "pg_proc", "aclexplode", "has_table_privilege", "has_sequence_privilege", "is_grantable")))
    check("owner-derived verification fails closed on any missing contract element",
          "least-privilege" in refused(lambda: rc.verify_backup_least_privilege(Connection((True,) * 8 + (False,)))))

    # db.env HAS TWO PARSERS AND ONLY ONE OF THEM IS PYTHON.  These cover the
    # shell one.  The failure being fenced is real and cost six days: a value
    # carrying `&channel_binding=require` reached db.env unquoted on 2026-08-20,
    # zsh aborted the whole `set -a; . db.env` at that line, and every key in the
    # file — not just the bad one — went missing for every job on the machine.
    with isolated_state() as raw:
        broken = raw / "broken.env"
        broken.write_text(
            "CARR_X_URL=postgresql://u:pw@h.example/db?sslmode=require&channel_binding=require\n",  # ci-secret-scan: allow — hermetic fixture
            encoding="utf-8")
        quoted = raw / "quoted.env"
        quoted.write_text(
            "CARR_X_URL='postgresql://u:pw@h.example/db?sslmode=require&channel_binding=require'\n",  # ci-secret-scan: allow — hermetic fixture
            encoding="utf-8")
        check("an unquoted ampersand DSN is reported as a shell parse failure",
              rc.shell_parse_failure(str(broken)) == "1")
        check("the single-quoted form of the same DSN parses",
              rc.shell_parse_failure(str(quoted)) is None)
        check("a shell-unsourceable candidate is refused by name and line",
              "does not parse as shell at line 1"
              in refused(lambda: rc._require_shell_sourceable(str(broken))))
        # A machine without the probe is where this tool already stood; the
        # guard must not turn "cannot ask" into "must not rotate".
        real_zsh = rc.ZSH
        rc.ZSH = str(raw / "no-such-shell")
        try:
            check("an unrunnable shell probe never blocks a rotation",
                  rc.shell_parse_failure(str(broken)) is None
                  and rc._require_shell_sourceable(str(broken)) is None)
        finally:
            rc.ZSH = real_zsh

    with isolated_state() as raw:
        env_path = Path(rc.ENV_PATH)
        env_path.write_text("CARR_KEEP_URL='postgresql://k:kp@h.example/db'\n# a comment\n\n",  # ci-secret-scan: allow — hermetic fixture
                            encoding="utf-8")
        env_path.chmod(0o600)
        before = env_path.read_bytes()
        rc.write_env_key("CARR_X_URL", "postgresql://u:pw@h.example/db?sslmode=require&channel_binding=require")  # ci-secret-scan: allow — hermetic fixture
        after = env_path.read_text(encoding="utf-8").splitlines()
        check("a written value is single-quoted and the rest of the file survives",
              after[-1].startswith("CARR_X_URL='") and after[-1].endswith("'")
              and after[:3] == ["CARR_KEEP_URL='postgresql://k:kp@h.example/db'",  # ci-secret-scan: allow — hermetic fixture
                                "# a comment", ""])
        check("the file rotate-credential just wrote is sourceable",
              rc.shell_parse_failure(str(env_path)) is None)

        # The quoting and the guard are two claims, and only the second one
        # survives a future writer that forgets the first.  Disabling the quoting
        # is the only way to prove write_env_key is actually wired to the guard
        # rather than merely being correct today.
        env_path.write_bytes(before)
        real_quote = rc.shell_quote
        rc.shell_quote = lambda value: value
        try:
            check("write_env_key refuses a value the shell could not source",
                  "does not parse as shell at line" in refused(
                      lambda: rc.write_env_key(
                          "CARR_X_URL",
                          "postgresql://u:pw@h.example/db?sslmode=require&channel_binding=require")))  # ci-secret-scan: allow — hermetic fixture
        finally:
            rc.shell_quote = real_quote
        check("the refused write left db.env exactly as it was",
              env_path.read_bytes() == before)

        # The guard must fence the SWAP, not merely report afterwards: a
        # credential file that fails its contract may not be live for an instant.
        env_path.write_bytes(before)
        original = env_path.read_bytes()
        def _always_bad(_candidate: str) -> None:
            raise SystemExit("verify refused")
        try:
            rc._durable_replace(rc.ENV_PATH, "CARR_X_URL=broken&value\n", prefix=".db.env.",
                                verify=_always_bad)
        except SystemExit:
            pass
        leftovers = [entry.name for entry in env_path.parent.iterdir()
                     if entry.name.startswith(".db.env.")]
        check("a refused write leaves the live file byte-identical and no temp behind",
              env_path.read_bytes() == original and leftovers == [])

    least_privilege_mutation_suite()

    if FAILURES:
        print(f"rotate-credential-mint-selftest: {len(FAILURES)} FAILED")
        return 1
    print("rotate-credential-mint-selftest: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
