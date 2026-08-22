#!/usr/bin/env python3
"""Run canonical CI against a disposable, loopback-only local PostgreSQL 17.

This is the default database-development lane.  It creates no persistent
cluster, never accepts a caller DSN, strips cloud/provider credentials from the
child environment, and always stops and removes the temporary cluster.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence


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


def refuse_hosted_execution() -> None:
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        raise LocalPGRefusal("local-db-ci cannot run on a hosted GitHub runner")


def scrub_cloud_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Build the minimal nonsecret environment needed by local tools."""
    allowed = {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
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


def _failure_detail(result: CommandResult) -> str:
    detail = (result.stderr or result.stdout).strip().splitlines()
    return detail[-1][:240] if detail else "command failed without output"


def run_local_ci(
    *,
    repo: Path,
    ci_class: str,
    port: int,
    runner: CommandRunner | None = None,
) -> int:
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
    binaries = find_postgres_binaries()
    command_runner = runner or SubprocessRunner()
    root = Path(tempfile.mkdtemp(prefix="carr-local-pg-ci."))
    data = root / "data"
    clean_env = scrub_cloud_environment(os.environ)
    clean_env["LC_ALL"] = "C"
    dsn = f"postgres://carr_ci@127.0.0.1:{port}/carr_ci"
    start_attempted = False
    exit_code = 0

    def setup(command: Sequence[str | Path]) -> bool:
        nonlocal exit_code
        result = command_runner.run(command, env=clean_env, cwd=repo, capture=True)
        if result.returncode:
            print(f"local-db-ci setup failed: {_failure_detail(result)}", file=sys.stderr)
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
                f"-h 127.0.0.1 -p {port}",
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
        ci_env = dict(clean_env)
        ci_env["CARR_CI_DATABASE_URL"] = dsn
        ci_command: list[str | Path] = [repo / "ops/ci.sh"]
        if ci_class == "strict":
            ci_command.append("--strict")
        else:
            ci_command.extend(["--only", "migration"])
        result = command_runner.run(ci_command, env=ci_env, cwd=repo)
        exit_code = result.returncode
        if exit_code:
            print("local-db-ci: canonical CI failed", file=sys.stderr)
        else:
            repo_python = repo / ".venv/bin/python"
            acceptance_python = repo_python if repo_python.is_file() and os.access(repo_python, os.X_OK) else Path(sys.executable)
            acceptance_script = repo / "ops/atomic-rule-approval-local-pg-acceptance.py"
            if not acceptance_python.is_file() or not os.access(acceptance_python, os.X_OK):
                print("local-db-ci: repository Python environment is unavailable", file=sys.stderr)
                exit_code = 78
            elif not acceptance_script.is_file():
                print("local-db-ci: atomic rule authority acceptance is unavailable", file=sys.stderr)
                exit_code = 78
            else:
                acceptance_env = dict(clean_env)
                acceptance_env["CARR_LOCAL_PG_DSN"] = dsn
                acceptance = command_runner.run(
                    [acceptance_python, acceptance_script],
                    env=acceptance_env,
                    cwd=repo,
                    capture=True,
                )
                exit_code = acceptance.returncode
                if exit_code:
                    print(
                        f"local-db-ci: atomic rule authority acceptance failed: "
                        f"{_failure_detail(acceptance)}",
                        file=sys.stderr,
                    )
                else:
                    canary_script = repo / "ops/calendar-canary-local-pg-acceptance.py"
                    canary = command_runner.run(
                        [acceptance_python, canary_script],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if canary.returncode:
                        print(
                            f"local-db-ci: calendar canary acceptance failed: "
                            f"{_failure_detail(canary)}",
                            file=sys.stderr,
                        )
                        exit_code = canary.returncode
                    else:
                        print("local-db-ci: calendar canary isolation acceptance passed")
                if exit_code == 0:
                    nightly_canary_script = repo / "ops/nightly-canary-local-pg-acceptance.py"
                    nightly_canary = command_runner.run(
                        [acceptance_python, nightly_canary_script],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if nightly_canary.returncode:
                        print(
                            f"local-db-ci: Nightly canary acceptance failed: "
                            f"{_failure_detail(nightly_canary)}",
                            file=sys.stderr,
                        )
                        exit_code = nightly_canary.returncode
                    else:
                        print("local-db-ci: Nightly canary isolation acceptance passed")
                if exit_code == 0:
                    renewal_ingress_script = repo / "ops/renewal-signed-ingress-local-pg-acceptance.py"
                    renewal_ingress = command_runner.run(
                        [acceptance_python, renewal_ingress_script],
                        env=acceptance_env,
                        cwd=repo,
                        capture=True,
                    )
                    if renewal_ingress.returncode:
                        print(
                            f"local-db-ci: renewal signed ingress acceptance failed: "
                            f"{_failure_detail(renewal_ingress)}",
                            file=sys.stderr,
                        )
                        exit_code = renewal_ingress.returncode
                    else:
                        print("local-db-ci: renewal signed ingress least-privilege acceptance passed")
                if exit_code == 0:
                    print(
                        f"local-db-ci: {ci_class} proof and atomic Joe authority lifecycle "
                        "passed on disposable PostgreSQL"
                    )
    finally:
        if start_attempted:
            stopped = command_runner.run(
                [binaries.pg_ctl, "-D", data, "-m", "fast", "-w", "stop"],
                env=clean_env,
                cwd=repo,
                capture=True,
            )
            if stopped.returncode and exit_code == 0:
                print("local-db-ci: PostgreSQL teardown failed", file=sys.stderr)
                exit_code = stopped.returncode
        try:
            shutil.rmtree(root)
        except OSError as exc:
            print(f"local-db-ci: temporary cluster cleanup failed: {exc}", file=sys.stderr)
            if exit_code == 0:
                exit_code = 70
    return exit_code


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
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    try:
        return run_local_ci(
            repo=repo, ci_class=args.ci_class, port=args.port, runner=SubprocessRunner()
        )
    except LocalPGRefusal as exc:
        print(f"local-db-ci refused: {exc}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
