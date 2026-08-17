#!/usr/bin/env python3
# ci: db-gate
"""Disposable-DB proof that the two-target staging seed is bounded and atomic.

This gate exercises the importer core under CI's throwaway database.  Runtime
staging role selection is covered by the hermetic contract test; CI uses a
local database role and cannot honestly impersonate Neon ``app_writer``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import pathlib
import sys
import tempfile
import uuid
from dataclasses import replace

import psycopg


REPO = pathlib.Path(__file__).resolve().parent.parent
SEEDER = REPO / "pipelines" / "staging_retrieval_doctrine_seed.py"


def load_seed():
    spec = importlib.util.spec_from_file_location("staging_retrieval_seed_gate", SEEDER)
    if spec is None or spec.loader is None:
        raise RuntimeError("staging retrieval seed module is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def require(cur, sql: str, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"expected one row from: {sql[:80]}")
    return row[0]


def remap(item, *, slug: str, section_key: str, content_class: str):
    section = {**item.document["sections"][0], "section_key": section_key}
    document = {**item.document, "slug": slug, "sections": [section]}
    return replace(item, target=replace(item.target, slug=slug, section_key=section_key,
                                        content_class=content_class), document=document)


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("staging-retrieval-doctrine-seed-gate: DATABASE_URL is required", file=sys.stderr)
        return 1
    seed = load_seed()
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        runbook = root / "runbook.md"
        runbook.write_text(
            "# Runbook\n\n## Ignore\n\nnot seeded\n\n"
            "## Diagnosis checklist (in order, 2 minutes)\n\nseeded diagnosis\n",
            encoding="utf-8",
        )
        review = root / "playbook-review.md"
        review.write_text("# Playbook Review\n\nseeded review preamble\n\n## Ignore\n\nnot seeded\n", encoding="utf-8")
        old_vault = seed.VAULT
        seed.VAULT = root
        targets = (
            replace(seed.TARGETS[0], source_path=runbook,
                    source_sha256=hashlib.sha256(runbook.read_bytes()).hexdigest()),
            replace(seed.TARGETS[1], source_path=review,
                    source_sha256=hashlib.sha256(review.read_bytes()).hexdigest()),
        )
        fixture_authority = seed._fixture_manifest_authority_for_test(targets)
        parsed = seed._parse_fixture_targets_for_test(targets, fixture_authority)
        seed.VAULT = old_vault

        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            # Core success.  The outer rollback makes this gate replayable.
            batch_no = -int(uuid.uuid4().int % 2_000_000_000)
            receipt = seed.apply_parsed_seed(cur, batch_no, parsed)
            if receipt["state"] != "verified" or receipt["proposal_mutations"] != 0:
                raise RuntimeError("seed receipt is not verified/no-proposal-mutation")
            for item in parsed:
                count = require(
                    cur,
                    """select count(*) from doctrine_section s join doctrine_document d on d.id=s.document_id
                         where d.slug=%s and s.section_key=%s and d.visibility='shared' and s.status='active'""",
                    (item.target.slug, item.target.section_key),
                )
                if count != 1:
                    raise RuntimeError(f"{item.address}: target was not seeded exactly once")
                total = require(cur, "select count(*) from doctrine_section s join doctrine_document d on d.id=s.document_id where d.slug=%s", (item.target.slug,))
                if total != 1:
                    raise RuntimeError(f"{item.address}: non-target section leaked into seeded document")
            batch_state = require(cur, "select state from doctrine_migration_batch where batch_no=%s", (batch_no,))
            if batch_state != "verified":
                raise RuntimeError("verified staging seed batch is missing")
            proposals_before = require(cur, "select count(*) from retrieval_proposal")

            # A bad second insert aborts the savepoint, leaving neither target
            # nor a batch receipt from that failed attempt.
            atomic_first = remap(parsed[0], slug="atomic-seed-one", section_key="one", content_class="sop")
            bad_second = remap(parsed[1], slug="atomic-seed-two", section_key="two", content_class="not-a-class")
            failed_batch_no = batch_no - 1
            cur.execute("savepoint atomic_seed_failure")
            try:
                seed.apply_parsed_seed(cur, failed_batch_no, (atomic_first, bad_second))
            except Exception:
                cur.execute("rollback to savepoint atomic_seed_failure")
            else:
                raise RuntimeError("invalid second target was accepted")
            failed_batch = require(cur, "select count(*) from doctrine_migration_batch where batch_no=%s", (failed_batch_no,))
            if failed_batch != 0:
                raise RuntimeError("failed seed left a batch receipt outside its transaction")
            if require(cur, "select count(*) from doctrine_document where slug in ('atomic-seed-one','atomic-seed-two')") != 0:
                raise RuntimeError("failed seed left a partial target document outside its transaction")
            if require(cur, "select count(*) from retrieval_proposal") != proposals_before:
                raise RuntimeError("seeding changed retrieval proposals")
            conn.rollback()
    print("PASS: staging retrieval doctrine seed is two-target, verified, atomic, and proposal-neutral")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"staging-retrieval-doctrine-seed-gate: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
