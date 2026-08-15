#!/usr/bin/env python3
"""Contract tests for the feature-gated hybrid retrieval candidate."""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import tempfile
from datetime import datetime, timezone
from unittest import mock


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
from retrieval_hybrid import fuse_candidates, normalize_document_identity  # noqa: E402


PRIMARY_SCOPE_PROOF = {
    "section_scope_applied_before_rank": True,
    "fts_scope_applied_before_rank": True,
}


def check(label: str, condition: bool) -> None:
    print(f"  {'ok' if condition else 'FAIL'}  {label}")
    if not condition:
        raise AssertionError(label)


def hit(target: str, rank: int, *, scope: str = "carr-internal", current: bool = True,
        provenance: bool = True) -> dict[str, object]:
    return {
        "target": target,
        "rank": rank,
        "scope_ref": scope,
        "current": current,
        "provenance_complete": provenance,
    }


check("doctrine document and section pointers share one document identity",
      normalize_document_identity("doctrine:runbook")
      == normalize_document_identity("runbook#diagnosis-checklist"))

fused = fuse_candidates(
    section_hits=[hit("doctrine:runbook", 1)],
    fts_hits=[hit("runbook#diagnosis-checklist", 2)],
    scope_ref="carr-internal",
    **PRIMARY_SCOPE_PROOF,
)
check("two primary engines produce a result", fused["status"] == "ok")
check("rank fusion joins document-shaped and section-shaped sources", fused["hits"][0]["document"] == "runbook")
check("two-stage selection preserves the FTS evidence section", fused["hits"][0]["section"] == "diagnosis-checklist")
check("the candidate exposes both supporting sources", fused["hits"][0]["sources"] == ["doctrine_fts", "section_index"])
check("primary evidence suppresses the broad fallback", fused["fallback_used"] is False)

filtered = fuse_candidates(
    section_hits=[hit("doctrine:good", 3)],
    fts_hits=[
        hit("bad#old", 1, current=False),
        hit("wrong-scope#section", 1, scope="other-tenant"),
        hit("unproven#section", 1, provenance=False),
        hit("archive/forbidden#section", 1),
    ],
    scope_ref="carr-internal",
    forbidden_target_patterns=["archive/"],
    **PRIMARY_SCOPE_PROOF,
)
check("scope, currentness, provenance, and forbidden checks run before fusion",
      filtered["status"] == "ok" and filtered["hits"][0]["document"] == "good")

tie = fuse_candidates(
    section_hits=[hit("doctrine:zeta", 1), hit("doctrine:alpha", 1)],
    fts_hits=[],
    scope_ref="carr-internal",
    **PRIMARY_SCOPE_PROOF,
)
check("tie breaks deterministically by document identity", [item["document"] for item in tie["hits"]] == ["alpha", "zeta"])

fallback = fuse_candidates(
    section_hits=[],
    fts_hits=[],
    fallback_hits=[hit("fallback#exact-section", 1), hit("second#section", 2), hit("third#section", 3)],
    scope_ref="carr-internal",
    top_k=1,
    fallback_scope_applied_before_rank=True,
    **PRIMARY_SCOPE_PROOF,
)
check("bounded fallback is used only when both primary generators lack evidence",
      fallback["fallback_used"] is True and [item["document"] for item in fallback["hits"]] == ["fallback"])

try:
    fuse_candidates(
        section_hits=[], fts_hits=[], fallback_hits=[hit("malformed#section", 0)],
        scope_ref="carr-internal", fallback_scope_applied_before_rank=True,
        **PRIMARY_SCOPE_PROOF,
    )
except ValueError:
    check("malformed fallback ranks fail closed", True)
else:
    raise AssertionError("malformed fallback rank was accepted")

one_available = fuse_candidates(
    section_hits=[hit("doctrine:available", 1)],
    fts_hits=None,
    scope_ref="carr-internal",
    section_scope_applied_before_rank=True,
)
check("one unavailable engine degrades to the usable engine", one_available["status"] == "ok")

both_unavailable = fuse_candidates(section_hits=None, fts_hits=None, scope_ref="carr-internal")
check("two unavailable primary engines report explicit Unknown", both_unavailable["status"] == "unknown")

no_answer = fuse_candidates(section_hits=[], fts_hits=[], scope_ref="carr-internal", **PRIMARY_SCOPE_PROOF)
check("an empty measured result reports no-answer instead of guessing", no_answer["status"] == "no_answer")

# Integration boundary: the command must actually invoke the hybrid candidate
# when its explicit feature gate is present.  The store is forced unavailable
# so this remains a deterministic, offline test of the executable read path.
import retrieve  # noqa: E402
import lib.record_sources as record_sources  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    vault = pathlib.Path(tmp)
    automation = vault / "Automation"
    automation.mkdir()
    contract = {
        "schema_version": "carr-section-index-v2",
        "scope_ref": "carr-internal",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_source_policy": "store-active-or-file-filtered-v1",
        "store_status": "verified",
    }
    (automation / "section-index.tsv").write_text(
        "# section-index.tsv — synthetic\n"
        + "# contract\t" + __import__("json").dumps(contract, sort_keys=True, separators=(",", ":")) + "\n"
        + "# path\tstart\tend\tlevel\theader\tparents\tgist\tsource\tsection_key\n"
        "doctrine:runbook\t1\t1\t1\tRecord layer outage diagnosis runbook\t"
        "CARR Runbook\tCurrent two-minute checklist\tstore\t"
        "diagnosis-checklist-in-order-2-minutes\n",
        encoding="utf-8",
    )
    output = io.StringIO()
    argv = [
        "retrieve.py", "--hybrid", "--vault", str(vault), "-n", "1",
        "record", "layer", "outage", "diagnosis", "runbook",
    ]
    with mock.patch.object(sys, "argv", argv), \
            mock.patch.object(record_sources, "doctrine_migrated_paths", return_value=set()), \
            mock.patch.object(record_sources, "_connect", side_effect=RuntimeError("offline test")), \
            contextlib.redirect_stdout(output):
        retrieve.main()
    rendered = output.getvalue()
    check("the explicit CLI feature gate executes the hybrid retrieval path",
          "HYBRID candidate (ok; feature-gated)" in rendered)
    check("the executable hybrid path returns the exact evidence section",
          '"section_key": "diagnosis-checklist-in-order-2-minutes"' in rendered)

    # The same arbitrary index without its explicit provenance contract must
    # not be silently relabelled as CARR/current/provenanced.
    (automation / "section-index.tsv").write_text(
        "# path\tstart\tend\tlevel\theader\tparents\tgist\tsource\tsection_key\n"
        "doctrine:runbook\t1\t1\t1\tRecord layer outage diagnosis runbook\t"
        "CARR Runbook\tCurrent two-minute checklist\tstore\t"
        "diagnosis-checklist-in-order-2-minutes\n",
        encoding="utf-8",
    )
    output = io.StringIO()
    with mock.patch.object(sys, "argv", argv), \
            mock.patch.object(record_sources, "doctrine_migrated_paths", return_value=set()), \
            mock.patch.object(record_sources, "_connect", side_effect=RuntimeError("offline test")), \
            contextlib.redirect_stdout(output):
        retrieve.main()
    check("an uncontracted custom index is refused instead of receiving a false scope claim",
          "section-index candidate unavailable (section index contract missing)" in output.getvalue()
          and "candidate generators unavailable" in output.getvalue())

print("PASS: hybrid retrieval self-test")
