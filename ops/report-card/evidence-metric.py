#!/usr/bin/env python3
"""Failure-sensitive parsers for completed Report Card evidence captures."""

import argparse
import os
import re
import sys


MARKERS = {
    "HEALTH": "CARR_EVIDENCE_COMPLETE health-check/v1",
    "CHECK": "CARR_EVIDENCE_COMPLETE check/v1",
}


def read_capture(name):
    path = os.environ.get(name)
    if not path:
        raise RuntimeError(f"{name} path is unset")
    with open(path) as handle:
        text = handle.read()
    if not text.strip().endswith(MARKERS[name]):
        raise RuntimeError(f"{name} evidence is incomplete")
    return text


def section(text, start, end):
    lines = text.splitlines()
    try:
        first = next(index for index, line in enumerate(lines) if line.startswith(start))
    except StopIteration as exc:
        raise RuntimeError(f"missing evidence section: {start}") from exc
    try:
        last = next(index for index in range(first + 1, len(lines))
                    if lines[index].startswith(end))
    except StopIteration as exc:
        raise RuntimeError(f"unterminated evidence section: {start}") from exc
    return lines[first + 1:last]


def export_deadman_failures(text):
    all_lines = text.splitlines()
    try:
        first = next(index for index, line in enumerate(all_lines)
                     if line.startswith("Export register —"))
        last = next(index for index in range(first + 1, len(all_lines))
                    if "R2 archive" in all_lines[index])
    except StopIteration as exc:
        raise RuntimeError("export register section missing or unterminated") from exc
    lines = all_lines[first + 1:last]
    if any("UNREADABLE" in line or " SKIP" in line or "UNKNOWN" in line for line in lines):
        raise RuntimeError("export register is unreadable or unknown")
    failures = [line for line in lines
                if re.match(r"^  ⚠︎ (STALE|NEVER RAN|NEVER OK) ", line)]
    healthy = [line for line in lines
               if re.match(r"^  OK \d+ registered target\(s\), all with a successful run ", line)]
    if failures:
        return len(failures)
    if len(healthy) == 1:
        return 0
    raise RuntimeError("export register has neither failure rows nor one healthy summary")


def doctrine_stale_sections(text):
    lines = section(text, "doctrine store", "CARR_EVIDENCE_COMPLETE health-check/v1")
    if any("store UNREADABLE" in line or "store row unparseable" in line
           or "doctrine store check failed" in line for line in lines):
        raise RuntimeError("doctrine store evidence is unreadable")
    matches = []
    for line in lines:
        found = re.search(r"stale-sections\s+(\d+) past review_after", line)
        if found:
            matches.append(int(found.group(1)))
    if len(matches) != 1:
        raise RuntimeError("doctrine stale-sections row missing or ambiguous")
    return matches[0]


def code_drift_rows(text):
    lines = section(text, "== Code drift ", "== Video pipeline drift ")
    findings = [line for line in lines
                if re.match(r"^  (DRIFT|MISSING)\s+", line)]
    recognized = [line for line in lines
                  if re.match(r"^  (OK|DRIFT|MISSING)\s+", line)]
    if not recognized:
        raise RuntimeError("code-drift section contains no recognized rows")
    return len(findings)


def facade_findings(text):
    """Count every unhealthy watched output in the façade section."""
    lines = section(text, "Façade check (rule 28)", "Schedule drift —")
    recognized = [line for line in lines if re.match(
        r"^  (OK |MISSING |-- GATED |⚠︎ )", line)]
    if not recognized:
        raise RuntimeError("façade section contains no recognized rows")
    return sum(1 for line in recognized
               if line.startswith("  MISSING ") or line.startswith("  ⚠︎ "))


def facade_findings_independent(text):
    """Independently classify the same missing/stale/behind/broken set."""
    lines = section(text, "Façade check (rule 28)", "Schedule drift —")
    rows = [line.strip() for line in lines if line.strip()]
    if not rows:
        raise RuntimeError("façade section contains no rows")
    count = 0
    recognized = 0
    for row in rows:
        if row.startswith("OK ") or row.startswith("-- GATED "):
            recognized += 1
        elif row.startswith("MISSING "):
            recognized += 1
            count += 1
        elif row.startswith("⚠︎ "):
            recognized += 1
            if not any(status in row for status in (
                    " STALE ", " BEHIND ", "CREDENTIAL PRESENT BUT UNREACHABLE")):
                raise RuntimeError("façade warning has an unknown status")
            count += 1
    if recognized == 0:
        raise RuntimeError("façade section contains no recognized rows")
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("metric", choices=(
        "export-deadman-failures", "doctrine-stale-sections", "code-drift-rows",
        "facade-findings", "facade-findings-independent"))
    args = parser.parse_args()
    try:
        if args.metric == "export-deadman-failures":
            value = export_deadman_failures(read_capture("HEALTH"))
        elif args.metric == "doctrine-stale-sections":
            value = doctrine_stale_sections(read_capture("HEALTH"))
        elif args.metric == "code-drift-rows":
            value = code_drift_rows(read_capture("CHECK"))
        elif args.metric == "facade-findings":
            value = facade_findings(read_capture("HEALTH"))
        else:
            value = facade_findings_independent(read_capture("HEALTH"))
    except Exception as exc:
        print(f"UNKNOWN {args.metric}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
