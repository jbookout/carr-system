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

            # SELF-CONTAINED CURATION FIXTURES (2026-08-22). This used to
            # promote every pending proposal and assert there were exactly 10 —
            # which broke the moment real curation life started: the snapshot
            # carries retrieval_proposal as reference data, so live proposals
            # (and, after the first production approval, rows already marked
            # approved) ride into every fresh build. The gate now files its own
            # ten proposals inside this rolled-back transaction, cloned from
            # 0135's seed payloads and bound by address to the fixture sections
            # above, so it proves the promote-and-rank path regardless of what
            # production's proposal table happens to hold.
            seed_proposals = (
                ("concept", {"concept_key": "record-layer-outage-diagnosis",
                             "label": "Record layer outage diagnosis",
                             "definition": "Diagnosing an unavailable or failing CARR record layer before attempting repair."}),
                ("concept", {"concept_key": "playbook-self-improvement-review",
                             "label": "Playbook self-improvement review",
                             "definition": "Reviewing operating evidence so the playbook learns from mistakes and improves."}),
                ("phrase", {"concept_key": "record-layer-outage-diagnosis",
                            "phrase": "record layer outage diagnosis runbook",
                            "match_mode": "exact", "min_similarity": 0.35, "weight": 1,
                            "source": "golden_miss", "source_ref": "RET-002"}),
                ("phrase", {"concept_key": "record-layer-outage-diagnosis",
                            "phrase": "database service unavailable troubleshooting steps",
                            "match_mode": "fts", "min_similarity": 0.35, "weight": 0.9,
                            "source": "golden_miss", "source_ref": "RET-PHRASE-001"}),
                ("phrase", {"concept_key": "record-layer-outage-diagnosis",
                            "phrase": "review cycle after a record layer outage diagnosis",
                            "match_mode": "fts", "min_similarity": 0.35, "weight": 0.8,
                            "source": "golden_miss", "source_ref": "RET-AMB-001"}),
                ("phrase", {"concept_key": "playbook-self-improvement-review",
                            "phrase": "playbook self improvement review cycle",
                            "match_mode": "exact", "min_similarity": 0.35, "weight": 1,
                            "source": "golden_miss", "source_ref": "RET-003"}),
                ("phrase", {"concept_key": "playbook-self-improvement-review",
                            "phrase": "how the operating playbook learns from mistakes",
                            "match_mode": "fts", "min_similarity": 0.35, "weight": 0.9,
                            "source": "golden_miss", "source_ref": "RET-PHRASE-002"}),
                ("phrase", {"concept_key": "playbook-self-improvement-review",
                            "phrase": "review cycle after a record layer outage playbook",
                            "match_mode": "fts", "min_similarity": 0.35, "weight": 0.8,
                            "source": "golden_miss", "source_ref": "RET-AMB-001"}),
                ("mapping", {"concept_key": "record-layer-outage-diagnosis",
                             "section_address": "runbook#diagnosis-checklist-in-order-2-minutes",
                             "role": "governs", "weight": 1,
                             "rationale": "This current runbook section is the ordered diagnosis procedure."}),
                ("mapping", {"concept_key": "playbook-self-improvement-review",
                             "section_address": "playbook-review#preamble",
                             "role": "governs", "weight": 1,
                             "rationale": "The current preamble states the playbook review and improvement cycle."}),
            )
            for index, (proposal_type, payload) in enumerate(seed_proposals, start=1):
                proposal_id = required_value(cur.execute(
                    """insert into retrieval_proposal
                         (proposal_type, payload, reason, proposer_id, idempotency_key)
                       values (%s, %s, %s, %s, %s) returning id""",
                    (proposal_type, Jsonb(payload),
                     "gate fixture, rolled back",
                     actor_id, f"99990000-0000-4000-8000-{index:012d}"),
                ).fetchone(), "fixture proposal insert")
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
