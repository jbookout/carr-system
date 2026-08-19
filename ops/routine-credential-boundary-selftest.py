#!/usr/bin/env python3
"""Hermetic refusal checks for unattended database credential boundaries."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
from typing import Any
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "bin" / "routine-credential-env.sh"
NIGHTLY = REPO / "bin" / "nightly.sh"
RESTORE = REPO / "bin" / "restore-rehearse.sh"
RECORD = REPO / "tools" / "ops-record.py"


def check(label: str, ok: bool) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        raise AssertionError(label)


def main() -> int:
    nightly = NIGHTLY.read_text(encoding="utf-8")
    restore = RESTORE.read_text(encoding="utf-8")
    check("nightly never sources db.env", '. "$HOME/.config/carr/db.env"' not in nightly)
    check("nightly uses a clean child environment", "carr_routine_exec \"$@\"" in nightly)
    check("admin nightly steps refuse through evidence-producing step calls",
          nightly.count("routine-admin-refusal.sh") == 5)
    check("routine nightly contains no db-tap escalation", "CARR_BREAK_GLASS=1" not in nightly)
    check("portability mirror uses only the backup capability",
          'step "portability mirror' in nightly and 'DATABASE_URL="${CARR_DB_BACKUP_URL:-}"' in nightly)
    check("each backup invocation binds the backup capability explicitly",
          nightly.count('env CARR_DB_BACKUP_URL="${CARR_DB_BACKUP_URL:-}" ./bin/backup-dump.sh') == 3)
    check("clean child preserves explicit export mode", 'CARR_EXPORT_LIVE="${CARR_EXPORT_LIVE:-}"' in HELPER.read_text(encoding="utf-8"))
    check("scheduled recovery refuses unprovisioned admin capability",
          "CARR_JOB_PAYLOAD" in restore and "routine dispatch refused" in restore)

    with tempfile.TemporaryDirectory() as raw:
        env_file = Path(raw) / "db.env"
        env_file.write_text("CARR_DB_JOBS_URL='postgresql://carr_jobs:pw@db/jobs?sslmode=require&channel_binding=require'\n"  # ci-secret-scan: allow
                            "DATABASE_URL=$(touch should-never-run)\n", encoding="utf-8")
        env_file.chmod(0o600)
        program = f'source "{HELPER}"; carr_clear_routine_db_env; carr_load_routine_db_env CARR_DB_JOBS_URL || exit $?; print -r -- "$CARR_DB_JOBS_URL|${{DATABASE_URL:-absent}}|${{BACKUP_DATABASE_URL:-absent}}|${{CARR_IMPORT_DB_URL:-absent}}|${{PGPASSWORD:-absent}}"'
        run = subprocess.run(["/bin/zsh", "-c", program], text=True, capture_output=True,
                             env={**os.environ, "CARR_ROUTINE_DB_ENV_FILE": str(env_file),
                                  "DATABASE_URL": "postgresql://neondb_owner:ambient@db/main",  # ci-secret-scan: allow
                                  "BACKUP_DATABASE_URL": "owner", "CARR_IMPORT_DB_URL": "writer",
                                  "PGPASSWORD": "owner-password"})
        check("quoted URI with literal query ampersand is accepted without evaluation",
              run.returncode == 0 and "channel_binding=require|absent|absent|absent|absent" in run.stdout)
        env_file.write_text("CARR_DB_JOBS_URL=x\nCARR_DB_JOBS_URL=y\n", encoding="utf-8")
        duplicate = subprocess.run(["/bin/zsh", "-c", program], text=True, capture_output=True,
                                   env={**os.environ, "CARR_ROUTINE_DB_ENV_FILE": str(env_file)})
        check("duplicate allowed credential is refused", duplicate.returncode == 78)
        env_file.write_text("CARR_DB_JOBS_URL=x\n", encoding="utf-8")
        env_file.chmod(0o644)
        insecure = subprocess.run(["/bin/zsh", "-c", program], text=True, capture_output=True,
                                  env={**os.environ, "CARR_ROUTINE_DB_ENV_FILE": str(env_file)})
        check("group-readable credential file is refused", insecure.returncode == 78)
        env_file.chmod(0o600)
        env_file.write_text("not-a-key-value-line\n", encoding="utf-8")
        malformed = subprocess.run(["/bin/zsh", "-c", program], text=True, capture_output=True,
                                   env={**os.environ, "CARR_ROUTINE_DB_ENV_FILE": str(env_file)})
        check("malformed credential entry is refused", malformed.returncode == 78)

    child = subprocess.run(
        ["/bin/zsh", "-c", f'source "{HELPER}"; carr_routine_exec /usr/bin/env'],
        text=True, capture_output=True,
        env={**os.environ, "HC_EXPORTS_RC": "1", "HC_BACKUP_RC": "2", "HC_CHAIN_RC": "3",
             "DATABASE_URL": "owner", "BACKUP_DATABASE_URL": "owner", "PGPASSWORD": "owner"},
    )
    child_env = child.stdout
    check("clean child preserves exact dead-man outcomes",
          child.returncode == 0 and "HC_EXPORTS_RC=1" in child_env
          and "HC_BACKUP_RC=2" in child_env and "HC_CHAIN_RC=3" in child_env)
    check("clean child excludes ambient owner credentials",
          "DATABASE_URL=owner" not in child_env and "BACKUP_DATABASE_URL=owner" not in child_env
          and "PGPASSWORD=owner" not in child_env)
    backup_child = subprocess.run(
        ["/bin/zsh", "-c", f'source "{HELPER}"; carr_routine_exec env CARR_DB_BACKUP_URL="$CARR_DB_BACKUP_URL" /usr/bin/env'],
        text=True, capture_output=True,
        env={**os.environ, "CARR_DB_BACKUP_URL": "postgresql://carr_backup:pw@db/backup"},  # ci-secret-scan: allow
    )
    generic_backup = subprocess.run(
        ["/bin/zsh", "-c", f'source "{HELPER}"; carr_routine_exec /usr/bin/env'],
        text=True, capture_output=True,
        env={**os.environ, "CARR_DB_BACKUP_URL": "postgresql://carr_backup:pw@db/backup"},  # ci-secret-scan: allow
    )
    check("backup URL reaches only its explicitly bound child",
          "CARR_DB_BACKUP_URL=postgresql://carr_backup:pw@db/backup" in backup_child.stdout  # ci-secret-scan: allow
          and "CARR_DB_BACKUP_URL=" not in generic_backup.stdout)

    spec = importlib.util.spec_from_file_location("ops_record", RECORD)
    assert spec and spec.loader
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._load_db_env = lambda: None
    old = os.environ.copy()
    try:
        os.environ["DATABASE_URL"] = "postgresql://neondb_owner:pw@db/main"  # ci-secret-scan: allow
        os.environ["CARR_DB_JOBS_URL"] = "postgresql://carr_jobs:pw@db/jobs"  # ci-secret-scan: allow
        check("unattended ledger write chooses jobs URL over ambient owner", module.dsn("routine") == os.environ["CARR_DB_JOBS_URL"])
        check("explicit release/deployment write preserves deliberate DATABASE_URL",
              module.dsn("write") == os.environ["DATABASE_URL"])
        os.environ["CARR_DB_JOBS_URL"] = "postgresql://neondb_owner:pw@db/main"  # ci-secret-scan: allow
        try:
            module.dsn("routine")
        except SystemExit:
            mislabeled_refused = True
        else:
            mislabeled_refused = False
        check("mislabelled owner URL is refused before connect", mislabeled_refused)
        os.environ.pop("CARR_DB_JOBS_URL")
        try:
            module.dsn("routine")
        except SystemExit:
            refused = True
        else:
            refused = False
        check("ops ledger write refuses owner-only ambient environment", refused)
    finally:
        os.environ.clear(); os.environ.update(old)
    print("routine-credential-boundary-selftest: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
