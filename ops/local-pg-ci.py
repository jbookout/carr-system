#!/usr/bin/env python3
"""Run canonical CI against a disposable, loopback-only local PostgreSQL 17.

This is the default database-development lane.  It creates no persistent
cluster, never accepts a caller DSN, strips cloud/provider credentials from the
child environment, and confirms teardown before removing the temporary cluster.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import signal
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence


sys.path.insert(0, str(Path(__file__).resolve().parent))
from db_acceptance_shards import select_programs, make_report, write_report


class LocalPGRefusal(RuntimeError):
    pass


@dataclass(frozen=True)
class PostgresBinaries:
    initdb: Path
    pg_ctl: Path
    createdb: Path
    psql: Path


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str | Path],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        capture: bool = False,
    ) -> CommandResult: ...


class SubprocessRunner:
    def run(
        self,
        command: Sequence[str | Path],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        capture: bool = False,
    ) -> CommandResult:
        completed = subprocess.run(
            [str(part) for part in command],
            env=None if env is None else dict(env),
            cwd=cwd,
            text=True,
            capture_output=capture,
            check=False,
        )
        return CommandResult(
            completed.returncode, completed.stdout or "", completed.stderr or ""
        )


def repository_python(repo: Path) -> Path:
    candidate = repo / ".venv/bin/python"
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else Path(sys.executable)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_snapshot_candidate(
    *, repo: Path, port: int, artifact_dir: Path, runner: CommandRunner | None = None
) -> int:
    """Export from one hosted disposable PG17 cluster and prove a fresh restore.

    This is a manual, explicitly opted-in workflow mode.  It never takes a DSN
    or provider credential from its caller, and it publishes nothing unless the
    candidate has restored and passed the canonical migration class.
    """
    validate_port(port)
    validate_port(port + 1)
    if (not hosted_execution_is_declared()
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_WORKFLOW") != "DB acceptance"):
        raise LocalPGRefusal("snapshot export requires the declared manual hosted DB-acceptance lane")
    if not port_is_available(port) or not port_is_available(port + 1):
        raise LocalPGRefusal("both disposable PostgreSQL loopback ports must be free")
    if not artifact_dir.is_absolute() or artifact_dir.exists():
        raise LocalPGRefusal("artifact directory must be a new absolute path")
    resolved_repo = repo.resolve()
    if artifact_dir.resolve().is_relative_to(resolved_repo):
        raise LocalPGRefusal("artifact directory must be outside the repository")

    binaries = find_postgres_binaries()
    command_runner = runner or SubprocessRunner()
    clean_env = scrub_cloud_environment(os.environ)
    clean_env["LC_ALL"] = "C"
    clean_env["PATH"] = f"{binaries.initdb.parent}{os.pathsep}{clean_env.get('PATH', '')}"
    python = repository_python(repo)

    def checked(command: Sequence[str | Path], *, env: Mapping[str, str] | None = None) -> CommandResult:
        result = command_runner.run(command, env=env or clean_env, cwd=repo, capture=True)
        if result.returncode:
            raise LocalPGRefusal(
                f"snapshot export command failed ({Path(str(command[0])).name}): {_failure_detail(result)}"
            )
        return result

    source_head = checked(["git", "rev-parse", "HEAD"]).stdout.strip()
    source_tree = checked(["git", "rev-parse", "HEAD^{tree}"]).stdout.strip()
    if not all(len(value) == 40 and all(ch in "0123456789abcdef" for ch in value)
               for value in (source_head, source_tree)):
        raise LocalPGRefusal("snapshot export source binding is not a full git HEAD/tree")
    if checked(["git", "status", "--porcelain"]).stdout.strip():
        raise LocalPGRefusal("snapshot export requires a clean exact-source checkout")
    pg_version = checked([binaries.initdb, "--version"]).stdout.strip()
    if "PostgreSQL) 17." not in pg_version:
        raise LocalPGRefusal("snapshot export requires PostgreSQL 17 binaries")
    baseline_sha256 = _sha256(repo / "db/schema.sql")

    root = Path(tempfile.mkdtemp(prefix="carr-local-pg-ci."))
    clusters = [(root / "source-data", port), (root / "restore-data", port + 1)]
    start_attempts: list[Path] = []
    candidate = root / "candidate.sql"
    baseline_copy = root / "baseline.sql"
    source_dsn = f"postgres://carr_ci@127.0.0.1:{port}/carr_ci"
    restore_dsn = f"postgres://carr_ci@127.0.0.1:{port + 1}/carr_ci"
    try:
        for data, cluster_port in clusters:
            checked([binaries.initdb, "-D", data, "-U", "carr_ci", "--auth=trust",
                     "--encoding=UTF8", "--no-locale"])
            # pg_ctl can launch the postmaster and then time out waiting for
            # readiness.  Such a cluster still needs teardown.
            start_attempts.append(data)
            checked([binaries.pg_ctl, "-D", data, "-l", root / f"postgres-{cluster_port}.log",
                     "-o", f"-h 127.0.0.1 -p {cluster_port}", "-w", "start"])
            checked([binaries.createdb, "-h", "127.0.0.1", "-p", str(cluster_port),
                     "-U", "carr_ci", "carr_ci"])
            checked([binaries.psql, "-h", "127.0.0.1", "-p", str(cluster_port),
                     "-U", "carr_ci", "-d", "carr_ci", "-v", "ON_ERROR_STOP=1",
                     "-c", "create role neondb_owner;"])

        checked([binaries.psql, source_dsn, "-v", "ON_ERROR_STOP=1", "-q",
                 "-f", repo / "db/schema.sql"])
        source_env = dict(clean_env)
        source_env["DATABASE_URL"] = source_dsn
        checked([python, repo / "tools/migrate.py", "--apply", "--yes"], env=source_env)
        checked([repo / "bin/schema-snapshot.sh", "--from-disposable-local", source_dsn,
                 "--output-candidate", candidate])
        if not candidate.is_file() or not candidate.stat().st_size:
            raise LocalPGRefusal("snapshot exporter produced no candidate bytes")

        checked([python, repo / "tools/test-schema-snapshot-grants.py", "--snapshot", candidate])

        # The canonical migration class loads db/schema.sql into a fresh
        # database itself, then checks its ledger and database-owned contracts.
        # Point it at the second independently initialized cluster, and make
        # its tracked-file input the candidate in this ephemeral checkout.
        shutil.copyfile(repo / "db/schema.sql", baseline_copy)
        shutil.copyfile(candidate, repo / "db/schema.sql")
        ci_env = dict(clean_env)
        ci_env["CARR_CI_DATABASE_URL"] = restore_dsn
        checked([repo / "ops/ci.sh", "--only", "migration"], env=ci_env)
        restored_roles = checked([
            binaries.psql, restore_dsn, "-X", "-Atq", "-v", "ON_ERROR_STOP=1", "-c",
            "select exists(select 1 from pg_roles where rolname='carr_ownership_issuer' "
            "and not rolcanlogin and not rolinherit and not rolbypassrls) "
            "and exists(select 1 from pg_roles where rolname='carr_ownership_issuer_g1' "
            "and rolcanlogin and not rolinherit and not rolbypassrls) "
            "and exists(select 1 from pg_roles where rolname='carr_ownership_issuer_g2' "
            "and rolcanlogin and not rolinherit and not rolbypassrls) "
            "and pg_has_role('carr_ownership_issuer_g1','carr_ownership_issuer','member') "
            "and pg_has_role('carr_ownership_issuer_g2','carr_ownership_issuer','member') "
            "and not pg_has_role('carr_writer','carr_ownership_issuer','member') "
            "and not pg_has_role('carr_jobs','carr_ownership_issuer','member') "
            "and (select count(*) from ops.canonical_ownership_issuer_generation)=2 "
            "and (select count(*) from ops.engineering_stale_contract_fence)>=2;",
        ]).stdout.strip()
        if restored_roles != "t":
            raise LocalPGRefusal("fresh restore failed issuer role, membership or seed proof")

        migration_hashes = {
            path.name: _sha256(path) for path in sorted((repo / "migrations").glob("*.sql"))
        }
        manifest = {
            "schema_version": "carr-disposable-schema-candidate.v1",
            "source_head": source_head,
            "source_tree": source_tree,
            "candidate_sha256": _sha256(candidate),
            "candidate_bytes": candidate.stat().st_size,
            "baseline_snapshot_sha256": baseline_sha256,
            "postgres_version": pg_version,
            "migration_sha256": migration_hashes,
            "restore": {"fresh_cluster": True, "port_separate": True,
                        "ledger_and_grants": "passed", "canonical_migration_class": "passed",
                        "issuer_roles_membership_seeds": "passed"},
        }
        artifact_dir.mkdir(parents=False)
        shutil.copyfile(candidate, artifact_dir / "schema.sql")
        (artifact_dir / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        print(f"schema candidate: {manifest['candidate_sha256']} from {source_head}")
        return 0
    finally:
        baseline_restore_error = None
        if baseline_copy.is_file():
            try:
                shutil.copyfile(baseline_copy, repo / "db/schema.sql")
            except OSError as exc:
                baseline_restore_error = exc
        teardown_failures = []
        for data in reversed(start_attempts):
            result = command_runner.run(
                [binaries.pg_ctl, "-D", data, "-m", "fast", "-w", "stop"],
                env=clean_env, cwd=repo, capture=True,
            )
            if result.returncode:
                status = command_runner.run(
                    [binaries.pg_ctl, "-D", data, "status"],
                    env=clean_env, cwd=repo, capture=True,
                )
                if status.returncode != 3:  # pg_ctl: 3 means no postmaster
                    teardown_failures.append(str(data))
        if not teardown_failures:
            shutil.rmtree(root)
        if baseline_restore_error is not None:
            raise LocalPGRefusal(
                f"snapshot export checkout restoration failed: {baseline_restore_error}"
            )
        if teardown_failures:
            raise LocalPGRefusal(
                f"snapshot export PostgreSQL teardown unconfirmed; retained {root}"
            )


def validate_port(value: int) -> int:
    if value < 1024 or value > 65535:
        raise LocalPGRefusal("local PostgreSQL port must be between 1024 and 65535")
    return value


def port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def hosted_execution_is_declared(source: Mapping[str, str] | None = None) -> bool:
    """True only for the one workflow that deliberately drives this lane.

    The blanket hosted refusal below kept this lane off runners from the day it
    was written, and the cost of that was not visible until it was measured: the
    acceptance programs this module runs after ci.sh had NO hosted coverage on
    any commit, ever, and the rule-delivery cutover acceptance sat red on main
    across four map re-pins while the required strict context stayed green.
    Defect fa03017b.

    The refusal is kept as the DEFAULT rather than deleted, because what it
    prevents is real: a runner that quietly initdbs clusters is a surprise, and
    nothing should reach this lane by merely happening to set GITHUB_ACTIONS.
    Opting in takes an explicit variable AND this repository AND a real run id --
    the same shape the acceptance gates already use for `hosted_disposable`
    (see ops/zz-engineering-controller-concurrency-gate.py). A fork, a local
    shell exporting GITHUB_ACTIONS by accident, and any workflow that has not
    said so in as many words all still refuse.
    """
    env = os.environ if source is None else source
    return (
        env.get("CARR_LOCAL_PG_ALLOW_HOSTED") == "1"
        and env.get("GITHUB_ACTIONS", "").lower() == "true"
        and env.get("GITHUB_REPOSITORY") == "jbookout/carr-system"
        and bool(env.get("GITHUB_RUN_ID"))
    )


def refuse_hosted_execution() -> None:
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        if hosted_execution_is_declared():
            return
        raise LocalPGRefusal("local-db-ci cannot run on a hosted GitHub runner")


def scrub_cloud_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Build the minimal nonsecret environment needed by local tools."""
    allowed = {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        # CI may explicitly forbid live Jev spend; this is a nonsecret mode.
        "CARR_JEV_OFFLINE_REPLAY",
        "LOGNAME",
        "PATH",
        "SHELL",
        "TERM",
        "TMPDIR",
        "USER",
    }
    return {key: value for key, value in source.items() if key in allowed}


def find_postgres_binaries() -> PostgresBinaries:
    override = os.environ.get("CARR_LOCAL_PG_BIN_DIR", "").strip()
    candidates: list[Path] = []
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise LocalPGRefusal("CARR_LOCAL_PG_BIN_DIR must be an absolute path")
        candidates.append(path)
    candidates.extend(
        [
            Path("/opt/homebrew/opt/postgresql@17/bin"),
            Path("/usr/local/opt/postgresql@17/bin"),
            Path("/opt/homebrew/opt/postgresql@16/bin"),
            Path("/usr/local/opt/postgresql@16/bin"),
        ]
    )
    located = shutil.which("initdb")
    if located:
        candidates.append(Path(located).resolve().parent)
    for directory in candidates:
        paths = {name: directory / name for name in ("initdb", "pg_ctl", "createdb", "psql")}
        if all(path.is_file() and os.access(path, os.X_OK) for path in paths.values()):
            return PostgresBinaries(**paths)  # type: ignore[arg-type]
    raise LocalPGRefusal(
        "PostgreSQL client/server binaries were not found; install Homebrew postgresql@17"
    )


def _failure_detail(result: CommandResult, lines: int = 12) -> str:
    """The tail of a failed command's output, not just its last line.

    This used to return `detail[-1]` alone, and that one line is routinely the
    least informative one in the whole output. initdb's final line is the
    literal string "Examine the log output." -- so a hosted failure printed
    `local-db-ci setup failed: Examine the log output.` and named no cause,
    while the cause sat two lines above in output this function had already
    been handed. Keep the tail instead: the last line is still there, with
    whatever actually failed above it.
    """
    stream = result.stderr.strip() or result.stdout.strip()
    detail = [line for line in stream.splitlines() if line.strip()]
    if not detail:
        return "command failed without output"
    tail = detail[-lines:]
    prefix = "" if len(detail) <= lines else f"(last {lines} of {len(detail)} lines) "
    return prefix + " | ".join(line.strip()[:240] for line in tail)


def run_required_local_gate(
    runner: CommandRunner,
    python: Path,
    script: Path,
    *,
    env: Mapping[str, str],
    cwd: Path,
) -> CommandResult:
    """Run one required disposable-PG gate, refusing a missing artifact."""
    if not script.is_file():
        return CommandResult(78, "", f"required local PostgreSQL gate is unavailable: {script.name}")
    return runner.run([python, script], env=env, cwd=cwd, capture=True)


def run_local_ci(
    *,
    repo: Path,
    ci_class: str,
    port: int,
    runner: CommandRunner | None = None,
    integration_base: str | None = None,
    shard: int = 0,
    report_path: Path | None = None,
    queued_at: float | None = None,
    job_started_at: float | None = None,
) -> int:
    programs = select_programs(shard)
    if shard and integration_base is not None:
        raise LocalPGRefusal("shard trial cannot replace the integration-base union proof")
    if report_path is not None and (ci_class != "migration" or integration_base is not None):
        raise LocalPGRefusal("shadow comparisons require the same migration setup without an integration-base overlay")
    if any(value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0)
           for value in (queued_at, job_started_at)):
        raise LocalPGRefusal("shadow timing must be finite positive Unix timestamps")
    if job_started_at is not None and (job_started_at > time.time() or (queued_at is not None and queued_at > job_started_at)):
        raise LocalPGRefusal("shadow queue/setup timing order is invalid")
    validate_port(port)
    refuse_hosted_execution()
    if ci_class not in {"migration", "strict"}:
        raise LocalPGRefusal("ci_class must be migration or strict")
    if not port_is_available(port):
        # NAME THE ESCAPE. This machine runs several sessions at once, so the
        # default port being taken is the ORDINARY case, not an error state —
        # it is almost always another session's disposable cluster. Until
        # 2026-08-22 the refusal stopped at "already in use", and a session that
        # did not already know about --port read it as "this lane is unavailable"
        # and hand-built its own cluster instead. One session did that seven
        # times in a night, initdb and all, while this flag sat one word away.
        raise LocalPGRefusal(
            f"127.0.0.1:{port} is already in use — almost always another session's "
            f"disposable cluster on this machine, not a problem with yours. "
            f"Re-run on a free port: ./run.sh local-db-ci --class {ci_class} --port {port + 8}")
    integration_source = None
    integration_port = port + 1
    if integration_base is not None:
        validate_port(integration_port)
        if not port_is_available(integration_port):
            raise LocalPGRefusal("integration proof needs an available adjacent port; select another --port")
        sys.path.insert(0, str(repo / "tools"))
        from integration_candidate import validate_candidate
        try:
            integration_source = validate_candidate(repo, integration_base)
        except ValueError as exc:
            raise LocalPGRefusal(str(exc)) from exc
    binaries = find_postgres_binaries()
    command_runner = runner or SubprocessRunner()
    # Keep nested consumers' Unix sockets below macOS's 104-byte limit.
    # /tmp is supported by both local macOS and the hosted Linux lane; mkdtemp
    # still creates a private, unpredictable per-run directory with mode 0700.
    root = Path(tempfile.mkdtemp(prefix="carr-local-pg-ci.", dir="/tmp"))
    data = root / "data"
    integration_data = root / "integration-data"
    integration_started = False
    clean_env = scrub_cloud_environment(os.environ)
    clean_env["LC_ALL"] = "C"
    # Every shard owns its socket and temporary files as well as TCP/data.
    # Ignore ambient TMPDIR so nested programs cannot share a caller namespace.
    (root / "tmp").mkdir(parents=True)
    (root / "socket").mkdir()
    clean_env["TMPDIR"] = str(root / "tmp")
    dsn = f"postgres://carr_ci@127.0.0.1:{port}/carr_ci"
    start_attempted = False
    exit_code = 0
    test_results = []
    started = time.time() if job_started_at is None else job_started_at
    queued = started if queued_at is None else queued_at
    setup_seconds = 0.0
    report_source = None
    report_tools = None
    if report_path is not None:
        if not report_path.is_absolute() or report_path.exists() or report_path.resolve().is_relative_to(repo.resolve()):
            shutil.rmtree(root)
            raise LocalPGRefusal("shadow report needs a new absolute path outside the repository")
        try:
            report_source, report_tools = shadow_source_binding(repo, binaries)
        except Exception:
            shutil.rmtree(root)
            raise

    def failure_detail(result: CommandResult) -> str:
        return f"subprocess exit {result.returncode}" if report_path is not None else _failure_detail(result)

    def setup(command: Sequence[str | Path]) -> bool:
        nonlocal exit_code
        result = command_runner.run(command, env=clean_env, cwd=repo, capture=True)
        if result.returncode:
            print(f"local-db-ci setup failed: {failure_detail(result)}", file=sys.stderr)
            exit_code = result.returncode
            return False
        return True

    try:
        print(f"local-db-ci: creating disposable PostgreSQL on 127.0.0.1:{port}")
        start_attempted = True
        if not setup(
            [
                binaries.initdb,
                "-D",
                data,
                "-U",
                "carr_ci",
                "--auth=trust",
                "--encoding=UTF8",
                "--no-locale",
            ]
        ):
            return exit_code
        if not setup(
            [
                binaries.pg_ctl,
                "-D",
                data,
                "-l",
                root / "postgres.log",
                "-o",
                f"-h 127.0.0.1 -p {port} -k {root / 'socket'}",
                "-w",
                "start",
            ]
        ):
            return exit_code
        if not setup(
            [binaries.createdb, "-h", "127.0.0.1", "-p", str(port), "-U", "carr_ci", "carr_ci"]
        ):
            return exit_code
        if not setup(
            [
                binaries.psql,
                "-h",
                "127.0.0.1",
                "-p",
                str(port),
                "-U",
                "carr_ci",
                "-d",
                "carr_ci",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "create role neondb_owner;",
            ]
        ):
            return exit_code
        acceptance_python = repository_python(repo)
        ownership_script = repo / "ops/canonical-ownership-lease-local-pg-gate.py"
        pre_database = "carr_ci_a2_pre"
        pre_dsn = f"postgres://carr_ci@127.0.0.1:{port}/{pre_database}"
        if not setup(
            [binaries.createdb, "-h", "127.0.0.1", "-p", str(port),
             "-U", "carr_ci", pre_database]
        ):
            return exit_code
        if not setup(
            [binaries.psql, "-h", "127.0.0.1", "-p", str(port),
             "-U", "carr_ci", "-d", pre_database, "-v", "ON_ERROR_STOP=1",
             "-q", "-f", repo / "db/schema.sql"]
        ):
            return exit_code
        pre_env = dict(clean_env)
        pre_env["DATABASE_URL"] = pre_dsn
        pre_apply = command_runner.run(
            [acceptance_python, repo / "tools/migrate.py", "--apply", "--yes",
             "--through", "0431_completion_register_schema.sql"],
            env=pre_env,
            cwd=repo,
            capture=True,
        )
        if pre_apply.returncode:
            print(
                "local-db-ci: pre-0450 baseline migration failed: "
                f"{failure_detail(pre_apply)}",
                file=sys.stderr,
            )
            exit_code = pre_apply.returncode
            return exit_code
        # The canonical ownership gate compares the current frontier against a
        # pre-0450 catalog fingerprint. Migration 0507a intentionally replaces
        # engineering_record_slice_receipt, so prepare that one reviewed seam
        # in the isolated baseline before capturing the fingerprint. The
        # companion candidate remains frontier-only: its ownership functions
        # are exactly what the unchanged comparator must continue to inspect.
        if not setup(
            [binaries.psql, "-h", "127.0.0.1", "-p", str(port),
             "-U", "carr_ci", "-d", pre_database, "-v", "ON_ERROR_STOP=1",
             "-q", "-f", repo / "ops/f03-receipt-validator.candidate.sql"]
        ):
            return exit_code
        fingerprint_env = dict(clean_env)
        fingerprint_env["CARR_LOCAL_PG_DSN"] = pre_dsn
        pre_fingerprint = command_runner.run(
            [acceptance_python, ownership_script, "--fingerprint-only"],
            env=fingerprint_env,
            cwd=repo,
            capture=True,
        )
        if pre_fingerprint.returncode:
            print(
                "local-db-ci: pre-0450 catalog fingerprint failed: "
                f"{failure_detail(pre_fingerprint)}",
                file=sys.stderr,
            )
            exit_code = pre_fingerprint.returncode
            return exit_code
        try:
            ownership_baseline = json.dumps(
                json.loads(pre_fingerprint.stdout), sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            print(
                "local-db-ci: pre-0450 catalog fingerprint was not exact JSON",
                file=sys.stderr,
            )
            exit_code = 78
            return exit_code
        if integration_base is not None:
            # Cluster-global roles require an independent cluster, not just a
            # database name. Preserve the canonical lane's fresh-role baseline.
            # Restore current main, forward the candidate and prove consumers.
            from integration_candidate import git, validate_candidate
            # Keep one active cluster: macOS has a small shared-memory ID budget.
            paused = command_runner.run(
                [binaries.pg_ctl, "-D", data, "-m", "fast", "-w", "stop"],
                env=clean_env, cwd=repo, capture=True,
            )
            if paused.returncode:
                print("local-db-ci: canonical PostgreSQL pause failed", file=sys.stderr)
                return paused.returncode
            start_attempted = False
            schema = root / "integration-main-schema.sql"
            schema.write_bytes(git(repo, "show", f"{integration_base}:db/schema.sql"))
            integration_dsn = f"postgres://carr_ci@127.0.0.1:{integration_port}/carr_ci_integration"
            integration_started = True
            integration_commands: tuple[tuple[str, list[str | Path]], ...] = (
                ("init", [binaries.initdb, "-D", integration_data, "-U", "carr_ci", "--auth=trust", "--encoding=UTF8", "--no-locale"]),
                ("start", [binaries.pg_ctl, "-D", integration_data, "-l", root / "integration-postgres.log", "-o", f"-h 127.0.0.1 -p {integration_port}", "-w", "start"]),
                ("create", [binaries.createdb, "-h", "127.0.0.1", "-p", str(integration_port), "-U", "carr_ci", "carr_ci_integration"]),
                ("role", [binaries.psql, integration_dsn, "-v", "ON_ERROR_STOP=1", "-q", "-c", "create role neondb_owner;"]),
                ("restore", [binaries.psql, integration_dsn, "-v", "ON_ERROR_STOP=1", "-q", "-f", schema]),
            )
            for stage, command in integration_commands:
                result = command_runner.run(command, env=clean_env, cwd=repo, capture=True)
                if result.returncode:
                    print(f"local-db-ci: integrated {stage} failed (exit {result.returncode})", file=sys.stderr)
                    return result.returncode
            forward_env = dict(clean_env)
            forward_env["DATABASE_URL"] = integration_dsn
            forward = command_runner.run([acceptance_python, repo / "tools/migrate.py", "--apply", "--yes"],
                                         env=forward_env, cwd=repo, capture=True)
            if forward.returncode:
                print("local-db-ci: current-main restore to candidate forward migration failed", file=sys.stderr)
                return forward.returncode
            for test_file, dsn_key in (
                ("find-rule-supersedes.test.mjs", "CARR_RULE_TEST_DATABASE_URL"),
                ("catch-me-up-writer-route.test.mjs", "CARR_WRITER_READ_TEST_DATABASE_URL"),
            ):
                consumer_env = dict(clean_env)
                consumer_env[dsn_key] = integration_dsn
                proof = command_runner.run(["node", "--test", f"mcp-server/test/{test_file}"],
                                           env=consumer_env, cwd=repo, capture=True)
                if proof.returncode:
                    print(f"local-db-ci: integrated consumer proof failed: {test_file}", file=sys.stderr)
                    return proof.returncode
            if validate_candidate(repo, integration_base) != integration_source:
                raise LocalPGRefusal("integration source changed during restore/forward/consumer proof")
            disposed = command_runner.run(
                [binaries.pg_ctl, "-D", integration_data, "-m", "fast", "-w", "stop"],
                env=clean_env, cwd=repo, capture=True,
            )
            if disposed.returncode:
                print("local-db-ci: integration PostgreSQL stop failed", file=sys.stderr)
                return disposed.returncode
            integration_started = False
            start_attempted = True
            resumed = command_runner.run(
                [binaries.pg_ctl, "-D", data, "-l", root / "postgres.log",
                 "-o", f"-h 127.0.0.1 -p {port} -k {root / 'socket'}", "-w", "start"],
                env=clean_env, cwd=repo, capture=True,
            )
            if resumed.returncode:
                print("local-db-ci: canonical PostgreSQL resume failed", file=sys.stderr)
                return resumed.returncode
            print("local-db-ci: " + json.dumps(integration_source, sort_keys=True))
        ci_env = dict(clean_env)
        ci_env["CARR_CI_DATABASE_URL"] = dsn
        ci_command: list[str | Path] = [repo / "ops/ci.sh"]
        if ci_class == "strict":
            ci_command.append("--strict")
        else:
            ci_command.extend(["--only", "migration"])
        result = command_runner.run(ci_command, env=ci_env, cwd=repo, capture=report_path is not None)
        exit_code = result.returncode
        if exit_code:
            print("local-db-ci: canonical CI failed", file=sys.stderr)
        setup_seconds = time.time() - started
        if exit_code == 0:
            acceptance_env = dict(clean_env)
            acceptance_env["PATH"] = f"{binaries.psql.parent}{os.pathsep}{clean_env.get('PATH', '')}"
            acceptance_env["CARR_LOCAL_PG_DSN"] = dsn
            acceptance_env["CARR_OWNERSHIP_PRE_0450_FINGERPRINT"] = ownership_baseline
            for program in programs:
                env = dict(acceptance_env)
                if program.kind == "f03":
                    env = dict(ci_env)
                    env["CARR_F03_PSQL"] = str(binaries.psql)
                    command = [acceptance_python, repo / program.path]
                elif program.kind == "node":
                    env = dict(ci_env)
                    env["CARR_CONTINUITY_EPHEMERAL_DATABASE_URL"] = dsn
                    env["CARR_CONTINUITY_DATABASE_DRIVER_MODULE"] = "pg"
                    command = ["node", "--test", program.path]
                elif program.kind == "snapshot":
                    command = [repo / program.path, "--from-disposable-local", dsn, "--verify-only"]
                else:
                    command = [acceptance_python, repo / program.path]
                before = time.monotonic()
                if not (repo / program.path).is_file():
                    result = CommandResult(78, "", "required acceptance program is unavailable")
                else:
                    try:
                        if program.kind == "python":
                            result = run_required_local_gate(command_runner, acceptance_python, repo / program.path,
                                                             env=env, cwd=repo)
                        else:
                            result = command_runner.run(command, env=env, cwd=repo, capture=True)
                    except Exception:
                        # Raw exception text can include DSNs, parameters or
                        # identity data. It is never part of the shadow sink.
                        result = CommandResult(78, "", "acceptance subprocess raised an exception")
                test_results.append({"id": program.id, "returncode": result.returncode,
                                     "seconds": time.monotonic() - before})
                if result.returncode:
                    if report_path is None:
                        print(f"local-db-ci: {program.id} failed: {failure_detail(result)}", file=sys.stderr)
                    else:
                        print(f"local-db-ci: {program.id} failed (exit {result.returncode}); no raw output in shadow reports", file=sys.stderr)
                    exit_code = result.returncode
                    break
                print(f"local-db-ci: {program.id} acceptance passed")
            if exit_code == 0 and integration_base is not None:
                if validate_candidate(repo, integration_base) != integration_source:
                    raise LocalPGRefusal("integration source changed during canonical candidate proof")
            if exit_code == 0:
                print(f"local-db-ci: {ci_class} proof and acceptance manifest passed (shard {shard})")
    except KeyboardInterrupt:
        exit_code = 130
    except LocalPGRefusal:
        # Existing integration callers distinguish a source refusal from a
        # child process exit. Preserve that contract after disposing resources.
        exit_code = 78
        raise
    except Exception:
        exit_code = 78
        print("local-db-ci: execution refused; disposable cleanup follows", file=sys.stderr)
    finally:
        cleanup = True
        for owned_data, attempted in ((integration_data, integration_started), (data, start_attempted)):
            if not attempted:
                continue
            try:
                stopped = command_runner.run(
                    [binaries.pg_ctl, "-D", owned_data, "-m", "fast", "-w", "stop"],
                    env=clean_env, cwd=repo, capture=True,
                )
                if stopped.returncode:
                    status = command_runner.run([binaries.pg_ctl, "-D", owned_data, "status"],
                                                env=clean_env, cwd=repo, capture=True)
                    # pg_ctl's code 3 means the server is not running. Any
                    # unknown acknowledgement retains data for recovery.
                    cleanup = cleanup and status.returncode == 3
            except Exception:
                cleanup = False
        if cleanup:
            try:
                shutil.rmtree(root)
            except OSError:
                cleanup = False
        if not cleanup:
            print(f"local-db-ci: cleanup unconfirmed; retained {root}; confirm owned postmaster status before retry", file=sys.stderr)
            exit_code = exit_code or 70
        if report_path is not None:
            try:
                # Recheck exact source after tests, before writing evidence.
                if shadow_source_binding(repo, binaries) != (report_source, report_tools):
                    exit_code = exit_code or 78
                finished = time.time()
                write_report(report_path, make_report(
                    shard=shard, source=report_source, toolchain=report_tools,
                    started=started, finished=finished, queued=queued,
                    setup_seconds=setup_seconds or finished-started, tests=test_results,
                    cleanup=cleanup, returncode=exit_code, port=port, root=root,
                ))
            except Exception:
                print("local-db-ci: shadow report refused; retain serial gate", file=sys.stderr)
                exit_code = exit_code or 78
    return exit_code


def shadow_source_binding(repo: Path, binaries: PostgresBinaries):
    """Evidence is commit-bound, never a claim about uncommitted source."""
    env = scrub_cloud_environment(os.environ)
    runner = SubprocessRunner()
    def read(command):
        result = runner.run(command, env=env, cwd=repo, capture=True)
        if result.returncode:
            raise LocalPGRefusal("shadow source/toolchain readback failed")
        return result.stdout.strip()
    head = read(["git", "rev-parse", "HEAD"])
    tree = read(["git", "rev-parse", "HEAD^{tree}"])
    if read(["git", "status", "--porcelain"]):
        raise LocalPGRefusal("shadow reports require clean committed source")
    # PGDG appends its Ubuntu package build in parentheses. Compare the
    # upstream version, rather than mistaking that suffix for the version.
    observed = re.fullmatch(r"initdb \(PostgreSQL\) (17\.\d+)(?: \([^()\n]+\))?",
                            read([binaries.initdb, "--version"]))
    if observed is None:
        raise LocalPGRefusal("shadow comparison requires PostgreSQL 17")
    return ({"head": head, "tree": tree},
            {"postgres": observed.group(1), "python": read([repository_python(repo), "--version"]).split()[-1], "node": read(["node", "--version"])})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--class",
        dest="ci_class",
        choices=("migration", "strict"),
        default="migration",
        help="migration is the fast DB lane; strict runs every canonical class locally",
    )
    parser.add_argument("--port", type=int, default=55432)
    parser.add_argument("--shard", type=int, choices=(0, 1, 2), default=0,
                        help="default serial; 1/2 are opt-in shadow acceptance partitions")
    parser.add_argument("--report", type=Path, help="new outside-repository shadow report")
    parser.add_argument("--queued-at", type=float, help="workflow run creation Unix timestamp")
    parser.add_argument("--job-started-at", type=float, help="runner setup start Unix timestamp")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--export-candidate",
        type=Path,
        metavar="ARTIFACT_DIR",
        help="manual hosted-only PG17 candidate export and independent restore",
    )
    mode.add_argument("--integration-base", help="exact current-main SHA for restore/forward/consumer union proof")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if args.export_candidate is not None and (args.shard or args.report):
        parser.error("snapshot export and shard comparison are separate modes")
    if args.shard and (args.report is None or args.ci_class != "migration"):
        parser.error("shadow shards require --class migration and --report")
    if args.report is None and (args.queued_at is not None or args.job_started_at is not None):
        parser.error("timing bindings require --report")
    def cancelled(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, cancelled)
    try:
        if args.export_candidate is not None:
            return export_snapshot_candidate(
                repo=repo, port=args.port, artifact_dir=args.export_candidate,
                runner=SubprocessRunner(),
            )
        return run_local_ci(
            repo=repo, ci_class=args.ci_class, port=args.port, runner=SubprocessRunner(), integration_base=args.integration_base,
            shard=args.shard, report_path=args.report, queued_at=args.queued_at, job_started_at=args.job_started_at
        )
    except LocalPGRefusal as exc:
        print(f"local-db-ci refused: {exc}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
