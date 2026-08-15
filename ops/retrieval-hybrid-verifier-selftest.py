#!/usr/bin/env python3
"""Independent adversarial contract tests for hybrid retrieval.

This deliberately tests policy boundaries the builder's happy-path self-test
does not cover.  It only imports the pure candidate; it never reads doctrine
or contacts production.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from retrieval_hybrid import fuse_candidates  # noqa: E402
from retrieval_lexical import load_index_contract  # noqa: E402

SAFE_SCOPE = {
    "section_scope_applied_before_rank": True,
    "fts_scope_applied_before_rank": True,
    "fallback_scope_applied_before_rank": True,
}


def check(label: str, condition: bool) -> None:
    print(f"  {'ok' if condition else 'FAIL'}  {label}")
    if not condition:
        raise AssertionError(label)


def hit(target: str, rank: object, *, scope: str = "carr-internal") -> dict[str, object]:
    return {
        "target": target,
        "rank": rank,
        "scope_ref": scope,
        "current": True,
        "provenance_complete": True,
    }


# RET-002/003 class: a document-level section-index result may establish the
# document, but an exact FTS section must be retained as answer evidence.
for case_id, document, section in (
    ("RET-002", "runbook", "diagnosis-checklist-in-order-2-minutes"),
    ("RET-003", "playbook-review", "preamble"),
):
    result = fuse_candidates(
        section_hits=[hit(f"doctrine:{document}", 1)],
        fts_hits=[hit(f"{document}#{section}", 3)],
        scope_ref="carr-internal",
        **SAFE_SCOPE,
    )
    check(f"{case_id} returns the exact evidence section, not only its document",
          result["hits"][0]["target"] == f"{document}#{section}"
          and result["hits"][0]["section"] == section)

# Equal RRF totals are not arbitrary: the declared confidence order is exact
# FTS, section index, then broad OR fallback, before the stable document key.
tie = fuse_candidates(
    section_hits=[hit("doctrine:alpha", 1)],
    fts_hits=[hit("zeta#exact", 1)],
    scope_ref="carr-internal",
    top_k=2,
    **SAFE_SCOPE,
)
check("an exact-FTS RRF tie outranks a section-index tie before document-key order",
      [item["document"] for item in tie["hits"]] == ["zeta", "alpha"])

# A malformed rank must be rejected deliberately, rather than leaking a
# Python sort/conversion accident.  This is a fail-closed data-contract test.
try:
    fuse_candidates(
        section_hits=[], fts_hits=[],
        fallback_hits=[hit("malformed#section", "not-a-rank")],
        scope_ref="carr-internal",
        **SAFE_SCOPE,
    )
except ValueError as exc:
    check("malformed fallback rank fails closed with a contract error",
          "candidate rank" in str(exc))
else:
    raise AssertionError("malformed fallback rank was accepted")

for malformed_rank in (True, 1.5):
    try:
        fuse_candidates(
            section_hits=[], fts_hits=[],
            fallback_hits=[hit("ambiguous-rank#section", malformed_rank)],
            scope_ref="carr-internal",
            **SAFE_SCOPE,
        )
    except ValueError:
        pass
    else:
        raise AssertionError(f"ambiguous fallback rank {malformed_rank!r} was accepted")
check("boolean and fractional fallback ranks fail closed", True)

# The fallback's bound is a rank-window, not merely a list-count convention.
rank_outside_window = fuse_candidates(
    section_hits=[], fts_hits=[],
    fallback_hits=[hit("outside-window#section", 9)],
    scope_ref="carr-internal",
    fallback_limit=8,
    **SAFE_SCOPE,
)
check("OR fallback refuses candidates beyond its declared rank window",
      rank_outside_window["status"] == "no_answer")

unproven_scope = fuse_candidates(
    section_hits=[hit("doctrine:never-rank-unscoped", 1)],
    fts_hits=None,
    scope_ref="carr-internal",
)
check("missing upstream scope proof fails closed as Unknown",
      unproven_scope["status"] == "unknown"
      and unproven_scope["scope_applied_before_rank"] is False)


def index_contract(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "carr-section-index-v2",
        "scope_ref": "carr-internal",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_source_policy": "store-active-or-file-filtered-v1",
        "store_status": "verified",
    }
    value.update(overrides)
    return value


def contract_result(value: object) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "section-index.tsv"
        serialized = value if isinstance(value, str) else json.dumps(value)
        path.write_text(f"# contract\t{serialized}\n", encoding="utf-8")
        try:
            load_index_contract(path, expected_scope="carr-internal")
        except (ValueError, json.JSONDecodeError) as exc:
            return str(exc)
        return "pass"


check("a fresh verified index contract passes", contract_result(index_contract()) == "pass")
check("a stale index contract is refused",
      "freshness" in contract_result(index_contract(
          generated_at=(datetime.now(timezone.utc) - timedelta(hours=27)).isoformat())))
check("a future-dated index contract is refused",
      "freshness" in contract_result(index_contract(
          generated_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())))
check("a wrong-scope index contract is refused",
      "scope mismatch" in contract_result(index_contract(scope_ref="other-tenant")))
check("a store-fallback index cannot claim current provenance",
      "without verified store" in contract_result(index_contract(store_status="fallback_files")))
check("a malformed index contract fails closed",
      contract_result("{not-json") != "pass")

print("PASS: hybrid retrieval independent adversarial self-test")
