#!/usr/bin/env python3
"""Pure lexical ranking used by both retrieval and its golden-query evaluator."""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "be", "do", "does", "how", "what", "which", "who",
    "when", "where", "why", "we", "our", "my", "your", "i", "you", "it",
    "this", "that", "these", "those", "about", "from", "at", "by", "as",
    "into", "can", "should", "would", "could", "did", "have", "has", "had",
    "will", "there", "any", "all", "me", "us", "they", "them", "their",
    "his", "her", "its", "if", "then", "than", "so", "not", "no", "get",
    "got", "need", "want", "find", "show", "tell", "give", "look", "up",
    "out", "new", "use",
}
KEEP_SHORT = {"al", "fl", "x", "ai", "cre", "npi", "dso", "sos", "loi", "na", "t1", "t2", "t3", "cpa"}
INDEX_CONTRACT_PREFIX = "# contract\t"
INDEX_SCHEMA = "carr-section-index-v2"
INDEX_SOURCE_POLICY = "store-active-or-file-filtered-v1"


def toks(text: str) -> list[str]:
    out = []
    for token in re.split(r"[^a-z0-9]+", text.lower()):
        if not token or token in STOP:
            continue
        if len(token) < 3 and token not in KEEP_SHORT:
            continue
        out.append(token[:-1] if len(token) > 4 and token.endswith("s") else token)
    return out


@dataclass(frozen=True)
class IndexRow:
    path: str
    start: int
    end: int
    level: int
    header: str
    parents: str
    gist: str
    source: str = "file"
    section_key: str = ""


@dataclass(frozen=True)
class RankedRow:
    score: float
    row: IndexRow


def load_index_contract(
    path: str | os.PathLike[str], *, expected_scope: str,
    max_age_hours: float = 26.0,
) -> dict[str, object]:
    """Validate the derived index's scope, freshness, and source provenance."""
    contract: dict[str, object] | None = None
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.startswith(INDEX_CONTRACT_PREFIX):
                value = json.loads(line[len(INDEX_CONTRACT_PREFIX):])
                if not isinstance(value, dict):
                    raise ValueError("section index contract must be an object")
                contract = value
                break
            if not line.startswith("#"):
                break
    if contract is None:
        raise ValueError("section index contract missing")
    if contract.get("schema_version") != INDEX_SCHEMA:
        raise ValueError("section index schema is unsupported")
    if contract.get("scope_ref") != expected_scope:
        raise ValueError("section index scope mismatch")
    if contract.get("current_source_policy") != INDEX_SOURCE_POLICY:
        raise ValueError("section index source policy is unsupported")
    if contract.get("store_status") != "verified":
        raise ValueError("section index was built without verified store sources")
    generated = contract.get("generated_at")
    if not isinstance(generated, str):
        raise ValueError("section index generated_at missing")
    try:
        generated_at = datetime.fromisoformat(generated.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("section index generated_at invalid") from exc
    if generated_at.tzinfo is None:
        raise ValueError("section index generated_at must carry a timezone")
    age_hours = (datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc)).total_seconds() / 3600
    if age_hours < -0.25 or age_hours > max_age_hours:
        raise ValueError("section index freshness outside allowed window")
    return contract


def load_index(path: str | os.PathLike[str]) -> list[IndexRow]:
    rows: list[IndexRow] = []
    with Path(path).open(encoding="utf-8") as handle:
        reader = csv.reader((line for line in handle if not line.startswith("#")), delimiter="\t")
        for raw in reader:
            if len(raw) < 7:
                continue
            rows.append(IndexRow(
                path=raw[0], start=int(raw[1]), end=int(raw[2]), level=int(raw[3]),
                header=raw[4], parents=raw[5], gist=raw[6],
                source=raw[7] if len(raw) > 7 else "file",
                section_key=raw[8] if len(raw) > 8 else "",
            ))
    return rows


def score_row(row: IndexRow, query: str) -> float:
    query_lower = query.lower()
    terms = set(toks(query_lower))
    filename = os.path.splitext(os.path.basename(row.path))[0]
    directories = os.path.dirname(row.path)
    score = 0.0
    score += 3.0 * len(terms & set(toks(row.header)))
    score += 3.0 * len(terms & set(toks(filename)))
    score += 2.0 * len(terms & set(toks(row.parents)))
    score += 2.0 * len(terms & set(toks(row.gist)))
    score += 1.0 * len(terms & set(toks(directories)))
    if len(query_lower) > 6 and (query_lower in row.header.lower() or query_lower in row.gist.lower()):
        score += 4.0
    return score


def rank_rows(
    rows: Iterable[IndexRow], query: str, *, top: int | None = 8,
    migrated_paths: set[str] | None = None,
    collapse_documents: bool = True,
) -> list[RankedRow]:
    migrated = migrated_paths or set()
    scored: list[RankedRow] = []
    for row in rows:
        score = score_row(row, query)
        if score <= 0:
            continue
        if row.source == "file" and row.path in migrated:
            continue
        scored.append(RankedRow(score=score, row=row))

    if not collapse_documents:
        ranked = sorted(
            scored,
            key=lambda item: (-item.score, item.row.path, item.row.section_key, item.row.header),
        )
        return ranked[:top] if top is not None else ranked

    best: dict[str, RankedRow] = {}
    for candidate in sorted(
        scored,
        key=lambda item: (-item.score, item.row.level == 0, item.row.end - item.row.start),
    ):
        prior = best.get(candidate.row.path)
        if (
            prior is None
            or candidate.score > prior.score + 0.01
            or (
                abs(candidate.score - prior.score) <= 0.01
                and candidate.row.level > prior.row.level
            )
        ):
            best[candidate.row.path] = candidate
    ranked = sorted(best.values(), key=lambda item: -item.score)
    return ranked[:top] if top is not None else ranked


def rank_index(
    index_path: str | os.PathLike[str], query: str, *, top: int | None = 8,
    migrated_paths: set[str] | None = None,
) -> list[RankedRow]:
    return rank_rows(load_index(index_path), query, top=top, migrated_paths=migrated_paths)


def rank_index_sections(
    index_path: str | os.PathLike[str], query: str, *, top: int | None = 8,
    migrated_paths: set[str] | None = None,
) -> list[RankedRow]:
    """Return section rows without collapsing them to one document result."""
    return rank_rows(
        load_index(index_path), query, top=top, migrated_paths=migrated_paths,
        collapse_documents=False,
    )


def target_for_row(row: IndexRow) -> str:
    """Return a stable document or document-section pointer for an index row."""
    if row.source == "store" and row.section_key:
        return f"{row.path.removeprefix('doctrine:')}#{row.section_key}"
    return row.path
