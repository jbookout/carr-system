#!/usr/bin/env python3
"""Regression test for the least-privilege cloud backup path.

The carr_backup role is deliberately readable only in the CARR-owned public
and ops schemas.  An unscoped pg_dump also touches Neon-managed schemas such
as neon_auth and turns that least-privilege boundary into a nightly failure.

This test executes the real backup script with hermetic pg_dump/age stand-ins.
It proves the command stays schema-scoped and that the normal size-floor and
promotion path still produce the encrypted-named artifact.
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "backup-dump.sh"


def executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def main() -> int:
    source = SCRIPT.read_text(encoding="utf-8")
    required = (
        '"$PG_DUMP_BIN" --no-owner --no-acl --enable-row-security '
        '--schema=public --schema=ops "$URL"'
    )
    assert required in source, (
        "backup-dump.sh must scope pg_dump to the two schemas carr_backup is "
        "authorized to read, and read under row_security=on (--enable-row-security) "
        "so ops.work_request's RLS does not fail the dump (WR-000044)"
    )

    with tempfile.TemporaryDirectory(prefix="carr-backup-selftest-") as raw:
        root = Path(raw)
        fake_bin = root / "bin"
        output = root / "out"
        args_file = root / "pg-dump-args.txt"
        fake_bin.mkdir()

        fake_pg_dump = fake_bin / "pg_dump"
        executable(
            fake_pg_dump,
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$@" > "$CARR_TEST_DUMP_ARGS"\n'
            "dd if=/dev/zero bs=1048576 count=2 2>/dev/null\n",
        )
        executable(fake_bin / "age", "#!/bin/sh\ncat\n")

        env = os.environ.copy()
        env.update(
            {
                "CARR_DB_BACKUP_URL": "postgresql://carr_backup:fixture@example.invalid/carr",  # ci-secret-scan: allow
                "BACKUP_SKIP_R2": "1",
                "BACKUP_OUTPUT_DIR": str(output),
                "CARR_TEST_DUMP_ARGS": str(args_file),
                "PG_DUMP_BIN": str(fake_pg_dump),
                "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/local/bin",
            }
        )
        run = subprocess.run(
            ["/bin/zsh", str(SCRIPT)],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert run.returncode == 0, run.stdout + run.stderr

        args = shlex.split(args_file.read_text(encoding="utf-8"))
        assert args[:5] == [
            "--no-owner",
            "--no-acl",
            "--enable-row-security",
            "--schema=public",
            "--schema=ops",
        ], args
        assert all("neon_auth" not in arg for arg in args), args

        # KEEPALIVES, added 2026-08-16 after a five-and-a-half-hour outage.
        # Neon dropped the connection and pg_dump never noticed: it held a
        # half-open socket, wrote zero bytes, and kept the nightly chain's lock
        # the whole time, so every later run skipped with exit 0. There was no
        # backend left to terminate server-side (pg_stat_activity was empty),
        # which makes a client-side timeout the only thing that can end it.
        # These params make libpq probe a silent peer and fail in minutes.
        url = args[-1]
        for param in ("keepalives=1", "keepalives_idle=", "keepalives_interval=",
                      "keepalives_count="):
            assert param in url, (
                f"pg_dump connection string must carry {param} so a dropped "
                f"connection fails instead of hanging forever — got {url}"
            )
        assert "connect_timeout=" in url, (
            f"pg_dump connection string must carry connect_timeout — got {url}"
        )
        # The original query params must survive being appended to.
        assert url.startswith("postgresql://carr_backup:fixture@example.invalid/carr"), url  # ci-secret-scan: allow
        artifacts = list(output.glob("carr-*.sql.age"))
        assert len(artifacts) == 1, artifacts
        assert artifacts[0].stat().st_size == 2 * 1024 * 1024

        owner = subprocess.run(
            ["/bin/zsh", str(SCRIPT)], cwd=REPO,
            env={**env, "CARR_DB_BACKUP_URL": "postgresql://neondb_owner:fixture@example.invalid/carr"},  # ci-secret-scan: allow
            text=True, capture_output=True, timeout=30, check=False,
        )
        assert owner.returncode == 78 and "carr_backup" in owner.stderr, owner.stderr

    print("backup-dump-selftest: scoped public+ops dump, keepalives and artifact path passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
