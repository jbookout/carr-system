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
                    url = line.split("=", 1)[1].strip()
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
        if prev is not None and prev > 0 and not bootstrap:
            drift = abs(row_count - prev) / prev * 100
            if drift > tol:
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
