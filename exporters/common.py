"""Exporter gate machinery (addendum A8) — 2026-07-30 build session.

Every exporter runs the same poison gate:
  build to TEMP -> validate (headers + row tolerance) -> keep generation ->
  atomic rename -> export_run row with a CANONICAL-DATA checksum.
A failed validation leaves the previous good file untouched and writes a
validation_failed run row: the alarm layer (digest dead-man at 26h) sees it.

DRAFT vs LIVE: exporters write to the draft dir (repo out/exports) unless
CARR_EXPORT_LIVE=1, which sends them to EXPORT_HOME — CARR's OneDrive since
Joe's ruling of 2026-08-22. It is no longer the Drive vault, which the
2026-08-19 cutoff retired.

Credential: CARR_DB_EXPORTER_URL from ~/.config/carr/db.env (carr_exporter
bundle: export views + export_run + system_config, nothing else).
"""

import errno
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
DRAFT = REPO / "out" / "exports"
VAULT = Path(os.environ.get("CARR_VAULT") or "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")

# WHERE THE LIVE FILES GO, ruled by Joe on 2026-08-22: CARR's own OneDrive.
#
# THE PROBLEM THIS SOLVES. The 2026-08-19 cutoff retired the Drive vault, and
# these exports were still addressed to it. They did not break — they build
# cleanly in seconds — they simply had nowhere to land, so thirteen nightly
# steps refused every night for want of a destination and the chain finished
# red. A chain that is always red is one nobody reads.
#
# WHY ONEDRIVE RATHER THAN A LOCAL FOLDER. These seven are the files a PERSON
# opens and hands to somebody: the client roster, the leads and vendor lists,
# the deal export. That is the one thing the store cannot do for them, and it
# is why they survived a cutoff that retired 38 generated documents. CARR's
# OneDrive is already where this system puts finished business documents —
# pipelines/backfill_document_attachments.py has written completed deal
# documents to Joe's Folder/Deals/Active Deals for months — so this puts the
# projections beside the documents rather than inventing a second home.
#
# READ THE FILE BACK BEFORE BELIEVING IT LANDED. OneDrive, like Google Drive
# File Stream before it, can serve a synced file as an online-only placeholder,
# so a path that exists is not proof that content does. The exporter's own
# checksum step reads the finished file, which is what makes a placeholder show
# up as a failure here rather than as a silently empty projection downstream.
EXPORT_HOME = Path(os.environ.get("CARR_EXPORT_HOME")
                   or "/Users/booko/Library/CloudStorage/OneDrive-CARR,Inc/Joe's Folder/CARR AI")
LIVE = os.environ.get("CARR_EXPORT_LIVE") == "1"
KEEP_GENERATIONS = 7
GENERATION_COPY_ATTEMPTS = 3
GENERATION_COPY_RETRY_ERRNOS = frozenset({errno.EAGAIN, errno.EDEADLK})
GENERATION_COPY_BACKOFF_SECONDS = (0.05, 0.10)


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


def record_run(cur, target, row_count, checksum, status, file_sha=None):
    # file_sha (0073, wave 1): sha256 of the WRITTEN FILE's bytes. The data
    # checksum proves what the DB said; this proves what the file was, so
    # ops/renders-verify.py can detect a render edited by anything other than
    # the exporter (the V-BNK-050 clobber class). Nullable: failure paths and
    # pre-0073 rows carry none. Written via a column-tolerant INSERT so this
    # code deploys before the migration lands without breaking exports.
    try:
        cur.execute("insert into export_run (target,row_count,checksum,status,file_sha) "
                    "values (%s,%s,%s,%s,%s)", (target, row_count, checksum, status, file_sha))
    except Exception:
        cur.connection.rollback()
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


def _generation_destinations(gen_dir: Path, final_path: Path, stamp: str):
    """Yield the bounded same-second name space, oldest-compatible name first."""
    yield gen_dir / f"{stamp}-{final_path.name}"
    for sequence in range(1, 100):
        yield gen_dir / f"{stamp}-{sequence:02d}-{final_path.name}"


class _PublishedGenerationCleanupError(RuntimeError):
    """The generation is durable; cleanup failed and must not trigger a recopy."""


def _remove_generation_temporary(temporary: Path) -> None:
    """Remove a staged file, retrying only the two FileProvider transient errors."""
    for attempt in range(GENERATION_COPY_ATTEMPTS):
        try:
            temporary.unlink(missing_ok=True)
            return
        except OSError as error:
            if error.errno not in GENERATION_COPY_RETRY_ERRNOS:
                raise
            if attempt + 1 == GENERATION_COPY_ATTEMPTS:
                raise
            time.sleep(GENERATION_COPY_BACKOFF_SECONDS[attempt])


def _write_generation_attempt(source: Path, gen_dir: Path, stamp: str) -> Path:
    """Stage one byte-for-byte generation, then atomically publish without clobber."""
    fd, temporary_name = tempfile.mkstemp(prefix=f".{stamp}-{source.name}.", dir=gen_dir)
    temporary = Path(temporary_name)
    published = None
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(source.read_bytes())
        # link(2) is the no-clobber publication primitive: EEXIST means another
        # run won this name after we began staging, so allocate the next name.
        for destination in _generation_destinations(gen_dir, source, stamp):
            try:
                os.link(temporary, destination)
                published = destination
                return destination
            except FileExistsError:
                continue
        first = gen_dir / f"{stamp}-{source.name}"
        raise FileExistsError(errno.EEXIST, "generation-name space exhausted", first)
    finally:
        try:
            _remove_generation_temporary(temporary)
        except OSError as error:
            if published is not None:
                raise _PublishedGenerationCleanupError(
                    f"generation published at {published}, but staging cleanup failed"
                ) from error
            raise


def keep_generation(final_path: Path):
    """Keep an atomic dated copy of the file about to be replaced.

    OneDrive's FileProvider can transiently return EAGAIN/EDEADLK while reading
    or writing a cloud-backed file.  Retry only those two documented errors,
    with a short bounded backoff.  All other errors (and an exhausted transient
    retry budget) escape: a permission, path, or storage failure must be seen by
    the exporter rather than silently discarding the rollback guarantee.

    The copy deliberately avoids shutil.copy2/copyfile, whose macOS fcopyfile
    fast path was the source of the 2026-08-25 EDEADLK.  The timestamp in the
    generation name is its provenance, so copying source metadata is not part of
    this contract.
    """
    if not final_path.exists():
        return
    gen_dir = final_path.parent / (final_path.name + ".generations")
    gen_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for attempt in range(GENERATION_COPY_ATTEMPTS):
        try:
            _write_generation_attempt(final_path, gen_dir, stamp)
            break
        except OSError as error:
            if error.errno not in GENERATION_COPY_RETRY_ERRNOS:
                raise
            if attempt + 1 == GENERATION_COPY_ATTEMPTS:
                raise
            time.sleep(GENERATION_COPY_BACKOFF_SECONDS[attempt])

    gens = sorted(gen_dir.iterdir())
    for old in gens[:-KEEP_GENERATIONS]:
        old.unlink()


def run_export(target_key, live_rel_path, build_fn, bootstrap=False):
    """build_fn(tmp_path, cur) -> (row_count, canonical_rows). Returns True on ok."""
    dest_dir = (EXPORT_HOME / live_rel_path).parent if LIVE else DRAFT
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = (EXPORT_HOME / live_rel_path) if LIVE else DRAFT / Path(live_rel_path).name
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
        # ASYMMETRIC BY DIRECTION, 2026-08-03. This guard exists to stop a
        # truncated or half-built export from overwriting a good file. That is
        # DATA LOSS, and data loss always shows up as rows going DOWN. Growth is
        # the opposite signal: on an accumulator (open-loops-backlog, the
        # record-layer dictionary) it is just the week's work landing. One
        # symmetric 5% gate treated both the same and FROZE open-loops-backlog.md
        # for two days across three nightly runs (51->59, then 65->72), so loop
        # #146 was filed into a render nobody could read on the morning it was
        # needed. A guard that fires on normal use is one people learn to
        # --bootstrap past without reading, which is exactly how the real failure
        # gets waved through. So: shrink stays tight, growth gets room. Growth is
        # still bounded, because a runaway duplication balloons rather than
        # truncates. Both limits are DB-tunable; the growth key falls back to its
        # default when absent, so no migration is owed.
        tol_grow = float(config(cur, "export.row_tolerance_growth_pct", 30))
        if prev is not None and prev > 0 and not bootstrap:
            delta = row_count - prev
            drift = abs(delta) / prev * 100
            limit = tol_grow if delta > 0 else tol
            if drift > limit and abs(delta) > abs_floor:
                record_run(cur, target_key, row_count, checksum, "validation_failed")
                conn.commit()
                tmp_path.unlink()
                way = "GREW to" if delta > 0 else "SHRANK to"
                kind = "growth" if delta > 0 else "shrink"
                print(f"[{target_key}] VALIDATION FAILED: {way} {row_count} rows from last ok {prev} "
                      f"({drift:.1f}% > {limit}% {kind} limit). Previous good file untouched. "
                      f"If the change is real, rerun this target with --bootstrap.",
                      file=sys.stderr)
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
        # file_sha is a LIVE-file tamper detector; a draft run's hash describes
        # out/exports/, not the vault, and recording it poisoned renders-verify
        # with false "tampered" flags (caught by the doctrine health pass,
        # 2026-08-08: three draft 03:43Z rows outranked the live 02:51Z ones).
        file_sha = hashlib.sha256(final_path.read_bytes()).hexdigest() if LIVE else None
        record_run(cur, target_key, row_count, checksum, "ok", file_sha)
        conn.commit()
        mode = "LIVE" if LIVE else "draft"
        print(f"[{target_key}] ok — {row_count} rows -> {final_path} ({mode})")
        return True
