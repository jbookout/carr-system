#!/usr/bin/env python3
"""Fail closed when a new calendar attendee has not completed intake.

The local-calendar capture can prove an attendee address exists.  It cannot
pretend that this has become a usable CARR record.  For each unmatched external
address, the companion intake worker must record three independently checkable
steps: a local-mail search, open-source research, and either an existing or a
new canonical record.  This gate is deliberately mechanical: no model decides
whether the evidence is enough, and a missing receipt is a refusal, not an
empty successful capture.

Evidence is a small operational hand-off, not canonical business prose.  The
worker which actually searches Joe's local mail and researches the contact owns
writing it; canonical findings and any record creation still go through their
record-layer verbs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_json(path: Path, label: str):
    try:
        with path.open() as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise ValueError(f"{label} is missing: {path}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is invalid JSON: {path} ({exc.msg})")


def load_evidence(path: Path):
    """The evidence ledger, where ABSENT means "nothing evidenced yet" — not an error.

    A missing file used to refuse the whole capture, which made a clean calendar
    indistinguishable from a broken one: on a Mac that had never recorded an
    intake, a run with ZERO unmatched attendees still exited 78 with "evidence is
    missing", and there was no first write to create the ledger because the only
    thing that writes it is the intake worker that unmatched attendees trigger.
    Found 2026-08-18 on Dell's Mac, first live fire: 63 events scanned, unknown
    list empty, refused anyway.

    Returning empty candidates does NOT weaken the gate. Every unmatched attendee
    is still looked up in this mapping and still refuses when its receipts are
    absent — an empty ledger simply means every one of them is missing evidence,
    which is exactly the fail-closed answer. Only the vacuous case changes, where
    there is nothing to evidence at all. An UNREADABLE or malformed file is still
    a hard refusal, because that is a damaged ledger rather than an empty one.
    """
    if not path.exists():
        return {"candidates": {}}
    return load_json(path, "calendar intake evidence")


def complete(entry: object) -> tuple[bool, list[str]]:
    """Return the missing evidence dimensions for one attendee.

    A status is intentionally not enough: a receipt must say where the search
    or research happened, and a canonical record result must name its ref.  This
    stops a future worker from clearing its own queue with ``status: done``.
    """
    if not isinstance(entry, dict):
        return False, ["mail_search", "research", "record"]
    missing: list[str] = []
    for key in ("mail_search", "research"):
        part = entry.get(key)
        if not isinstance(part, dict) or part.get("status") != "searched" or not str(part.get("source") or "").strip():
            missing.append(key)
    record = entry.get("record")
    if (not isinstance(record, dict)
            or record.get("status") not in {"created", "existing"}
            or not str(record.get("ref") or "").strip()):
        missing.append("record")
    return not missing, missing


def unresolved(proposals: object, evidence: object) -> dict[str, list[str]]:
    if not isinstance(proposals, dict):
        raise ValueError("calendar proposals must be an object")
    if not isinstance(evidence, dict):
        raise ValueError("calendar intake evidence must be an object")
    candidates = evidence.get("candidates", {})
    if not isinstance(candidates, dict):
        raise ValueError("calendar intake evidence candidates must be an object")
    gaps: dict[str, list[str]] = {}
    for row in proposals.get("unknown", []):
        if not isinstance(row, dict):
            continue
        email = str(row.get("email") or "").strip().lower()
        if not email:
            continue
        ok, missing = complete(candidates.get(email))
        if not ok:
            gaps[email] = missing
    return gaps


def main() -> int:
    ap = argparse.ArgumentParser(description="Refuse calendar completion until unmatched attendees have intake evidence")
    ap.add_argument("--proposals", type=Path, required=True)
    ap.add_argument("--evidence", type=Path, required=True)
    args = ap.parse_args()
    try:
        gaps = unresolved(load_json(args.proposals, "calendar proposals"),
                          load_evidence(args.evidence))
    except ValueError as exc:
        print(f"calendar-intake-gate: REFUSE {exc}", file=sys.stderr)
        return 78
    if gaps:
        print("calendar-intake-gate: REFUSE unmatched attendee intake remains", file=sys.stderr)
        for email, missing in sorted(gaps.items()):
            print(f"  {email}: missing {', '.join(missing)}", file=sys.stderr)
        return 78
    print("calendar-intake-gate: accepted all unmatched attendees carry mail, research, and canonical-record evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
