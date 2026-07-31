#!/usr/bin/env python3
"""
parse_party_links.py — ORDER 17 / amendment 4: turn `vendor.links_label` prose
into real `party_link` edges, so who-do-we-know and the reciprocity ledger stop
being questions somebody has to answer from memory.

WHY THIS IS A PIPELINE AND NOT A VERB (Fable ruling (a), 2026-07-31)
  This is BACKFILL of frozen imported text, the same class of work as
  `import_wave1.py`: one pass, direct DB as writer, a dry-run report reviewed
  BEFORE anything is written. Links created from here on go through the
  `link-parties` verb like any other record change. This script is not part of
  daily traffic and should not be rerun as a habit.

THE GRAMMAR, VERBATIM FROM THE ORDER (ruling (b)) — nothing beyond it
  * A token matching  V-[A-Z]{3}-\\d+ | T-\\d+ | C-\\d+ | L-\\d+  resolves
    MECHANICALLY to its party via the ref (T-### is a vendor ref too — the
    'Target (not yet met)' tier, 41 of them live).
  * A bare personal/org name resolves ONLY on exact unique match against
    `party.name`.
  * Anything else — no match, multiple matches, prose that isn't a link — is
    AMBIGUOUS, and ambiguous is NEVER guessed (ruling (c)).

WHAT AMBIGUOUS COSTS (ruling (c))
  One `event` row per vendor carrying the unparsed remainder, plus the full list
  in the dry-run report for a human review pass. The nine-orphans pattern from
  amendment 2 is the precedent: the record self-flags, a human decides.

EDGE SHAPE (ruling (d))
  party_link(from_party = the vendor's party, to_party = the resolved party,
  kind = 'intro' | 'referral' | 'knows', note = the original label VERBATIM).
  Default kind is 'knows' where the label names a party without a relation verb.
  Idempotent on (from_party, to_party, kind): a rerun writes zero rows.

WHAT THIS NEVER DOES (ruling (e) + the order's stop rules)
  `links_label` is NOT cleared, rewritten, or deleted. It is a shim with a death
  sentence per amendment 5 and it dies at the vendors-file repoint, not here.

Usage:
  DATABASE_URL=... .venv/bin/python pipelines/parse_party_links.py           # dry run (default)
  DATABASE_URL=... .venv/bin/python pipelines/parse_party_links.py --apply   # write
Writes out/party-link-parse-<stamp>.md either way. The dry run touches nothing.
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "out"

# ── the grammar, ruling (b) ──────────────────────────────────────────────────
REF_RE = re.compile(r"\b(?:V-[A-Z]{3}-\d+|T-\d+|C-\d+|L-\d+)\b")
# Items inside one label. ' / ' only with surrounding whitespace, so a slash
# inside a name ("Tubbs / George DVM" is a real party name) cannot split it by
# accident when it is written without spaces.
ITEM_SPLIT_RE = re.compile(r"\s*(?:;|·|\||\n|(?<=\s)/(?=\s))\s*")
PAREN_RE = re.compile(r"\([^)]*\)")
# Prose after a dash separator: 'Chris Kelly (V-CPA-006) — offered intro'
DASH_CUT_RE = re.compile(r"\s+[—–-]\s+.*$")

PROVENANCE_SOURCE = "links_label_parse"   # party_link.source is NOT NULL and is
                                          # a provenance column; this says exactly
                                          # where the row came from and collides
                                          # with neither 'stated' (verb-written)
                                          # nor 'import' (wave-1 records).
REVIEW_VERB = "amendment-4-review"


def kind_for(text):
    """Ruling (d): relation verb decides; 'knows' is the default."""
    t = text.lower()
    if "intro" in t:
        return "intro"
    if "refer" in t:
        return "referral"
    return "knows"


def name_candidate(item):
    """The bare-name form of an item: parentheticals and post-dash prose removed.

    Deliberately conservative — it strips decoration, it never rewrites a name.
    """
    s = PAREN_RE.sub(" ", item)
    s = DASH_CUT_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(" .,:")
    return s


def load(conn):
    """Everything the parse needs, read once."""
    cur = conn.execute("""
        select v.id, v.vendor_ref, p.id, p.name, coalesce(org.name,''), v.links_label
          from vendor v
          join party p on p.id = v.party_id
          left join party org on org.id = p.org_id
         where coalesce(trim(v.links_label),'') <> ''
         order by v.vendor_ref
    """)
    vendors = cur.fetchall()

    refs = {}          # ref -> (party_id, display, kind_of_record)
    for r, pid in conn.execute(
            "select vendor_ref, party_id from vendor where vendor_ref is not null").fetchall():
        refs[r] = (pid, "vendor")
    for r, pid in conn.execute(
            "select roster_ref, party_id from client where roster_ref is not null").fetchall():
        refs.setdefault(r, (pid, "client"))
    for r, pid in conn.execute(
            "select registry_ref, party_id from lead where registry_ref is not null").fetchall():
        refs.setdefault(r, (pid, "lead"))

    names = {}         # lower(name) -> [party_id, ...]
    for pid, nm in conn.execute("select id, name from party").fetchall():
        names.setdefault(nm.strip().lower(), []).append(pid)

    party_name = {pid: nm for pid, nm in conn.execute("select id, name from party").fetchall()}

    existing = set()
    for f, t, k in conn.execute("select from_party, to_party, kind from party_link").fetchall():
        existing.add((f, t, k))

    reviewed = {r[0] for r in conn.execute(
        "select subject_id from event where verb = %s and subject_type = 'vendor'",
        (REVIEW_VERB,)).fetchall()}

    return vendors, refs, names, party_name, existing, reviewed


def parse(vendors, refs, names, party_name):
    """Returns (edges, ambiguous). Pure — no DB, no writes."""
    edges = []          # dicts: from_party, to_party, kind, note, how, vendor_ref, item, target
    ambiguous = []      # dicts: vendor_ref, vendor_name, item, reason, label
    seen = set()        # in-run dedup on (from, to, kind)

    for vid, vref, vparty, vname, vorg, label in vendors:
        for raw_item in ITEM_SPLIT_RE.split(label):
            item = raw_item.strip()
            if not item:
                continue
            k = kind_for(item)
            found = REF_RE.findall(item)

            if found:
                for ref in found:
                    hit = refs.get(ref)
                    if not hit:
                        ambiguous.append(dict(
                            vendor_ref=vref, vendor_name=vname, item=item, label=label,
                            reason=f"ref {ref} resolves to no record"))
                        continue
                    tparty, rec_kind = hit
                    if tparty == vparty:
                        ambiguous.append(dict(
                            vendor_ref=vref, vendor_name=vname, item=item, label=label,
                            reason=f"ref {ref} resolves to this vendor's own party (self-link)"))
                        continue
                    key = (vparty, tparty, k)
                    if key in seen:
                        continue
                    seen.add(key)
                    edges.append(dict(
                        from_party=vparty, to_party=tparty, kind=k, note=label,
                        how="mechanical", vendor_ref=vref, vendor_name=vname, item=item,
                        target=f"{ref} {party_name.get(tparty,'?')}"))
                continue

            cand = name_candidate(item)
            if not cand:
                ambiguous.append(dict(
                    vendor_ref=vref, vendor_name=vname, item=item, label=label,
                    reason="no ref token and nothing name-shaped left after decoration"))
                continue
            hits = names.get(cand.lower(), [])
            if len(hits) == 1:
                tparty = hits[0]
                if tparty == vparty:
                    ambiguous.append(dict(
                        vendor_ref=vref, vendor_name=vname, item=item, label=label,
                        reason=f"'{cand}' is this vendor's own party (self-link)"))
                    continue
                key = (vparty, tparty, k)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(dict(
                    from_party=vparty, to_party=tparty, kind=k, note=label,
                    how="name-matched", vendor_ref=vref, vendor_name=vname, item=item,
                    target=party_name.get(tparty, "?")))
            elif len(hits) == 0:
                ambiguous.append(dict(
                    vendor_ref=vref, vendor_name=vname, item=item, label=label,
                    reason=f"'{cand}' matches no party name exactly"))
            else:
                ambiguous.append(dict(
                    vendor_ref=vref, vendor_name=vname, item=item, label=label,
                    reason=f"'{cand}' matches {len(hits)} parties — ambiguous by definition"))

    return edges, ambiguous


def write_report(path, vendors, edges, ambiguous, existing, reviewed, applied, wrote, events):
    mech = [e for e in edges if e["how"] == "mechanical"]
    namd = [e for e in edges if e["how"] == "name-matched"]
    dupes = [e for e in edges if (e["from_party"], e["to_party"], e["kind"]) in existing]
    L = []
    A = L.append
    A(f"# party_link parse — ORDER 17 / amendment 4 ({'APPLY' if applied else 'DRY RUN'})")
    A("")
    A(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
      f"{'wrote to the database' if applied else 'nothing written'}")
    A("")
    A("## Counts")
    A("")
    A(f"- vendors carrying a non-blank `links_label`: **{len(vendors)}**")
    A(f"- **mechanical edges** (ref token resolved): **{len(mech)}**")
    A(f"- **name-matched edges** (exact unique party name): **{len(namd)}**")
    A(f"- **ambiguous items** (never guessed): **{len(ambiguous)}**")
    A(f"- parseable total (edges the DB should hold after apply): **{len(edges)}**")
    A(f"- of those, already present before this run: **{len(dupes)}**")
    A(f"- rows actually inserted this run: **{wrote}**")
    A(f"- review `event` rows written this run: **{events}** "
      f"(vendors already carrying one: {len([v for v in vendors if v[0] in reviewed])})")
    A("")
    A("## Edges")
    A("")
    A("| vendor | kind | → target | how | item |")
    A("|---|---|---|---|---|")
    for e in edges:
        A(f"| {e['vendor_ref']} {e['vendor_name']} | `{e['kind']}` | {e['target']} | "
          f"{e['how']} | {e['item']} |")
    A("")
    A("## Ambiguous — Joe's review pass (nothing was written for these)")
    A("")
    if not ambiguous:
        A("None.")
    else:
        A("| vendor | unparsed item | why |")
        A("|---|---|---|")
        for a in ambiguous:
            A(f"| {a['vendor_ref']} {a['vendor_name']} | {a['item']} | {a['reason']} |")
    A("")
    path.write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the edges and the review events (default is a dry run)")
    a = ap.parse_args()

    url = os.environ.get("DATABASE_URL") or os.environ.get("CARR_IMPORT_DB_URL")
    if not url:
        print("DATABASE_URL (or CARR_IMPORT_DB_URL) is not set — nothing attempted.",
              file=sys.stderr)
        return 78

    OUT.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = OUT / f"party-link-parse-{stamp}.md"

    with psycopg.connect(url) as conn:
        vendors, refs, names, party_name, existing, reviewed = load(conn)
        edges, ambiguous = parse(vendors, refs, names, party_name)

        wrote = 0
        events = 0
        if a.apply:
            actor = conn.execute("select id from actor where slug='system'").fetchone()[0]
            for e in edges:
                key = (e["from_party"], e["to_party"], e["kind"])
                if key in existing:
                    continue
                conn.execute(
                    "insert into party_link (from_party, to_party, kind, note, source, created_by) "
                    "values (%s,%s,%s,%s,%s,%s)",
                    (e["from_party"], e["to_party"], e["kind"], e["note"],
                     PROVENANCE_SOURCE, actor))
                existing.add(key)
                wrote += 1

            by_vendor = {}
            for item in ambiguous:
                by_vendor.setdefault(item["vendor_ref"], []).append(item)
            vid_for = {v[1]: (v[0], v[5]) for v in vendors}
            for vref, items in by_vendor.items():
                vid, label = vid_for[vref]
                if vid in reviewed:
                    continue
                remainder = " · ".join(f"{i['item']}  [{i['reason']}]" for i in items)
                conn.execute(
                    "insert into event (occurred_at, actor_id, verb, subject_type, subject_id, "
                    "field, old_value, cause, agent_rationale) "
                    "values (now(), %s, %s, 'vendor', %s, 'links_label', to_jsonb(%s::text), "
                    "'import_migration', %s)",
                    (actor, REVIEW_VERB, vid, label,
                     "amendment-4 review: links_label text did not resolve under the "
                     "amendment-4 grammar and was NOT guessed — " + remainder +
                     ". Resolve by naming the party (then use link-parties), or confirm "
                     "the text is not a link."))
                reviewed.add(vid)
                events += 1
            conn.commit()

        write_report(report_path, vendors, edges, ambiguous, existing, reviewed,
                     a.apply, wrote, events)

    mech = sum(1 for e in edges if e["how"] == "mechanical")
    namd = sum(1 for e in edges if e["how"] == "name-matched")
    print(f"{'APPLIED' if a.apply else 'DRY RUN'} — vendors with labels {len(vendors)} · "
          f"mechanical {mech} · name-matched {namd} · ambiguous {len(ambiguous)} · "
          f"inserted {wrote} · review events {events}")
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
