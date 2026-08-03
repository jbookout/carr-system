"""Exporter gate machinery (addendum A8) — 2026-07-30 build session.

Every exporter runs the same poison gate:
  build to TEMP -> validate (headers + row tolerance) -> keep generation ->
  atomic rename -> export_run row with a CANONICAL-DATA checksum.
A failed validation leaves the previous good file untouched and writes a
validation_failed run row: the alarm layer (digest dead-man at 26h) sees it.

STAGING vs LIVE: until cutover, exporters write ONLY to the staging dir
(repo out/exports). Live vault paths activate with CARR_EXPORT_LIVE=1 —
flipped once, at cutover, never casually.

Credential: CARR_DB_EXPORTER_URL from ~/.config/carr/db.env (carr_exporter
bundle: export views + export_run + system_config, nothing else).
"""

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
STAGING = REPO / "out" / "exports"
VAULT = Path(os.environ.get(
    "CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"))
LIVE = os.environ.get("CARR_EXPORT_LIVE") == "1"
KEEP_GENERATIONS = 7


def connect():
    url = os.environ.get("CARR_DB_EXPORTER_URL")
    if not url:
        env = Path.home() / ".config/carr/db.env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("CARR_DB_EXPORTER_URL="):
                    # .strip("\"'") IS LOAD-BEARING, added 2026-08-02. db.env has TWO
                    # parsers with OPPOSITE requirements. `set -a; . db.env` (the exact
                    # line bin/nightly.sh uses) needs values QUOTED: an unquoted `&` in
                    # the jobs URL killed that line for two days and the cadence engine
                    # and availability matcher reported NOT CONFIGURED the whole time.
                    # Quoting the file fixed the shell and broke THIS parser, which fed
                    # psycopg a DSN with a literal apostrophe on the front and died with
                    # `invalid connection option` — blinding the export register, the one
                    # check that would report exports having stopped. Do not remove either
                    # half. Same fix in pipelines/brief_pack.py and lib/record_sources.py.
                    url = line.split("=", 1)[1].strip().strip("\"'")
    if not url:
        sys.exit("no CARR_DB_EXPORTER_URL (see ~/.config/carr/db.env)")
    return psycopg.connect(url)


def config(cur, key, default):
    cur.execute("select value from system_config where key = %s", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def canonical_checksum(rows):
    """sha256 of extracted DATA (never file bytes — openpyxl restamps metadata)."""
    blob = json.dumps(rows, default=str, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def last_ok_count(cur, target):
    cur.execute("select row_count from export_run where target=%s and status='ok' "
                "order by ran_at desc limit 1", (target,))
    row = cur.fetchone()
    return row[0] if row else None


def record_run(cur, target, row_count, checksum, status):
    cur.execute("insert into export_run (target,row_count,checksum,status) "
                "values (%s,%s,%s,%s)", (target, row_count, checksum, status))


# ---------------- the coverage note (absence is not absence, at the render) ----------------
#
# This system has read an empty cell as a fact five separate times: a lookup verb
# that invented an absence, an open-deals-only JSON read as the whole book,
# `stale-records` needing its own docstring to warn its caller that empty may mean
# nothing-captured, email silence treated as a cold prospect, and a grep too narrow
# to support what it was denying. Every one of those was caught after the fact.
#
# The renders are where the misreading actually starts, because a blank cell in a
# vault file looks exactly like a recorded zero to whoever opens it, and one of the
# two people opening these is newer to the system than the other. So each render
# carrying an absence-ambiguous column states its own coverage, in plain language,
# in the file, next to the column.
#
# WHICH columns are absence-ambiguous is a judgment and is authored per target: most
# blanks are ordinary (a vendor with no Notes is a vendor with no notes), and a note
# on every sparse column would be noise nobody reads. THE NUMBERS ARE NEVER AUTHORED.
# They are counted from the rows being written, on every run. That is not a style
# preference: the audit of 2026-08-02 measured vendor Last Touch at 0 of 290, and by
# the time this shipped the export view was already deriving 2. A number typed into a
# file would have shipped stale on day one. A column at full coverage drops out of
# the note entirely, so the note empties itself as the book fills in, and nobody has
# to remember to go delete it.


def _has_value(v):
    """Blank is blank: None, empty string and whitespace-only all count as unrecorded.

    NOT `is not None`. A view that renders '' and a view that renders NULL look the
    same to the reader, and the reader is who this number is for.
    """
    return v is not None and str(v).strip() != ""


def coverage_findings(rows, cols, watched):
    """[(column, filled, total, blurb)] for each watched column short of full coverage.

    `watched` maps a column name to the misreading it invites, phrased as what a
    blank does NOT tell you. `rows`/`cols` are the rows and header being written, so
    the count describes the file the partners open rather than some wider query
    sitting behind it. A watched column the render does not carry is skipped, which
    is what lets two targets share one watch list.
    """
    total = len(rows)
    if not total:
        return []
    found = []
    for col, blurb in watched.items():
        if col not in cols:
            continue
        i = cols.index(col)
        filled = sum(1 for r in rows if _has_value(r[i]))
        if filled < total:
            found.append((col, filled, total, blurb))
    return found


def _pct(filled, total):
    return round(filled / total * 100)


def coverage_note_md(rows, cols, watched, noun):
    """The markdown block for a rendered table, or [] when every watched column is full."""
    findings = coverage_findings(rows, cols, watched)
    if not findings:
        return []
    bullets = [f"- **{col}**: recorded on {filled} of {total} {noun} ({_pct(filled, total)}%). "
               f"A blank tells you nothing about {blurb}."
               for col, filled, total, blurb in findings]
    return [
        "",
        "### Coverage note",
        "",
        "Blanks in the columns listed here mean nobody has recorded a value yet. Read them",
        "as unknown.",
        "",
        *bullets,
        "",
        "*Counted from the rows above on every export. A column leaves this list once every",
        "row has a value.*",
    ]


def coverage_note_cell(finding, noun):
    """The same finding as one flat sentence, sized for a spreadsheet cell comment."""
    col, filled, total, blurb = finding
    return (f"Coverage note (generated). {col} is recorded on {filled} of {total} {noun} "
            f"({_pct(filled, total)}%). A blank tells you nothing about {blurb}. "
            f"Recounted on every export.")


def keep_generation(final_path: Path):
    if not final_path.exists():
        return
    gen_dir = final_path.parent / (final_path.name + ".generations")
    gen_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(final_path, gen_dir / f"{stamp}-{final_path.name}")
    gens = sorted(gen_dir.iterdir())
    for old in gens[:-KEEP_GENERATIONS]:
        old.unlink()


def run_export(target_key, live_rel_path, build_fn, bootstrap=False):
    """build_fn(tmp_path, cur) -> (row_count, canonical_rows). Returns True on ok."""
    dest_dir = (VAULT / live_rel_path).parent if LIVE else STAGING
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = (VAULT / live_rel_path) if LIVE else STAGING / Path(live_rel_path).name
    tmp_path = final_path.with_name(final_path.name + ".tmp")

    with connect() as conn, conn.cursor() as cur:
        try:
            row_count, canonical = build_fn(tmp_path, cur)
        except Exception as e:
            record_run(cur, target_key, 0, "build_error", "failed")
            conn.commit()
            print(f"[{target_key}] BUILD FAILED: {e}", file=sys.stderr)
            if tmp_path.exists():
                tmp_path.unlink()
            return False

        checksum = canonical_checksum(canonical)
        prev = last_ok_count(cur, target_key)
        tol = float(config(cur, "export.row_tolerance_pct", 5))
        # Small files break percent gates: 2 rules -> 3 is 50% "drift" but normal
        # growth. An absolute floor lets small deltas through regardless of percent.
        abs_floor = float(config(cur, "export.row_tolerance_abs", 3))
        if prev is not None and prev > 0 and not bootstrap:
            drift = abs(row_count - prev) / prev * 100
            if drift > tol and abs(row_count - prev) > abs_floor:
                record_run(cur, target_key, row_count, checksum, "validation_failed")
                conn.commit()
                tmp_path.unlink()
                print(f"[{target_key}] VALIDATION FAILED: {row_count} rows vs last ok {prev} "
                      f"({drift:.1f}% > {tol}%). Previous good file untouched.", file=sys.stderr)
                return False
        elif prev is None and not bootstrap:
            record_run(cur, target_key, row_count, checksum, "validation_failed")
            conn.commit()
            tmp_path.unlink()
            print(f"[{target_key}] no prior ok run — rerun with --bootstrap to accept first output",
                  file=sys.stderr)
            return False

        keep_generation(final_path)
        os.replace(tmp_path, final_path)
        record_run(cur, target_key, row_count, checksum, "ok")
        conn.commit()
        mode = "LIVE" if LIVE else "staging"
        print(f"[{target_key}] ok — {row_count} rows -> {final_path} ({mode})")
        return True
