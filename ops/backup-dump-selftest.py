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
        '"$PG_DUMP_BIN" --no-owner --no-acl '
        '--schema=public --schema=ops "$URL"'
    )
    assert required in source, (
        "backup-dump.sh must scope pg_dump to the two schemas carr_backup is "
        "authorized to read"
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
                "BACKUP_DATABASE_URL": "postgresql://example.invalid/carr",
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
        assert args[:4] == [
            "--no-owner",
            "--no-acl",
            "--schema=public",
            "--schema=ops",
        ], args
        assert all("neon_auth" not in arg for arg in args), args
        artifacts = list(output.glob("carr-*.sql.age"))
        assert len(artifacts) == 1, artifacts
        assert artifacts[0].stat().st_size == 2 * 1024 * 1024

    print("backup-dump-selftest: scoped public+ops dump and artifact path passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
