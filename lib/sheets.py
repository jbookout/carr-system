#!/usr/bin/env python3
"""
sheets.py — the ONE header-validated reader for the lead workbooks.

Orchestrator-lane corrective #1 (baseline report, 2026-07-25): four scripts used
to read lead-router-*.xlsx and lead-registry.xlsx by POSITIONAL index (r[4],
r[10], ...) with no header check — an inserted or reordered column would silently
shift every field (phone into email) with zero errors. This module replaces that:
consumers resolve columns BY HEADER NAME once, and if an expected header is
missing or renamed the script dies loudly with a message naming exactly what
moved, instead of shipping corrupt rows.

Usage (see generators/build-lead-board.py for the pattern):
    from sheets import header_map, data_rows
    ws = wb["Registry"]
    c = header_map(ws, REQUIRED_REGISTRY_COLS, "lead-registry.xlsx[Registry]")
    for r in data_rows(ws):
        name = r[c["Contact Name"]]

Vault-copy note: in the repo this lives at lib/sheets.py; manifest.tsv syncs a
flat copy next to each consumer's vault-runtime dir (Automation/, Automation/radar/)
so the fallback copies keep working. Consumers use a small bootstrap that finds
either location.
"""
import sys


def header_map(ws, required, label):
    """Read row 1 of ws, return {header name: 0-based index}.

    Dies with a loud, specific SystemExit if any required header is absent —
    that is the point: a moved column must halt the run, never mis-read it.
    Header comparison is exact (the workbooks' headers are stable vocabulary;
    a rename is a structural change that should be noticed, not papered over).
    """
    hdr = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
    m = {str(h).strip(): i for i, h in enumerate(hdr) if h is not None}
    missing = [name for name in required if name not in m]
    if missing:
        raise SystemExit(
            f"SCHEMA HALT [{label}]: expected column(s) not found: {missing}. "
            f"Headers present: {sorted(m)}. A column was renamed, moved, or removed — "
            f"fix the workbook or update the consumer; do NOT read positionally around this."
        )
    return m


def data_rows(ws):
    """Data rows (row 2 down), values_only tuples — same shape consumers already use."""
    return ws.iter_rows(min_row=2, values_only=True)


# The two workbooks' required vocabularies, shared so every consumer validates
# the same contract. A consumer may require a subset; these are the supersets
# actually read anywhere in the repo today.
ROUTER_REQUIRED = ["SEGMENT", "THE PLAY", "Owns?", "Name", "Profession", "Lic Yrs",
                   "Age Band", "# at Address", "Practice Address", "City", "County",
                   "Email", "Phone"]
REGISTRY_REQUIRED = ["Lead ID", "Date In", "Owner", "Stage", "Segment", "Contact Name",
                     "Practice", "Specialty", "City/Market", "County", "Email", "Phone",
                     "Next Action", "Next Action Date", "Last Touch", "Detail File", "Notes"]


if __name__ == "__main__":
    print(__doc__)
    sys.exit(0)
