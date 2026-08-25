#!/usr/bin/env python3
"""Refuse a rule-enforceability audit row that says 'nothing enforces this'
while the tree already names that rule in a live enforcement artifact.

WHY THIS EXISTS. The 2026-08-14 rule-enforceability audit produced
audits/rule-enforceability-audit-2026-08-14.tsv: one row per active rule,
bucketed E (enforced), P (partial), U (specified but unbuilt), J (judgment).
Buckets P and U are a WORK QUEUE — open loop 410 tells the next session to pick
rows out of it — and every row carries an `evidence` column recording what the
auditor checked at the time.

That queue has no way to learn. Rows are closed by whichever session builds the
gate, in its own branch, and nothing walks back to the .tsv. Within a day of the
audit, 11 of its 46 U rows named a gate that already existed. A session picking
from the queue spends a full probe cycle per row discovering this, and the loop's
own method text has to warn about it in prose ("PROBE IT FIRST ... two candidates
this session turned out to guard doors that do not exist"). Prose does not bind.
This does.

It is also the top known failure class in this system by a wide margin:
`dated-artifact-read-as-present-state`, with most caught by a human rather than
by any check. An audit table is exactly that shape — accurate the morning it was
written, silently wrong afterwards. No count is quoted here: this file carried
"11 occurrences" and the ledger moved to twelve the same week, which would have
made a file about stale figures carry one. `standing-context` returns the live
ledger.

THE PREDICATE, and why it is a predicate and not a judgment. Deciding whether a
rule is "really" enforced is a judgment, and a gate that tried to make it would
be wrong often enough to get deleted. So this check does not grade enforcement
at all. It looks for a row CONTRADICTING ITSELF: the row's own evidence column
asserts that nothing exists ("No existing check", "Verified NOT present"), and
the rule's id is nonetheless written into a hook, a githook, a selftest or a
lint. Those two facts cannot both be current. Something changed after the row
was written, and the row is the thing that is stale.

That keeps the false-positive rate at essentially zero, because the row itself
supplied the negative claim. It also deliberately under-reports: a row that
never claimed anything is left alone even if a gate now covers it. Under-report
was chosen over over-report so that the fix is always "correct one demonstrably
wrong row", never "argue with the checker about a judgment call".

WHAT THE REMEDY IS. Re-triage the row: change its bucket to E or P and replace
the evidence text with what is true now. That is a one-line edit and it is always
available, which matters — a gate that punishes an honest interim state gets
removed, and then nothing is checked at all (the same design ruling that governs
ops/rule-enforcement-map-check.py's placeholder ageing).
"""
from __future__ import annotations

import csv
import fnmatch
import json
import os
import re
import sys
from glob import glob

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Buckets that assert open work. E (enforced) and J (judgment) make no claim
# this check can contradict.
OPEN_BUCKETS = {"P", "U"}

# The row's own negative claim. Kept literal and narrow on purpose: each pattern
# is a phrase the auditor actually used to mean "I looked and there is nothing",
# not a paraphrase of one.
NOTHING_EXISTS = re.compile(
    r"no existing check"
    r"|verified not present"
    r"|no such check exists"
    r"|currently only guards",
    re.I,
)

# What counts as a live enforcement artifact. Hooks and githooks DENY; selftests
# are the fixture that proves a gate is wired and running (ops/ci.sh discovers
# ops/*-selftest.py by glob, so a selftest naming a rule means that rule's gate
# is under test in CI); lints refuse content. Everything else in the tree may
# mention a rule id in passing without enforcing anything.
ARTIFACT_GLOBS = (
    "hooks/*.py",
    "ops/*-selftest.py",
    "ops/githooks/*.py",
    "ops/*-check.py",
    "tools/*-lint.py",
    "tools/*-check.py",
)

# Vendored trees carry unrelated code and can be enormous; never walk them.
SKIP_DIRS = {"__pycache__", "doc-convo", "node_modules", "vendor"}

RULE_ID = re.compile(r"^[0-9a-f]{8}$")
AUDIT_FIELDS = ("id", "plain_name", "bucket", "enforcement_or_sketch", "evidence")
AUDIT_BUCKETS = {"E", "P", "U", "J"}
MAP_PATH = os.path.join("ops", "config", "rule-enforcement-map.json")

# THE ESCAPE HATCH, and why the check needs one to stay alive.
#
# A rule id inside an enforcement artifact is not always enforcement. Hooks in
# this repo cite the rule they were BUILT UNDER in their header prose —
# gate_paths.py names a8c55a47 to explain why two gates share one path list, and
# guard-unattended.py names 94806da2 in a comment about why a hostname was
# verified live. Neither file enforces the rule it names. Both were verified by
# hand on 2026-08-15 and both are honest U rows.
#
# Without a way to say so, those two rows fail this check forever, the only way
# to get CI green is to mis-triage them as enforced, and the check ends up
# actively corrupting the table it was written to keep true. So a row may name
# the artifacts whose mention is incidental:
#
#     [mention-only: hooks/gate_paths.py, hooks/guard-unattended.py]
#
# anywhere in its evidence column. Those paths stop counting FOR THAT ROW ONLY.
# The judgment stays in the table where a human reads it, next to the evidence
# it qualifies, instead of being buried in this file as a silent exclusion list
# — and a row that suppresses everything is visibly a row that suppressed
# everything.
MENTION_ONLY = re.compile(r"\[mention-only:\s*([^\]]*)\]", re.I)


def suppressed_paths(evidence: str) -> set[str]:
    """Artifact paths this row has explicitly marked as incidental mentions."""
    out: set[str] = set()
    for block in MENTION_ONLY.findall(evidence or ""):
        for item in block.split(","):
            item = item.strip()
            if item:
                out.add(item)
    return out


def newest_audit(repo: str) -> str | None:
    """The most recent audit table, or None if the audits dir has none."""
    found = sorted(glob(os.path.join(repo, "audits", "rule-enforceability-audit-*.tsv")))
    return found[-1] if found else None


def active_rule_ids(repo: str) -> tuple[set[str], list[str]]:
    """Read the versioned active inventory that the audit must cover exactly."""
    path = os.path.join(repo, MAP_PATH)
    try:
        with open(path, encoding="utf-8") as fh:
            mapping = json.load(fh)
    except (OSError, ValueError) as exc:
        return set(), [f"cannot read {MAP_PATH}: {exc}"]

    scopes = mapping.get("active_rule_ids")
    if not isinstance(scopes, dict) or not scopes:
        return set(), [f"{MAP_PATH} has no active_rule_ids inventory"]

    ids: list[str] = []
    errors: list[str] = []
    for scope, scope_ids in scopes.items():
        if not isinstance(scope, str) or not scope:
            errors.append("active_rule_ids has an invalid scope")
            continue
        if not isinstance(scope_ids, list):
            errors.append(f"active_rule_ids.{scope} is not a list")
            continue
        for rid in scope_ids:
            if not isinstance(rid, str) or not RULE_ID.fullmatch(rid):
                errors.append(f"active_rule_ids.{scope} has malformed id {rid!r}")
            else:
                ids.append(rid)
    if len(ids) != len(set(ids)):
        errors.append("active_rule_ids contains duplicate ids")
    return set(ids), errors


def inventory_errors(repo: str, audit: str | None) -> list[str]:
    """Return every structural or membership error in the current audit table.

    The enforcement map is a reviewed, versioned projection of the active store.
    The audit is an assessment *of that same population*, not a historical sample;
    it therefore fails closed when either source cannot be joined exactly.
    """
    expected, errors = active_rule_ids(repo)
    if audit is None:
        return errors + ["no rule-enforceability audit table found"]

    try:
        with open(audit, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            if reader.fieldnames != list(AUDIT_FIELDS):
                return errors + [
                    f"{os.path.relpath(audit, repo)} has malformed header "
                    f"(expected tab-separated {', '.join(AUDIT_FIELDS)})"
                ]
            audit_ids: list[str] = []
            for line, row in enumerate(reader, start=2):
                if row is None or None in row:
                    errors.append(f"row {line} has the wrong number of columns")
                    continue
                if any(not isinstance(row.get(field), str) or not row[field].strip()
                       for field in AUDIT_FIELDS):
                    errors.append(f"row {line} has a blank required field")
                    continue
                rid = row["id"].strip()
                if not RULE_ID.fullmatch(rid):
                    errors.append(f"row {line} has malformed id {rid!r}")
                    continue
                bucket = row["bucket"].strip()
                if bucket not in AUDIT_BUCKETS:
                    errors.append(f"row {line} has invalid bucket {bucket!r}")
                    continue
                audit_ids.append(rid)
    except OSError as exc:
        return errors + [f"cannot read {os.path.relpath(audit, repo)}: {exc}"]

    seen: set[str] = set()
    duplicates = sorted({rid for rid in audit_ids if rid in seen or seen.add(rid)})
    if duplicates:
        errors.append("audit has duplicate id(s): " + ", ".join(duplicates))
    actual = set(audit_ids)
    missing = sorted(expected - actual)
    extras = sorted(actual - expected)
    if missing:
        errors.append("audit is missing active map id(s): " + ", ".join(missing))
    if extras:
        errors.append("audit contains inactive/unknown id(s): " + ", ".join(extras))
    return errors


def enforcement_artifacts(repo: str) -> dict[str, str]:
    """Path -> text for every file matching ARTIFACT_GLOBS, read once."""
    out: dict[str, str] = {}
    for top in ("hooks", "ops", "tools"):
        base = os.path.join(repo, top)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".venv")
            ]
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, repo)
                if not any(fnmatch.fnmatch(rel, g) for g in ARTIFACT_GLOBS):
                    continue
                try:
                    with open(full, errors="ignore") as fh:
                        out[rel] = fh.read()
                except OSError:
                    continue
    return out


def stale_rows(repo: str) -> tuple[list[tuple[str, str, str, list[str]]], str | None]:
    """Rows whose own 'nothing exists' claim the tree contradicts.

    Returns (findings, audit_path). Each finding is
    (rule_id, bucket, plain_name, [artifact paths that name the rule]).
    """
    audit = newest_audit(repo)
    if audit is None:
        return [], None
    artifacts = enforcement_artifacts(repo)
    findings = []
    with open(audit, newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            rid = (row.get("id") or "").strip()
            if not RULE_ID.match(rid):
                continue
            if (row.get("bucket") or "").strip() not in OPEN_BUCKETS:
                continue
            evidence = row.get("evidence") or ""
            if not NOTHING_EXISTS.search(evidence):
                continue
            skip = suppressed_paths(evidence)
            hits = sorted(
                p for p, text in artifacts.items() if rid in text and p not in skip
            )
            if hits:
                findings.append(
                    (rid, row["bucket"].strip(), (row.get("plain_name") or "").strip(), hits)
                )
    return findings, audit


def main() -> int:
    repo = sys.argv[1] if len(sys.argv) > 1 else REPO
    findings, audit = stale_rows(repo)
    errors = inventory_errors(repo, audit)
    if errors:
        print("RULE-ENFORCEABILITY AUDIT INVENTORY INVALID\n")
        for error in errors:
            print(f"  {error}")
        print(
            "\nFIX: refresh the audit so it has one well-formed current row for every "
            "id in ops/config/rule-enforcement-map.json active_rule_ids, and no others."
        )
        return 1
    assert audit is not None
    rel_audit = os.path.relpath(audit, repo)
    if not findings:
        print(f"audit queue fresh — no row in {rel_audit} contradicts the tree")
        return 0

    print(f"STALE AUDIT ROWS in {rel_audit} — {len(findings)} row(s)\n")
    print(
        "Each row below records that nothing enforced the rule, but the rule id is\n"
        "written into a live gate, githook, selftest or lint. Both cannot be current.\n"
    )
    for rid, bucket, name, hits in findings:
        print(f"  [{bucket}] {rid}  {name}")
        for h in hits[:6]:
            print(f"        named in {h}")
        if len(hits) > 6:
            print(f"        ... and {len(hits) - 6} more")
        print()
    print(
        "FIX: re-triage each row in that table — move it out of P/U if the gate now\n"
        "covers it, and replace the evidence text with what is true today. The row is\n"
        "the stale thing here, not the code."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
