#!/usr/bin/env python3
"""Check declared findings manifests against a fix brief or PR report.

# doctrine: engineering-workflow-sop

Findings-source: <repo-relative JSON path> binds the source denominator.
Findings-total: <count> must equal its findings array length.
Finding: <id> | fixed | <evidence> closes a finding in a report.
Finding: <id> | not_a_defect | <reason> records the only unfixed disposition.
Briefs may use planned in place of fixed. Severity never filters coverage.
This checks completeness, not the truth of fix evidence or dismissal reasons.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")


def check(body: str, root: Path, phase: str) -> int:
    sources = re.findall(r"^Findings-source:\s*(.+?)\s*$", body, re.M)
    if not sources:
        if re.search(r"^Findings-(?:source|total):|^Finding:", body, re.M):
            raise ValueError("findings report lacks Findings-source")
        return 0
    expected = set()
    for source in sources:
        relative = Path(source)
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root.resolve()):
            raise ValueError("findings source must stay inside the repository")
        doc = json.loads(path.read_text())
        findings = doc.get("findings") if isinstance(doc, dict) else None
        if not isinstance(findings, list):
            raise ValueError("findings source must contain a findings array")
        for finding in findings:
            fid = finding.get("id") if isinstance(finding, dict) else None
            if not isinstance(fid, str) or not ID.fullmatch(fid) or fid in expected:
                raise ValueError("finding IDs must be valid and unique across sources")
            expected.add(fid)
    totals = re.findall(r"^Findings-total:\s*(\d+)\s*$", body, re.M)
    if len(totals) != 1 or int(totals[0]) != len(expected):
        raise ValueError(f"Findings-total must equal the full count {len(expected)}")
    seen = set()
    allowed = {"planned", "not_a_defect"} if phase == "brief" else {"fixed", "not_a_defect"}
    for line in body.splitlines():
        if not line.startswith("Finding:"):
            continue
        parts = line.removeprefix("Finding:").split("|", 2)
        if len(parts) != 3:
            raise ValueError("finding row must name an ID, disposition, and evidence or reason")
        fid, disposition, evidence = (part.strip() for part in parts)
        if fid not in expected or fid in seen:
            raise ValueError(f"unknown or duplicate finding ID: {fid}")
        if disposition not in allowed or not evidence:
            raise ValueError(f"{fid}: use {sorted(allowed)} with evidence or a non-defect reason")
        seen.add(fid)
    missing = sorted(expected - seen)
    if missing:
        raise ValueError(f"uncovered findings: {', '.join(missing)}; fix every severity or justify non-defect")
    return len(expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--brief", type=Path)
    parser.add_argument("--phase", choices=("brief", "report"), default="report")
    args = parser.parse_args()
    try:
        if args.brief:
            body = args.brief.read_text()
        elif os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
            body = event["pull_request"]["body"]
            if body is None:
                body = ""
            if not isinstance(body, str):
                raise ValueError("PR body must be text")
        else:
            print("findings coverage: no PR event or brief supplied")
            return 0
        count = check(body, args.root, args.phase)
        declared = bool(re.search(r"^Findings-source:", body, re.M))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"REFUSED findings coverage: {exc}", file=sys.stderr)
        return 1
    if declared:
        print(f"findings coverage: {count} findings accounted for; fix omissions before delivery")
    else:
        print("findings coverage: no source declared; completeness not asserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
