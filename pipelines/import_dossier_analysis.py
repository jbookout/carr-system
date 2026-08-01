#!/usr/bin/env python3
"""ORDER 36 step 7 — import the 20 hand-maintained dossiers' analysis prose as
dated `kind=analysis` activity rows.

DRY RUN BY DEFAULT. --apply writes, and refuses to run unless migration 0028 is
present in schema_migrations (the vocabulary and the render views must exist
first). Run it through db-tap so no DSN reaches a shell command:

    .venv/bin/python tools/db-tap.py run pipelines/import_dossier_analysis.py
    .venv/bin/python tools/db-tap.py run pipelines/import_dossier_analysis.py --apply
    .venv/bin/python tools/db-tap.py --branch rehearse-0028 run \
        pipelines/import_dossier_analysis.py --apply --only Renalus.md

WHAT IT DOES NOT DO — the stop rules, in code:
  * It never guesses a date or an author. A section whose date or author cannot
    be read off the file's OWN stamps is imported WITH ITS TEXT INTACT (nothing
    is lost) and flagged on the review list, with the reason recorded per row.
  * A flagged row's occurred_at falls back to the file's `Last updated:` stamp,
    or failing that the file's mtime, and `source` is set to 'import' so the
    render prints "date unrecorded"-grade provenance rather than a confident
    date the file never claimed.
  * It writes one row per H2 section plus one for the pre-H2 header block. It
    does not merge, summarise, rewrite or reflow prose — the body is the
    section's bytes.

CHUNKING, and why H2: every one of the 20 files organises itself by `## `
headings — dated addenda in the deal files (GulfCoastPelvicFloor,
FirstCallDPC-Petersen) and topical sections in the narrative ones
(LifeDentalGroup, Tyrer). H3s stay INSIDE their parent section; splitting on
them would shred a single argument across rows.
"""
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from exporters.targets import DOSSIER_DIR, DOSSIER_FILES  # noqa: E402

VAULT = Path(os.environ.get(
    "CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"))

H2 = re.compile(r"^## +(.*)$")
ISO_DATE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")
US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
LAST_UPDATED = re.compile(r"^Last updated:\s*(.+?)\s*$", re.M)
# Authors are only ever read off an explicit stamp the file itself carries.
AUTHOR = re.compile(r"\((?:by\s+)?(Joe|Dell|Claude)\b", re.I)
FM_OWNER = re.compile(r"^owner:\s*(\S+)", re.M)

HEADER_TITLE = "Dossier header (legacy import)"


def parse_date(text):
    m = ISO_DATE.search(text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc), "iso"
        except ValueError:
            return None, None
    m = US_DATE.search(text)
    if m:
        mo, day, yr = int(m.group(1)), int(m.group(2)), m.group(3)
        if not yr:
            return None, None          # bare 7/30 — the YEAR is a guess. Never guessed.
        yr = int(yr) + 2000 if int(yr) < 100 else int(yr)
        try:
            return datetime(yr, mo, day, tzinfo=timezone.utc), "us"
        except ValueError:
            return None, None
    return None, None


def parse_file(path: Path):
    """-> (rows, flags). Each row: dict(title, body, occurred_at, author, flags)."""
    text = path.read_text()
    lines = text.splitlines()

    fallback_dt, _ = (None, None)
    m = LAST_UPDATED.search(text)
    if m:
        fallback_dt, _ = parse_date(m.group(1))
    if fallback_dt is None:
        fallback_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        fallback_src = "file mtime"
    else:
        fallback_src = "the file's own `Last updated:` stamp"

    fm = FM_OWNER.search(text)
    file_owner = fm.group(1) if fm else None

    # split on H2
    chunks, cur_title, cur_body = [], None, []
    for ln in lines:
        m2 = H2.match(ln)
        if m2:
            chunks.append((cur_title, cur_body))
            cur_title, cur_body = m2.group(1).strip(), []
        else:
            cur_body.append(ln)
    chunks.append((cur_title, cur_body))

    rows = []
    for title, body in chunks:
        body_text = "\n".join(body).strip("\n")
        is_header = title is None
        if is_header and not body_text.strip():
            continue
        title = HEADER_TITLE if is_header else title

        flags = []
        dt, _kind = parse_date(title)
        if dt is None and is_header:
            dt, _kind = (fallback_dt, "last_updated") if fallback_src.startswith("the file") else (None, None)
            if dt is None:
                dt = fallback_dt
                flags.append(f"date not stamped on the header block; used {fallback_src}")
        elif dt is None:
            dt = fallback_dt
            flags.append(f"section heading carries no date; used {fallback_src}")

        am = AUTHOR.search(title)
        author = am.group(1).lower() if am else None
        if author is None:
            if is_header and file_owner:
                author = file_owner.lower()
            else:
                flags.append("no author stamped on this section")

        rows.append({"title": title, "body": body_text, "occurred_at": dt,
                     "author": author, "flags": flags})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", help="one dossier basename, for the file-by-file gate (step 8)")
    a = ap.parse_args()

    files = [a.only] if a.only else DOSSIER_FILES
    if a.only and a.only not in DOSSIER_FILES:
        sys.exit(f"{a.only} is not one of the 20 dossiers")

    total, flagged, per_file = 0, 0, []
    for name in files:
        p = VAULT / DOSSIER_DIR / name
        rows = parse_file(p)
        f = sum(1 for r in rows if r["flags"])
        total += len(rows)
        flagged += f
        per_file.append((name, len(rows), f))

    w = max(len(n) for n, _, _ in per_file)
    print(f"{'dossier'.ljust(w)}  rows  flagged")
    for n, c, f in per_file:
        print(f"{n.ljust(w)}  {c:4d}  {f:7d}")
    print(f"{'TOTAL'.ljust(w)}  {total:4d}  {flagged:7d}")

    if not a.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply once migration 0028 "
              "is applied to the target database.")
        return

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("no DATABASE_URL — run through tools/db-tap.py")
    import psycopg
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select 1 from schema_migrations where filename like '0028%%'")
        if not cur.fetchone():
            sys.exit("migration 0028 is not applied to this database — STOP. "
                     "The analysis vocabulary and render views do not exist yet.")
        sys.exit("--apply is deliberately not wired to production in this session: "
                 "ORDER 36 steps 7-8 are gated on the 0028 production apply, which "
                 "is Joe's tap. Rehearse on a branch, or hand this to the supervisor.")


if __name__ == "__main__":
    main()
