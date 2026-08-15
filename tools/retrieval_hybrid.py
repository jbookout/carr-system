#!/usr/bin/env python3
"""Pure, feature-gated hybrid retrieval candidate.

The candidate deliberately does not read storage or choose a tenant.  Callers
must provide already-scoped candidates from the section index and doctrine FTS.
This keeps safety filtering observable and makes the ranking policy testable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any


SOURCE_ORDER = ("doctrine_fts", "section_index", "doctrine_fts_or")
SOURCE_PRIORITY = {source: position for position, source in enumerate(SOURCE_ORDER)}


@dataclass(frozen=True)
class Candidate:
    target: str
    rank: int
    scope_ref: str
    current: bool
    provenance_complete: bool
    source: str

    @property
    def document(self) -> str:
        return normalize_document_identity(self.target)

    @property
    def section(self) -> str | None:
        pointer = self.target.removeprefix("doctrine:")
        if "#" not in pointer:
            return None
        section = pointer.split("#", 1)[1].strip()
        return section or None


def normalize_document_identity(target: str) -> str:
    """Map ``doctrine:slug`` and ``slug#section`` to the same document key."""
    if not isinstance(target, str) or not target.strip():
        raise ValueError("retrieval target must be a non-empty string")
    pointer = target.strip().removeprefix("doctrine:")
    document = pointer.split("#", 1)[0].strip()
    if not document:
        raise ValueError("retrieval target must name a document")
    return document


def _coerce(raw: Mapping[str, Any], source: str) -> Candidate:
    rank = raw.get("rank")
    if isinstance(rank, bool) or not isinstance(rank, int):
        raise ValueError(f"{source} candidate rank must be a positive integer")
    if rank < 1:
        raise ValueError(f"{source} candidate rank must be a positive integer")
    target = raw.get("target")
    scope_ref = raw.get("scope_ref")
    if not isinstance(scope_ref, str) or not scope_ref:
        raise ValueError(f"{source} candidate scope_ref must be a non-empty string")
    return Candidate(
        target=target if isinstance(target, str) else "",
        rank=rank,
        scope_ref=scope_ref,
        current=raw.get("current") is True,
        provenance_complete=raw.get("provenance_complete") is True,
        source=source,
    )


def _filter_before_rank(
    hits: Sequence[Mapping[str, Any]], *, source: str, scope_ref: str,
    forbidden: Sequence[re.Pattern[str]],
) -> list[Candidate]:
    """Drop unsafe candidates before any rank or fusion operation."""
    eligible: list[Candidate] = []
    for raw in hits:
        candidate = _coerce(raw, source)
        if candidate.scope_ref != scope_ref:
            continue
        if not candidate.current or not candidate.provenance_complete:
            continue
        if not candidate.target:
            continue
        try:
            normalize_document_identity(candidate.target)
        except ValueError:
            continue
        if any(pattern.search(candidate.target) for pattern in forbidden):
            continue
        eligible.append(candidate)
    return sorted(eligible, key=lambda hit: (hit.rank, hit.target))


def _best_per_document(candidates: Sequence[Candidate]) -> dict[str, Candidate]:
    """One engine may have many sections of a document; its best rank counts once."""
    best: dict[str, Candidate] = {}
    for candidate in candidates:
        prior = best.get(candidate.document)
        if prior is None or (candidate.rank, candidate.target) < (prior.rank, prior.target):
            best[candidate.document] = candidate
    return best


def _evidence_for(document: str, candidates: Sequence[Candidate]) -> Candidate:
    options = [candidate for candidate in candidates if candidate.document == document]
    # An explicit section is the evidence-bearing answer.  Then prefer direct
    # FTS over the section index and resolve all residual ties canonically.
    return min(
        options,
        key=lambda candidate: (
            candidate.section is None,
            SOURCE_PRIORITY[candidate.source],
            candidate.rank,
            candidate.target,
        ),
    )


def fuse_candidates(
    *,
    section_hits: Sequence[Mapping[str, Any]] | None,
    fts_hits: Sequence[Mapping[str, Any]] | None,
    fallback_hits: Sequence[Mapping[str, Any]] | None = None,
    scope_ref: str,
    section_scope_applied_before_rank: bool = False,
    fts_scope_applied_before_rank: bool = False,
    fallback_scope_applied_before_rank: bool = False,
    forbidden_target_patterns: Sequence[str] = (),
    top_k: int = 3,
    rrf_k: int = 60,
    fallback_limit: int = 8,
) -> dict[str, Any]:
    """Fuse scoped primary candidates with deterministic reciprocal-rank fusion.

    ``None`` means that an engine was unavailable; an empty list means it ran
    and found no eligible evidence.  The broad OR fallback is eligible only
    after *both* measured primary generators have no safe candidates, and it
    is bounded before fusion.  Therefore absence is represented explicitly:
    ``unknown`` for no usable primary engine and ``no_answer`` for a measured
    search that found no evidence.
    """
    if not isinstance(scope_ref, str) or not scope_ref:
        raise ValueError("scope_ref must be a non-empty string")
    if top_k < 1 or rrf_k < 1 or fallback_limit < 1:
        raise ValueError("top_k, rrf_k, and fallback_limit must be positive")
    forbidden = [re.compile(pattern, re.IGNORECASE) for pattern in forbidden_target_patterns]

    # A row-level scope label cannot prove it was filtered before a generator's
    # top-N cutoff.  Accept source results only with that generator's explicit
    # attestation; unsafe input is treated as unavailable, never re-ranked.
    section_available = section_hits is not None and section_scope_applied_before_rank is True
    fts_available = fts_hits is not None and fts_scope_applied_before_rank is True
    if not section_available and not fts_available:
        return {
            "status": "unknown",
            "reason": "primary_engines_unavailable",
            "hits": [],
            "fallback_used": False,
            "scope_applied_before_rank": False,
        }

    section = _filter_before_rank(section_hits or (), source="section_index", scope_ref=scope_ref, forbidden=forbidden) if section_available else []
    fts = _filter_before_rank(fts_hits or (), source="doctrine_fts", scope_ref=scope_ref, forbidden=forbidden) if fts_available else []
    primary = [*fts, *section]
    fallback_used = False

    # Fallback is a query expansion of FTS, so it cannot be trusted to repair
    # an unavailable FTS engine.  It is never mixed into a result that already
    # has a safe primary candidate.
    fallback: list[Candidate] = []
    if (not primary and section_available and fts_available and fallback_hits is not None
            and fallback_scope_applied_before_rank is True):
        eligible_fallback = _filter_before_rank(
            fallback_hits, source="doctrine_fts_or", scope_ref=scope_ref, forbidden=forbidden,
        )
        # The limit is a rank window, not just a maximum number of records.
        fallback = [candidate for candidate in eligible_fallback if candidate.rank <= fallback_limit][:fallback_limit]
        fallback_used = bool(fallback)

    all_candidates = [*primary, *fallback]
    if not all_candidates:
        return {
            "status": "no_answer",
            "hits": [],
            "fallback_used": False,
            "scope_applied_before_rank": True,
        }

    by_source = {
        source: _best_per_document([candidate for candidate in all_candidates if candidate.source == source])
        for source in SOURCE_ORDER
    }
    scores: dict[str, float] = {}
    documents: set[str] = set()
    for source, best in by_source.items():
        del source
        for document, candidate in best.items():
            documents.add(document)
            scores[document] = scores.get(document, 0.0) + 1.0 / (rrf_k + candidate.rank)
    best_source_priority = {
        document: min(
            SOURCE_PRIORITY[source]
            for source, best in by_source.items()
            if document in best
        )
        for document in documents
    }
    ordered_documents = sorted(
        documents,
        key=lambda document: (-scores[document], best_source_priority[document], document),
    )[:top_k]
    hits: list[dict[str, Any]] = []
    for rank, document in enumerate(ordered_documents, 1):
        evidence = _evidence_for(document, all_candidates)
        sources = [
            source for source in SOURCE_ORDER
            if document in by_source[source]
        ]
        hits.append({
            "target": evidence.target,
            "document": document,
            "section": evidence.section,
            "rank": rank,
            "rrf_score": round(scores[document], 12),
            "sources": sources,
            "current": True,
            "provenance_complete": True,
            "scope_ref": scope_ref,
        })
    return {
        "status": "ok",
        "hits": hits,
        "fallback_used": fallback_used,
        "scope_applied_before_rank": True,
    }
