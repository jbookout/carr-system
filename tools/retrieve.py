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
  python3 tools/retrieve.py [-n TOP] [--vault PATH] <question words...>

If the index is missing or stale, rebuild with: ./run.sh section-index
(health-check watches staleness). No hits is a real answer: fall back to the
INDEX.md router — never guess paths.
"""
import sys, os

from retrieval_lexical import rank_index, toks

def main():
    args = sys.argv[1:]
    top = 8
    vault = os.environ.get("CARR_VAULT",
        "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
    words = []
    i = 0
    while i < len(args):
        if args[i] == "-n":
            top = int(args[i + 1]); i += 2
        elif args[i] == "--vault":
            vault = args[i + 1]; i += 2
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
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        from lib.record_sources import _connect, doctrine_migrated_paths
        migrated_rel = doctrine_migrated_paths(vault)
        with _connect() as conn, conn.cursor() as cur:
            # AND first (websearch), OR fallback: long natural questions rarely
            # land every word in one section, and the file scorer is OR-based —
            # without the fallback the store looked blind exactly on the queries
            # humans actually type (caught on the batch-5 sanity check).
            def _fts(qtext):
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
            store_hits = _fts(" ".join(words))
            if not store_hits and len(words) > 1:
                store_hits = _fts(" OR ".join(words))
    except Exception as exc:
        print(f"retrieve: store pass skipped ({type(exc).__name__}) — file index only")

    index = os.path.join(vault, "Automation", "section-index.tsv")
    if not os.path.exists(index):
        print(f"retrieve: {index} missing — run ./run.sh section-index first"); sys.exit(1)

    # PHASE 1 (2026-08-13): the index carries an 8th column now — source,
    # 'file' or 'store' (build-section-index.py). A 'store' row's path is
    # `doctrine:<slug>`, never a real file, so it prints as a read-doctrine
    # pointer below instead of a file-open. Rows with only 7 columns (a stale
    # TSV from before this build) default to 'file' — same behaviour as ever.
    ranked_all = rank_index(index, query, top=None, migrated_paths=migrated_rel)

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
        header, parents, source = item.row.header, item.row.parents, item.row.source
        crumb = f"{parents} > {header}" if parents else header
        if source == "store":
            slug = path.split("doctrine:", 1)[1]
            print(f"  {score:5.1f}  [store]  {crumb}")
            print(f"           read-doctrine {{\"document\": \"{slug}\"}}")
        else:
            span = f"lines {start}-{end}" if level else f"whole file (1-{end})"
            print(f"  {score:5.1f}  {path}  [{span}]  {crumb}")
    print("open the top hit only; follow one link out if it points elsewhere (the one-edge rule)")

if __name__ == "__main__":
    main()
