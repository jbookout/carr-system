"""Import idea-bank.md into loop_item rows, kind 'idea' (ORDER 40 step 3).

  00_Context/idea-bank.md -> loop_block (file scaffolding) + loop_item (kind idea)

Requires migration 0031 (adds 'idea' to the loop_item / loop_block kind CHECKs).

WHY loop_item AND NOT A NEW TABLE. The idea bank's shape is the loop shape:
numbered rows, a lifecycle (Parked -> promoted or Retired), prose scaffolding
around tables, and a file that must keep rendering at the same path for the
monthly resurface task. loop_block already solves the hard part — the file's
doctrine prose (the flow list, the "Last surfaced" convention) stays DATA, so Joe
editing it is a row update and not a code change.

An idea is deliberately NOT an open_loop. The bank exists precisely because an
idea has no owner and no commitment yet; open-loops holds committed actions with
an owner. `owner` is therefore NULL on every idea row, and that NULL is the
distinction the file was created to preserve, not a missing value.

COLUMN MAPPING, ruled by the Fable seat 2026-08-01 (the three questions this
importer could not answer itself):
  Parked  | # -> number | Idea -> title | Domain -> extra:domain
          | Captured -> since_text | Why it's good / the spark -> body
          | Status -> extra:status | Last surfaced -> extra:last_surfaced
  Retired | # -> number | Idea -> title | Domain -> extra:domain
          | Captured -> since_text | Retired -> closed_text
          | Why retired -> outcome  (AND close_outcome, see below)

`Status` goes to extra:status because loop_item.status is the LIFECYCLE column
(open/done/dropped) and the file's Status column is content ("Parked", "Parked
(standing)", "ADOPTED 7/31 -> negotiation.md"). Collapsing the two would destroy
the file's own words.

A retired row's "Why retired" is written to BOTH `outcome` and `close_outcome`.
That is not duplication for its own sake — it is the exact precedent
import_loops.py set for the Done tables ("a closed row's outcome IS its
close_outcome — the file's own column"): `outcome` is the rendered cell,
`close_outcome` is the record-level constraint that makes a reasonless retirement
unstorable. Which is the idea bank's own rule made structural: "Move, don't
delete — the reasoning stays visible so we don't re-litigate it later."

STRUCTURAL REPAIR, reported not silent. The Retired table in the frozen original
is malformed: its `|---|` separator row sits BELOW two data rows (#39 and #34)
instead of above them, so a strict markdown parser reads those two as part of a
different run. Per the Fable ruling, #39 and #34 are read as Retired rows and the
stray separator is dropped. The render therefore emits a WELL-FORMED table where
the source had a broken one — a real, intended divergence from the frozen
original, and the only one that is not byte-identical.

FOUR ROWS CARRY AN ADOPTED STATUS (#47, #46, #45, #44: "ADOPTED 7/31 -> <file>").
By the bank's own rule 5 those should already have moved to Retired. They are
imported AS THEY SIT — status 'open', their Status cell verbatim — because moving
them is a content decision nobody has ruled. They are on the review list.

IDEMPOTENT by record_source (source_system, external_key). A rerun writes 0 rows.

Usage:
  CARR_IMPORT_DB_URL=... .venv/bin/python -m pipelines.import_idea_bank [--dry-run]
Writes out/idea-bank-import-<stamp>.md.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

SOURCE_SYSTEM = "idea-bank"
REL_PATH = "00_Context/idea-bank.md"
VAULT = Path(os.environ.get(
    "CARR_VAULT",
    os.path.expanduser("~/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/"
                       "My Drive/CARR AI")))
SRC = VAULT / "00_Context" / "idea-bank.md"

PARKED_COLS = ["number", "title", "extra:domain", "since_text", "body",
               "extra:status", "extra:last_surfaced"]
RETIRED_COLS = ["number", "title", "extra:domain", "since_text", "closed_text", "outcome"]

SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")


def split_cells(line):
    """A markdown table row -> its cells, verbatim, outer pipes discarded."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse(path):
    """-> (blocks, rows_parked, rows_retired).

    A block's prose_md is everything emitted BEFORE its table (loop_block's own
    definition), so the `---` rule sitting between the Parked table and the
    `## Retired` heading belongs to the RETIRED block, not to Parked. Getting
    that boundary wrong drops two horizontal rules — which the first round-trip
    diff caught.

    The file ends with a trailing newline, so the last block is a prose-only
    block holding the empty remainder. Same device ORDER 31 used for
    open-loops-backlog.md, and for the same reason: "\\n".join drops the final
    newline otherwise, and the round-trip diff is byte-level.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    def resumes_table(i):
        """True if a table row follows position i before any real prose.

        Needed because the Parked table in the frozen original is interrupted by
        a BLANK LINE mid-run (between rows 22 and 24). In markdown a blank line
        terminates a table, so the source file's Parked table is structurally
        two tables — the same class of defect as the Retired table's misplaced
        separator. Both are repaired by the render and reported; neither is
        content, and neither is silently swallowed."""
        j = i
        while j < len(lines) and not lines[j].strip():
            j += 1
        return j < len(lines) and lines[j].strip().startswith("|")

    def scan_table(start):
        """-> (header, rows, prose_before, index_after_table, blank_repairs)."""
        header, rows, prose, i, repairs = None, [], [], start, 0
        while i < len(lines):
            s = lines[i].strip()
            if s.startswith("|"):
                if SEP_RE.match(s):
                    i += 1
                    continue                      # separator, incl. the misplaced one
                cells = split_cells(s)
                if header is None:
                    header = cells
                elif any(cells):
                    rows.append(cells)
                i += 1
                continue
            if header is not None:
                if not s and resumes_table(i):
                    repairs += 1                  # intra-table blank line, dropped
                    i += 1
                    continue
                break                             # table finished
            prose.append(lines[i])
            i += 1
        return header, rows, "\n".join(prose), i, repairs

    hdr_p, rows_p, prose_p, after_p, rep_p = scan_table(0)
    hdr_r, rows_r, prose_r, after_r, rep_r = scan_table(after_p)
    prose_tail = "\n".join(lines[after_r:])
    if rep_p or rep_r:
        print(f"NOTE: {rep_p + rep_r} intra-table blank line(s) dropped — the source "
              "tables are malformed markdown; the render emits well-formed tables.")

    blocks = [
        {"seq": 1, "block_key": "parked", "prose_md": prose_p,
         "header_cols": hdr_p, "col_order": PARKED_COLS, "renders_closed": False},
        {"seq": 2, "block_key": "retired", "prose_md": prose_r,
         "header_cols": hdr_r, "col_order": RETIRED_COLS, "renders_closed": True},
        {"seq": 3, "block_key": None, "prose_md": prose_tail,
         "header_cols": None, "col_order": None, "renders_closed": False},
    ]
    return blocks, rows_p, rows_r


def map_row(cells, col_order, review, where):
    """Cells -> (fields, extra_cells, row_col_order|None). Width mismatch is
    preserved positionally and reported, never guessed at (the ORDER 31 rule)."""
    order = list(col_order)
    row_order = None
    if len(cells) != len(order):
        review.append(f"{where}: {len(cells)} cells against a {len(order)}-column "
                      f"header — preserved positionally as extra cells")
        if len(cells) > len(order):
            order = order + [f"extra:col{i}" for i in range(len(order), len(cells))]
            row_order = order
        else:
            order = order[:len(cells)]
            row_order = order
    fields, extra = {}, {}
    for name, val in zip(order, cells):
        if name.startswith("extra:"):
            extra[name.split(":", 1)[1]] = val
        else:
            fields[name] = val
    return fields, extra, row_order


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    url = os.environ.get("CARR_IMPORT_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set CARR_IMPORT_DB_URL (or DATABASE_URL)")
    if not SRC.exists():
        sys.exit(f"REFUSING: {SRC} not found.")

    blocks, rows_p, rows_r = parse(SRC)
    review, report = [], []
    inserted = skipped = 0

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select id, slug from actor")
        actors = {s: i for i, s in cur.fetchall()}
        sys_id, joe = actors["system"], actors["joe"]

        block_ids = {}
        for b in blocks:
            cur.execute("select id from loop_block where rel_path=%s and seq=%s",
                        (REL_PATH, b["seq"]))
            hit = cur.fetchone()
            if hit:
                block_ids[b["block_key"]] = hit[0]
                continue
            cur.execute(
                "insert into loop_block (rel_path, kind, seq, block_key, prose_md, "
                "header_cols, col_order, renders_closed, created_by, updated_by) "
                "values (%s,'idea',%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (REL_PATH, b["seq"], b["block_key"], b["prose_md"],
                 b["header_cols"], b["col_order"], b["renders_closed"], sys_id, sys_id))
            block_ids[b["block_key"]] = cur.fetchone()[0]

        def load(rows, key, cols, closed):
            nonlocal inserted, skipped
            for n, cells in enumerate(rows, start=1):
                fields, extra, row_order = map_row(
                    cells, cols, review, f"{key} row {n}")
                number = fields.get("number") or f"?{n}"
                ext_key = f"{key}#{number}"

                cur.execute("select entity_id from record_source "
                            "where source_system=%s and external_key=%s",
                            (SOURCE_SYSTEM, ext_key))
                if cur.fetchone():
                    skipped += 1
                    continue

                outcome = fields.get("outcome")
                if closed and not outcome:
                    sys.exit(f"REFUSING: retired idea {number} has no 'Why retired' "
                             "text; a retirement with no reason is unstorable.")
                cur.execute(
                    "insert into loop_item (kind, number, block_id, render_seq, col_order, "
                    "title, body, owner, since_text, closed_text, outcome, extra_cells, "
                    "marker, status, close_outcome, closed_at, tier, personal_to, "
                    "created_by, updated_by) "
                    "values ('idea',%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s::jsonb,'none',"
                    "%s,%s,%s,'personal',%s,%s,%s) returning id",
                    (number, block_ids[key], n, row_order,
                     fields.get("title"), fields.get("body"),
                     fields.get("since_text"), fields.get("closed_text"), outcome,
                     json.dumps(extra),
                     "done" if closed else "open",
                     outcome if closed else None,
                     datetime.now(timezone.utc) if closed else None,
                     joe, sys_id, sys_id))
                lid = cur.fetchone()[0]
                cur.execute(
                    "insert into record_source (entity_type, entity_id, source_system, "
                    "external_key, imported_at) values ('loop_item', %s, %s, %s, now())",
                    (lid, SOURCE_SYSTEM, ext_key))
                inserted += 1
                report.append(f"- [{key}] #{number} — {(fields.get('title') or '')[:80]}")

                st = extra.get("status", "")
                if st.upper().startswith("ADOPTED"):
                    review.append(f"parked #{number} carries Status {st!r} — by the bank's "
                                  "own rule 5 this should already be Retired; imported "
                                  "as it sits, Joe rules")

        load(rows_p, "parked", PARKED_COLS, closed=False)
        load(rows_r, "retired", RETIRED_COLS, closed=True)

        if a.dry_run:
            conn.rollback()
        else:
            conn.commit()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(__file__).resolve().parent.parent / "out" / f"idea-bank-import-{stamp}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        f"# idea-bank import ({'DRY RUN' if a.dry_run else 'APPLIED'}) {stamp}\n\n"
        f"- parked rows: {len(rows_p)}\n- retired rows: {len(rows_r)}\n"
        f"- imported: {inserted}\n- skipped (idempotent): {skipped}\n"
        f"- review items: {len(review)}\n\n## Rows\n" + "\n".join(report) +
        "\n\n## Review list\n" + ("\n".join(f"- {r}" for r in review) or "- (none)") + "\n")

    print(f"parked={len(rows_p)} retired={len(rows_r)} imported={inserted} "
          f"skipped={skipped} review={len(review)}")
    print(f"report: {out}")


if __name__ == "__main__":
    main()
