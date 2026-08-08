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
import sys, os, re, csv

STOP = {"the","a","an","and","or","of","to","in","on","for","with","is","are","was","be",
        "do","does","how","what","which","who","when","where","why","we","our","my","your",
        "i","you","it","this","that","these","those","about","from","at","by","as","into",
        "can","should","would","could","did","have","has","had","will","there","any","all",
        "me","us","they","them","their","his","her","its","if","then","than","so","not","no",
        "get","got","need","want","find","show","tell","give","look","up","out","new","use"}
KEEP_SHORT = {"al","fl","x","ai","cre","npi","dso","sos","loi","na","t1","t2","t3","cpa"}

def toks(text):
    out = []
    for t in re.split(r"[^a-z0-9]+", text.lower()):
        if not t or t in STOP:
            continue
        if len(t) < 3 and t not in KEEP_SHORT:
            continue
        out.append(t[:-1] if len(t) > 4 and t.endswith("s") else t)
    return out

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
    migrated_rel = set()
    store_hits = []
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        from lib.record_sources import _connect
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("""
                select b.source_paths from doctrine_migration_batch b
                 where b.state = 'verified'""")
            for (paths,) in cur.fetchall():
                for p in paths or []:
                    rel = p
                    for pref in (vault + os.sep, vault.replace(
                            "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com",
                            "/Users/booko") + os.sep):
                        if rel.startswith(pref):
                            rel = rel[len(pref):]
                    migrated_rel.add(rel)
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

    scored = []
    with open(index, encoding="utf-8") as f:
        rdr = csv.reader((ln for ln in f if not ln.startswith("#")), delimiter="\t")
        for row in rdr:
            if len(row) < 7:
                continue
            path, start, end, level, header, parents, gist = row[:7]
            fname = os.path.splitext(os.path.basename(path))[0]
            dirs  = os.path.dirname(path)
            score = 0.0
            score += 3.0 * len(q & set(toks(header)))
            score += 3.0 * len(q & set(toks(fname)))
            score += 2.0 * len(q & set(toks(parents)))
            score += 2.0 * len(q & set(toks(gist)))
            score += 1.0 * len(q & set(toks(dirs)))
            if len(query) > 6 and (query in header.lower() or query in gist.lower()):
                score += 4.0
            if score > 0:
                # migrated files: the store already answered for these; a file
                # hit would be a duplicate pointing at a frozen copy
                if path in migrated_rel:
                    continue
                scored.append((score, path, int(start), int(end), int(level), header, parents))

    if store_hits:
        print(f"retrieve: STORE hits (read via verbs, these are the live copies):")
        for slug, key, title, rank, snippet in store_hits:
            label = f"{slug} § {key}" + (f" — {title}" if title else "")
            print(f"  [store]  {label}")
            print(f"           read-doctrine {{\"document\":\"{slug}\"}} · {snippet}")

    if not scored and not store_hits:
        print("retrieve: no keyword hits — fall back to the INDEX.md router (do not guess paths)")
        sys.exit(0)
    if not scored:
        sys.exit(0)

    # prefer the sharpest section per file; file-level rows only win when no
    # section in that file scored (keeps reads narrow, per the one-section rule)
    best = {}
    for s in sorted(scored, key=lambda r: (-r[0], r[4] == 0, r[3] - r[2])):
        key = (s[1], s[2])
        cur = best.get(s[1])
        if cur is None or s[0] > cur[0] + 0.01 or (abs(s[0] - cur[0]) <= 0.01 and s[4] > cur[4]):
            best[s[1]] = s
    ranked = sorted(best.values(), key=lambda r: -r[0])[:top]

    print(f"retrieve: top {len(ranked)} of {len(best)} matching files for: {' '.join(sorted(q))}")
    for score, path, start, end, level, header, parents in ranked:
        crumb = f"{parents} > {header}" if parents else header
        span = f"lines {start}-{end}" if level else f"whole file (1-{end})"
        print(f"  {score:5.1f}  {path}  [{span}]  {crumb}")
    print("open the top hit only; follow one link out if it points elsewhere (the one-edge rule)")

if __name__ == "__main__":
    main()
