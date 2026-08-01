"""Import decision-history.md + its archive into decision EVENT rows (ORDER 40 step 1).

  00_Context/decision-history.md          -> event, subject_type 'decision'
  00_Context/decision-history-archive.md  -> same

WHY AN IMPORTER AND NOT A VERB (R-40a: "verbs are for the present, importers are
for the past"). No verb writes a free-standing event — writeEvent is an internal
helper in mcp-server/src/tools.js and every caller pins the subject to a record it
just wrote. Even if one existed, it would stamp occurred_at=now() and
actor_id=the caller, destroying the two things this import exists to preserve:
the original dates and the entries' own authorship. Same line ORDER 39 and
import_wave1 draw.

WHY event AND NOT activity. R-40a rules decisions are event rows. The schema
agrees: `event` is the only table carrying the human_quote / agent_rationale /
cause separation this import must preserve. `activity` has the titled long-text
shape but none of those three columns. (ORDER 36's 0028 reached the same read
from the other side and put analysis on activity, which has no quote/rationale
pair to lose.)

NO VOCABULARY WAS EXTENDED, and that is a finding, not an omission:
  * event.subject_type is text with NO CHECK and NO FK — 0001 carries a comment
    listing examples and nothing else. 'decision' is a new value in an open
    column, not a vocabulary extension.
  * event.verb is likewise unconstrained. Rows are written verb='log-decision' —
    the verb a future present-tense verb would use — so imported rows and
    verb-written rows are one shape and one render.
  * event.cause IS constrained, and already carries 'import_migration', which is
    what a bulk legacy import is. Widening a constrained vocabulary to say what
    record_source.source_system already says precisely would be the opposite of
    minimal. Flagged for ratification in the report.
Migration 0031 asserts all three of these premises in its guard block, so a later
migration that quietly constrains them fails loudly instead of corrupting this
importer silently.

SUBJECT IDENTITY WITHOUT A NEW TABLE. event.subject_id is NOT NULL and R-40a says
no new table. subject_id is therefore a DETERMINISTIC uuid5 of the entry's
external_key: stable across reruns, unique per entry, and it makes the decision
its own subject rather than borrowing some unrelated record's identity. Nothing
dereferences it — no FK exists on event.subject_id by design (it is polymorphic).

AUTHORSHIP, stated plainly because it is the one place this import generalizes.
41 of 192 entries carry a [stamp:]. Every one of those 41 names Joe's brain (or a
Fable seat operating inside it); ZERO name Dell's brain, which is what you would
expect of a file that lives in 00_Context and has never been in the DNA share.
Entries are therefore authored 'joe', and each entry's stamp — when it has one —
is preserved VERBATIM in new_value.provenance so the specific seat is never lost.
This is a property of the file, not a guess about an entry; it is flagged for
ratification rather than assumed silently. If a stamp ever names Dell, the parser
honours it (see STAMP_ACTOR).

QUOTES: R-40b, never fabricated. A human_quote is written ONLY where the source
explicitly attributes words to Joe — `Joe: "..."`, `Joe's ruling: "..."` and the
handful of sibling forms in ATTRIBUTED_QUOTE. Everything else imports quote-absent
with new_value.provenance saying so. An entry's full body always lands in
agent_rationale verbatim, so no text is lost either way.

DATES: never guessed. ISO date at the head of the heading, else a spelled date
inside it ("July 2-3, 2026" -> the range's start, which is when the thing began).
An entry whose date cannot be READ is not imported; it goes to the review list.

IDEMPOTENT by record_source (source_system, external_key) — the import_wave1 /
ORDER 39 pattern and the same UNIQUE constraint. A rerun writes 0 rows.

Usage:
  CARR_IMPORT_DB_URL=... .venv/bin/python -m pipelines.import_decision_history [--dry-run]
Writes out/decision-history-import-<stamp>.md.
"""

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg

SOURCE_SYSTEM = "decision-history"
VAULT = Path(os.environ.get(
    "CARR_VAULT",
    os.path.expanduser("~/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/"
                       "My Drive/CARR AI")))

SOURCES = [
    ("decision-history", VAULT / "00_Context" / "decision-history.md"),
    ("decision-history-archive", VAULT / "00_Context" / "decision-history-archive.md"),
]

# Stable namespace for decision subject ids. Fixed literal, never regenerated:
# changing it would re-key every decision and break idempotency.
NS_DECISION = uuid.UUID("6f2b1d4a-9c33-4e58-b7a1-0d5e8c214f70")

MONTHS = ("january february march april may june july august september october "
          "november december").split()

# A date at the very start of the heading, the dominant form.
ISO_HEAD = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")
# "July 2-3, 2026" / "July 4, 2026" / "added July 2, 2026" — anywhere in the heading.
SPELLED = re.compile(
    r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:\s*[-–]\s*\d{1,2})?,?\s+(\d{4})\b", re.I)

STAMP = re.compile(r"\[stamp:([^\]]*)\]", re.I)

# Only these forms count as Joe's verbatim words. Deliberately narrow: a quoted
# phrase with no attribution is somebody else's line, a file name, or emphasis.
ATTRIBUTED_QUOTE = re.compile(
    r"Joe(?:'s)?\s*(?:ruling|directive|own words|words|call|order|verdict|correction)?\s*:?\s*"
    r"[\"“]([^\"”]{5,400})[\"”]", re.I)


def slugify(s, maxlen=80):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:maxlen].strip("-")


def parse_date(heading):
    """-> 'YYYY-MM-DD' or None. Reads; never guesses."""
    m = ISO_HEAD.match(heading)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = SPELLED.search(heading)
    if m:
        mon = MONTHS.index(m.group(1).lower()) + 1
        return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(2)):02d}"
    return None


def stamp_actor(stamp_text):
    """A stamp naming Dell's brain is honoured. Nothing else overrides 'joe'."""
    if stamp_text and re.search(r"dell'?s brain|,\s*dell\b", stamp_text, re.I):
        return "dell"
    return "joe"


def split_entries(path, source_name):
    """Split a decision-history file into its `## ` entries, in file order."""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    out = []
    for n, i in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        heading = lines[i][3:].strip()
        body = "\n".join(lines[i + 1:end]).strip()
        out.append({
            "source": source_name,
            "heading": heading,
            "body": body,
            "seq": n + 1,
        })
    return out


def build(entry):
    """Entry dict -> the row we would write, or a review-list reason."""
    heading, body = entry["heading"], entry["body"]

    entry_date = parse_date(heading)
    if not entry_date:
        return None, "date not readable from the heading"

    # Title = the heading minus its leading date clause, so the render can show a
    # title without repeating the date. Falls back to the whole heading.
    title = re.sub(r"^\s*\d{4}-\d{2}-\d{2}\s*(\([^)]*\))?\s*[—\-–]?\s*", "", heading).strip()
    title = title or heading

    sm = STAMP.search(body)
    stamp_text = sm.group(1).strip() if sm else None
    author = stamp_actor(stamp_text)

    qm = ATTRIBUTED_QUOTE.search(heading) or ATTRIBUTED_QUOTE.search(body)
    quote = qm.group(1).strip() if qm else None

    if stamp_text:
        provenance = (f"imported from {entry['source']}.md, "
                      f"stamp: {stamp_text}")
    else:
        provenance = (f"imported from {entry['source']}.md, entry {entry['seq']} "
                      f"({entry_date}), no [stamp:] recorded in the source")
    if not quote:
        provenance += "; no verbatim quote recorded"

    ext_key = f"{entry['source']}#{entry_date}-{slugify(title) or entry['seq']}"
    return {
        "external_key": ext_key,
        "subject_id": str(uuid.uuid5(NS_DECISION, ext_key)),
        "occurred_on": entry_date,
        "author": author,
        "title": title,
        "human_quote": quote,
        "agent_rationale": body,
        "new_value": {
            "title": title,
            "heading": heading,
            "source_file": entry["source"],
            "provenance": provenance,
            "quote_absent": quote is None,
            "stamp": stamp_text,
        },
    }, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="parse, report, write nothing (transaction rolled back)")
    a = ap.parse_args()

    url = os.environ.get("CARR_IMPORT_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set CARR_IMPORT_DB_URL (or DATABASE_URL)")

    rows, review = [], []
    for name, path in SOURCES:
        if not path.exists():
            sys.exit(f"REFUSING: {path} not found. Nothing imported.")
        for e in split_entries(path, name):
            built, why = build(e)
            if built:
                rows.append(built)
            else:
                review.append({"source": name, "seq": e["seq"],
                               "heading": e["heading"][:120], "why": why})

    # Collisions would silently drop an entry. Refuse instead.
    keys = [r["external_key"] for r in rows]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        sys.exit(f"REFUSING: {len(dupes)} duplicate external_key(s), e.g. "
                 f"{sorted(dupes)[:3]}. Nothing imported.")

    inserted = skipped = 0
    report = []

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select id, slug from actor")
        actors = {slug: aid for aid, slug in cur.fetchall()}

        for r in rows:
            author_id = actors.get(r["author"])
            if not author_id:
                sys.exit(f"REFUSING: author {r['author']!r} is not an actor.")

            cur.execute("select entity_id from record_source "
                        "where source_system = %s and external_key = %s",
                        (SOURCE_SYSTEM, r["external_key"]))
            if cur.fetchone():
                skipped += 1
                report.append(f"- SKIP (already imported) `{r['external_key']}`")
                continue

            cur.execute(
                "insert into event (occurred_at, actor_id, verb, subject_type, subject_id, "
                "new_value, cause, human_quote, agent_rationale) "
                "values (%s::date, %s, 'log-decision', 'decision', %s, %s::jsonb, "
                "'import_migration', %s, %s) returning id",
                (r["occurred_on"], author_id, r["subject_id"],
                 json.dumps(r["new_value"]), r["human_quote"], r["agent_rationale"]))
            ev_id = cur.fetchone()[0]

            cur.execute(
                "insert into record_source (entity_type, entity_id, source_system, "
                "external_key, imported_at) values ('event', %s, %s, %s, now())",
                (ev_id, SOURCE_SYSTEM, r["external_key"]))
            inserted += 1
            q = "quote" if r["human_quote"] else "no-quote"
            report.append(f"- {r['occurred_on']} [{q}] `{r['external_key']}`")

        if a.dry_run:
            conn.rollback()
        else:
            conn.commit()

    quoted = sum(1 for r in rows if r["human_quote"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(__file__).resolve().parent.parent / "out" / f"decision-history-import-{stamp}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        f"# decision-history import ({'DRY RUN' if a.dry_run else 'APPLIED'}) {stamp}\n\n"
        f"- entries parsed: {len(rows) + len(review)}\n"
        f"- imported: {inserted}\n- skipped (idempotent): {skipped}\n"
        f"- review list (not imported): {len(review)}\n"
        f"- with a verbatim Joe quote: {quoted} / {len(rows)}\n\n"
        "## Rows\n" + "\n".join(report) +
        "\n\n## Review list\n" +
        ("\n".join(f"- {r['source']} entry {r['seq']}: {r['why']} — {r['heading']}"
                   for r in review) or "- (none)") + "\n")

    print(f"parsed={len(rows) + len(review)} imported={inserted} skipped={skipped} "
          f"review={len(review)} quoted={quoted}")
    print(f"report: {out}")


if __name__ == "__main__":
    main()
