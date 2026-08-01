"""Import ai-operating-notes.md into the rule store (ORDER 40 step 2).

  00_Context/ai-operating-notes.md -> rule rows, status 'proposed'

WHY AN IMPORTER AND NOT `teach` (R-40a: "verbs are for the present, importers are
for the past"). `teach` REQUIRES human_quote — and these rules do not have one.
The file carries exactly ONE verbatim Joe quote across all 94 lines (the
loops-are-records bullet, which is parked here anyway as ORDER 38's mechanism
text). Every other bullet is a compressed operative rule written by Claude across
many sessions; Joe's original words live in decision-history. Calling `teach` for
these would mean fabricating 60-odd quotes into a column whose whole meaning is
"the human's literal words", and the compiled render prints that column as a
quotation. R-40b settles it: quote-absent is fully valid, and provenance says so.

NOTHING IS ACTIVATED. Rules land status='proposed'. Activation is Joe's explicit
yes via activate-rule, one sitting, from the batch list in the report. A proposed
rule binds nobody and renders nowhere (v_compiled_rules is active-only, 0015).

TIER (R-40c), the test applied to every item: "would Dell's brain need to obey
it?" Yes -> shared scope (personal_to NULL). No -> personal_to = joe. Genuinely
arguable -> REVIEW, imported personal (the safe side: a personal rule that should
be shared is a filing error Joe can correct in one call; a shared rule that
should be personal has already reached Dell). R-40c's filing-error correction is
applied in full: this file is Joe-personal, but ~two thirds of its content
governs shared assets and lands SHARED regardless of which file it sat in.

NOT A RULE -> PARKED, never forced into a rule row (the order's own stop rule).
Fifteen items are parked in three groups, each named in PARK_REASON:
  * roster    — the COO seat roster: seven pointer-shaped seat descriptions and
                its "common discipline" preamble. A roster is not a rule.
  * mechanism — descriptions of machinery that other orders own and are actively
                converting (the loop accumulators, the session marker, the
                every-session interrupt check, the idea bank this very order is
                converting, the CLAUDE.md pointer-stub channel). These belong to
                ORDER 38's dna-protocol rewrite. Importing them would freeze a
                description that is about to become false.
  * pointer    — bullets whose entire content is "see that other file", and one
                environment-mechanics bullet (the device-bridge write path).

STATEMENT TEXT is the bullet VERBATIM with `**` markers stripped — no words
changed, no rewording. The strip is required because the compiled render already
bolds the whole statement, so nested markers would render as literal asterisks.

IDEMPOTENT by record_source (source_system, external_key). A rerun writes 0 rows.

Usage:
  CARR_IMPORT_DB_URL=... .venv/bin/python -m pipelines.import_operating_notes [--dry-run]
Writes out/operating-notes-import-<stamp>.md, which contains the activation batch
list and the tier table.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

SOURCE_SYSTEM = "ai-operating-notes"
VAULT = Path(os.environ.get(
    "CARR_VAULT",
    os.path.expanduser("~/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/"
                       "My Drive/CARR AI")))
SRC = VAULT / "00_Context" / "ai-operating-notes.md"

SHARED, PERSONAL, REVIEW, PARK = "shared", "personal", "review", "park"

# ---------------------------------------------------------------------------
# THE CLASSIFICATION TABLE. Declared as data, not inferred, so it is auditable
# line-by-line against the frozen original and so Joe can overrule any single
# row without touching parsing logic. Key = "<section-slug>#<n>", assigned by the
# parser in file order.
#
# Default for any key absent from this table is REVIEW — a new bullet added to
# the file later surfaces for a ruling instead of being silently imported.
# ---------------------------------------------------------------------------
CLASS = {
    # ---- Core conduct: Claude's behaviour. Both brains run the same DNA, so
    # these are shared unless the rule is about Joe the person.
    "core-conduct#1": SHARED,    # no sycophancy / accuracy over flattery
    "core-conduct#2": SHARED,    # complete solutions, cleanest correct way
    "core-conduct#3": PERSONAL,  # "encourage JOE's technical stretch" — his appetite, not Dell's
    "core-conduct#4": SHARED,    # double-check; SEARCH to verify technical specifics
    "core-conduct#5": SHARED,    # verify the artifact, not the summary
    "core-conduct#6": SHARED,    # assessment vs action
    "core-conduct#7": SHARED,    # check the clock, don't infer it
    "core-conduct#8": SHARED,    # a lone surname never merges records
    "core-conduct#9": SHARED,    # unknowns pass before unfamiliar deliverables
    "core-conduct#10": SHARED,   # subagents suggest / model tiering
    "core-conduct#11": SHARED,   # fan-out hygiene — the file itself says "Joe + Dell approved"
    "core-conduct#12": SHARED,   # expansion posture
    "core-conduct#13": PARK,     # session-marker / dna-protocol 24-27 — ORDER 38's world
    "core-conduct#14": SHARED,   # weekends off — the bullet says "both humans"
    "core-conduct#15": SHARED,   # doctor cadence is the message
    "core-conduct#16": SHARED,   # stale notes are not status
    "core-conduct#17": SHARED,   # never pre-qualify leads before the board

    # ---- CARR voice & business: governs client-facing work both partners do.
    "carr-voice-business#1": SHARED,
    "carr-voice-business#2": SHARED,
    "carr-voice-business#3": SHARED,
    "carr-voice-business#4": SHARED,
    "carr-voice-business#5": SHARED,
    "carr-voice-business#6": SHARED,   # HIPAA spelling + compliance double-check

    # ---- Standing mechanics
    "standing-mechanics#1": SHARED,    # the placement test
    "standing-mechanics#2": REVIEW,    # retrieval-as-code — run.sh is Joe's Mac; Dell's
                                       # side may not have the repo. Genuinely arguable.
    "standing-mechanics#3": PARK,      # open-loops pull mechanism — superseded by #4
    "standing-mechanics#4": PARK,      # "the loops are RECORDS now" — ORDER 31/38 mechanism
    "standing-mechanics#5": SHARED,    # no lead-outreach reminders on glanceable surfaces
    "standing-mechanics#6": PARK,      # idea capture — describes the file THIS order converts
    "standing-mechanics#7": SHARED,    # source-truth hierarchy on conflict
    "standing-mechanics#8": SHARED,    # correction -> rule, every draft
    "standing-mechanics#9": SHARED,    # autonomous runs: done-condition, failure path, ledger
    "standing-mechanics#10": SHARED,   # risk colors on every automation
    "standing-mechanics#11": SHARED,   # independent verification before anything ships
    "standing-mechanics#12": SHARED,   # twin readiness: tier at creation
    "standing-mechanics#13": PARK,     # pointer-only (writing-rules + skills-rule)

    # ---- File & folder standards
    "file-folder-standards#1": SHARED,
    "file-folder-standards#2": SHARED,
    "file-folder-standards#3": SHARED,
    "file-folder-standards#4": PARK,   # device-bridge write path — environment mechanics
    "file-folder-standards#5": SHARED,
    "file-folder-standards#6": SHARED,
    "file-folder-standards#7": SHARED,
    "file-folder-standards#8": SHARED,

    # ---- Channels & routing
    "channels-routing#1": PERSONAL,    # Joe's own AI-Gmail queue + his address whitelist
    "channels-routing#2": PERSONAL,    # Life AI is Joe's separate environment; Dell has none
    "channels-routing#3": PARK,        # CLAUDE.md pointer-stub channel — ORDER 38's world
    "channels-routing#4": SHARED,      # artifact tombstones

    # ---- Working with Joe: the genuinely personal section, but not uniformly.
    "working-with-joe#1": PERSONAL,    # never make Joe remember the summons
    "working-with-joe#2": SHARED,      # ux-doctrine — the bullet itself says "Joe/Dell"
    "working-with-joe#3": SHARED,      # a confirmation question is an audit trigger
    "working-with-joe#4": SHARED,      # consolidation bias
    "working-with-joe#5": PERSONAL,    # cheapest-window timing — how JOE decides
    "working-with-joe#6": PERSONAL,    # he executes his human-side steps reliably
    "working-with-joe#7": PERSONAL,    # concept coherence is HIS edge
    "working-with-joe#8": SHARED,      # promotion watch -> skills-rule

    # ---- The COO seat roster: a roster, not rules. All parked.
    "the-coo-seat-roster-system-w#1": PARK,
    "the-coo-seat-roster-system-w#2": PARK,
    "the-coo-seat-roster-system-w#3": PARK,
    "the-coo-seat-roster-system-w#4": PARK,
    "the-coo-seat-roster-system-w#5": PARK,
    "the-coo-seat-roster-system-w#6": PARK,
    "the-coo-seat-roster-system-w#7": PARK,

    # ---- Prose-only sections, carried as one rule each (see PROSE_SECTIONS).
    "division-of-labor#1": SHARED,             # Copilot vs Claude division
    "scaffolding-policy-judgment-#1": SHARED,  # merit tests for new structure
    "the-every-session-interrupt-#1": PARK,    # action-required + close-loop — ORDER 38
    "the-partner-impact-test-jul-#1": SHARED,  # tell-Dell test
}

PARK_REASON = {
    "core-conduct#13": "mechanism — session-marker / dna-protocol 24-27, ORDER 38's rewrite",
    "standing-mechanics#3": "mechanism — open-loops placement, superseded by the loop records",
    "standing-mechanics#4": "mechanism — the loop accumulators as records, ORDER 31/38",
    "standing-mechanics#6": "mechanism — describes idea-bank.md, which ORDER 40 converts",
    "standing-mechanics#13": "pointer — content is 'see writing-rules.md / skills-rule.md'",
    "file-folder-standards#4": "environment mechanics — device bridge / Drive connector write path",
    "channels-routing#3": "mechanism — the CLAUDE.md pointer-stub instructions channel",
    "the-every-session-interrupt-#1": "mechanism — action-required.md + close-loop, ORDER 38",
    **{f"the-coo-seat-roster-system-w#{i}": "roster — a seat roster of pointers, not a rule"
       for i in range(1, 8)},
}

# Sections whose content is a paragraph rather than bullets. Carried as one rule.
PROSE_SECTIONS = {
    "division-of-labor",
    "scaffolding-policy-judgment-",
    "the-every-session-interrupt-",
    "the-partner-impact-test-jul-",
}


def slug(s, n=28):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n]


def strip_md(s):
    """Remove ** markers only. No words are changed."""
    return re.sub(r"\*\*", "", s).strip()


def parse(path):
    """-> [{key, section, text}] in file order. Mirrors the enumeration the
    classification table was built against."""
    lines = path.read_text(encoding="utf-8").split("\n")
    sec, n, items, prose = "preamble", 0, [], []

    def flush_prose():
        nonlocal prose
        if sec in PROSE_SECTIONS and prose:
            body = strip_md(" ".join(p.strip() for p in prose if p.strip()))
            if body:
                items.append({"key": f"{sec}#1", "section": sec, "text": body})
        prose = []

    for ln in lines:
        if ln.startswith("## "):
            flush_prose()
            sec, n = slug(ln[3:]), 0
            continue
        if sec == "preamble":
            continue
        if sec in PROSE_SECTIONS:
            if not ln.startswith("#"):
                prose.append(ln)
            continue
        if ln.startswith("- ") or (ln.startswith("**") and len(ln) > 60):
            n += 1
            items.append({"key": f"{sec}#{n}", "section": sec,
                          "text": strip_md(ln.lstrip("- "))})
    flush_prose()
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    url = os.environ.get("CARR_IMPORT_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set CARR_IMPORT_DB_URL (or DATABASE_URL)")
    if not SRC.exists():
        sys.exit(f"REFUSING: {SRC} not found.")

    items = parse(SRC)
    unknown = [i["key"] for i in items if i["key"] not in CLASS]
    if unknown:
        sys.exit("REFUSING: unclassified item(s) — the file changed since the "
                 f"classification table was built: {unknown}. Nothing imported.")

    inserted = skipped = 0
    imported_rows, parked, review = [], [], []

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select id, slug from actor")
        actors = {s: i for i, s in cur.fetchall()}
        joe = actors["joe"]

        for it in items:
            verdict = CLASS[it["key"]]
            if verdict == PARK:
                parked.append((it["key"], PARK_REASON.get(it["key"], "not a rule"), it["text"]))
                continue

            personal_to = None if verdict == SHARED else joe
            if verdict == REVIEW:
                review.append((it["key"], it["text"]))

            ext_key = it["key"]
            cur.execute("select entity_id from record_source "
                        "where source_system=%s and external_key=%s",
                        (SOURCE_SYSTEM, ext_key))
            hit = cur.fetchone()
            if hit:
                skipped += 1
                imported_rows.append((ext_key, verdict, it["text"], str(hit[0])))
                continue

            scope = {
                "source": "ai-operating-notes.md",
                "section": it["section"],
                "key": it["key"],
                "tier_verdict": verdict,
                "quote_absent": True,
                "provenance": ("imported from ai-operating-notes.md "
                               f"[{it['section']}], no verbatim quote recorded"),
            }
            cur.execute(
                "insert into rule (statement, human_quote, taught_by, scope, personal_to, "
                "status) values (%s, NULL, %s, %s::jsonb, %s, 'proposed') returning id",
                (it["text"], joe, json.dumps(scope), personal_to))
            rid = cur.fetchone()[0]
            cur.execute(
                "insert into record_source (entity_type, entity_id, source_system, "
                "external_key, imported_at) values ('rule', %s, %s, %s, now())",
                (rid, SOURCE_SYSTEM, ext_key))
            inserted += 1
            imported_rows.append((ext_key, verdict, it["text"], str(rid)))

        if a.dry_run:
            conn.rollback()
        else:
            conn.commit()

    n_shared = sum(1 for r in imported_rows if r[1] == SHARED)
    n_personal = sum(1 for r in imported_rows if r[1] == PERSONAL)
    n_review = sum(1 for r in imported_rows if r[1] == REVIEW)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(__file__).resolve().parent.parent / "out" / f"operating-notes-import-{stamp}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        f"# ai-operating-notes import ({'DRY RUN' if a.dry_run else 'APPLIED'}) {stamp}\n\n"
        f"- content units parsed: {len(items)}\n"
        f"- imported as rules (status proposed): {len(imported_rows)}"
        f"  (new {inserted}, already present {skipped})\n"
        f"- parked, not a rule: {len(parked)}\n"
        f"- tier: shared {n_shared} · personal {n_personal} · review {n_review}\n\n"
        "## Tier table\n\n| key | tier | rule |\n|---|---|---|\n" +
        "\n".join(f"| {k} | {v} | {t[:150].replace('|', '/')} |"
                  for k, v, t, _ in imported_rows) +
        "\n\n## Activation batch list (rule ids for activate-rule)\n\n" +
        "\n".join(f"- `{rid}` [{v}] {t[:110].replace('|', '/')}"
                  for k, v, t, rid in imported_rows) +
        "\n\n## Review list (tier genuinely arguable — imported PERSONAL, Joe rules)\n\n" +
        ("\n".join(f"- {k}: {t[:180]}" for k, t in review) or "- (none)") +
        "\n\n## Parked — not a rule\n\n" +
        "\n".join(f"- **{k}** ({why}) — {t[:130]}" for k, why, t in parked) + "\n")

    print(f"parsed={len(items)} rules={len(imported_rows)} (new={inserted} "
          f"skipped={skipped}) parked={len(parked)} "
          f"shared={n_shared} personal={n_personal} review={n_review}")
    print(f"report: {out}")


if __name__ == "__main__":
    main()
