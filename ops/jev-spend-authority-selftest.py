#!/usr/bin/env python3
"""Real SQL/concurrency acceptance on a disposable Unix-socket PostgreSQL."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("jev_local_pg", ROOT / "ops/local-pg-ci.py")
assert spec and spec.loader
pg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pg
spec.loader.exec_module(pg)


def main():
    try:
        binaries = pg.find_postgres_binaries()
    except pg.LocalPGRefusal as error:
        print(f"SKIP disposable Jev PostgreSQL: {error}")
        return 78
    with tempfile.TemporaryDirectory(prefix="jev-spend-pg-") as tmp:
        root = Path(tmp)
        data = root / "data"
        # Never inherit a provider DSN or a configured database connection.
        # A pinned locale: macOS postmaster refuses to start without one.
        env = pg.scrub_cloud_environment(os.environ)
        env.update(CARR_JEV_OFFLINE="1", JEV_TEST_PG_SOCKET=tmp, LC_ALL="C")
        env["PATH"] = f"{binaries.initdb.parent}{os.pathsep}{env.get('PATH', '')}"
        subprocess.run([str(binaries.initdb), "-D", str(data), "-U", "carr_ci",
                        "--auth=trust", "--encoding=UTF8", "--no-locale"],
                       env=env, check=True, timeout=30)
        try:
            subprocess.run([str(binaries.pg_ctl), "-D", str(data), "-l", str(root / "pg.log"),
                            "-o", f"-c listen_addresses='' -k {tmp}", "-w", "start"],
                           env=env, capture_output=True, check=True, timeout=30)
            return subprocess.run(["node", "--test", "mcp-server/test/jev-spend-postgres.mjs"],
                                  cwd=ROOT, env=env, timeout=60).returncode
        finally:
            subprocess.run([str(binaries.pg_ctl), "-D", str(data), "-m", "immediate", "-w", "stop"],
                           env=env, capture_output=True, timeout=15)


if __name__ == "__main__":
    raise SystemExit(main())
