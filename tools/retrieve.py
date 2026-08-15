#!/usr/bin/env python3
"""
retrieve.py — retrieval-as-code for the CARR AI vault (graph-engineering
build, 2026-07-25). Scores every file/section from Automation/section-index.tsv
WITHOUT opening a single knowledge file, then prints the handful of reads that
answer the question. Sessions run this FIRST, then read only what it returns.

The ladder this implements (T0 — zero model tokens):
  1. strip the question to keywords
  2. score every section from the index alone
  3. print the top sections as exact path + line-range reads

Usage:
  ./run.sh retrieve "which lenders do we intro for dental startups"
  python3 tools/retrieve.py [-n TOP] [--vault PATH] [--hybrid] <question words...>

If the index is missing or stale, rebuild with: ./run.sh section-index
(health-check watches staleness). No hits is a real answer: fall back to the
INDEX.md router — never guess paths.
"""
import json
import sys, os

from retrieval_hybrid import fuse_candidates
from retrieval_lexical import (
    load_index_contract, rank_index, rank_index_sections, target_for_row, toks,
)


SCOPE_REF = "carr-internal"
FORBIDDEN_TARGET_PATTERNS = (
    r"_to_delete/", r"\.generations/", r"archive", r"portability-mirror",
)


def _fts(cur, qtext, top):
    cur.execute("""
        select d.slug, s.section_key, coalesce(s.title,''),
               ts_rank_cd(r.search_vector, websearch_to_tsquery('english', %s)) as rank,
               ts_headline('english', r.plain_text,
                           websearch_to_tsquery('english', %s),
                           'MaxWords=18, MinWords=8') as snippet
          from doctrine_section s
          join doctrine_document d on d.id = s.document_id
          join doctrine_revision r on r.id = s.current_revision_id
         where s.status = 'active' and d.visibility = 'shared'
           and r.search_vector @@ websearch_to_tsquery('english', %s)
         order by rank desc limit %s""", (qtext, qtext, qtext, top))
    return cur.fetchall()

def main():
    args = sys.argv[1:]
    top = 8
    hybrid = os.environ.get("CARR_RETRIEVAL_HYBRID") == "1"
    vault = os.environ.get("CARR_VAULT",
        "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
    words = []
    i = 0
    while i < len(args):
        if args[i] == "-n":
            top = int(args[i + 1]); i += 2
        elif args[i] == "--vault":
            vault = args[i + 1]; i += 2
        elif args[i] == "--hybrid":
            hybrid = True; i += 1
        else:
            words.append(args[i]); i += 1
    if not words:
        print(__doc__); sys.exit(2)
    words = " ".join(words).split()   # a quoted question is one argv entry; re-split
    query = " ".join(words).lower()
    q = set(toks(query))
    if not q:
        print("retrieve: query reduced to nothing after stopwords; be more specific"); sys.exit(1)

    # ---- DUAL-READ, store pass first (doctrine-store build P4, 2026-08-08;
    # decisions 82a2fb62 + the import-door entry). Migrated doctrine lives in
    # the record layer; its vault .md copies are frozen dual-read fallbacks
    # until the cutoff. The store pass runs the SAME FTS the search-doctrine
    # verb runs; a store hit prints as a verb pointer, never a file path.
    # FAIL-SOFT: any error prints one line and the file pass still answers —
    # a dead store must never make retrieval blind (record_sources doctrine).
    #
    # PHASE 1 (2026-08-13): migrated_rel now comes from lib.record_sources.
    # doctrine_migrated_paths — the SAME function build-section-index.py and
    # build-system-graph.py call, so all three systemic readers agree on
    # exactly which paths are store-held. It used to be computed inline here
    # (three copies drifting was the risk rule 73381d78 names).
    migrated_rel = set()
    store_hits = []
    store_fallback_hits = []
    store_available = False
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        from lib.record_sources import _connect, doctrine_migrated_paths
        migrated_rel = doctrine_migrated_paths(vault)
        with _connect() as conn, conn.cursor() as cur:
            # AND first (websearch), OR fallback: long natural questions rarely
            # land every word in one section, and the file scorer is OR-based —
            # without the fallback the store looked blind exactly on the queries
            # humans actually type (caught on the batch-5 sanity check).
            store_hits = _fts(cur, " ".join(words), top)
            store_available = True
            if not hybrid and not store_hits and len(words) > 1:
                store_hits = _fts(cur, " OR ".join(words), top)
    except Exception as exc:
        print(f"retrieve: store pass skipped ({type(exc).__name__}) — file index only")

    index = os.path.join(vault, "Automation", "section-index.tsv")
    if not os.path.exists(index):
        print(f"retrieve: {index} missing — run ./run.sh section-index first"); sys.exit(1)

    # PHASE 1 (2026-08-13): the index carries an 8th column — source,
    # 'file' or 'store' (build-section-index.py). A 'store' row's path is
    # `doctrine:<slug>`, never a real file, so it prints as a read-doctrine
    # pointer below instead of a file-open. Rows with only 7 columns (a stale
    # TSV from before this build) default to 'file' — same behaviour as ever.
    # The optional 9th column preserves a store section_key. Old 7/8-column
    # indexes remain readable; they simply return a document-level pointer.
    ranked_all = rank_index(index, query, top=None, migrated_paths=migrated_rel)

    if hybrid:
        section_scope_proven = False
        section_hits = None
        try:
            load_index_contract(index, expected_scope=SCOPE_REF)
            section_scope_proven = True
            ranked_sections = rank_index_sections(
                index, query, top=max(top, 8), migrated_paths=migrated_rel,
            )
            section_hits = [{
                "target": target_for_row(item.row),
                "rank": rank,
                "scope_ref": SCOPE_REF,
                "current": True,
                "provenance_complete": item.row.source in ("file", "store") and bool(item.row.path),
            } for rank, item in enumerate(ranked_sections, 1)]
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"retrieve: section-index candidate unavailable ({exc})")

        # Broad OR is a bounded fallback, including at the execution-cost
        # boundary: do not even issue it when either primary generator found
        # safe evidence. Reopen a short read connection only for the rare true
        # no-candidate case.
        if store_available and not store_hits and section_hits == [] and len(words) > 1:
            try:
                from lib.record_sources import _connect
                with _connect() as conn, conn.cursor() as cur:
                    store_fallback_hits = _fts(cur, " OR ".join(words), top)
            except Exception as exc:
                print(f"retrieve: store fallback skipped ({type(exc).__name__})")

        def _store_candidates(rows):
            return [{
                "target": f"{slug}#{key}",
                "rank": rank,
                "scope_ref": SCOPE_REF,
                "current": True,
                "provenance_complete": bool(slug and key),
            } for rank, (slug, key, _title, _score, _snippet) in enumerate(rows, 1)]

        fused = fuse_candidates(
            section_hits=section_hits,
            fts_hits=_store_candidates(store_hits) if store_available else None,
            fallback_hits=_store_candidates(store_fallback_hits) if store_available else None,
            scope_ref=SCOPE_REF,
            section_scope_applied_before_rank=section_scope_proven,
            fts_scope_applied_before_rank=store_available,
            fallback_scope_applied_before_rank=store_available,
            forbidden_target_patterns=FORBIDDEN_TARGET_PATTERNS,
            top_k=top,
        )
        print(f"retrieve: HYBRID candidate ({fused['status']}; feature-gated)")
        if fused["status"] == "unknown":
            print("retrieve: candidate generators unavailable — no passing result")
            return
        if fused["status"] == "no_answer":
            print("retrieve: no safe evidence found — fall back to the INDEX.md router (do not guess paths)")
            return
        for hit in fused["hits"]:
            if hit["section"]:
                pointer = {"document": hit["document"], "section_key": hit["section"]}
                print(f"  {hit['rank']:2d}  [hybrid:{'+'.join(hit['sources'])}]  read-doctrine {json.dumps(pointer, sort_keys=True)}")
            elif hit["target"].startswith("doctrine:"):
                pointer = {"document": hit["document"]}
                print(f"  {hit['rank']:2d}  [hybrid:{'+'.join(hit['sources'])}]  read-doctrine {json.dumps(pointer, sort_keys=True)}")
            else:
                print(f"  {hit['rank']:2d}  [hybrid:{'+'.join(hit['sources'])}]  {hit['target']}")
        return

    if store_hits:
        print(f"retrieve: STORE hits (read via verbs, these are the live copies):")
        for slug, key, title, rank, snippet in store_hits:
            label = f"{slug} § {key}" + (f" — {title}" if title else "")
            print(f"  [store]  {label}")
            print(f"           read-doctrine {{\"document\":\"{slug}\"}} · {snippet}")

    if not ranked_all and not store_hits:
        print("retrieve: no keyword hits — fall back to the INDEX.md router (do not guess paths)")
        sys.exit(0)
    if not ranked_all:
        sys.exit(0)
    ranked = ranked_all[:top]

    print(f"retrieve: top {len(ranked)} of {len(ranked_all)} matching files for: {' '.join(sorted(q))}")
    for item in ranked:
        score = item.score
        path, start, end, level = item.row.path, item.row.start, item.row.end, item.row.level
        header, parents, source, section_key = item.row.header, item.row.parents, item.row.source, item.row.section_key
        crumb = f"{parents} > {header}" if parents else header
        if source == "store":
            slug = path.split("doctrine:", 1)[1]
            print(f"  {score:5.1f}  [store]  {crumb}")
            pointer = {"document": slug}
            if section_key:
                pointer["section_key"] = section_key
            print(f"           read-doctrine {pointer}")
        else:
            span = f"lines {start}-{end}" if level else f"whole file (1-{end})"
            print(f"  {score:5.1f}  {path}  [{span}]  {crumb}")
    print("open the top hit only; follow one link out if it points elsewhere (the one-edge rule)")

if __name__ == "__main__":
    main()
