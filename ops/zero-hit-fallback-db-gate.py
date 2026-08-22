#!/usr/bin/env python3
# ci: db-gate
# doctrine: doctrine-search-zero-hit-fallback
"""Rollback-only acceptance for the doctrine-search zero-hit fallback (loop 518).

THE GAP THIS GUARDS. Doctrine search parses a query with every-word-required
semantics, so a natural question naming one word the right section lacks used
to return nothing at all — a false "nothing exists" with no second try. The
fallback is a sixth argument on search_doctrine_situations, OFF by default:
the strict lane (the golden gate, the near-miss and expect-no-hits cases, and
every five-argument caller) keeps its exact semantics, and only the live verb
opts in. When the strict pass is empty, the ranker retries with any-word
matching and marks EVERY such row's provenance with fallback:true, so a
session can tell a confident answer from a best-effort one.

Everything here runs in one transaction and rolls back.
"""
from __future__ import annotations

import hashlib
import os
import sys
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


FIXTURES = (
    ("zh-runbook", "ordered-recovery", "Ordered recovery",
     "ordered recovery protocol for the record layer"),
    ("zh-playbook", "review-cadence", "Review cadence",
     "monthly review cadence for the operating playbook"),
)

# Every word of this query appears in the first fixture.
ALL_WORDS = "ordered recovery protocol"
# "zebra" appears in no fixture, so the strict every-word pass returns nothing.
ONE_WORD_OFF = "ordered recovery protocol zebra"


def fail(message: str) -> int:
    print(f"zero-hit-fallback-db-gate: FAIL — {message}", file=sys.stderr)
    return 1


def required_value(row: tuple[Any, ...] | None, label: str) -> Any:
    if row is None:
        raise RuntimeError(f"{label} returned no row")
    return row[0]


def search(cur: psycopg.Cursor, query: str, allow_fallback: bool | None) -> list[tuple]:
    if allow_fallback is None:
        return cur.execute(
            """select doc_slug||'#'||section_key, lexical_score, concept_score, provenance
                 from search_doctrine_situations(%s, null, null, 10, null)
                order by final_score desc, section_key""",
            (query,),
        ).fetchall()
    return cur.execute(
        """select doc_slug||'#'||section_key, lexical_score, concept_score, provenance
             from search_doctrine_situations(%s, null, null, 10, null, %s)
            order by final_score desc, section_key""",
        (query, allow_fallback),
    ).fetchall()


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
                       values (%s,1,%s,%s,%s,%s,'zero-hit fixture') returning id""",
                    (section_id, actor_id, Jsonb({"text": body}), body, content_hash),
                ).fetchone(), "fixture revision insert")
                cur.execute(
                    """update doctrine_section
                          set current_revision_id=%s,body_hash=%s where id=%s""",
                    (revision_id, content_hash, section_id),
                )

            # ONE function only. An added default parameter via a second CREATE
            # would leave the old five-argument overload behind, and every old
            # call site would silently keep resolving to the old body.
            overloads = required_value(cur.execute(
                """select count(*) from pg_proc
                    where proname='search_doctrine_situations'
                      and pronamespace='public'::regnamespace"""
            ).fetchone(), "overload count")
            if overloads != 1:
                return fail(f"expected exactly 1 search_doctrine_situations, found {overloads}")

            # The five-argument call shape every existing caller uses must keep
            # strict semantics: one absent word means no answer.
            strict = search(cur, ONE_WORD_OFF, allow_fallback=None)
            if strict:
                return fail(f"five-argument strict call must stay empty, got {strict}")

            # Fallback explicitly off behaves exactly like the strict lane.
            if search(cur, ONE_WORD_OFF, allow_fallback=False):
                return fail("allow_fallback=false must behave exactly like the strict lane")

            # Fallback on: the same query now returns the best-effort answer,
            # and every returned row says so in its provenance.
            rescued = search(cur, ONE_WORD_OFF, allow_fallback=True)
            if not rescued:
                return fail("allow_fallback=true returned nothing for a query the fallback should rescue")
            addresses = [row[0] for row in rescued]
            if "zh-runbook#ordered-recovery" not in addresses:
                return fail(f"fallback missed the near-match section: {addresses}")
            unmarked = [row[0] for row in rescued if row[3].get("fallback") is not True]
            if unmarked:
                return fail(f"fallback rows must carry provenance fallback:true, unmarked: {unmarked}")
            concept_scored = [row[0] for row in rescued if row[2] != 0]
            if concept_scored:
                return fail(f"fallback rows carry no concept evidence, got scores on: {concept_scored}")

            # When the strict pass answers, the fallback must NOT fire — the
            # rows are the strict rows, and none of them is marked.
            confident = search(cur, ALL_WORDS, allow_fallback=True)
            if not confident:
                return fail("a query the strict pass answers must still answer with fallback allowed")
            leaked = [row[0] for row in confident if "fallback" in row[3]]
            if leaked:
                return fail(f"strict answers must not carry a fallback mark: {leaked}")

            # THE PHANTOM CONCEPT SCORE (defect f4a4405f, 2026-08-22). In
            # PostgreSQL least(1.0, NULL) ignores the NULL and returns 1.0, so
            # coalesce(least(1.0, ce.concept_score), 0) handed every section
            # with NO concept evidence a free 1.0 — real evidence, capped at
            # 1.0, could never outrank it, and the whole curation lane was
            # inert while looking wired. With zero approved curation in this
            # transaction, every strict row must score concept 0.
            phantom = [row[0] for row in confident if float(row[2]) != 0.0]
            if phantom:
                return fail(f"strict rows without curation must score concept 0, got: {phantom}")

            # Deterministic replay holds on the fallback lane too.
            if search(cur, ONE_WORD_OFF, allow_fallback=True) != rescued:
                return fail("fallback results must replay byte-identically")

            # The read and write roles can call the new shape; nothing public can.
            for role in ("carr_reader", "carr_writer"):
                role_exists = required_value(cur.execute(
                    "select count(*) from pg_roles where rolname=%s", (role,)
                ).fetchone(), "role existence")
                if role_exists and not required_value(cur.execute(
                    """select has_function_privilege(%s,
                         'public.search_doctrine_situations(text,uuid,text[],integer,text,boolean)',
                         'execute')""", (role,)
                ).fetchone(), "grant check"):
                    return fail(f"{role} lost execute on the recreated search function")
        conn.rollback()
        print("PASS: zero-hit fallback (strict lane untouched, fallback flagged, single function, grants held)")
        return 0
    except Exception as exc:
        conn.rollback()
        return fail(str(exc))
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
