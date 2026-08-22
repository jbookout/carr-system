#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only PostgreSQL acceptance gate for WR-AI-006."""

from __future__ import annotations

import hashlib
import os
import sys
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


SUITE_DIGEST = "b1a5a61945c5e5fc5f7c74f45c3403f2c5df3e61db29e58f281d49015f63dae3"
FIXTURES = (
    ("carr-mature-software-end-state-bduf", "s39-fresh-session-prompt",
     "Fresh session prompt", "write acceptance tests first"),
    ("runbook", "diagnosis-checklist-in-order-2-minutes",
     "Diagnosis checklist (in order, 2 minutes)", "ordered recovery protocol"),
    ("playbook-review", "preamble", None, "monthly learning procedure"),
    ("document-factory-routing", "preamble", None, "document factory"),
    ("npi-sweep-sop", "preamble", None, "weekly new provider detection"),
    ("social-media-workflow",
     "placement-the-entire-social-run-is-local-now-joe-july-18-2026", None,
     "social publishing review approval"),
    ("space-search-sop", "preamble", None, "space search multi source"),
    ("lead-system-handoff", "preamble", None, "lead system"),
    ("carr-control-room-bduf", "s30-test-and-verification-strategy", None,
     "restore backup rehearsal"),
    ("deal-enrichment-sop", "rules", None, "single source of truth"),
    ("2026-07-30-agent-systems-explainer-takeaways", "preamble", None,
     "agent systems validation"),
)


def fail(message: str) -> int:
    print(f"situation-retrieval-db-gate: FAIL — {message}", file=sys.stderr)
    return 1


def required_value(row: tuple[Any, ...] | None, label: str) -> Any:
    if row is None:
        raise RuntimeError(f"{label} returned no row")
    return row[0]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            actor_id = required_value(cur.execute(
                "select id from actor where slug='joe' and kind='human'"
            ).fetchone(), "human actor lookup")
            for slug, section_key, title, body in FIXTURES:
                doc_id = required_value(cur.execute(
                    """insert into doctrine_document
                         (slug,title,content_class,visibility,created_by)
                       values (%s,%s,'sop','shared',%s) returning id""",
                    (slug, slug.replace("-", " "), actor_id),
                ).fetchone(), "fixture document insert")
                section_id = required_value(cur.execute(
                    """insert into doctrine_section
                         (document_id,section_key,title,ordinal,status,current_version)
                       values (%s,%s,%s,10,'active',1) returning id""",
                    (doc_id, section_key, title),
                ).fetchone(), "fixture section insert")
                content_hash = hashlib.sha256(body.encode()).hexdigest()
                revision_id = required_value(cur.execute(
                    """insert into doctrine_revision
                         (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
                       values (%s,1,%s,%s,%s,%s,'acceptance fixture') returning id""",
                    (section_id, actor_id, Jsonb({"text": body}), body, content_hash),
                ).fetchone(), "fixture revision insert")
                cur.execute(
                    """update doctrine_section
                          set current_revision_id=%s,body_hash=%s where id=%s""",
                    (revision_id, content_hash, section_id),
                )

            # SCOPED TO 0135'S OWN SEEDS, not to every pending proposal in the
            # database. This counted globally until 2026-08-22, which made it
            # assert something it did not mean: the moment Production carried a
            # REAL pending proposal, the refreshed schema snapshot carried it too
            # and this gate failed on every branch that refreshed the snapshot —
            # with a message blaming the seed count. That is what happened at
            # 16:32:49 that day, when a golden-miss probe filed a live concept
            # proposal twenty seconds after a migration landed and thirty-three
            # seconds before the snapshot was regenerated. The seeds all carry
            # 0135's reserved idempotency-key block, so scope on that: the gate
            # then measures its own fixture and is indifferent to real review
            # traffic. The promotion loop below is scoped by the same query for
            # the same reason — promoting a real pending proposal would have been
            # a live approval nobody made.
            proposals = cur.execute(
                """select id from retrieval_proposal where status='pending'
                     and idempotency_key::text like '13500000-0000-4000-8000-%'
                     order by case proposal_type when 'concept' then 1
                              when 'phrase' then 2 when 'mapping' then 3 else 4 end,
                              created_at,id"""
            ).fetchall()
            if len(proposals) != 10:
                return fail(f"expected 10 pending seed proposals, found {len(proposals)}")
            for (proposal_id,) in proposals:
                cur.execute("select promote_retrieval_proposal(%s,%s)",
                            (proposal_id, actor_id))

            golden = cur.execute(
                "select case_id,status,target_rank from assert_situation_retrieval_golden(%s)",
                (SUITE_DIGEST,),
            ).fetchall()
            failed = [row for row in golden if row[1] != "pass"]
            if len(golden) != 17 or failed:
                return fail(f"golden suite returned {len(golden)} rows; failures={failed}")

            for policy in ("lexical-dominant-v1", "coequal-normalized-v1"):
                for query, target in (
                    ("record layer outage diagnosis runbook",
                     "runbook#diagnosis-checklist-in-order-2-minutes"),
                    ("playbook self improvement review cycle", "playbook-review#preamble"),
                ):
                    hits = cur.execute(
                        """select doc_slug||'#'||section_key,lexical_score,concept_score,provenance
                             from search_doctrine_situations(%s,null,null,3,%s)
                            order by final_score desc,concept_score desc,lexical_score desc,section_key""",
                        (query, policy),
                    ).fetchall()
                    addresses = [row[0] for row in hits]
                    if target not in addresses:
                        return fail(f"{policy} missed {target} for {query!r}: {addresses}")
                    hit = hits[addresses.index(target)]
                    provenance = hit[3]
                    if hit[2] <= 0 or not provenance.get("complete") or not all(
                        provenance.get(key) for key in ("phrase_ids", "concept_ids", "mapping_ids")
                    ):
                        return fail(f"{policy} returned incomplete concept provenance for {target}")

            replay_query = "review cycle after a record layer outage"
            replay_sql = """select doc_slug,section_key,lexical_score,concept_score,final_score,provenance
                              from search_doctrine_situations(%s,null,null,10,'coequal-normalized-v1')
                             order by final_score desc,concept_score desc,lexical_score desc,section_key"""
            if cur.execute(replay_sql, (replay_query,)).fetchall() != cur.execute(
                    replay_sql, (replay_query,)).fetchall():
                return fail("deterministic replay mismatch")

            near_miss = required_value(cur.execute(
                """select count(*) from search_doctrine_situations(
                       'outage communication template',null,null,100,null)
                     where doc_slug='runbook'
                       and section_key='diagnosis-checklist-in-order-2-minutes'"""
            ).fetchone(), "near-miss count")
            if near_miss:
                return fail("near-miss query returned the governed diagnosis target")

            target_id = required_value(cur.execute(
                """select m.section_id from doctrine_concept_mapping m
                     join retrieval_concept c on c.id=m.concept_id
                    where c.concept_key='record-layer-outage-diagnosis'
                      and m.status='approved'"""
            ).fetchone(), "approved mapping target lookup")
            cur.execute("update doctrine_section set status='retired' where id=%s", (target_id,))
            leaked = required_value(cur.execute(
                """select count(*) from search_doctrine_situations(
                       'record layer outage diagnosis runbook',null,null,100,null)
                     where doc_slug='runbook'
                       and section_key='diagnosis-checklist-in-order-2-minutes'"""
            ).fetchone(), "retired target leakage count")
            repairs = required_value(
                cur.execute("select count(*) from v_retrieval_mapping_repair").fetchone(),
                "repair queue count",
            )
            if leaked or not repairs:
                return fail("retired target leaked or its mapping missed the repair queue")

            raw_columns = required_value(cur.execute(
                """select count(*) from information_schema.columns
                    where table_name='retrieval_query_log'
                      and column_name in ('raw_query','query_text')"""
            ).fetchone(), "raw query column count")
            bad_hashes = required_value(cur.execute(
                "select count(*) from retrieval_query_log where length(normalized_hash)<>64"
            ).fetchone(), "malformed query hash count")
            if raw_columns or bad_hashes:
                return fail("query evidence contains raw text or a malformed normalized hash")
        conn.rollback()
        print("PASS: situation retrieval database acceptance (17 golden, 2 policies, lifecycle, replay, hashed logs)")
        return 0
    except Exception as exc:
        conn.rollback()
        return fail(str(exc))
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
