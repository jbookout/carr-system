#!/usr/bin/env python3
"""test-section-index-store-classification.py — regression set for the Phase 1
store-vs-file classification (2026-08-13, doctrine-store build).

WHY THIS FILE EXISTS. build-section-index.py and build-system-graph.py stopped
opening any vault .md file recorded on a VERIFIED doctrine_migration_batch —
that content now comes from one read-only doctrine_section query instead. The
build's own spec proposed deriving the skip-list from exporters.targets.
TARGETS and corpus/corpus-set.tsv; measured against the live store before
writing the pipelines, BOTH of those lists would have silently deleted real
content (40 export-target .md files that are not doctrine at all, and 4
corpus-mirror files never migrated) with no store replacement. The gate that
actually ships is narrower and provable: a path is store-held iff it is the
recorded IMPORT SOURCE of a live document (doctrine_migration_batch, state=
'verified') AND the store pass that proved that succeeded this run. Both
halves are pinned here so a future edit cannot silently widen the skip-list
back to the unsafe version, or drop the fail-soft gate that protects a DB
outage from becoming a content loss.

Also covers store_rows(): the shape build-section-index.py hands retrieve.py
for doctrine content — one document-level row plus one row per active
section, all tagged source='store', path=`doctrine:<slug>` (never a real file
path, so retrieve.py's print loop routes it to read-doctrine).

No database needed: every case here is pure-function, synthetic input.

Run: .venv/bin/python tools/test-section-index-store-classification.py
Exit 0 = every case behaves; exit 1 = a named case regressed.
"""
import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PIPELINE = os.path.join(REPO, "pipelines", "build-section-index.py")
spec = importlib.util.spec_from_file_location("build_section_index", PIPELINE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load build-section-index.py from {PIPELINE} — "
                     f"the file is missing or unreadable, so this test proves nothing")
bsi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bsi)

sys.path.insert(0, REPO)
from lib.record_sources import _strip_vault_prefix  # noqa: E402

failures: list[str] = []


def check(name, got, expected):
    if got != expected:
        failures.append(f"{name}: got {got!r}, expected {expected!r}")


VAULT = "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"

# ---------------------------------------------------------------- classification gate
migrated = {"DNA/writing-rules.md", "DNA/brand-voice.md"}

check("migrated path, store pass OK -> skip walking it",
      bsi.is_store_held("DNA/writing-rules.md", migrated, store_ok=True), True)

check("non-migrated path, store pass OK -> still walked",
      bsi.is_store_held("DNA/Team/dell-starter-kit/DELL-START-HERE.md", migrated, store_ok=True),
      False)

check("MUST-STAY-SAFE: store pass FAILED -> even a migrated path is walked "
      "(a dead DB must never delete content, only lose freshness)",
      bsi.is_store_held("DNA/writing-rules.md", migrated, store_ok=False), False)

check("an export-target render (never migrated) is walked regardless — "
      "the build spec's TARGETS-based skip-list would have wrongly deleted this",
      bsi.is_store_held("00_Context/decision-history.md", migrated, store_ok=True), False)

# ---------------------------------------------------------------- path stripping
check("strip the mounted GoogleDrive path down to vault-relative",
      _strip_vault_prefix(VAULT + "/DNA/writing-rules.md", VAULT), "DNA/writing-rules.md")

check("strip the ~/My Drive alias form (retrieve.py's second prefix) down to vault-relative",
      _strip_vault_prefix("/Users/booko/My Drive/CARR AI/DNA/writing-rules.md", VAULT),
      "DNA/writing-rules.md")

check("a path outside the vault is left untouched (no accidental truncation)",
      _strip_vault_prefix("/Users/booko/Desktop/scratch.md", VAULT),
      "/Users/booko/Desktop/scratch.md")

# ---------------------------------------------------------------- store_rows() shape
sections = [
    {"slug": "writing-rules", "doc_title": "Writing Rules", "content_class": "playbook",
     "section_key": "preamble", "title": None, "ordinal": 10,
     "plain_text": "The zero-tolerance list.\nMore body text."},
    {"slug": "writing-rules", "doc_title": "Writing Rules", "content_class": "playbook",
     "section_key": "no-em-dashes", "title": "No em-dashes", "ordinal": 20,
     "plain_text": "Never use an em-dash."},
    {"slug": "brand-voice", "doc_title": "Brand Voice", "content_class": "playbook",
     "section_key": "preamble", "title": None, "ordinal": 10,
     "plain_text": "Voice doctrine."},
]
rows = bsi.store_rows(sections)

check("one doc-level row + one row per section, across both documents",
      len(rows), 2 + 1 + 1 + 1)  # writing-rules: doc + 2 sections; brand-voice: doc + 1 section

wr_rows = [r for r in rows if r[0] == "doctrine:writing-rules"]
check("every row for a document shares its `doctrine:<slug>` path (the dedup key retrieve.py groups on)",
      len(wr_rows), 3)

check("every store row is tagged source='store'",
      all(r[7] == "store" for r in rows), True)

check("no store row's path is ever a real file path",
      any(r[0].endswith(".md") for r in rows), False)

doc_level = [r for r in wr_rows if r[3] == 0]
check("exactly one document-level (level 0) row per document",
      len(doc_level), 1)
check("document-level row's header is the document title",
      doc_level[0][4], "Writing Rules")
check("document-level row's gist comes from the preamble section's first line",
      doc_level[0][6], "The zero-tolerance list.")

section_level = [r for r in wr_rows if r[3] == 1]
check("one row per active section (excluding the doc-level row)",
      len(section_level), 2)
titled = [r for r in section_level if r[4] == "No em-dashes"]
check("a titled section keeps its title as the header",
      len(titled), 1)
check("a titled section's breadcrumb (parents) is the document title",
      titled[0][5], "Writing Rules")

untitled = [r for r in section_level if r[4] == "preamble"]
check("an untitled section falls back to its section_key as the header",
      len(untitled), 1)

if failures:
    print(f"FAILED ({len(failures)} case(s)):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all cases passed")
