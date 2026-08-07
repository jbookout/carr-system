#!/usr/bin/env python3
"""backup-archive-r2.py — archive one encrypted DB dump to R2 (ORDER 42b, 2026-08-06).

Called by bin/backup-dump.sh right after it writes backups/carr-<STAMP>.sql.age
locally. Backups used to be committed to git (A9's original design); ORDER 42
flagged that as PII exposure (full encrypted production dumps, tracked forever
in history). The dump still writes to backups/ locally — restore-rehearse.sh
and any manual `age -d` still find it there — but the directory is now
gitignored, and this script gives the dump a durable off-Mac copy the same way
every other document the factory produces gets one: lib/r2_archive.py's
quota-guarded uploader (ORDER 20). Same ledger (out/r2-usage.json), same
system_config r2.quota_gb cap, same "under-counting is the only failure that
costs money, resolve every ambiguity upward" correction rule — no second
implementation.

WHAT THIS DOES NOT DO: write an `attachment` row. That table's r2_key is
not-null-unique and scoped to document/deal subjects (see
pipelines/prepare_document.py); a backup has no deal to hang off of. This
script only reserves capacity in the shared ledger and uploads the bytes.

Key namespace: `backups/<sha256[:16]>/<filename>` — content-addressed like the
document pipeline's `documents/<client_ref>/<sha256[:16]>/<filename>`, distinct
prefix so the two lanes never collide in the bucket or in the ledger.

Usage:
  .venv/bin/python bin/backup-archive-r2.py backups/carr-20260806.sql.age
  .venv/bin/python bin/backup-archive-r2.py backups/carr-20260806.sql.age --dry-run

Exit 0 on an actual upload, an already-archived no-op, OR a quota refusal (the
refusal is reported, not fatal — the local dump in backups/ stands either way,
same posture prepare_document.py takes on its OWED path: nothing deleted to
make room, the gap is visible rather than silently swallowed). Exit 1 only on
a real upload/wrangler error, so the caller (backup-dump.sh) can tell "backup
taken, archive copy owed or failed" from "backup-dump itself is broken."
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import r2_archive as r2  # noqa: E402

# ── TIMEOUTS ADDED 2026-08-07, after this script hung for 3h20m. ──────────────
# The DATABASE_URL connection below is opened only to read one config value
# (r2.quota_gb) and reconcile the ledger. It had no connect timeout, no
# statement timeout and no TCP keepalives, so when Neon dropped the socket
# mid-run the read blocked forever on a connection the kernel still believed
# was ESTABLISHED. The process never exited, so backup-dump.sh never returned,
# so nightly.sh's `set -e` never fired, so the scheduled task reported NOTHING.
# A hang here was completely silent — an exit-code check could never catch it,
# because there was no exit code.
#
# Every wrangler call in lib/r2_archive.py already carries a subprocess timeout
# (90s bucket info, 600s upload, 120s object get). The DB connection was the
# one unbounded wait in the path, and the watchdog below is the backstop for
# anything unbounded that gets added later.
CONNECT_TIMEOUT_S = 10
STATEMENT_TIMEOUT_MS = 30_000
WATCHDOG_S = 1200          # comfortably above 90 + 600 + 120 of internal timeouts


def arm_watchdog(seconds: int = WATCHDOG_S) -> None:
    """Hard wall-clock ceiling on this process. Never silent, always an exit code."""
    def bail(_signum, _frame):
        print(f"WATCHDOG: backup-archive-r2.py exceeded {seconds}s and was aborted. "
              "The local dump stands; the R2 archive copy is OWED for this run.",
              file=sys.stderr)
        sys.stderr.flush()
        os._exit(1)
    signal.signal(signal.SIGALRM, bail)
    signal.alarm(seconds)


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    argv = sys.argv[1:]
    dry_run = "--dry-run" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        sys.exit("usage: backup-archive-r2.py <path-to-.sql.age> [--dry-run]")
    path = paths[0]
    if not os.path.exists(path):
        sys.exit(f"not found: {path}")

    arm_watchdog()

    # Best-effort DB connection: quota_bytes() reads system_config.r2.quota_gb
    # when given one and defaults to 8 GB (under the free tier) without one.
    # backup-dump.sh already resolved a production URL for pg_dump; the caller
    # may pass it through as DATABASE_URL so the real cap is consulted instead
    # of the default.
    #
    # BEST-EFFORT MEANS BOUNDED (2026-08-07). Every failure of this connection
    # is already non-fatal — the quota falls back to the 8 GB default and the
    # archive proceeds. That posture is only honest if the connection can
    # actually fail. Without a connect timeout and TCP keepalives it could not:
    # it hung instead, and a hang is worse than the error it was standing in
    # for, because nothing downstream can see it.
    conn = None
    url = os.environ.get("DATABASE_URL")
    if url:
        c = None
        try:
            import psycopg
            c = psycopg.connect(
                url,
                connect_timeout=CONNECT_TIMEOUT_S,
                keepalives=1, keepalives_idle=30,
                keepalives_interval=10, keepalives_count=3,
            )
            c.autocommit = True
            with c.cursor() as cur:
                cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
                cur.execute(f"SET idle_in_transaction_session_timeout = {STATEMENT_TIMEOUT_MS}")
            conn = c
        except Exception as e:  # noqa: BLE001
            if c is not None:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
            print(f"note: DATABASE_URL set but connect failed ({type(e).__name__}); "
                  f"quota defaults to {r2.DEFAULT_QUOTA_GB} GB", file=sys.stderr)

    try:
        cap, provenance = r2.quota_bytes(conn)
        led = r2.load_ledger()
        if conn is not None:
            r2.reconcile(led, conn)

        sha = sha256_of(path)
        size = os.path.getsize(path)
        key = f"backups/{sha[:16]}/{os.path.basename(path)}"

        try:
            res = r2.upload(path, key, sha, size, cap, provenance, led, dry_run=dry_run)
        except r2.QuotaExceeded as q:
            print(q.message, file=sys.stderr)
            print(json.dumps({"archived": False, "reason": "quota_exceeded", "key": key,
                               "bytes": size, "sha256": sha}, indent=2))
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"R2 upload error: {e}", file=sys.stderr)
            return 1

        print(json.dumps({"archived": res["uploaded"] or "already archived" in res["reason"],
                           "key": key, "bucket": res["bucket"], "reason": res["reason"],
                           "bytes": size, "sha256": sha}, indent=2))
        print(r2.usage_summary(led, cap, provenance), file=sys.stderr)
        return 0
    finally:
        # finally, not the tail of the success path: a quota refusal and an
        # upload error both returned before the old close() and leaked the
        # connection.
        signal.alarm(0)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    raise SystemExit(main())
