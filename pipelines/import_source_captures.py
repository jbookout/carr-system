"""Import DNA/Marketing/Source Material/INDEX.md into source_capture rows (0070).

One-time flip importer, same posture as import_idea_bank.py / import_loops.py:
idempotent (re-run skips rows already present on captured_on + session exact
match), dry-run supported, structural repairs REPORTED rather than silent.

MAPPING
  Captured table | Date -> captured_on | Session -> session | Status -> merge_note
                 | status = 'merged' (every Captured row is by the file's own
                   definition absorbed — including partial-decline rows, whose
                   decline reasoning lives verbatim in merge_note)
  Queue section  | its one real row (the 2026-07-11 AiWithRubab thread) carries a
                   "distilled into writing-rules.md" note — it was queued and then
                   absorbed without ever moving tables. Imported as status
                   'merged' with the full note; the render will therefore show it
                   in the Captured table. STRUCTURAL REPAIR, reported not silent.

  visibility: '[public source' in the row -> public; '[colleague source' ->
  colleague; 'INTERNAL' in the session cell -> internal; else member_gated (the
  unmarked rows are the training.carr.us / portal / CARR-internal batch).

  source_url: only when a literal URL appears in the row (rare — most rows name
  handles, not links). The dedup key for future rows is the URL when the verb
  gets one, session-name similarity otherwise.

Usage:
  CARR_IMPORT_DB_URL=... python3 pipelines/import_source_captures.py [--dry-run]
"""

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg

SRC = Path(os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com"
    "/My Drive/CARR AI/DNA/Marketing/Source Material/INDEX.md"))

ROW_RE = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|(.+)\|\s*$")
URL_RE = re.compile(r"https?://\S+|x\.com/\S+")


def classify_visibility(session, note):
    blob = f"{session} {note}"
    if "[public source" in blob:
        return "public"
    if "[colleague source" in blob:
        return "colleague"
    if "INTERNAL" in session:
        return "internal"
    return "member_gated"


def parse(path):
    rows, in_queue = [], False
    for line in path.read_text().splitlines():
        if line.startswith("## Queue"):
            in_queue = True
            continue
        m = ROW_RE.match(line)
        if not m:
            continue
        date, rest = m.group(1), m.group(2)
        cells = [c.strip() for c in rest.split("|")]
        if not cells or not cells[0] or cells[0].startswith("---"):
            continue
        session = cells[0]
        note = " — ".join(c for c in cells[1:] if c)
        url_m = URL_RE.search(f"{session} {note}")
        rows.append({
            "captured_on": date,
            "session": session,
            "merge_note": note,
            "visibility": classify_visibility(session, note),
            # the queue's one real row was absorbed in place — structural repair,
            # reported in main(); everything imports as merged
            "status": "merged",
            "source_url": url_m.group(0) if url_m else None,
            "from_queue": in_queue,
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    url = os.environ.get("CARR_IMPORT_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set CARR_IMPORT_DB_URL (or DATABASE_URL)")
    if not SRC.exists():
        sys.exit(f"REFUSING: {SRC} not found.")

    rows = parse(SRC)
    repairs = [r for r in rows if r["from_queue"]]

    if a.dry_run:
        print(f"would import {len(rows)} rows")
        for r in rows[:3] + rows[-3:]:
            print(" ", r["captured_on"], "|", r["session"][:60], "|", r["visibility"])
        for r in repairs:
            print("STRUCTURAL REPAIR (queue row imported as merged):", r["session"][:70])
        return

    inserted = skipped = 0
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select id, slug from actor")
        actors = {s: i for i, s in cur.fetchall()}
        importer = actors.get("system") or actors["joe"]
        for r in rows:
            cur.execute(
                "select 1 from source_capture where captured_on=%s and session=%s",
                (r["captured_on"], r["session"]))
            if cur.fetchone():
                skipped += 1
                continue
            cur.execute(
                """insert into source_capture
                     (captured_on, session, source_url, visibility, status,
                      merge_note, created_by, updated_by)
                   values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (r["captured_on"], r["session"], r["source_url"], r["visibility"],
                 r["status"], r["merge_note"], importer, importer))
            inserted += 1
        conn.commit()
    print(f"imported {inserted}, skipped {skipped} (already present)")
    for r in repairs:
        print("STRUCTURAL REPAIR (queue row imported as merged):", r["session"][:70])


if __name__ == "__main__":
    main()
