#!/usr/bin/env python3
"""
apply-graph-colors.py — add Graph-System area colours to .obsidian/graph.json.

⚠️ OBSIDIAN MUST BE CLOSED. Obsidian holds graph settings in memory and flushes
them on its own schedule, silently overwriting any external edit. (Observed
2026-07-25: an edit was reverted 41 seconds later while the app was open.)

Usage:  python3 tools/apply-graph-colors.py "<vault path>"
Idempotent — re-running does not duplicate groups.
"""
import json, sys, os, subprocess

vault = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
p = os.path.join(vault, ".obsidian", "graph.json")

if subprocess.run(["pgrep", "-x", "Obsidian"], capture_output=True).returncode == 0:
    sys.exit("REFUSING: Obsidian is running and will overwrite this. Quit Obsidian, then re-run.")

GROUPS = [
    ("tag:#sys-area",       "ffffff"), ("tag:#sys-context",    "e8542f"),
    ("tag:#sys-doctrine",   "d4a017"), ("tag:#sys-leads",      "2ecc71"),
    ("tag:#sys-network",    "3b82f6"), ("tag:#sys-deals",      "8b5cf6"),
    ("tag:#sys-clients",    "ec4899"), ("tag:#sys-marketing",  "f59e0b"),
    ("tag:#sys-team",       "14b8a6"), ("tag:#sys-automation", "94a3b8"),
    ("tag:#sys-research",   "a3e635"), ("tag:#sys-reference",  "64748b"),
    ("tag:#sys-root",       "f43f5e"),
]
d = json.load(open(p))
have = {g["query"] for g in d.get("colorGroups", [])}
added = 0
for q, h in GROUPS:
    if q not in have:
        d.setdefault("colorGroups", []).append({"query": q, "color": {"a": 1, "rgb": int(h, 16)}})
        added += 1
json.dump(d, open(p, "w"), indent=2)
print(f"added {added} colour groups; {len(d['colorGroups'])} total")
