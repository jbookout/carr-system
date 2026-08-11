#!/usr/bin/env python3
"""renders-verify.py — the render-tamper detector (wave 1 C1, decision a317439f).

For every export target whose latest ok run stored a file_sha (0073), re-hash
the live vault file and compare. A mismatch means the render was modified by
something OTHER than the exporter after it was written — Drive web edit,
another device, partial sync, or a gate bypass — the exact class that silently
destroyed V-BNK-050.

Health-row contract per rule 590b11e1 (no metric without a bound action): the
output line names its response inline. On mismatch: re-export the named target
(CARR_EXPORT_LIVE=1 ./run.sh export --only <t> ) AND investigate what touched
the file, because a re-export alone erases the evidence of the edit.

Prints ONE line. Exit 0 OK / 1 mismatch / 2 SKIP.
"""

import hashlib
import os
import sys

VAULT = os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
ENV = os.path.expanduser("~/.config/carr/db.env")


def main() -> int:
    if not os.path.exists(ENV):
        print("SKIP: no db.env")
        return 2
    # EXPORTER credential, not jobs: export_run belongs to the exporter lane
    # (0006), and information_schema only shows a role the columns it can read
    # — the jobs role produced a false "0073 not applied" SKIP on first live
    # run, the same role-visibility class as the vendor_category grant bug.
    url = None
    for key in ("CARR_DB_EXPORTER_URL=", "CARR_DB_JOBS_URL="):
        for line in open(ENV):
            if line.startswith(key):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
        if url:
            break
    if not url:
        print("SKIP: no exporter/jobs credential in db.env")
        return 2
    try:
        import psycopg
        c = psycopg.connect(url)
        cur = c.cursor()
        cur.execute("select 1 from information_schema.columns "
                    "where table_name='export_run' and column_name='file_sha'")
        if not cur.fetchone():
            print("SKIP: migration 0073 not applied here yet")
            return 2
        # latest ok run per target that carries a file hash
        cur.execute("""
            select distinct on (target) target, file_sha
              from export_run
             where status='ok' and file_sha is not null
             order by target, ran_at desc""")
        rows = cur.fetchall()
        cur.execute("select value #>> '{}' from system_config where key in "
                    "('doctrine.md_renders_retiring','doctrine.md_renders_retired')")
        markdown_cutoff = any(str(row[0]).lower() == "true" for row in cur.fetchall())
    except Exception as e:
        print(f"SKIP: read failed ({type(e).__name__}: {str(e).splitlines()[0]})")
        return 2

    # map target -> vault path via the exporter registry (parsed like the gate does)
    sys.path.insert(0, os.path.expanduser("~/carr-system"))
    try:
        from exporters.targets import TARGETS
        paths = {k: rel for k, (rel, _fn) in TARGETS.items()}
    except Exception as e:
        print(f"SKIP: registry unreadable ({type(e).__name__})")
        return 2

    checked = mismatched = missing = retired = 0
    bad = []
    for target, stored in rows:
        rel = paths.get(target)
        if not rel:
            continue          # loop-file targets etc. register elsewhere; covered as adopted
        if markdown_cutoff and rel.lower().endswith(".md"):
            retired += 1
            continue
        p = os.path.join(VAULT, rel)
        if not os.path.exists(p):
            missing += 1
            bad.append(f"{target}(GONE)")
            continue
        checked += 1
        live = hashlib.sha256(open(p, "rb").read()).hexdigest()
        if live != stored:
            mismatched += 1
            bad.append(target)

    if mismatched or missing:
        print(f"WARN renders-verify — {mismatched} tampered + {missing} missing of {checked} hashed "
              f"renders: {', '.join(bad[:5])} · on breach: re-export the target AND investigate "
              f"the edit first (a re-export erases the evidence)")
        return 1
    suffix = f"; {retired} Markdown projection(s) retired" if retired else ""
    print(f"OK renders-verify — {checked} hashed renders match their export bytes "
          f"({len(paths) - checked} not yet hashed; they gain a hash at next export) "
          f"· on breach: re-export + investigate{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
