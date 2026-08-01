#!/usr/bin/env python3
"""ORDER 36 step 8 — render every dossier and diff it against its frozen original.

STAGING ONLY, and by construction rather than by promise: it calls build_dossier
directly with a read cursor and writes to out/dossier-diff/ in the repo (which is
gitignored). It never touches the vault, never writes an export_run row, and
never needs --bootstrap — the A8 gate's row-tolerance machinery is about
protecting a LIVE file, and there is no live file in play here.

    .venv/bin/python tools/db-tap.py --branch <name> run tools/diff-dossier-renders.py
    .venv/bin/python tools/db-tap.py run tools/diff-dossier-renders.py   # production read

The frozen originals come out of the Drive freeze zips, not the live vault
files, so the diff is against exactly what was frozen before the import ran.
"""
import argparse
import difflib
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from exporters.targets import DOSSIER_DIR, DOSSIER_FILES, build_dossier  # noqa: E402

VAULT = Path(os.environ.get(
    "CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"))
SNAPSHOTS = VAULT / "Archive" / "snapshots"
FREEZES = [("2026-08-01-dossiers-freeze.zip", "2026-08-01-dossiers"),
           ("2026-08-01-dossiers-freeze-2.zip", "2026-08-01-dossiers-2")]
OUT = Path(__file__).resolve().parent.parent / "out" / "dossier-diff"


def frozen_originals():
    """basename -> frozen text, read from the Drive zips."""
    got = {}
    for zip_name, prefix in FREEZES:
        p = SNAPSHOTS / zip_name
        if not p.exists():
            sys.exit(f"freeze zip missing: {p}")
        with zipfile.ZipFile(p) as z:
            for member in z.namelist():
                base = os.path.basename(member)
                if base.endswith(".md"):
                    got[base] = z.read(member).decode()
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--context", type=int, default=3)
    a = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("no DATABASE_URL — run through tools/db-tap.py")
    import psycopg

    frozen = frozen_originals()
    names = [a.only] if a.only else DOSSIER_FILES
    missing = [n for n in names if n not in frozen]
    if missing:
        sys.exit(f"no frozen original for: {', '.join(missing)} — freeze before diffing")

    OUT.mkdir(parents=True, exist_ok=True)
    summary = []
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        for name in names:
            rel = f"{DOSSIER_DIR}/{name}"
            rendered_path = OUT / name
            rows, _canonical = build_dossier(rel, DOSSIER_FILES[name])(rendered_path, cur)
            new = rendered_path.read_text()
            old = frozen[name]

            diff = list(difflib.unified_diff(
                old.splitlines(keepends=True), new.splitlines(keepends=True),
                fromfile=f"frozen/{name}", tofile=f"rendered/{name}", n=a.context))
            (OUT / (name + ".diff")).write_text("".join(diff))

            added = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
            removed = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
            summary.append((name, rows, len(old.splitlines()), len(new.splitlines()),
                            added, removed))

    w = max(len(n) for n, *_ in summary)
    print(f"{'dossier'.ljust(w)}  rows  frozen  render   +     -")
    for n, rows, o, nn, add, rem in summary:
        print(f"{n.ljust(w)}  {rows:4d}  {o:6d}  {nn:6d}  {add:4d}  {rem:4d}")
    print(f"\ndiffs written to {OUT}")


if __name__ == "__main__":
    main()
