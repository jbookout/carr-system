#!/usr/bin/env python3
"""
health-check.py — the rule-28 façade check as code (phase 3 pilot, 2026-07-24).

Protocol rule 28 (DNA/Team/dna-protocol.md): an automation is verified by OUTPUT
freshness, never by its schedule existing. This script checks every watched
pipeline output two ways:
  STALE  — the output is older than its cadence allows
  BEHIND — a derived view is older than one of its inputs (the picture lies)
Read-only. Exit 0 = all fresh; exit 1 = at least one STALE/BEHIND (so the
heartbeat can surface findings). Run: python3 tools/health-check.py  (or run.sh health)
Cadences are calendar days, padded so weekends (off days, both humans) never
false-alarm a weekly pipeline.
"""
import os, sys, glob, time

VAULT = os.environ.get("CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")

# name, output (glob ok — newest match wins), max_age_days (None = no cadence),
# input globs (any newer than output => BEHIND), note shown on failure
WATCH = [
    ("Lead Board",        "Automation/lead-board.html", 9,
     ["DNA/Leads/lead-registry.xlsx", "DNA/Leads/lead-router-*.xlsx",
      "Automation/renewal-radar.json", "Automation/entity-formation-leads.json",
      "Automation/pre-entity-watch.json", "Automation/lead-board-decisions.json",
      "Automation/lead-board-hot.json"],
     "weekly Monday run (lead-system-weekly.md)"),
    ("Deal Room",         "DNA/Team/live-boards/deal-room-panhandle.html", None,
     ["DNA/Deal Management/panhandle-team-deals.json"],
     "refresh-on-change law (deal-enrichment-sop.md)"),
    ("Renewal feed",      "Automation/renewal-radar.json", 9,
     ["DNA/Leads/renewal-radar-*.xlsx"],
     "weekly with the Monday run"),
    ("Entity formation",  "Automation/entity-formation-leads.json", 9, [],
     "weekly Sunbiz/AL sweep (corp-filings-sop.md; fires on Mac wake)"),
    ("Pre-entity watch",  "Automation/pre-entity-watch.json", 9, [],
     "corroborate.py rides the Monday radar run"),
    ("Radar digest",      "Automation/radar/radar-digest-2*.md", 9, [],
     "Monday radar run (radar-digest-sop.md)"),
    ("PECOS pool",        "Automation/radar/upstream/pecos.json", 100, [],
     "quarterly (Jan/Apr/Jul/Oct; next diff vs the Q1 baseline in Oct)"),
    ("Joe calendar feed", "DNA/Team/calendar-latest.ics", 4, [],
     "fetch-calendar.sh; business days only"),
    ("Dell calendar feed","DNA/Team/calendar-latest-dell.ics", 4, [],
     "KNOWN BLOCKED on Dell's OS update (memory: dell-calendar-fetch-blocked) — expected stale until he updates"),
]

def newest(pattern):
    hits = glob.glob(os.path.join(VAULT, pattern))
    return max(hits, key=os.path.getmtime) if hits else None

def age_days(path):
    return (time.time() - os.path.getmtime(path)) / 86400

rc = 0
print(f"Façade check (rule 28) — {time.strftime('%Y-%m-%d %H:%M')} — outputs, not schedules")
for name, out_pat, max_age, inputs, note in WATCH:
    out = newest(out_pat)
    if not out:
        print(f"  MISSING {name:<18} no file matches {out_pat}  · {note}")
        rc = 1
        continue
    a = age_days(out)
    problems = []
    if max_age is not None and a > max_age:
        problems.append(f"STALE {a:.1f}d old (cadence {max_age}d)")
    behind = [os.path.basename(i) for pat in inputs
              if (i := newest(pat)) and os.path.getmtime(i) > os.path.getmtime(out) + 60]
    if behind:
        problems.append(f"BEHIND inputs: {', '.join(behind)}")
    if problems:
        print(f"  ⚠︎ {name:<18} {'; '.join(problems)}  · {note}")
        rc = 1
    else:
        print(f"  OK {name:<18} {a:.1f}d old")
sys.exit(rc)
