"""Import the two in-scope markdown ledgers into activity rows (ORDER 39 steps 2-3).

  DNA/Network/hunt-ledger.md            -> activity, kind note, one row per subject touched
  DNA/Network/deals.md (Reciprocity)    -> activity, kind note, on the vendor the entry is about

WHY THIS IS AN IMPORTER AND NOT A VERB CALL. ORDER 39 step 2 requires "date/author
preserved from the ledger's existing stamps". `log-activity` cannot do that: it is
the only insert path into `activity` (mcp-server/src/tools.js) and it hardcodes
`source:'stated'` with `actor_id` = the authenticated caller, and identity.js will
only ever issue `joe` or `dell` to a verb. Writing a 2026-07-13 ledger entry through
it would stamp it 'stated' — the same word the DB uses for "a human said this to me
just now" — and re-attribute it to whoever ran the import. Production already
distinguishes the two: 63 activity rows carry source='import' and one of them
(V-BNK-013) is authored `dell` while a sibling on the same day is authored `joe`.
That is the ORDER 31 / import_wave1 pattern, and this importer follows it.
Ratified by the Fable supervisor seat 2026-08-01 (lane (b)).

WHAT IS IN SCOPE, AND WHY IT IS ONLY TWO ENTRIES. The two ledgers hold nine entries
between them. Seven are parked or review-listed and are NOT touched here, because
every one of them fails a stop rule the order states:

  hunt-ledger H2  Hunt #1, "run by the Monday brief" — that is the `automation`
                  actor, which no verb and no ruling has made writable. Author
                  cannot be preserved, so it is not imported. Parked.
  hunt-ledger H3  "Honest note on hunt #1" — no date stamp, no author stamp, and
                  no subject ref to attach to. Review list.
  hunt-ledger H4  "Hunt #2 upgrade" — dated, author unstamped, no subject ref.
                  Review list.
  deals R2/R3     Nilesh Patel / Jon Shaw "plugged into N deals" — no date, no
                  author, and they are DERIVED COUNTS: deals.md's own line 120
                  says of exactly these "don't hand-maintain counts, derive them
                  on demand". Importing one would fossilize a number the file
                  calls derived. Review list.
  deals R4/R5     Nate @ EOS / Trey @ Lazzarri — each is in the vendor book TWICE,
                  never merged (V-MKT-001/V-MSC-024 and V-GC-001/V-GC-013). The
                  lone-name rule bars picking one. Parked pending Joe's merge.

Nothing is lost by leaving them: neither .md file is being retired (11 live writer
sites still point at them), and both are frozen at
CARR AI/Archive/snapshots/2026-08-01-{hunt-ledger,deals-reciprocity}-freeze.zip.

WHAT R1 IMPORTS, AND WHAT IT DELIBERATELY DOES NOT. Step 3 says import only the
portion NOT already covered by ORDERS 32/34's edges. The cross-reference was run
row by row against all 31 production edges: V-SUP-051 -> C-155 and V-SUP-051 ->
L-208 both already exist, so the REFERRAL RELATIONSHIP is edge-covered and is not
re-imported here. What no edge carries is the commercial arrangement — the Provide
1% lender referral credit, standing, on every deal he brings. That is what this row
is, and its detail says so, so a reader never mistakes this row for the whole fact.

IDEMPOTENT by record_source (source_system, external_key), the import_wave1 pattern
and the same UNIQUE constraint. A rerun writes 0 rows. The external key is
"<ledger>#<entry>:<subject ref>" — stable, human-legible, and unique per row.

AUTHOR vs IMPORTER. activity.actor_id is the ENTRY'S author (preserved from the
ledger stamp). The import event's actor is `system`, because the import is the
system's act, not Joe's. import_wave1 draws the same line.

Usage:
  CARR_IMPORT_DB_URL=... .venv/bin/python -m pipelines.import_md_ledgers [--dry-run]
Writes out/md-ledger-import-<stamp>.md.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import psycopg

SOURCE_SYSTEM = "md-ledger"

# ---------------------------------------------------------------------------
# The entries, declared as data so the import is auditable against the frozen
# originals rather than buried in parsing logic. Two entries, six rows.
#
# `text` is the entry's own words, carried verbatim from the frozen file so the
# record does not paraphrase the ledger. `summary` is a one-line handle; the
# ledger's full text lands in detail.
# ---------------------------------------------------------------------------

HUNT_H1_TEXT = (
    "HUNT #2 — QUEUED, aim set 2026-07-13 (Joe's directive). The two zero-coverage "
    "categories, both of which are LEAD SOURCES rather than referral partners: "
    "(a) hospital physician liaisons — Ascension, Baptist, HCA Florida West, Santa Rosa "
    "Medical Center. Paid to know which doctors are moving, unhappy, or going independent. "
    "Zero of them in 284 rows. (b) practice-management / MSO firms — four exist and all four "
    "are stalled with no touch date (V-MSC-017 Zane Dash, V-CPA-019 Steve Spear, "
    "V-BRK-015 Julie Canfield-Buddin, T-022 Blake Hatcher). These firms learn about a new "
    "independent practice before the landlord does. Why now: the Brown lead (L-165) exposed "
    "it — an interventional cardiologist filed his own LLC and the network had no one within "
    "reach of him, because we had hunted categories that serve buildings and never categories "
    "that serve doctors leaving employment. The doctrine, worth keeping: the network's job is "
    "to catch intent BEFORE it touches a public record — that is the one thing no dataset can "
    "do. Candidates proposed: (to run). Verdicts: (to run)."
)

R1_TEXT = (
    "Standing reciprocity arrangement with Joe Ed Jackson (V-SUP-051, Benco Dental): CARR is "
    "passing him the Provide 1% lender referral credit on this deal and on every deal he "
    "brings. Repayment in kind for his giving us L-208 / C-155 (Dr. James Allen Tyrer). "
    "Standing arrangement, Joe 2026-07-29. "
    "NOTE ON SCOPE: the referral RELATIONSHIP this repays is already carried by the intro "
    "graph (party_link V-SUP-051 -> C-155 and V-SUP-051 -> L-208, both live since ORDER 32). "
    "This row imports only the commercial arrangement, which no edge carries. It is not the "
    "whole fact on its own — read it with those two edges."
)

ENTRIES = [
    {
        "entry_id": "H1",
        "ledger": "hunt-ledger",
        "source_file": "DNA/Network/hunt-ledger.md",
        "author": "joe",           # "(Joe's directive)" — stamped in the entry itself
        "occurred_on": "2026-07-13",
        "summary": "Vendor hunt #2 QUEUED — aim set: hospital physician liaisons + "
                   "practice-management/MSO firms (both zero-coverage lead-source categories)",
        "detail": HUNT_H1_TEXT,
        # Every ref named by the entry as a subject it touches. All five resolved
        # against v_ref_index before this list was written; none is a guess.
        "subjects": ["V-MSC-017", "V-CPA-019", "V-BRK-015", "T-022", "L-165"],
    },
    {
        "entry_id": "R1",
        "ledger": "deals-reciprocity",
        "source_file": "DNA/Network/deals.md",
        "author": "joe",           # "Standing arrangement, Joe 2026-07-29"
        "occurred_on": "2026-07-29",
        "summary": "Standing reciprocity: Joe Ed Jackson gets the Provide 1% lender referral "
                   "credit on every deal he brings",
        "detail": R1_TEXT,
        "subjects": ["V-SUP-051"],
    },
]

FK = {"vendor": "vendor_id", "lead": "lead_id", "client": "client_id", "deal": "deal_id"}


def resolve(cur, ref):
    """Ref -> (subject_type, subject_id). Exact ref match only; never a name match."""
    cur.execute("select subject_type, subject_id, display_name, merged "
                "from v_ref_index where ref = %s", (ref,))
    rows = cur.fetchall()
    if not rows:
        sys.exit(f"REFUSING: ref {ref} does not resolve. Nothing imported.")
    if len(rows) > 1:
        sys.exit(f"REFUSING: ref {ref} resolves to {len(rows)} records. Nothing imported.")
    st, sid, name, merged = rows[0]
    if merged:
        sys.exit(f"REFUSING: ref {ref} ({name}) is a merged/tombstoned record. Nothing imported.")
    if st not in FK:
        sys.exit(f"REFUSING: ref {ref} has subject_type {st!r}, which activity cannot carry.")
    return st, sid, name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve, report, write nothing (transaction rolled back)")
    a = ap.parse_args()

    url = os.environ.get("CARR_IMPORT_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set CARR_IMPORT_DB_URL (or DATABASE_URL)")

    report, inserted, skipped = [], 0, 0

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select id, slug from actor")
        actors = {slug: aid for aid, slug in cur.fetchall()}
        system = actors["system"]

        for e in ENTRIES:
            author_id = actors.get(e["author"])
            if not author_id:
                sys.exit(f"REFUSING: entry {e['entry_id']} names author {e['author']!r}, "
                         "which is not an actor. Author is never guessed.")

            for ref in e["subjects"]:
                st, sid, name = resolve(cur, ref)
                ext_key = f"{e['ledger']}#{e['entry_id']}:{ref}"

                cur.execute("select entity_id from record_source "
                            "where source_system = %s and external_key = %s",
                            (SOURCE_SYSTEM, ext_key))
                if cur.fetchone():
                    skipped += 1
                    report.append(f"- SKIP (already imported) `{ext_key}` — {ref} {name}")
                    continue

                cur.execute(
                    f"insert into activity (occurred_at, actor_id, kind, summary, detail, "
                    f"{FK[st]}, source) "
                    "values (%s::date, %s, 'note', %s, %s, %s, 'import') returning id",
                    (e["occurred_on"], author_id, e["summary"], e["detail"], sid))
                act_id = cur.fetchone()[0]

                cur.execute(
                    "insert into record_source (entity_type, entity_id, source_system, "
                    "external_key, imported_at) values ('activity', %s, %s, %s, now())",
                    (act_id, SOURCE_SYSTEM, ext_key))

                # The import ACTION is the system's; the ENTRY's author is on the
                # activity row itself. import_wave1 draws the same line.
                cur.execute(
                    "insert into event (occurred_at, actor_id, verb, subject_type, subject_id, "
                    "new_value, cause) values (now(), %s, 'import', %s, %s, %s::jsonb, "
                    "'import_migration')",
                    (system, st, sid,
                     f'{{"activity": "{act_id}", "external_key": "{ext_key}", '
                     f'"source_file": "{e["source_file"]}", "author": "{e["author"]}"}}'))

                inserted += 1
                report.append(f"- INSERT `{ext_key}` — {ref} {name} · {st} · "
                              f"author {e['author']} · occurred {e['occurred_on']}")

        if a.dry_run:
            conn.rollback()
        else:
            conn.commit()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ["# ORDER 39 — md ledger import", "",
           f"*{'DRY RUN (rolled back)' if a.dry_run else 'APPLIED'} · {stamp}*", "",
           f"**inserted {inserted} · skipped-existing {skipped} · "
           f"entries in scope 2 of 9 (7 parked/review-listed, see module docstring)**", "",
           *report, ""]
    os.makedirs("out", exist_ok=True)
    path = os.path.join("out", f"md-ledger-import-{stamp}.md")
    with open(path, "w") as f:
        f.write("\n".join(out))
    print("\n".join(out))
    print(f"\nreport: {path}")


if __name__ == "__main__":
    main()
