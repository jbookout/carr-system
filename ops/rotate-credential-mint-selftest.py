#!/usr/bin/env python3
"""Hermetic tests for the dormant carr_backup rotation primitives.

Backup mutation is deliberately unavailable until a canonical server-validated
receipt exists.  These tests therefore exercise no provider or database: they
cover the fail-closed entrypoints and the future-enablement helpers only.
"""
from __future__ import annotations

import contextlib
import ast
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rotate_credential", REPO / "tools" / "rotate-credential.py")
if SPEC is None or SPEC.loader is None:
    raise SystemExit("rotate-credential-mint-selftest: cannot load credential tool")
rc: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rc)

FAILURES: list[str] = []
OWNER = "postgresql://owner:ownerpw@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require"
PEER = "postgresql://carr_jobs:oldpw@ep-x-123.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"


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


def main() -> int:
    source = (REPO / "tools" / "rotate-credential.py").read_text(encoding="utf-8")
    workflow = (REPO / ".github" / "workflows" / "backup-nightly.yml").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    check("no callable carr_backup ALTER ROLE path remains",
          "_rotate_backup_role_enabled" not in function_names
          and 'sql.Identifier("carr_backup")' not in source
          and "alter role carr_backup" not in source.lower()
          and {"rotate_role", "rotate_backup_role"}.issubset(function_names)
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

    # Both public backup entrypoints must stop before every local or provider
    # primitive.  Replacements would throw if any one were reached.
    real_lock, real_read, real_run = rc.credential_env_lock, rc.read_env, rc.subprocess.run
    rc.credential_env_lock = lambda: (_ for _ in ()).throw(AssertionError("lock reached"))
    rc.read_env = lambda: (_ for _ in ()).throw(AssertionError("env reached"))
    rc.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess reached"))
    try:
        check("rotate_role backup refusal precedes all mutation primitives",
              "disabled" in refused(lambda: rc.rotate_role("carr_backup", True, True)))
        check("direct backup entrypoint refusal precedes all mutation primitives",
              "disabled" in refused(lambda: rc.rotate_backup_role(True)))
        check("generic internal helper cannot bypass carr_backup refusal",
              "permitted only" in refused(lambda: rc._rotate_existing_role("carr_backup", True)))
        check("deepest ALTER ROLE helper cannot bypass carr_backup refusal",
              "permitted only" in refused(lambda: rc._rotate_existing_role_locked("carr_backup", True)))
    finally:
        rc.credential_env_lock, rc.read_env, rc.subprocess.run = real_lock, real_read, real_run

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
        rc.set_github_secret("postgresql://carr_backup:NEVERPRINT@host/db?sslmode=require")
        check("GitHub call pins repository, host, and a bounded timeout",
              recorded["argv"] == ["gh", "secret", "set", "BACKUP_DATABASE_URL", "--repo", "jbookout/carr-system"]
              and recorded["env"]["GH_HOST"] == "github.com" and recorded["timeout"] == 30)
        rc.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("gh", 30))
        check("GitHub timeout is secret-free and resumable", "timed out" in refused(
            lambda: rc.set_github_secret("postgresql://carr_backup:NEVERPRINT@host/db?sslmode=require")))
        os.environ["GH_HOST"] = "evil.example"
        check("ambient GitHub host redirect is refused", "non-github.com" in refused(
            lambda: rc.set_github_secret("postgresql://carr_backup:NEVERPRINT@host/db?sslmode=require")))
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

    if FAILURES:
        print(f"rotate-credential-mint-selftest: {len(FAILURES)} FAILED")
        return 1
    print("rotate-credential-mint-selftest: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
