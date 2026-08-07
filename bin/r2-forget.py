#!/usr/bin/env python3
"""r2-forget.py — remove one or more objects from the R2 archive and the ledger.

ADDED 2026-08-07, for a failure that had no remedy. That night pg_dump died
mid-dump, a weak guard in backup-dump.sh promoted the resulting 200-byte file to
that day's official backup, and bin/backup-archive-r2.py dutifully uploaded it.
The archive then held TWO objects both claiming to be the 2026-08-07 backup, one
of them empty, and nothing in the system could remove either — lib/r2_archive.py
could only ever add. The ledger's own note says it is never hand-edited, so the
only available options were both bad.

Selection is by KEY PREFIX, never by a glob and never by "the newest N". A
prefix is exact, it is printable, and it makes the blast radius visible in the
command itself. Backup keys are content-addressed
(`backups/<sha256[:16]>/<filename>`), so a single corrupt upload is named
precisely by its hash prefix without touching the good object beside it.

NOTHING IS DELETED WITHOUT --yes. Run it once to see the matches, once more to
act. The listing is the confirmation step.

  bin/r2-forget.py --prefix backups/2b07aad1c99563e2/          # list only
  bin/r2-forget.py --prefix backups/2b07aad1c99563e2/ --yes    # delete

Exit 0 on success or on a listing run, 1 on a delete error, 2 on bad usage.
"""
from __future__ import annotations

import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import r2_archive as r2  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Remove objects from the R2 archive and the ledger.")
    ap.add_argument("--prefix", required=True,
                    help="key prefix to match, e.g. backups/2b07aad1c99563e2/")
    ap.add_argument("--yes", action="store_true",
                    help="actually delete; without it this lists and exits")
    args = ap.parse_args()

    if not args.prefix.strip():
        print("refusing an empty prefix: that would match the whole archive", file=sys.stderr)
        return 2

    led = r2.load_ledger()
    matches = sorted(k for k in led["objects"] if k.startswith(args.prefix))
    if not matches:
        print(f"no ledger object matches prefix {args.prefix!r}")
        print(f"ledger holds {len(led['objects'])} objects, "
              f"{r2.human_bytes(r2.ledger_bytes(led))}")
        return 0

    total = sum(int(led["objects"][k].get("bytes", 0)) for k in matches)
    print(f"{len(matches)} object(s) match {args.prefix!r}, {r2.human_bytes(total)} total:")
    for k in matches:
        o = led["objects"][k]
        print(f"  {r2.human_bytes(int(o.get('bytes', 0))):>10}  {o.get('uploaded_at', '?')}  {k}")

    if not args.yes:
        print("\nlisting only — nothing was deleted. Re-run with --yes to act.")
        return 0

    rc = 0
    for k in matches:
        try:
            res = r2.delete_object(k, led)
            print(f"DELETED  {r2.human_bytes(res['bytes'])}  {k}")
        except Exception as e:  # noqa: BLE001
            print(f"FAILED   {k}: {e}", file=sys.stderr)
            rc = 1

    print(f"\nledger now holds {len(led['objects'])} objects, "
          f"{r2.human_bytes(r2.ledger_bytes(led))}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
