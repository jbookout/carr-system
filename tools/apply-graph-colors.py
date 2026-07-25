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

# Obsidian applies the FIRST matching colour group, so order is precedence.
# Owner goes at the front deliberately (Joe, 2026-07-25): which half of the
# business a record belongs to is the biggest-picture separator, and it should
# beat record type. `owner-unassigned` is intentionally NOT coloured — those 154
# records fall through to their type colour instead of forming a third bloc.
OWNER_FIRST = [
    ("tag:#owner-joe",    "00e5ff"),   # bright cyan  — Joe's half
    ("tag:#owner-dell",   "ff9100"),   # bright orange — Dell's half
    ("tag:#owner-shared", "ffffff"),   # white — jointly owned
]
GROUPS = [
    # --- people graph skeleton (path:Graph/) ---
    ("tag:#struct-stage",   "ffffff"),   # ① LEADS → ② CLIENTS → ③ DEALS, the backbone
    ("tag:#struct-source",  "22c55e"),   # ⚑ Lead Board, CARR Website, SBDC, referrers
    ("tag:#struct-firm",    "64748b"),   # 🏢 colleagues at the same company
    # --- system graph (path:Graph-System) ---
    ("tag:#sys-router",     "fbbf24"),   # 📇 INDEX
    ("tag:#sys-tier-dna",   "3b82f6"),   # shared tier — the single share to Dell
    ("tag:#sys-tier-dell",  "ff9100"),   # Dell's twin
    ("tag:#sys-tier-joe",   "00e5ff"),   # Joe-personal tier
    ("tag:#sys-pole",       "ffffff"),
]
# Tags retired when the graph model changed from flat areas/hubs to a structural
# skeleton. Their colour groups match nothing now and just clutter the panel.
STALE = {"tag:#sys-area", "tag:#sys-context", "tag:#sys-doctrine", "tag:#sys-leads",
         "tag:#sys-network", "tag:#sys-deals", "tag:#sys-clients", "tag:#sys-marketing",
         "tag:#sys-team", "tag:#sys-automation", "tag:#sys-research", "tag:#sys-reference",
         "tag:#sys-root", "tag:#hub-firm", "tag:#hub-market", "tag:#hub-owner",
         "tag:#hub-category", "tag:#hub-specialty", "tag:#hub-channel",
         "tag:#hub-referrer", "tag:#hub-lane"}

d = json.load(open(p))
before = len(d.get("colorGroups", []))
d["colorGroups"] = [g for g in d.get("colorGroups", []) if g["query"] not in STALE]
pruned = before - len(d["colorGroups"])
if pruned: print(f"pruned {pruned} stale colour groups from the old graph model")
groups = d["colorGroups"]
have = {g["query"] for g in groups}
added = 0
for q, h in reversed(OWNER_FIRST):          # reversed so the listed order survives insert(0)
    if q not in have:
        groups.insert(0, {"query": q, "color": {"a": 1, "rgb": int(h, 16)}}); added += 1
for q, h in GROUPS:
    if q not in have:
        groups.append({"query": q, "color": {"a": 1, "rgb": int(h, 16)}}); added += 1
# Default the view to ONE graph. Unfiltered, Obsidian renders the people graph,
# the system graph and every real vault note together — which reads as a hairball
# and hides that they are separate models (Joe, 2026-07-25). Flip the filter to
# `path:Graph-System` for system flow; clear it to see everything.
if not d.get("search"):
    d["search"] = "path:Graph/"
    print('set default graph filter to  path:Graph/  (use path:Graph-System for system flow)')

json.dump(d, open(p, "w"), indent=2)
print(f"added {added} colour groups; {len(groups)} total "
      f"(owner groups first — they take precedence over record type)")
