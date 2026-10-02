"""review_tiers — the Python reader for ops/config/review-tiers.v1.json.

doctrine: engineering-workflow-sop

One map decides how much review a changed path gets (engineering-workflow-sop
section 15, "Risk tier by path"). Before it, four places each kept their own
list and they disagreed. The consumers now ask this module:

    ops/jev_done_checks.py        review triage floors a file "high" at tier 3
    pipelines/run_codex_review.py council security lens arms at tier 2
    ops/jev_code_partition.py     drops review noise before a model reads code

The Worker's merge controller cannot read files at request time, so it reads
mcp-server/src/review-tiers.generated.js, which ops/sync-review-tiers.py
renders from the same JSON and checks for drift. The matching below and the
JS reader in mcp-server/src/review-tiers.js implement the same five string
operations; tier-vectors.v1.json and both test suites hold them equal.

Tier 3 never blocks a human: it means the merge controller declines to
auto-merge and a human presses merge (decision 8daefaba).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Iterable

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(REPO, "ops", "config", "review-tiers.v1.json")
SCHEMA_VERSION = "review-tiers.v1"
MATCH_KINDS = ("path", "prefix", "suffix", "basename", "contains")
CLASSES = ("protected", "security_sensitive", "adversarial")
TIERS = (1, 2, 3)
TOP_TIER = 3

# Consumer thresholds, one place. The map says what a path IS; these say what
# each consumer does about it.
MERGE_REFUSAL_TIER = 3
TRIAGE_HIGH_FLOOR_TIER = 3
SECURITY_LENS_TIER = 2


def _is_tier(value) -> bool:
    """An int tier; a bool is refused even though True == 1 in Python."""
    return type(value) is int and value in TIERS


def validate(doc) -> list[str]:
    """Problems with a map document; empty means well formed."""
    problems = []
    if not isinstance(doc, dict):
        return ["map must be an object"]
    if doc.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    for key in ("purpose", "provenance"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            problems.append(f"{key} missing")
    if not _is_tier(doc.get("default_tier")):
        problems.append("default_tier must be 1, 2 or 3")

    def check_rows(name, rows, tiered):
        if not isinstance(rows, list) or not rows:
            problems.append(f"{name} must be a non-empty list")
            return
        seen = set()
        for index, row in enumerate(rows):
            where = f"{name}[{index}]"
            if not isinstance(row, dict):
                problems.append(f"{where} must be an object")
                continue
            rid = row.get("id")
            if not isinstance(rid, str) or not rid:
                problems.append(f"{where}: id missing")
            elif rid in seen:
                problems.append(f"{where}: duplicate id {rid}")
            seen.add(rid)
            if row.get("match") not in MATCH_KINDS:
                problems.append(f"{where}: match must be one of {MATCH_KINDS}")
            if not isinstance(row.get("pattern"), str) or not row["pattern"]:
                problems.append(f"{where}: pattern missing")
            if "case_insensitive" in row and not isinstance(row["case_insensitive"], bool):
                problems.append(f"{where}: case_insensitive must be a boolean")
            if not isinstance(row.get("why"), str) or not row["why"].strip():
                problems.append(f"{where}: why missing")
            if tiered:
                if not _is_tier(row.get("tier")):
                    problems.append(f"{where}: tier must be 1, 2 or 3")
                if row.get("class") not in CLASSES:
                    problems.append(f"{where}: class must be one of {CLASSES}")

    check_rows("rules", doc.get("rules"), True)
    check_rows("noise_exclusions", doc.get("noise_exclusions"), False)
    check_rows("never_exclude", doc.get("never_exclude"), False)
    return problems


def load(path: str = MAP_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def _map() -> dict:
    doc = load()
    problems = validate(doc)
    if problems:
        raise ValueError(f"{MAP_PATH} is malformed: {problems[:3]}")
    return doc


def normalize(path) -> str | None:
    """Repo-relative, forward slashes, no leading './'. None when unusable."""
    if not isinstance(path, str) or not path:
        return None
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path or None


def matches(row: dict, path: str) -> bool:
    pattern = row["pattern"]
    subject = path
    if row.get("case_insensitive"):
        subject, pattern = subject.lower(), pattern.lower()
    kind = row["match"]
    if kind == "path":
        return subject == pattern
    if kind == "prefix":
        return subject.startswith(pattern)
    if kind == "suffix":
        return subject.endswith(pattern)
    if kind == "basename":
        return subject.rsplit("/", 1)[-1] == pattern
    if kind == "contains":
        return pattern in subject
    raise ValueError(f"unknown match kind {kind!r}")


def tier_for_path(path, doc: dict | None = None) -> int:
    """The highest tier of every rule matching `path`, else the default.
    A path that cannot be read is the top tier: fail toward more review."""
    doc = doc or _map()
    normal = normalize(path)
    if normal is None:
        return TOP_TIER
    tier = doc["default_tier"]
    for row in doc["rules"]:
        if row["tier"] > tier and matches(row, normal):
            tier = row["tier"]
    return tier


def tier_for_paths(paths: Iterable, doc: dict | None = None) -> int:
    """A change set takes the highest tier of its paths."""
    doc = doc or _map()
    return max((tier_for_path(p, doc) for p in paths), default=doc["default_tier"])


def is_review_noise(path, doc: dict | None = None) -> bool:
    """True when `path` is dropped before a model reads a diff. Never lowers a tier."""
    doc = doc or _map()
    normal = normalize(path)
    if normal is None:
        return False
    if any(matches(row, normal) for row in doc["never_exclude"]):
        return False
    return any(matches(row, normal) for row in doc["noise_exclusions"])
