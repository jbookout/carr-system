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
    """-> rows. Each row carries its own PROVENANCE, not just its values.

    THE OWNER FALLBACK (ratified by Joe, 2026-08-01). The first pass flagged 84
    of 104 rows, overwhelmingly for a missing per-section author stamp — a review
    gate nobody could work through. The ruling: a section with no stamp of its
    own inherits the file-level `owner:` frontmatter, recorded with a DIFFERENT
    provenance string so nothing ever reads as more certain than it is.

    Provenance is tracked per field and travels with the row:
      date   — 'section heading' | 'file stamp (Last updated)' | 'file mtime'
      author — 'section stamp'   | 'file stamp (owner)'        | 'none'

    A row is FLAGGED when a value cannot be traced to something the file itself
    asserts: no owner stamp anywhere, or a date that had to come from filesystem
    mtime. mtime is deliberately NOT treated as a file stamp — it records when
    the bytes last changed, which is routinely months off the content's date and
    is exactly the kind of confident-looking wrong fact the stop rule exists to
    keep off a dossier.
    """
    text = path.read_text()
    lines = text.splitlines()

    fallback_dt = None
    m = LAST_UPDATED.search(text)
    if m:
        fallback_dt, _ = parse_date(m.group(1))
    if fallback_dt is None:
        fallback_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        fallback_prov = "file mtime"
    else:
        fallback_prov = "file stamp (Last updated)"

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
        if dt is not None:
            date_prov = "section heading"
        else:
            dt, date_prov = fallback_dt, fallback_prov
            if date_prov == "file mtime":
                where = "the header block" if is_header else "this section's heading"
                flags.append(f"no date on {where} and the file carries no "
                             f"`Last updated:` stamp; fell back to file mtime")

        am = AUTHOR.search(title)
        if am:
            author, author_prov = am.group(1).lower(), "section stamp"
        elif file_owner:
            author, author_prov = file_owner.lower(), "file stamp (owner)"
        else:
            author, author_prov = None, "none"
            flags.append("no author stamped on this section and the file carries "
                         "no `owner:` frontmatter")

        rows.append({"title": title, "body": body_text, "occurred_at": dt,
                     "author": author, "date_prov": date_prov,
                     "author_prov": author_prov, "flags": flags})
    return rows


def _is_production(url: str) -> bool:
    """True if this DSN is production. Asks Neon, never the caller.

    --rehearse exists to write rows, so it has to prove it is pointed somewhere
    disposable. Comparing hosts against the production connection string Neon
    itself hands back is the only check that cannot be defeated by a typo in a
    branch name.
    """
    import subprocess
    from urllib.parse import urlparse
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    neonctl = os.path.join(repo, "mcp-server", "node_modules", ".bin", "neonctl")
    out = subprocess.run(
        [neonctl, "connection-string", "production", "--project-id",
         "steep-field-48688294", "--role-name", "neondb_owner"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PATH": "/usr/local/opt/node@22/bin:/opt/homebrew/bin:"
             + os.environ.get("PATH", "")})
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit("could not resolve production's DSN to compare against — refusing "
                 "to guess whether this is production")
    return urlparse(url).hostname == urlparse(out.stdout.strip()).hostname


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rehearse", action="store_true",
                    help="permit --apply against a NEON BRANCH only, to produce the "
                         "review packet's diffs; refuses production")
    ap.add_argument("--only", help="one dossier basename, for the file-by-file gate (step 8)")
    a = ap.parse_args()

    files = [a.only] if a.only else DOSSIER_FILES
    if a.only and a.only not in DOSSIER_FILES:
        sys.exit(f"{a.only} is not one of the {len(DOSSIER_FILES)} dossiers")

    # The gate runs on the DRY RUN too, not only on --apply. A dry run whose
    # counts are quoted into a review packet needs to have been checked against
    # the database those counts will be imported into — otherwise the packet
    # describes a plan for a schema that may not exist. Skipped silently when
    # there is no DSN, so the parser stays runnable offline.
    url = os.environ.get("DATABASE_URL")
    if url:
        import psycopg
        with psycopg.connect(url) as conn, conn.cursor() as cur:
            cur.execute("select 1 from schema_migrations where filename like '0028%%'")
            if not cur.fetchone():
                sys.exit("migration 0028 is not applied to this database — STOP. "
                         "The analysis vocabulary and render views do not exist yet.")
            cur.execute("select count(*) from client where notes_path is not null")
            n = cur.fetchone()[0]
            if n != len(DOSSIER_FILES):
                sys.exit(f"the database carries {n} dossier notes_path rows but "
                         f"DOSSIER_FILES lists {len(DOSSIER_FILES)} — the set moved. "
                         "STOP and reconcile before importing.")
            cur.execute("select count(*) from activity where kind='analysis'")
            existing = cur.fetchone()[0]
        print(f"gate: 0028 applied · {n} notes_path rows match DOSSIER_FILES · "
              f"{existing} analysis row(s) already present\n")

    total, flagged, per_file = 0, 0, []
    prov = {"date": {}, "author": {}}
    for name in files:
        p = VAULT / DOSSIER_DIR / name
        rows = parse_file(p)
        f = sum(1 for r in rows if r["flags"])
        for r in rows:
            prov["date"][r["date_prov"]] = prov["date"].get(r["date_prov"], 0) + 1
            prov["author"][r["author_prov"]] = prov["author"].get(r["author_prov"], 0) + 1
        total += len(rows)
        flagged += f
        per_file.append((name, len(rows), f))

    w = max(len(n) for n, _, _ in per_file)
    print(f"{'dossier'.ljust(w)}  rows  flagged")
    for n, c, f in per_file:
        print(f"{n.ljust(w)}  {c:4d}  {f:7d}")
    print(f"{'TOTAL'.ljust(w)}  {total:4d}  {flagged:7d}")

    print("\nprovenance — date")
    for k, v in sorted(prov["date"].items(), key=lambda kv: -kv[1]):
        print(f"  {v:4d}  {k}")
    print("provenance — author")
    for k, v in sorted(prov["author"].items(), key=lambda kv: -kv[1]):
        print(f"  {v:4d}  {k}")

    if not a.apply:
        print("\nDRY RUN — nothing written.")
        return

    if not url:
        sys.exit("no DATABASE_URL — run through tools/db-tap.py")

    if not a.rehearse:
        sys.exit("--apply is HELD against production: the live import is the "
                 "supervisor's tap and waits on Joe's review of the diff packet "
                 "(record-layer/order36-dossier-review-packet-2026-08-01.md). The "
                 "gate above already confirmed the database is ready. To produce "
                 "the packet's diffs, run --apply --rehearse against a Neon branch. "
                 "To go live, remove this hold.")

    # --rehearse: import into a BRANCH so the review packet can show Joe the real
    # post-import diffs. It proves it is not production rather than trusting the
    # caller — a flag that only promises to be safe is not a safety mechanism.
    if _is_production(url):
        sys.exit("--rehearse was pointed at PRODUCTION. Refusing. Run it through "
                 "tools/db-tap.py --branch <name>.")

    import psycopg
    written = skipped = 0
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        # WHO THE RENDER CALLS THE AUTHOR is actor_id — v_export_dossier_analysis
        # selects act.slug. So the actor has to BE the author the file names, or
        # the owner-fallback ruling is lost the moment the row lands: every
        # imported row would read "· system" and the file's own stamps would
        # survive nowhere.
        #
        # Actor = the named human (section stamp or file `owner:`), else 'system'.
        # The CONFIDENCE of that name rides `source`, which is already the
        # provenance column on this table ('stated', 'import', 'mail_ingest',
        # 'call_recording'), so no DDL and no new vocabulary table:
        #   import               — the section stamped its own author
        #   import_file_stamp    — inherited from the file's `owner:` frontmatter
        #   import_unattributed  — no owner anywhere; actor is 'system'
        # The render prints the distinction (see _dossier_stamp), so a reader can
        # never mistake an inherited stamp for one the section actually carried.
        # FLAGGED for ratification: three source values where one existed.
        cur.execute("select slug, id from actor")
        actors = {s: i for s, i in cur.fetchall()}
        if "system" not in actors:
            sys.exit("no 'system' actor to attribute unattributed rows to")

        SOURCE_BY_PROV = {"section stamp": "import",
                          "file stamp (owner)": "import_file_stamp",
                          "none": "import_unattributed"}
        for name in files:
            rel = f"{DOSSIER_DIR}/{name}"
            cur.execute("select client_id from v_export_dossier_subject where rel_path=%s", (rel,))
            got = cur.fetchone()
            if not got:
                print(f"  SKIP {name}: no subject row"); skipped += 1; continue
            client_id = got[0]
            for r in parse_file(VAULT / DOSSIER_DIR / name):
                actor_id = actors.get(r["author"] or "", actors["system"])
                cur.execute(
                    "insert into activity (occurred_at, actor_id, kind, summary, detail, "
                    "                      client_id, source) "
                    "values (%s, %s, 'analysis', %s, %s, %s, %s)",
                    (r["occurred_at"], actor_id, r["title"], r["body"], client_id,
                     SOURCE_BY_PROV[r["author_prov"]]))
                written += 1
        conn.commit()
    print(f"\nREHEARSAL import: {written} analysis rows written to the branch, "
          f"{skipped} file(s) skipped. Production untouched.")


if __name__ == "__main__":
    main()
