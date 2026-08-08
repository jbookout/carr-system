#!/usr/bin/env python3
"""doctrine_load_test.py — the P6 preflight load bar (design §5, council).

THE BAR: 20 concurrent sessions, each doing 5 hot doc.reads + 1 search every
10 seconds, for 15 minutes → p95 read <150ms warm, p95 search <300ms.
Runs as the RUNTIME READER ROLE (the 0076/0077/0078 lesson), against the
production store the fleet will actually hit.

    DATABASE_URL=<app_reader dsn> .venv/bin/python pipelines/doctrine_load_test.py [--minutes 15]
"""
import os, sys, time, threading, statistics, random

import psycopg

MINUTES = 15
if "--minutes" in sys.argv:
    MINUTES = float(sys.argv[sys.argv.index("--minutes") + 1])

URL = os.environ["DATABASE_URL"]
HOT = ["writing-rules", "templates", "lead-system", "x-strategy-2026",
       "negotiation", "carr-profile"]
QUERIES = ["conflict of interest listing broker", "lease renewal timeline",
           "vendor introduction", "banned phrases", "dental startup lender",
           "TI allowance negotiation"]

reads, searches, errors = [], [], []
lock = threading.Lock()
stop_at = time.time() + MINUTES * 60


def worker(n):
    try:
        conn = psycopg.connect(URL)
    except Exception as e:
        with lock:
            errors.append(f"connect: {e}")
        return
    cur = conn.cursor()
    while time.time() < stop_at:
        try:
            for slug in random.sample(HOT, 5):
                t0 = time.time()
                cur.execute(
                    "select snapshot_json from doctrine_snapshot s "
                    "join doctrine_document d on d.id = s.document_id "
                    "where d.slug = %s", (slug,))
                cur.fetchone()
                with lock:
                    reads.append(time.time() - t0)
            q = random.choice(QUERIES)
            t0 = time.time()
            cur.execute(
                "select d.slug, s.section_key from doctrine_section s "
                "join doctrine_document d on d.id = s.document_id "
                "join doctrine_revision r on r.id = s.current_revision_id "
                "where r.search_vector @@ websearch_to_tsquery('english', %s) "
                "order by ts_rank_cd(r.search_vector, "
                "websearch_to_tsquery('english', %s)) desc limit 10", (q, q))
            cur.fetchall()
            with lock:
                searches.append(time.time() - t0)
        except Exception as e:
            with lock:
                errors.append(str(e)[:120])
            time.sleep(1)
        time.sleep(10)
    conn.close()


threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
for t in threads:
    t.start()
for t in threads:
    t.join()


def p95(xs):
    return sorted(xs)[int(len(xs) * 0.95) - 1] * 1000 if xs else -1


print(f"RESULT reads={len(reads)} p50={statistics.median(reads)*1000:.0f}ms "
      f"p95={p95(reads):.0f}ms | searches={len(searches)} "
      f"p50={statistics.median(searches)*1000:.0f}ms p95={p95(searches):.0f}ms | "
      f"errors={len(errors)}")
print(f"BAR read_p95<150ms: {'PASS' if 0 < p95(reads) < 150 else 'FAIL'} · "
      f"search_p95<300ms: {'PASS' if 0 < p95(searches) < 300 else 'FAIL'} · "
      f"errors==0: {'PASS' if not errors else 'FAIL ' + errors[0]}")
