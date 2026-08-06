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
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import r2_archive as r2  # noqa: E402


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

    # Best-effort DB connection: quota_bytes() reads system_config.r2.quota_gb
    # when given one and defaults to 8 GB (under the free tier) without one.
    # backup-dump.sh already resolved a production URL for pg_dump; the caller
    # may pass it through as DATABASE_URL so the real cap is consulted instead
    # of the default.
    conn = None
    url = os.environ.get("DATABASE_URL")
    if url:
        try:
            import psycopg
            conn = psycopg.connect(url)
        except Exception as e:  # noqa: BLE001
            print(f"note: DATABASE_URL set but connect failed ({type(e).__name__}); "
                  f"quota defaults to {r2.DEFAULT_QUOTA_GB} GB", file=sys.stderr)

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
    if conn is not None:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
