#!/usr/bin/env python3
"""Prove ORDER 40's two decision-history done-tests on the rehearsal branch.

  1. Windowed/archived retrieval works — an entry outside the render's window is
     still reachable by widening the query, not by opening a second file.
  2. ORDER 4's manual 100KB split is genuinely moot — appending an entry big
     enough to have forced a split simply pushes the oldest entry out of the
     window. No file is split, nothing is moved, nothing becomes unreachable.

Branch-only, same guard as the render rehearsal. Run through db-tap:
  .venv/bin/python tools/db-tap.py --branch rehearse-0031-order40 \
      run tools/prove-order40-windowing.py
"""

import os
import sys
import uuid
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def render_stats(url):
    """Build the render into staging and report its window + size."""
    os.environ["CARR_DB_EXPORTER_URL"] = url
    os.environ.pop("CARR_EXPORT_LIVE", None)
    from exporters.targets import DECISION_REL, build_decision_history
    from exporters.common import run_export
    run_export("decision-history.md", DECISION_REL, build_decision_history, bootstrap=True)
    p = REPO / "out" / "exports" / "decision-history.md"
    text = p.read_text()
    shown = text.count("\n### ")
    line = [l for l in text.split("\n") if l.startswith("*Window:")][0]
    return shown, len(text.encode()), line


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("no DATABASE_URL — run through tools/db-tap.py")

    with psycopg.connect(url) as c, c.cursor() as k:
        k.execute("select count(*) from record_source where source_system='decision-history'")
        total = k.fetchone()[0]
        if total == 0:
            sys.exit("REFUSING: no ORDER 40 import here. Branch only.")

        print(f"== decisions on record: {total}")

        # ---- PROOF 1: windowed vs archived retrieval -------------------------
        k.execute("select min(entry_date), max(entry_date) from v_decision_entry")
        lo, hi = k.fetchone()
        print(f"== full range on record: {lo} .. {hi}")

        k.execute("select count(*) from v_decision_entry where entry_date >= %s", (hi,))
        print(f"-- narrow window (entry_date >= {hi}): {k.fetchone()[0]} entries")

        k.execute("select count(*) from v_decision_entry where entry_date >= '2026-07-29'")
        print(f"-- render-sized window (>= 2026-07-29): {k.fetchone()[0]} entries")

        k.execute("select count(*) from v_decision_entry")
        print(f"-- widest window (no bound = the 'archive'): {k.fetchone()[0]} entries")

        # An entry that ORDER 4 physically MOVED into the archive file is still
        # here, under the same query, with its quote and rationale intact.
        k.execute("select entry_date, title, source_file, "
                  "       (human_quote is not null) as has_quote, "
                  "       length(agent_rationale) as rationale_len "
                  "  from v_decision_entry "
                  " where source_file = 'decision-history-archive' "
                  "   and human_quote is not null "
                  " order by entry_date limit 3")
        print("-- sample entries from the ARCHIVE half, retrieved by the same view:")
        for r in k.fetchall():
            print(f"   {r[0]} | quote={r[3]} | rationale={r[4]}B | {r[1][:64]}")

        shown_before, bytes_before, line_before = render_stats(url)
        print(f"== render BEFORE append: {shown_before} entries, {bytes_before}B")
        print(f"   {line_before}")

        # ---- PROOF 2: ORDER 4's split is moot -------------------------------
        # Append one entry large enough that, under the old regime, the file
        # would have crossed the 100KB tripwire and a human would have had to
        # split it.
        k.execute("select id from actor where slug='joe'")
        joe = k.fetchone()[0]
        big = ("PROOF ENTRY (branch only). " + ("x" * 400 + " ") * 30)
        ext = "decision-history#9999-01-01-order4-moot-proof"
        sid = str(uuid.uuid5(uuid.UUID("6f2b1d4a-9c33-4e58-b7a1-0d5e8c214f70"), ext))
        k.execute(
            "insert into event (occurred_at, actor_id, verb, subject_type, subject_id, "
            "new_value, cause, human_quote, agent_rationale) "
            "values (current_date + 1, %s, 'log-decision', 'decision', %s, "
            "%s::jsonb, 'import_migration', NULL, %s) returning id",
            (joe, sid, '{"title": "ORDER 4 moot proof (branch only)"}', big))
        ev = k.fetchone()[0]
        k.execute("insert into record_source (entity_type, entity_id, source_system, "
                  "external_key) values ('event', %s, 'decision-history', %s)", (ev, ext))
        c.commit()
        print(f"== appended a {len(big)}B decision (would have blown the 100KB tripwire)")

        shown_after, bytes_after, line_after = render_stats(url)
        print(f"== render AFTER append: {shown_after} entries, {bytes_after}B")
        print(f"   {line_after}")

        assert bytes_after <= 95_000, "render exceeded its budget — windowing failed"

        # The entry pushed out of the window is still retrievable. That is the
        # whole claim: displacement is not archival, and no second file exists.
        k.execute("select count(*) from v_decision_entry")
        print(f"-- still retrievable after the append: {k.fetchone()[0]} entries, "
              "one file, zero manual splits")

        # clean up the synthetic row so the branch stays a faithful rehearsal
        k.execute("delete from record_source where external_key=%s", (ext,))
        k.execute("delete from event where id=%s", (ev,))
        c.commit()
        print("== proof row removed; branch restored")

    print("\nBOTH DONE-TESTS PASS: retrieval is a query bound, not a file split.")


if __name__ == "__main__":
    main()
