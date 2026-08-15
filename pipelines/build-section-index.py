#!/usr/bin/env python3
"""
build-section-index.py — derived section-level index of the CARR AI vault.

Walks the vault's knowledge tier and emits one TSV row per markdown section:
path, start line, end line, header level, header text, parent breadcrumb, a
one-line gist (file-level rows only), a source tag (file|store), and an
optional doctrine section key. This is
the machine layer retrieve.py scores against so a session can decide WHERE an
answer lives without opening files (graph-engineering build, 2026-07-25;
doctrine: the index is for the model, the graph view is for Joe).

DERIVED VIEW: regenerate any time with `./run.sh section-index`; never
hand-edit the output.

PHASE 1 (2026-08-13, the doctrine-store build, gate for the Aug 21 retirement
start): doctrine content is no longer read off disk here. Every vault path
recorded on a VERIFIED doctrine_migration_batch (lib/record_sources.
doctrine_migrated_paths) is skipped by the walk below; its sections are pulled
by ONE read-only query (lib/record_sources.doctrine_sections, carr_exporter
credential) and turned into rows carrying source='store', path=`doctrine:<slug>`
— retrieve.py points a session at `read-doctrine` for those, never a file path.
Truth for store-held content is the database now, not the markdown file.

THE KEEP-LIST IS THE MIGRATION LEDGER, NOT THE EXPORT-TARGET OR CORPUS LISTS.
The build spec for this phase proposed deriving the skip-list from what is NOT
a registered export target (exporters.targets.TARGETS) and NOT a corpus mirror
(corpus/corpus-set.tsv). Measured against the live store before writing this
(2026-08-13): every one of the 40 .md export targets (decision-history.md,
clients-active.md, the compiled-rules-*.md family, record-layer-dictionary.md,
CLAUDE.md, abilities.md, the md-ledger and dossier renders...) has ZERO rows in
doctrine_document — none of that content is doctrine, it renders OTHER record-
layer tables (decision_event, the rule store, etc.), so excluding it here would
delete it from the index with no replacement. And 4 of the 42 corpus-set.tsv
vault-relative rows (the Dell-starter-kit onboarding files) are corpus mirrors
that have never been migrated to the store either. Skipping by either list would
have been the exact "unexplained loss" the parity gate exists to catch. The
migration ledger is the only set that is BOTH a store-content proof and a
1:1 substitution — every path in it was the literal import source for a live
document, so trading its file row for the store's row loses nothing and gains
freshness (the store is live; the file is a snapshot until next export).

FAIL-SOFT: if the store pass errors (no exporter credential, psycopg missing,
the DB unreachable), migrated files fall back to being WALKED like before —
retrieve must never go blind because the database had a bad moment. Same
posture as retrieve.py's own dual-read pass.

Also excluded on purpose: cold storage (Source Material, Output, archives),
staging and pending-deletion folders (_asset_staging, _to_delete), the
exporter's own *.generations snapshot directories, app internals (.obsidian,
.claude), and any file self-marked SUPERSEDED — retrieval must never route a
session into stale data. Graph/ IS indexed: its per-entity notes are derived
from the live xlsx/JSON sources of truth, so they are the entity-level nodes
retrieval should land on.

Usage: python3 build-section-index.py [CARR_ROOT]
Output: <CARR_ROOT>/Automation/section-index.tsv
"""
import json
import sys, os, re
from datetime import datetime, timezone

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT  = os.path.join(ROOT, "Automation", "section-index.tsv")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

SKIP_DIRS  = {"Source Material", "Output", ".obsidian", ".claude", ".git",
              "source-exports", "photos", "Prospects",
              # 2026-08-03, IT sweep. _asset_staging put 352 rows into the index
              # and _to_delete 36 — a folder whose entire purpose is pending
              # deletion was being served to sessions as retrievable knowledge.
              "_asset_staging", "_to_delete",
              # 2026-08-13, Phase 1 loose end. Backups/portability-mirror is a
              # disaster-recovery snapshot of doctrine content that already
              # lives elsewhere (its own MANIFEST.md says "never read in normal
              # operation") — indexing its 223 .md files had every search
              # competing live content against stale duplicates of itself.
              "portability-mirror"}
SKIP_FILES = {"decision-history-archive.md", "open-loops-closed.md", "section-index.tsv"}
SUPERSEDED = re.compile(r"SUPERSEDED|RETIRED\b", re.IGNORECASE)

# THE ARCHIVE LEAK, 2026-08-03. The exporter keeps per-file version snapshots in
# SUFFIX-named directories ("introduction-rules.md.generations"), and the prune
# below only dropped dot-PREFIXED names. Forty such dirs exist in the vault and
# ZERO are named ".generations", so every one of them was walked: 55 dead paths
# entered the index, and a live query ranked a DELETED archive copy of
# introduction-rules.md at 12.0 ABOVE the live file at 8.0.
#
# The SUPERSEDED guard could never have caught these either. It reads a file's
# first three lines, and a byte-copy snapshot carries the ORIGINAL's header —
# including its "GENERATED ... do not hand-edit" banner — not an archive marker.
# A snapshot is indistinguishable from the live file by content; only its
# LOCATION says it is old. So this has to be a path rule.
GENERATIONS_SUFFIX = ".generations"

HDR = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")

def clean(text):
    """Strip markdown noise so the TSV stays one line per row."""
    return re.sub(r"\s+", " ", text.replace("\t", " ").replace("*", "").replace("`", "")).strip()

def gist_of(lines):
    """First real prose line after the title — this vault's files open with an
    italic purpose line, which is exactly the description retrieval wants."""
    for ln in lines[1:40]:
        s = ln.strip()
        if not s or HDR.match(s) or s.startswith(("|", "-", ">", "```", "<!--")):
            continue
        return clean(s)[:200]
    return ""

def index_file(abspath, relpath, rows):
    with open(abspath, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    n = len(lines)
    if n == 0:
        return
    if any(SUPERSEDED.search(ln) for ln in lines[:3]):
        return  # frozen backups never enter the retrieval layer
    # file-level row (level 0) so headerless files still score
    rows.append((relpath, 1, n, 0, os.path.splitext(os.path.basename(relpath))[0],
                 "", gist_of(lines), "file", ""))
    heads, fence = [], False
    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        m = None if fence else HDR.match(ln)
        if m:
            heads.append((i, len(m.group(1)), clean(m.group(2))))
    for j, (start, level, text) in enumerate(heads):
        end = n
        for k in range(j + 1, len(heads)):
            if heads[k][1] <= level:
                end = heads[k][0] - 1
                break
        crumbs = []
        lvl = level
        for k in range(j - 1, -1, -1):
            if heads[k][1] < lvl:
                crumbs.append(heads[k][2])
                lvl = heads[k][1]
        rows.append((relpath, start, end, level, text,
                     " > ".join(reversed(crumbs)), "", "file", ""))


def store_rows(sections):
    """Doctrine store content as index rows, same shape a file's rows take: one
    document-level row (mirrors gist_of's job) plus one row per active section
    (mirrors a header row). path is a store address (`doctrine:<slug>`), never
    a file path — retrieve.py's print loop routes source='store' rows to
    `read-doctrine`, not to opening a file."""
    rows = []
    by_doc = {}
    for s in sections:
        by_doc.setdefault(s["slug"], []).append(s)
    for slug, secs in sorted(by_doc.items()):
        secs.sort(key=lambda s: s["ordinal"])
        doc_title = secs[0]["doc_title"]
        path = f"doctrine:{slug}"
        preamble = next((s for s in secs if s["section_key"] == "preamble"), secs[0])
        gist = clean(preamble["plain_text"].splitlines()[0]) if preamble["plain_text"] else ""
        rows.append((path, 0, 0, 0, clean(doc_title), "", gist[:200], "store", ""))
        for s in secs:
            title = clean(s["title"] or s["section_key"].replace("-", " "))
            rows.append((path, s["ordinal"], s["ordinal"], 1, title,
                         clean(doc_title), "", "store", s["section_key"]))
    return rows


def is_store_held(relpath, migrated, store_ok):
    """The Phase 1 classification gate: skip walking this .md file iff the
    store pass succeeded AND this exact path was the import source for a live
    doctrine document (lib.record_sources.doctrine_migrated_paths). store_ok
    gates it so a failed store pass never causes a silent content loss — see
    the fail-soft note above main(). Pulled out as its own function so the
    classification is unit-testable without a database (tools/test-section-
    index-store-classification.py)."""
    return store_ok and relpath in migrated


def main():
    rows = []
    migrated = set()
    store_ok = False
    try:
        sys.path.insert(0, REPO)
        from lib.record_sources import doctrine_migrated_paths, doctrine_sections
        migrated = doctrine_migrated_paths(ROOT)
        rows.extend(store_rows(doctrine_sections()))
        store_ok = True
    except Exception as exc:  # noqa: BLE001 — fail-soft, same posture as retrieve.py
        print(f"section-index: store pass skipped ({type(exc).__name__}) — "
              f"walking all files (no store-held skip)", file=sys.stderr)

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS
                             and not d.startswith(".")
                             and not d.endswith(GENERATIONS_SUFFIX))
        for name in sorted(filenames):
            if not name.endswith(".md") or name in SKIP_FILES:
                continue
            abspath = os.path.join(dirpath, name)
            relpath = os.path.relpath(abspath, ROOT)
            if is_store_held(relpath, migrated, store_ok):
                continue  # store-held now; already indexed from the database above
            index_file(abspath, relpath, rows)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# section-index.tsv — DERIVED, never hand-edit. Rebuild: ./run.sh section-index\n")
        contract = {
            "schema_version": "carr-section-index-v2",
            "scope_ref": "carr-internal",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "current_source_policy": "store-active-or-file-filtered-v1",
            "store_status": "verified" if store_ok else "fallback_files",
        }
        f.write("# contract\t" + json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n")
        f.write("# path\tstart\tend\tlevel\theader\tparents\tgist\tsource\tsection_key\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    files = len({r[0] for r in rows if r[7] == "file"})
    docs = len({r[0] for r in rows if r[7] == "store"})
    print(f"section-index: {files} files + {docs} store documents "
          f"({len(migrated)} store-held paths skipped on disk), "
          f"{len(rows)} rows -> {os.path.relpath(OUT, ROOT)}")

if __name__ == "__main__":
    main()
