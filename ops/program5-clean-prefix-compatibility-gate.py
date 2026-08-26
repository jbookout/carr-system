# ci: db-prefix-gate
"""Physical clean-prefix compatibility proof for the Program 5 0315a path.

Unlike the bounded-contract DB gate, this starts from a brand-new disposable
loopback database, loads the canonical production snapshot fixture, and lets
the real migration runner establish only the pending ledger through 0315a. It
never deletes ledger rows or replays guarded historical data migrations against
an empty business database.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys

try:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
except ImportError:
    sys.exit("program5-clean-prefix-compatibility-gate: psycopg not installed")


REPO = pathlib.Path(__file__).resolve().parent.parent
TARGET = "0315a_program5_bounded_forward_fix_rehearsal.sql"
HELD = ("0316_rule_delivery_audit_counts.sql", "0317_atomic_rule_delivery_cutover.sql")
SNAPSHOT_BOUNDARY = "0312_engineering_dispatch_controller.sql"
SNAPSHOT_COUNT = 248
URI_RE = re.compile(r"postgres(?:ql)?://[^\s'\"]+", re.I)
ERROR_LINE_RE = re.compile(r"(?:error|exception|failed|failure|refused|undefined|does not exist|violat)", re.I)
ROUTINE_STDOUT_PREFIXES = ("pending:", "held back:", "applying:", "host:", "applied:",
                           "authorized prefix:", "selected:")


def blocked(detail: str) -> int:
    clean = URI_RE.sub("postgresql://[redacted]", detail).splitlines()[0][:360]
    print("program5 clean-prefix compatibility: BLOCKED — " + clean)
    print("safe fixture alternative: build a reviewed disposable staging fixture from the canonical snapshot "
          "and prove its exact 0315a prefix; never delete/rewrite an existing staging ledger.")
    return 78


def migration_failure_detail(stderr: str, stdout: str) -> str:
    """Return the most specific redacted runner failure, never a traceback header."""
    def lines_from(text: str) -> list[str]:
        return [URI_RE.sub("postgresql://[redacted]", line).strip()
                for line in text.splitlines()
                if line.strip() and line.strip() != "Traceback (most recent call last):"]

    stderr_lines = lines_from(stderr)
    # Python tracebacks put the actionable exception at the tail; PostgreSQL
    # does likewise with ERROR after contextual lines. Prefer the last such
    # line rather than reporting the useless traceback banner. Stderr is the
    # runner's authoritative failure channel; ordinary stdout progress must
    # never override it merely because a migration filename contains “failure”.
    stderr_candidates = [line for line in stderr_lines if ERROR_LINE_RE.search(line)]
    if stderr_candidates:
        return stderr_candidates[-1][:360]
    if stderr_lines:
        return stderr_lines[-1][:360]

    stdout_lines = [line for line in lines_from(stdout)
                    if not line.lower().startswith(ROUTINE_STDOUT_PREFIXES)]
    stdout_candidates = [line for line in stdout_lines if ERROR_LINE_RE.search(line)]
    if stdout_candidates:
        return stdout_candidates[-1][:360]
    if stdout_lines:
        return stdout_lines[-1][:360]
    return "migration runner returned nonzero"


def node_release_check(dsn: str, expected_count: int, expected_digest: str) -> None:
    """Invoke the real buildRelease with a local psql-backed tagged SQL adapter."""
    module_uri = (REPO / "mcp-server" / "src" / "release.js").as_uri()
    source = f"""
import {{ spawnSync }} from 'node:child_process';
import {{ buildRelease }} from {module_uri!r};
const sql = async (strings, ...values) => {{
  if (values.length) throw new Error('unexpected release SQL interpolation');
  const query = strings.join(' ').replace(/;\\s*$/, '');
  const wrapped = `select coalesce(json_agg(row_to_json(q)), '[]'::json) from (${{query}}) q`;
  const run = spawnSync('psql', ['-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-c',wrapped], {{encoding:'utf8', env:process.env}});
  if (run.status !== 0) throw new Error('local psql query failed');
  return JSON.parse(run.stdout || '[]');
}};
const out = await buildRelease({{
  env: {{CARR_ENV:'staging',GIT_SHA:'a'.repeat(40)}}, sql, verbCount:211,
  now: () => new Date('2026-08-25T00:00:00.000Z'),
}});
if (out.schema.reason !== null || out.schema.highest_applied_migration !== {TARGET!r}
    || out.schema.applied_count !== {expected_count} || out.schema.ledger_sha256 !== {expected_digest!r}) {{
  throw new Error('buildRelease did not read the exact clean 0315a ledger: ' + JSON.stringify(out.schema));
}}
"""
    info = conninfo_to_dict(dsn)
    env = {**os.environ, "PGHOST": info["host"], "PGUSER": info["user"],
           "PGDATABASE": info["dbname"]}
    for source_key, env_key in (("port", "PGPORT"), ("password", "PGPASSWORD"),
                                ("sslmode", "PGSSLMODE")):
        if info.get(source_key):
            env[env_key] = info[source_key]
    result = subprocess.run(["node", "--input-type=module", "-e", source], cwd=REPO,
                            env=env, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("actual buildRelease check failed: " +
                           migration_failure_detail(result.stderr, result.stdout))


def reset_disposable_target(admin_dsn: str, db_name: str) -> None:
    """Recreate only CI's exact loopback carr_ci database after prefix proof.

    PostgreSQL roles are cluster-wide, so a second database created after the
    ordinary snapshot has loaded cannot replay historical role migrations.
    The prefix proof therefore consumes the initially empty carr_ci database,
    then restores that one disposable target for the normal snapshot lane.
    """
    if db_name != "carr_ci":
        raise RuntimeError("prefix gate reset target is not the exact carr_ci database")
    with psycopg.connect(admin_dsn, autocommit=True) as admin, admin.cursor() as cur:
        cur.execute("select current_database(),current_user")
        if cur.fetchone() != ("postgres", "carr_ci"):
            raise RuntimeError("prefix gate reset is not the exact disposable admin session")
        cur.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity "
            "where datname=%s and pid<>pg_backend_pid()",
            (db_name,),
        )
        cur.execute(sql.SQL("drop database {}") .format(sql.Identifier(db_name)))
        cur.execute(sql.SQL("create database {} owner carr_ci") .format(sql.Identifier(db_name)))

    recreated = dict(conninfo_to_dict(admin_dsn))
    recreated["dbname"] = db_name
    with psycopg.connect(make_conninfo(**recreated)) as conn, conn.cursor() as cur:
        cur.execute("select current_database(),current_user,to_regclass('public.schema_migrations')")
        if cur.fetchone() != ("carr_ci", "carr_ci", None):
            raise RuntimeError("prefix gate did not recreate an empty carr_ci database")
        cur.execute("""select count(*) from information_schema.tables
                        where table_schema not in ('pg_catalog','information_schema')""")
        if cur.fetchone()[0] != 0:
            raise RuntimeError("prefix gate recreated carr_ci with unexpected user tables")


def main() -> int:
    source_dsn = os.environ.get("DATABASE_URL")
    if not source_dsn:
        return blocked("DATABASE_URL is not configured for a disposable loopback gate")
    if os.environ.get("CARR_CI_DATABASE_URL") != source_dsn:
        return blocked("DATABASE_URL is not the exact CARR_CI_DATABASE_URL disposable target")
    info = conninfo_to_dict(source_dsn)
    host = (info.get("host") or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return blocked("DATABASE_URL is not loopback; prefix proof refused")
    db_name = info.get("dbname") or ""
    if db_name != "carr_ci" or info.get("user") != "carr_ci":
        return blocked("prefix proof requires the exact carr_ci disposable identity")
    admin_info = dict(info)
    admin_info["dbname"] = "postgres"
    admin_dsn = make_conninfo(**admin_info)
    outcome = 1
    reset_required = False
    try:
        with psycopg.connect(source_dsn) as conn, conn.cursor() as cur:
            cur.execute("select current_database(),current_user,to_regclass('public.schema_migrations')")
            if cur.fetchone() != ("carr_ci", "carr_ci", None):
                raise RuntimeError("prefix proof target is not the fresh empty carr_ci database")
            cur.execute("""select count(*) from information_schema.tables
                            where table_schema not in ('pg_catalog','information_schema')""")
            if cur.fetchone()[0] != 0:
                raise RuntimeError("prefix proof target already contains user tables")
        # Only an observed-empty exact CI target is disposable. A refusal on a
        # pre-populated database must never turn into permission to drop it.
        reset_required = True
        psql = shutil.which("psql")
        if not psql:
            raise RuntimeError("psql is unavailable for the canonical snapshot fixture")
        # psql receives individual libpq fields through its private child
        # environment. The complete URI is never placed in argv or diagnostics.
        psql_env = {**os.environ, "PGHOST": info["host"], "PGUSER": info["user"],
                    "PGDATABASE": info["dbname"],
                    "PGOPTIONS": "--client-min-messages=warning"}
        for source_key, env_key in (("port", "PGPORT"), ("password", "PGPASSWORD"),
                                    ("sslmode", "PGSSLMODE")):
            if info.get(source_key):
                psql_env[env_key] = info[source_key]
        snapshot = subprocess.run(
            [psql, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(REPO / "db" / "schema.sql")],
            cwd=REPO, env=psql_env,
            text=True, capture_output=True, check=False)
        if snapshot.returncode:
            outcome = blocked("canonical snapshot fixture did not load: " +
                              migration_failure_detail(snapshot.stderr, snapshot.stdout))
            return outcome
        with psycopg.connect(source_dsn) as conn, conn.cursor() as cur:
            cur.execute("select count(*),max(filename collate \"C\") from public.schema_migrations")
            if cur.fetchone() != (SNAPSHOT_COUNT, SNAPSHOT_BOUNDARY):
                raise RuntimeError("canonical snapshot is not the exact Production 0312/248 boundary")
        runner = subprocess.run(
            [sys.executable, str(REPO / "tools" / "migrate.py"), "--apply", "--yes", "--through", TARGET],
            cwd=REPO, env={**os.environ, "DATABASE_URL": source_dsn}, text=True,
            capture_output=True, check=False)
        if runner.returncode:
            outcome = blocked("empty replay stopped before 0315a: " +
                              migration_failure_detail(runner.stderr, runner.stdout))
        else:
            with psycopg.connect(source_dsn) as conn, conn.cursor() as cur:
                cur.execute("select filename,sha256 from public.schema_migrations order by filename collate \"C\"")
                rows = list(cur.fetchall())
                names = [name for name, _digest in rows]
                if len(rows) != SNAPSHOT_COUNT + 3 or not rows or names[-1] != TARGET or any(name in names for name in HELD):
                    raise RuntimeError("clean runner did not stop at the exact 0315a ledger boundary")
                material = "".join(f"{name}\0{digest}\n" for name, digest in rows).encode()
                digest = "sha256:" + __import__("hashlib").sha256(material).hexdigest()
                cur.execute("select to_regprocedure('ops.rule_delivery_audit_counts(integer)'), to_regclass('ops.rule_delivery_activation_target')")
                if cur.fetchone() != (None, None):
                    raise RuntimeError("held-back 0316/0317 database objects exist at the clean 0315a prefix")
            node_release_check(source_dsn, len(rows), digest)
            print(f"program5 clean-prefix compatibility: PASS ({len(rows)} migrations through 0315a; 0316/0317 absent)")
            outcome = 0
    except (psycopg.Error, OSError, RuntimeError) as exc:
        outcome = blocked(type(exc).__name__ + ": " + str(exc))
    finally:
        if reset_required:
            try:
                reset_disposable_target(admin_dsn, db_name)
            except (psycopg.Error, RuntimeError):
                print("program5 clean-prefix compatibility: FAILED to recreate its disposable carr_ci database", file=sys.stderr)
                outcome = 1
    return outcome


if __name__ == "__main__":
    raise SystemExit(main())
