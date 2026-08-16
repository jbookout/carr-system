#!/usr/bin/env python3
"""
p1-rebuild-gate.py — PROGRAM 1's REBUILD CLAUSE, executable.

THE REQUIREMENT, from the maturity baseline's infrastructure section: "A fresh
non-production environment can be reconstructed from repository declarations and
approved secret references." Program 1's own bullet list names the mechanism:
"ephemeral Neon branches for migration/integration/recovery tests" and
"environment rebuild and drift detection."

Until this file, reconstruction had never been attempted. Not once. The claim
that the repository is sufficient to rebuild an environment was an argument, and
the only way to find out was to need it — which is the worst moment to discover
a missing piece.

WHAT THIS PROVES THAT ops/ci.sh DOES NOT. CI loads db/schema.sql into a
throwaway Postgres and applies pending migrations, which proves the SQL is
coherent. It does not prove the schema stands up on NEON, where the roles,
grants and extensions are the vendor's rather than a container's, and where
every real environment actually lives. This runs the reconstruction on the
vendor, on a branch created for the purpose and destroyed at the end.

WHY A BRANCH OF STAGING AND NEVER PRODUCTION. A Neon branch is a copy-on-write
child of its parent, so a branch of production would hand a throwaway database
every production row — the exact leak ops/p1-environment-gate.py exists to
prevent. This branches STAGING, and refuses outright if the project it resolved
is production's. That refusal is the first thing it does.

THE GUARDS, in the order they run, each one modelled on bin/restore-rehearse.sh
which learned them the expensive way:

  0. The target project is NOT production. Compared by pinned id, before
     anything is created.
  1. The created branch is not the project's DEFAULT branch. If branch-create
     ever returns the default, the teardown below would delete the environment
     it was meant to protect.
  2. The branch endpoint is a different HOST from both production and staging's
     own default. Same host means the reconstruction is about to write
     somewhere real.
  3. The reconstruction target is a FRESH DATABASE on that branch, never the
     inherited one. A branch inherits staging's copy; loading a schema on top of
     it would collide and prove nothing.

THE FOUR ASSERTIONS.

  1. THE COMMITTED SCHEMA LOADS ON NEON, from db/schema.sql alone.
  2. EVERY COMMITTED MIGRATION APPLIES ON TOP, in order, with none pending
     afterwards.
  3. THE REBUILT DATABASE CARRIES THE TABLES THE SYSTEM NEEDS — the record
     layer, the doctrine store and the operational schema, spot-checked by
     name so an empty-but-valid database cannot pass.
  4. THE BRANCH IS GONE at the end. An orphaned branch is a vendor bill and a
     database nobody is watching; teardown runs on every exit path, and says so
     loudly when it cannot.

USAGE
    .venv/bin/python ops/p1-rebuild-gate.py
    .venv/bin/python ops/p1-rebuild-gate.py --keep-branch   # leave it for debugging

It writes to NOTHING that already existed. Everything it creates it destroys.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("db_tap", REPO / "tools" / "db-tap.py")
if _spec is None or _spec.loader is None:
    sys.exit("p1-rebuild-gate: could not load tools/db-tap.py")
db_tap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_tap)

REBUILD_DB = "rebuild_check"

# Spot-checks, one per major surface. A reconstruction that produces an empty
# database is a reconstruction that failed quietly, and a table count alone
# would not notice.
REQUIRED_TABLES = (
    "public.party",              # the record layer
    "public.deal",
    "public.doctrine_section",   # the doctrine store
    "public.rule",
    "ops.service",               # the operational schema
    "ops.release",               # P0-1, the newest thing that must survive a rebuild
)

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def say(msg: str) -> None:
    print(msg, flush=True)


def host_of(conn_string: str) -> str:
    tail = conn_string.split("@", 1)[-1]
    return tail.split("/", 1)[0].split("?", 1)[0]


def neon(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([db_tap.NEONCTL, *args],
                          capture_output=True, text=True, timeout=180, env=env)


def psql(conn_string: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([db_tap.psql_bin(), "-v", "ON_ERROR_STOP=1", "-q",
                           "-d", conn_string, *args],
                           capture_output=True, text=True, timeout=900)


def _jobs_login_dsn(owner_dsn: str, password: str) -> str:
    """Make a carr_jobs URL for the same disposable database without logging it."""
    parsed = urlsplit(owner_dsn)
    host = parsed.hostname
    if parsed.scheme not in {"postgres", "postgresql"} or not host:
        raise ValueError("rebuilt database URL has no PostgreSQL host")
    rendered_host = f"[{host}]" if ":" in host else host
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("rebuilt database URL has an invalid port") from exc
    if port is not None:
        rendered_host = f"{rendered_host}:{port}"
    return urlunsplit((parsed.scheme,
                       f"carr_jobs:{quote(password, safe='')}@{rendered_host}",
                       parsed.path, parsed.query, ""))


def verify_rebuilt_jobs_login(
    owner_dsn: str,
    *,
    connect: Callable[[str], Any] | None = None,
    password_factory: Callable[[int], str] = secrets.token_urlsafe,
    sql_module: Any | None = None,
) -> bool:
    """Prove the rebuilt runtime role can authenticate as itself.

    Password is set only on the disposable rebuilt database, never printed, and
    deliberately does *not* add LOGIN. A NOLOGIN snapshot therefore fails this
    probe rather than being repaired by the test itself.
    """
    if connect is None or sql_module is None:
        import psycopg
        if connect is None:
            connect = psycopg.connect
        if sql_module is None:
            from psycopg import sql as sql_module
    assert connect is not None
    assert sql_module is not None
    password = password_factory(32)
    with connect(owner_dsn) as owner:
        owner.execute(sql_module.SQL("alter role {} password {}").format(
            sql_module.Identifier("carr_jobs"), sql_module.Literal(password)))
        owner.commit()
    with connect(_jobs_login_dsn(owner_dsn, password)) as jobs:
        row = jobs.execute("select session_user, current_user").fetchone()
    return isinstance(row, tuple) and row == ("carr_jobs", "carr_jobs")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-branch", action="store_true",
                    help="leave the ephemeral branch for debugging (it is a cost "
                         "and a database nobody watches — delete it by hand)")
    args = ap.parse_args()

    say("p1-rebuild-gate: reconstruct a non-production environment from the repo alone")

    key = db_tap._neon_api_key()
    env = {**os.environ,
           "PATH": "/usr/local/opt/node@22/bin:/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    if key:
        env["NEON_API_KEY"] = key

    staging_spec = db_tap.PROJECTS["staging"]
    prod_spec = db_tap.PROJECTS["production"]
    project_id = staging_spec.get("id") or db_tap._project_id_by_name(staging_spec["name"], env)

    # ── GUARD 0: never production ────────────────────────────────────────────
    if project_id == prod_spec.get("id"):
        sys.exit("p1-rebuild-gate: the staging name resolved to the PRODUCTION project id. "
                 "Refusing to branch, write to, or delete anything.")
    say(f"  guard 0: target project is staging ({staging_spec['name']}), not production")

    prod_host = host_of(db_tap.dsn(project="production"))
    stg_default = db_tap.dsn(project="staging")
    stg_host = host_of(stg_default)

    branch_id = ""
    branch_name = f"rebuild-check-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    try:
        # ── create the ephemeral branch ──────────────────────────────────────
        out = neon(env, "branches", "create", "--project-id", project_id,
                   "--name", branch_name, "--output", "json")
        if out.returncode != 0:
            sys.exit(f"p1-rebuild-gate: could not create the branch: {out.stderr.strip()[:300]}")
        payload = json.loads(out.stdout)
        branch = payload.get("branch", payload)
        branch_id = branch.get("id", "")
        if not branch_id:
            sys.exit("p1-rebuild-gate: branch create returned no id")

        # ── GUARD 1: not the default branch ──────────────────────────────────
        listed = neon(env, "branches", "list", "--project-id", project_id, "--output", "json")
        default_ids = {b.get("id") for b in (json.loads(listed.stdout) if listed.returncode == 0 else [])
                       if b.get("default")}
        if branch_id in default_ids:
            branch_id = ""      # do not let teardown delete it
            sys.exit("p1-rebuild-gate: branch create returned the DEFAULT branch id — "
                     "refusing to continue or delete")
        say(f"  guard 1: created {branch_name} ({branch_id}), which is not the default branch")

        # ── GUARD 2: a different host from production AND staging ────────────
        out = neon(env, "connection-string", branch_id, "--project-id", project_id,
                   "--role-name", "neondb_owner")
        if out.returncode != 0 or not out.stdout.strip():
            sys.exit("p1-rebuild-gate: could not obtain the branch connection string")
        branch_dsn = out.stdout.strip()
        branch_host = host_of(branch_dsn)
        if branch_host in (prod_host, stg_host):
            sys.exit("p1-rebuild-gate: the branch resolves to the same host as an existing "
                     "environment — refusing to write anywhere near it")
        say("  guard 2: the branch endpoint is its own host")

        # ── GUARD 3: a FRESH database, never the inherited one ───────────────
        made = psql(branch_dsn, "-c", f'create database {REBUILD_DB}')
        if made.returncode != 0:
            sys.exit(f"p1-rebuild-gate: could not create {REBUILD_DB}: {made.stderr.strip()[:300]}")
        target = branch_dsn.split("?", 1)
        rebuilt_dsn = (target[0].rsplit("/", 1)[0] + "/" + REBUILD_DB
                       + ("?" + target[1] if len(target) > 1 else ""))
        say(f"  guard 3: reconstructing into a fresh database, not the inherited copy")
        say("")

        # ── 1. the committed schema loads ────────────────────────────────────
        loaded = psql(rebuilt_dsn, "-f", str(REPO / "db" / "schema.sql"))
        check("1. db/schema.sql loads on Neon from the repository alone",
              loaded.returncode == 0,
              loaded.stderr.strip().splitlines()[-1][:200] if loaded.stderr.strip() else "")
        if loaded.returncode != 0:
            return 1

        # ── 2. every committed migration applies on top ──────────────────────
        migrate = subprocess.run(
            [sys.executable, str(REPO / "tools" / "migrate.py"), "--apply", "--yes"],
            capture_output=True, text=True, timeout=1800,
            env={**os.environ, "DATABASE_URL": rebuilt_dsn})
        applied_ok = migrate.returncode == 0
        check("2. every committed migration applies on top, in order",
              applied_ok,
              migrate.stderr.strip().splitlines()[-1][:200] if migrate.stderr.strip() else
              migrate.stdout.strip().splitlines()[-1][:200] if migrate.stdout.strip() else "")

        verify = subprocess.run(
            [sys.executable, str(REPO / "tools" / "migrate.py")],
            capture_output=True, text=True, timeout=600,
            env={**os.environ, "DATABASE_URL": rebuilt_dsn})
        check("2b. nothing is left pending afterwards",
              "pending: 0" in verify.stdout,
              verify.stdout.strip().splitlines()[-1][:160] if verify.stdout.strip() else "")

        # 0176 intentionally leaves its FK-bound scheduler surface registry
        # empty at migration time.  Synchronize the checked-in manifest before
        # the DB gate verifies exact registry parity; seeding it earlier would
        # fail on a fresh database where job definitions do not yet exist.
        synced = subprocess.run(
            [sys.executable, str(REPO / "tools" / "control-plane.py"), "sync"],
            capture_output=True, text=True, timeout=600,
            env={**os.environ, "DATABASE_URL": rebuilt_dsn})
        check("2b1. authority sync populates the FK-bound scheduler registry after definitions",
              synced.returncode == 0,
              synced.stderr.strip().splitlines()[-1][:200]
              if synced.stderr.strip() else
              synced.stdout.strip().splitlines()[-1][:200] if synced.stdout.strip() else "")

        # A role can exist and receive every grant while still being NOLOGIN.
        # Use an in-process password only on this disposable database and prove
        # the actual authenticated session; never repair LOGIN in this probe.
        try:
            jobs_login_ok = verify_rebuilt_jobs_login(rebuilt_dsn)
            jobs_login_detail = ""
        except Exception as exc:
            jobs_login_ok = False
            jobs_login_detail = f"{type(exc).__name__}: {str(exc)[:160]}"
        check("2c. rebuilt carr_jobs authenticates as session_user/current_user carr_jobs",
              jobs_login_ok, jobs_login_detail)

        # The migration chain existing is not enough: exercise the job ledger,
        # authority structure and owner-session refusal, retries, leases, receipts, cache, provider
        # health, and cost admission on the same disposable Neon database.
        control_plane = subprocess.run(
            [sys.executable, str(REPO / "ops" / "control-plane-db-gate.py")],
            capture_output=True, text=True, timeout=900,
            env={**os.environ, "DATABASE_URL": rebuilt_dsn})
        check("2d. control-plane ledger, authority structure/owner-refusal gate passes",
              control_plane.returncode == 0,
              control_plane.stderr.strip().splitlines()[-1][:200]
              if control_plane.stderr.strip() else
              control_plane.stdout.strip().splitlines()[-1][:200]
              if control_plane.stdout.strip() else "")

        # ── 3. the rebuilt database carries the tables the system needs ──────
        missing = []
        for table in REQUIRED_TABLES:
            got = psql(rebuilt_dsn, "-At", "-c", f"select to_regclass('{table}')")
            if got.returncode != 0 or not got.stdout.strip():
                missing.append(table)
        check("3. the rebuilt database carries the record, doctrine and ops tables",
              not missing,
              "absent: " + ", ".join(missing) if missing else "")

    finally:
        # ── 4. teardown, on every exit path ──────────────────────────────────
        if branch_id:
            if args.keep_branch:
                say(f"\n  teardown: KEEPING branch {branch_id} (--keep-branch).")
                say(f"            {db_tap.NEONCTL} branches delete {branch_id} "
                    f"--project-id {project_id}")
            else:
                gone = neon(env, "branches", "delete", branch_id, "--project-id", project_id)
                if gone.returncode == 0:
                    say(f"\n  ok    4. the ephemeral branch {branch_id} is gone")
                else:
                    FAILURES.append("4. teardown deleted the ephemeral branch")
                    say(f"\n  FAIL  4. COULD NOT DELETE branch {branch_id} — delete it by hand:")
                    say(f"            {db_tap.NEONCTL} branches delete {branch_id} "
                        f"--project-id {project_id}")

    print()
    if FAILURES:
        print(f"p1-rebuild-gate: {len(FAILURES)} FAILED")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("p1-rebuild-gate: a non-production environment reconstructs from the repo alone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
