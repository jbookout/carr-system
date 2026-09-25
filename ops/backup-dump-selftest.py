#!/usr/bin/env python3
"""Hermetic black-box tests for the guarded backup wrapper.

The live PostgreSQL/age round trip and RLS/concurrency cases belong to
backup-guard-selftest.py. This suite copies the real wrapper and helper into a
private fixture repository, then replaces only the database-backed Guard with
a test guard. The production encrypted_dump implementation still owns the
pg_dump/age pipeline, SQL observation, size floors and atomic promotion.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "backup-dump.sh"
GUARD = REPO / "bin" / "backup-guard.py"
SECRET = "fixture-secret"
DSN = (
    f"postgresql://carr_backup:{SECRET}@127.0.0.1:5432/carr"  # ci-secret-scan: allow — synthetic loopback fixture DSN
    "?sslmode=disable&connect_timeout=7&keepalives=1&keepalives_idle=60"
    "&keepalives_interval=15&keepalives_count=12"
)


def executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload(size: int) -> bytes:
    assert size >= 4
    return b"--" + (b"x" * (size - 3)) + b"\n"


def build_fixture_repo(root: Path) -> tuple[Path, Path]:
    fixture = root / "fixture-repo"
    fixture_bin = fixture / "bin"
    fixture_bin.mkdir(parents=True)
    copied_wrapper = fixture_bin / "backup-dump.sh"
    copied_helper = fixture_bin / "backup-guard-real.py"
    shutil.copyfile(SCRIPT, copied_wrapper)
    shutil.copyfile(GUARD, copied_helper)
    copied_wrapper.chmod(copied_wrapper.stat().st_mode | stat.S_IXUSR)
    copied_helper.chmod(copied_helper.stat().st_mode | stat.S_IXUSR)
    assert copied_wrapper.read_bytes() == SCRIPT.read_bytes()
    assert copied_helper.read_bytes() == GUARD.read_bytes()

    # Preserve the active virtual environment, including its psycopg install.
    # Resolving sys.executable would point at the base interpreter and silently
    # discard the environment's site-packages.
    environment = Path(sys.prefix)
    assert (environment / "bin" / "python").exists(), environment
    (fixture / ".venv").symlink_to(environment, target_is_directory=True)
    (fixture / "backups-public-key.txt").write_text(
        "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
        encoding="utf-8",
    )

    # Replace only the database session. The copied helper's encrypted_dump
    # still constructs both subprocesses and decides whether to promote.
    executable(
        fixture_bin / "backup-guard.py",
        """#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

real_path = Path(__file__).with_name("backup-guard-real.py")
spec = importlib.util.spec_from_file_location("fixture_real_backup_guard", real_path)
real = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(real)

class FakeGuard:
    def __init__(self):
        self.dsn = os.environ["CARR_DB_BACKUP_URL"]
        self.snapshot = "00000003-00000001-1"
        self.tables = []
        self.acks = 0

    def ack(self):
        self.acks += 1
        fail_after = int(os.environ.get("CARR_TEST_ACK_FAIL_AFTER", "0"))
        children_started = (
            Path(os.environ["CARR_TEST_DUMP_STARTED"]).exists()
            and Path(os.environ["CARR_TEST_AGE_STARTED"]).exists()
        )
        if fail_after and self.acks >= fail_after and children_started:
            raise real.BackupError("synthetic guard deadline")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--pg-dump", required=True)
    args = parser.parse_args()
    call_log = os.environ.get("CARR_TEST_HELPER_CALL")
    if call_log:
        Path(call_log).write_text(json.dumps({"argv": sys.argv[1:]}), encoding="utf-8")
    try:
        result = real.encrypted_dump(
            FakeGuard(), args.output, args.recipient, args.pg_dump,
            int(os.environ.get("BACKUP_LOCK_TIMEOUT_MS", "10000")),
            float(os.environ.get("BACKUP_POLL_SECONDS", "0.01")),
        )
        print(json.dumps(result))
        return 0
    except (real.BackupError, OSError, ValueError) as exc:
        print(f"fixture-guard: {exc}", file=sys.stderr)
        return 1

raise SystemExit(main())
""",
    )
    return fixture, copied_wrapper


def build_fake_tools(root: Path) -> Path:
    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    executable(
        fake_bin / "pg_dump",
        """#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

Path(os.environ["CARR_TEST_DUMP_STARTED"]).write_text("started", encoding="utf-8")
record = {
    "argv": sys.argv[1:],
    "env": {key: value for key, value in os.environ.items()
            if key.startswith("PG") or key == "CARR_DB_BACKUP_URL"},
}
Path(os.environ["CARR_TEST_DUMP_CALL"]).write_text(json.dumps(record), encoding="utf-8")
mode = os.environ.get("CARR_TEST_DUMP_MODE", "ok")
if mode == "hang":
    time.sleep(60)
    raise SystemExit(99)
size = int(os.environ.get("CARR_TEST_DUMP_BYTES", str(2 * 1024 * 1024)))
sys.stdout.buffer.write(b"--" + b"x" * (size - 3) + b"\\n")
sys.stdout.buffer.flush()
if mode == "fail":
    raise SystemExit(23)
""",
    )
    executable(
        fake_bin / "age",
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

Path(os.environ["CARR_TEST_AGE_STARTED"]).write_text("started", encoding="utf-8")
data = sys.stdin.buffer.read()
if os.environ.get("CARR_TEST_AGE_MODE") == "fail":
    raise SystemExit(24)
sys.stdout.buffer.write(data)
""",
    )
    return fake_bin


def case_env(
    fake_bin: Path,
    case: Path,
    output: Path,
    *,
    dump_mode: str = "ok",
    age_mode: str = "ok",
    dump_bytes: int = 2 * 1024 * 1024,
    ack_fail_after: int = 0,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "CARR_DB_BACKUP_URL": DSN,  # ci-secret-scan: allow
            "BACKUP_SKIP_R2": "1",
            "BACKUP_OUTPUT_DIR": str(output),
            "BACKUP_POLL_SECONDS": "0.01",
            "PG_DUMP_BIN": str(fake_bin / "pg_dump"),
            "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/local/bin",
            "CARR_TEST_DUMP_MODE": dump_mode,
            "CARR_TEST_AGE_MODE": age_mode,
            "CARR_TEST_DUMP_BYTES": str(dump_bytes),
            "CARR_TEST_ACK_FAIL_AFTER": str(ack_fail_after),
            "CARR_TEST_DUMP_STARTED": str(case / "dump.started"),
            "CARR_TEST_AGE_STARTED": str(case / "age.started"),
            "CARR_TEST_DUMP_CALL": str(case / "dump-call.json"),
            "CARR_TEST_HELPER_CALL": str(case / "helper-call.json"),
        }
    )
    return env


def run_wrapper(wrapper: Path, fixture: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/zsh", str(wrapper)],
        cwd=fixture,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def seed_previous(output: Path, size: int) -> tuple[Path, str]:
    output.mkdir(parents=True, exist_ok=True)
    prior = output / "carr-20000101.sql.age"
    prior.write_bytes(b"P" * size)
    return prior, sha256(prior)


def assert_private_tempfiles_removed(output: Path) -> None:
    leftovers = [path for path in output.iterdir()
                 if path.name.startswith(".") and path.name.endswith(".tmp")]
    assert not leftovers, leftovers


def refusal_case(
    *,
    root: Path,
    fixture: Path,
    wrapper: Path,
    fake_bin: Path,
    label: str,
    dump_mode: str = "ok",
    age_mode: str = "ok",
    dump_bytes: int = 2 * 1024 * 1024,
    previous_size: int = 4 * 1024 * 1024,
    ack_fail_after: int = 0,
) -> subprocess.CompletedProcess[str]:
    case = root / label
    output = case / "out"
    case.mkdir()
    prior, prior_digest = seed_previous(output, previous_size)
    env = case_env(
        fake_bin, case, output,
        dump_mode=dump_mode,
        age_mode=age_mode,
        dump_bytes=dump_bytes,
        ack_fail_after=ack_fail_after,
    )
    run = run_wrapper(wrapper, fixture, env)
    assert (case / "dump.started").exists(), f"{label}: pg_dump was not reached"
    assert (case / "age.started").exists(), f"{label}: age was not reached"
    assert run.returncode != 0, f"{label} was reported successful\n{run.stdout}{run.stderr}"
    assert sha256(prior) == prior_digest, f"{label} changed the prior artifact"
    current = output / f"carr-{datetime.now(timezone.utc):%Y%m%d}.sql.age"
    assert not current.exists(), f"{label} promoted {current}"
    assert_private_tempfiles_removed(output)
    return run


def load_guard_module(guard_path: Path):
    spec = importlib.util.spec_from_file_location("carr_backup_guard_direct", guard_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def case_stop_child_reap_timeout_still_cleans_up(root: Path, guard_path: Path = GUARD) -> None:
    """Regression test for the encrypted_dump() finally-block cleanup.

    stop_child() kills a still-running dump/age child with SIGTERM, then
    SIGKILL if that doesn't reap it in time. Reaping a just-SIGKILL'd child
    can itself time out under real system load — this is exactly what
    produced the original flake: an unguarded second `child.wait(timeout=2)`
    call let subprocess.TimeoutExpired escape stop_child(), which escaped
    encrypted_dump()'s finally block BEFORE temporary.unlink() ran, leaving
    a private ciphertext temp file behind and masking the guard's real
    BackupError.

    This mocks subprocess.Popen.wait(timeout=...) to always raise
    TimeoutExpired (simulating that slow-reap condition deterministically,
    regardless of real host load) and os.killpg to a no-op (so this proves
    only the exception-handling control flow, not real signal delivery).
    Against the pre-fix code this fails: either the private temp file
    survives, or the original 'synthetic guard deadline' error is replaced
    by an uncaught TimeoutExpired. Against the fixed code both symptoms are
    gone.
    """
    real = load_guard_module(guard_path)

    case = root / "stop-child-reap-timeout"
    out = case / "out"
    case.mkdir()
    out.mkdir()
    fake_bin = case / "fake-bin"
    fake_bin.mkdir()

    dump_started = case / "dump.started"
    age_started = case / "age.started"

    # A pg_dump that outlives the guard's refusal, so stop_child() still
    # finds it alive (poll() is None) when the finally block runs.
    executable(fake_bin / "pg_dump", """#!/usr/bin/env python3
import os
import time
from pathlib import Path
Path(os.environ["CARR_TEST_DUMP_STARTED"]).write_text("started", encoding="utf-8")
time.sleep(2)
""")
    executable(fake_bin / "age", """#!/usr/bin/env python3
import os
import sys
from pathlib import Path
Path(os.environ["CARR_TEST_AGE_STARTED"]).write_text("started", encoding="utf-8")
sys.stdin.buffer.read()
""")

    class FakeGuard:
        def __init__(self) -> None:
            self.dsn = DSN
            self.snapshot = "00000003-00000001-1"
            self.tables: list[dict] = []
            self.acks = 0

        def ack(self) -> None:
            self.acks += 1
            if dump_started.exists() and age_started.exists():
                raise real.BackupError("synthetic guard deadline")

    output = out / "carr-test.sql.age"
    recipient = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"

    env_patch = {
        "CARR_TEST_DUMP_STARTED": str(dump_started),
        "CARR_TEST_AGE_STARTED": str(age_started),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
    }

    original_wait = subprocess.Popen.wait

    def wait_times_out_when_given_a_timeout(self, timeout=None):
        if timeout is not None:
            raise subprocess.TimeoutExpired(cmd=getattr(self, "args", "?"), timeout=timeout)
        return original_wait(self, timeout=timeout)

    raised: BaseException | None = None
    with mock.patch.dict(os.environ, env_patch), \
         mock.patch.object(subprocess.Popen, "wait", wait_times_out_when_given_a_timeout), \
         mock.patch("os.killpg"):
        try:
            real.encrypted_dump(FakeGuard(), output, recipient, str(fake_bin / "pg_dump"), 10000, 0.01)
        except BaseException as exc:  # capture ANY exception, including a masking one
            raised = exc

    assert raised is not None, "stop-child-reap-timeout: encrypted_dump unexpectedly succeeded"
    assert isinstance(raised, real.BackupError), (
        f"stop-child-reap-timeout: original BackupError was masked by cleanup: {raised!r}"
    )
    assert "synthetic guard deadline" in str(raised), (
        f"stop-child-reap-timeout: unexpected error content: {raised!r}"
    )

    leftovers = [p for p in out.iterdir() if p.name.startswith(".") and p.name.endswith(".tmp")]
    assert not leftovers, f"stop-child-reap-timeout: private temp file leaked: {leftovers}"


def case_finally_survives_stop_child_raising(root: Path, guard_path: Path = GUARD) -> None:
    """Regression test for encrypted_dump()'s cleanup structure itself.

    stop_child() is now hardened to never raise (see
    case_stop_child_reap_timeout_still_cleans_up), but encrypted_dump()'s
    finally block was ALSO changed to give each cleanup step its own
    try/finally, specifically so a future/unknown failure in one step can
    never skip the ones after it. That structure is only exercised when a
    cleanup step actually raises — which the fixed stop_child() no longer
    does — so this test forces the issue directly: it monkeypatches
    real.stop_child itself to raise an arbitrary exception, and checks that
    temporary.unlink() still runs and the guard's original BackupError is
    still what propagates (not the injected stop_child failure). Against a
    version of encrypted_dump() with a single flat
    `stop_child(dump); stop_child(age); pump.join(...); temporary.unlink()`
    finally (no nested try/finally), this fails — the injected exception
    replaces the original error and skips the unlink. Against the fixed
    nested structure it passes.
    """
    real = load_guard_module(guard_path)

    case = root / "finally-survives-stop-child-raising"
    out = case / "out"
    case.mkdir()
    out.mkdir()
    fake_bin = case / "fake-bin"
    fake_bin.mkdir()

    dump_started = case / "dump.started"
    age_started = case / "age.started"

    executable(fake_bin / "pg_dump", """#!/usr/bin/env python3
import os
import time
from pathlib import Path
Path(os.environ["CARR_TEST_DUMP_STARTED"]).write_text("started", encoding="utf-8")
time.sleep(2)
""")
    executable(fake_bin / "age", """#!/usr/bin/env python3
import os
import sys
from pathlib import Path
Path(os.environ["CARR_TEST_AGE_STARTED"]).write_text("started", encoding="utf-8")
sys.stdin.buffer.read()
""")

    class FakeGuard:
        def __init__(self) -> None:
            self.dsn = DSN
            self.snapshot = "00000003-00000001-1"
            self.tables: list[dict] = []
            self.acks = 0

        def ack(self) -> None:
            self.acks += 1
            if dump_started.exists() and age_started.exists():
                raise real.BackupError("synthetic guard deadline")

    output = out / "carr-test.sql.age"
    recipient = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"

    env_patch = {
        "CARR_TEST_DUMP_STARTED": str(dump_started),
        "CARR_TEST_AGE_STARTED": str(age_started),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
    }

    def stop_child_always_raises(child):
        raise RuntimeError("injected: pretend stop_child misbehaves")

    raised: BaseException | None = None
    with mock.patch.dict(os.environ, env_patch), \
         mock.patch.object(real, "stop_child", stop_child_always_raises):
        try:
            real.encrypted_dump(FakeGuard(), output, recipient, str(fake_bin / "pg_dump"), 10000, 0.01)
        except BaseException as exc:  # capture ANY exception, including a masking one
            raised = exc

    assert raised is not None, "finally-survives-stop-child-raising: encrypted_dump unexpectedly succeeded"
    assert isinstance(raised, real.BackupError), (
        f"finally-survives-stop-child-raising: original BackupError was masked "
        f"by the injected stop_child failure: {raised!r}"
    )
    assert "synthetic guard deadline" in str(raised), (
        f"finally-survives-stop-child-raising: unexpected error content: {raised!r}"
    )

    leftovers = [p for p in out.iterdir() if p.name.startswith(".") and p.name.endswith(".tmp")]
    assert not leftovers, f"finally-survives-stop-child-raising: private temp file leaked: {leftovers}"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="carr-backup-wrapper-selftest-") as raw:
        root = Path(raw)
        fixture, wrapper = build_fixture_repo(root)
        fake_bin = build_fake_tools(root)

        positive = root / "positive"
        positive.mkdir()
        output = positive / "out"
        env = case_env(fake_bin, positive, output)
        run = run_wrapper(wrapper, fixture, env)
        assert run.returncode == 0, run.stdout + run.stderr
        assert (positive / "dump.started").exists()
        assert (positive / "age.started").exists()
        artifacts = list(output.glob("carr-*.sql.age"))
        assert len(artifacts) == 1, artifacts
        assert artifacts[0].read_bytes() == payload(2 * 1024 * 1024), "age passthrough changed bytes"
        assert "R2 archive: skipped" in run.stdout, run.stdout
        assert_private_tempfiles_removed(output)

        helper_call = json.loads((positive / "helper-call.json").read_text(encoding="utf-8"))
        helper_argv = helper_call["argv"]
        assert helper_argv == [
            "--output", str(artifacts[0]),
            "--recipient", (fixture / "backups-public-key.txt").read_text(encoding="utf-8"),
            "--pg-dump", str(fake_bin / "pg_dump"),
        ], helper_argv
        assert all(SECRET not in arg for arg in helper_argv), helper_argv

        dump_call = json.loads((positive / "dump-call.json").read_text(encoding="utf-8"))
        dump_argv = dump_call["argv"]
        assert dump_argv[:8] == [
            "--no-owner", "--no-acl", "--enable-row-security",
            "--schema=public", "--schema=ops", "--format=plain", "--verbose",
            ("--dbname=keepalives=1 keepalives_idle=60 "
             "keepalives_interval=15 keepalives_count=12"),
        ], dump_argv
        assert dump_argv[8:] == [
            "--snapshot=00000003-00000001-1", "--lock-wait-timeout=10000ms"
        ], dump_argv
        assert all(SECRET not in arg and "carr_backup" not in arg for arg in dump_argv), dump_argv
        pg_env = dump_call["env"]
        assert pg_env["PGPASSWORD"] == SECRET
        assert pg_env["PGUSER"] == "carr_backup"
        assert pg_env["PGHOST"] == "127.0.0.1"
        assert pg_env["PGPORT"] == "5432"
        assert pg_env["PGDATABASE"] == "carr"
        assert pg_env["PGSSLMODE"] == "disable"
        assert pg_env["PGCONNECT_TIMEOUT"] == "7"

        refusal_case(
            root=root, fixture=fixture, wrapper=wrapper, fake_bin=fake_bin,
            label="pg-dump-fails-after-output", dump_mode="fail",
        )
        refusal_case(
            root=root, fixture=fixture, wrapper=wrapper, fake_bin=fake_bin,
            label="age-fails-after-consuming-output", age_mode="fail",
        )
        refusal_case(
            root=root, fixture=fixture, wrapper=wrapper, fake_bin=fake_bin,
            label="absolute-size-floor", dump_bytes=512 * 1024, previous_size=1024,
        )
        refusal_case(
            root=root, fixture=fixture, wrapper=wrapper, fake_bin=fake_bin,
            label="relative-size-floor", dump_bytes=1280 * 1024,
            previous_size=4 * 1024 * 1024,
        )
        hung = refusal_case(
            root=root, fixture=fixture, wrapper=wrapper, fake_bin=fake_bin,
            label="hanging-pg-dump", dump_mode="hang", ack_fail_after=1,
        )
        assert "synthetic guard deadline" in hung.stderr, hung.stderr

        case_stop_child_reap_timeout_still_cleans_up(root)
        case_finally_survives_stop_child_raising(root)

    print(
        "backup-dump-selftest: real helper pipeline argv/env, passthrough, failures, "
        "deadline, floors, survivor and reap-timeout cleanup passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
