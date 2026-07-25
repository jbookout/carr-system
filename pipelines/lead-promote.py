#!/usr/bin/env python3
"""
lead-promote.py — the promotion gate between the reservoir and the registry.

THE PROBLEM (found 2026-07-25)
  `DNA/Leads/lead-router-*.xlsx` holds 9,320 licensed providers in territory,
  segmented by buying signal. `lead-registry.xlsx` holds 199 worked rows. Only 37
  had ever crossed over, and 3 of 9,855 board records carry an agent's name.

  The cause is structural, not neglect: lead-system.md's intake architecture was
  designed 2026-07-06 and lists six sources — vendor intros, walk-ins, past-client
  referrals, Axon, the FL license feed, "anything else". The router file arrived a
  week later (2026-07-13) and was wired to the BOARD as a display layer. No intake
  SOP, no feed-router row, no promotion path. So 268 practices flagged ripe for
  sale and 228 in a lease-decision window sit where nothing can act on them.

WHAT THIS DOES — AND DELIBERATELY DOES NOT DO
  It does NOT bulk-import. 9,320 rows into a 199-row registry would drown the
  drip logic, the graph, the health checks and the Monday brief. The reservoir is
  a market map; the registry is a working pipeline. They should stay different sizes.

  It produces a weekly SHORTLIST from the event-driven segments, deduped against
  everything already known, ranked, ready for a human to claim.

  It does NOT write to the registry. Claim-before-touch is law (lead-system.md):
  "Nobody contacts a lead whose Owner is blank or Shared until an Owner gets
  stamped." And ownership is ASKED, never assumed (Joe's rule, 2026-07-13). So a
  row enters the registry only after a human claims it — this just decides who is
  worth asking about.

Usage:
  run.sh lead-promote [--count N] [--county NAME] [--segment KEY] [--all-segments]
"""
import sys, os, re, json, glob, argparse
from collections import Counter, defaultdict
import openpyxl

ap = argparse.ArgumentParser()
ap.add_argument("root", nargs="?")
ap.add_argument("--count", type=int, default=20, help="shortlist size (default 20)")
ap.add_argument("--county", help="restrict to one county")
ap.add_argument("--segment", help="restrict to one segment (substring match)")
ap.add_argument("--all-segments", action="store_true",
                help="include watch-list segments, not just event-driven ones")
a = ap.parse_args()

ROOT = a.root or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

def s(v): return str(v if v is not None else "").strip()

def rows(path, sheet=None):
    if not os.path.exists(path): return []
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb[wb.sheetnames[0]]
    it = ws.iter_rows(values_only=True)
    hdr = [s(h) for h in next(it, [])]
    out = [dict(zip(hdr, r)) for r in it if any(x is not None for x in r)]
    wb.close(); return out

# ---------- sources ----------
routers = sorted(glob.glob(os.path.join(ROOT, "DNA/Leads/lead-router-*.xlsx")))
if not routers:
    sys.exit("no lead-router-*.xlsx found in DNA/Leads/")
RESERVOIR = routers[-1]                                    # latest by name
reservoir = rows(RESERVOIR, "Lead Router")
registry  = rows(os.path.join(ROOT, "DNA/Leads/lead-registry.xlsx"), "Registry")
clients   = rows(os.path.join(ROOT, "DNA/Clients/client-roster.xlsx"), "Clients")
dealsp    = os.path.join(ROOT, "DNA/Deal Management/panhandle-team-deals.json")
deals     = json.load(open(dealsp)).get("deals", []) if os.path.exists(dealsp) else []

# ---------- the dedupe gate ----------
# The lead-sweep workflow once created L-170 for a practice already a client at
# Closing. Anything promoted has to be checked against every record we hold, by
# BOTH name and email — name alone misses the spelling drift this vault is full of
# (Brielmayer/Breilmayer, Lindsay/Lindsey, Connor/Conner).
def norm(x):
    return re.sub(r"[^a-z]", "", s(x).lower())

known_names, known_emails = set(), set()
for r in registry:
    known_names.add(norm(r.get("Contact Name"))); known_names.add(norm(r.get("Practice")))
    known_emails.add(s(r.get("Email")).lower())
for c in clients:
    known_names.add(norm(c.get("Name"))); known_names.add(norm(c.get("Practice / Entity")))
    known_emails.add(s(c.get("Email")).lower())
for d in deals:
    for k in ("name", "contact", "company"):
        known_names.add(norm(d.get(k)))
    known_emails.add(s(d.get("email")).lower())
known_names.discard(""); known_emails.discard("")

# ---------- segments: THREE lanes, not one ----------
# Read from THE PLAY column rather than guessed. The segments do not all mean
# "work this lead" — they encode three different actions, and conflating them was
# the first draft's mistake.
LANES = {
    # LANE 1 — PROMOTE: a real 1:1 opportunity, becomes an L-### row on claim
    "POST-SALE FOUNDER":  ("PROMOTE", "Re-engagement",
        "sold, serving an earn-out; restarts with capital and a book — 'THE HOTTEST CLASS'"),
    "PRACTICE OWNER":     ("PROMOTE", "Re-engagement",
        "owns the entity: expansion, 2nd location, buy-vs-lease, refi"),
    "LEASE EVENT":        ("PROMOTE", "Renewal-Relocation", "lease decision window"),
    "RELOCATING OWNER":   ("PROMOTE", "Hot – Relocator", "moving into territory"),
    "OWNER-OCCUPIER":     ("PROMOTE", "Hot – Startup", "second location"),
    "NEW ENTITY":         ("PROMOTE", "Hot – Startup", "corp filing just landed"),
    "NATIONAL ACCOUNT":   ("PROMOTE", "National Account", "multi-location / expanding"),
    # LANE 2 — DRIP: nurture, never 1:1. lead-system.md: "Associate – Nurture …
    # straight to Nurture (Drip) — monthly newsletter list."
    "ASSOCIATE":          ("DRIP", "Associate – Nurture",
        "2-14 yrs in, owns nothing — 'be the only broker he knows'"),
    "DSO ASSOCIATE":      ("DRIP", "DSO / DSO-track", "DSO burnout → independent"),
    # LANE 3 — REFER: not a CARR client yet. Goes to a practice broker; CARR earns
    # the referral fee and the buyer's real estate later.
    "RIPE FOR SALE":      ("REFER", "—",
        "owner 60+, no succession → sells externally; refer to practice brokers"),
    # LANE 4 — WATCH: no action. Stays in the reservoir.
    "SOLO":               ("WATCH", "—", "entity unconfirmed — needs a Sunbiz check"),
    "NEW GRAD":           ("WATCH", "—", "too new, revisit in ~3 years"),
    "WINDING DOWN":       ("WATCH", "—", "retiring, not restarting — courtesy only"),
    "UNCLASSIFIED":       ("WATCH", "—", "unsegmented"),
    "INSTITUTIONAL":      ("WATCH", "—", "watch the physicians, not the institution"),
    "PRE-ENTITY":         ("WATCH", "—", "upstream watch"),
}
LANE_ORDER = {"PROMOTE": 0, "DRIP": 1, "REFER": 2, "WATCH": 3}

# Longest key first: "DSO ASSOCIATE" must not be swallowed by "ASSOCIATE".
_KEYS = sorted(LANES, key=len, reverse=True)

def classify(seg):
    u = seg.upper()
    for key in _KEYS:
        if key in u:
            return LANES[key]
    return ("WATCH", "—", "")

def lane_of(seg):     return classify(seg)[0]
def registry_segment(seg): return classify(seg)[1]
def why(seg):         return classify(seg)[2]

# ---------- filter ----------
cands, dropped_dupe = [], 0
for r in reservoir:
    seg = s(r.get("SEGMENT"))
    if not (a.all_segments or lane_of(seg) in ("PROMOTE", "DRIP", "REFER")): continue
    if a.segment and a.segment.upper() not in seg.upper(): continue
    if a.county and a.county.lower() != s(r.get("County")).lower(): continue
    if norm(r.get("Name")) in known_names or s(r.get("Email")).lower() in known_emails:
        dropped_dupe += 1; continue
    if not (s(r.get("Email")) or s(r.get("Phone"))):   # uncontactable
        continue
    cands.append(r)

cands.sort(key=lambda r: (LANE_ORDER[lane_of(s(r.get("SEGMENT")))],
                          -len(s(r.get("Email"))), s(r.get("County")), s(r.get("Name"))))

# ---------- report ----------
print(f"\nLEAD PROMOTION — shortlist from {os.path.basename(RESERVOIR)}")
print("=" * 78)
print(f"reservoir {len(reservoir):,} · already known (deduped out) {dropped_dupe:,} · "
      f"eligible {len(cands):,} · showing {min(a.count, len(cands))}")
if not a.all_segments:
    print("scope: event-driven segments only — "
          "use --all-segments to include associates / new grads / watch-list")
print()
lanes = Counter(lane_of(s(r.get("SEGMENT"))) for r in cands)
for ln in ("PROMOTE", "DRIP", "REFER"):
    if not lanes.get(ln): continue
    print(f"\n  {ln}  ({lanes[ln]:,})")
    for seg, n in Counter(s(r.get("SEGMENT")) for r in cands
                          if lane_of(s(r.get("SEGMENT"))) == ln).most_common():
        print(f"     {n:5,}  {seg}")
        print(f"            {why(seg)}")

print("\n" + "=" * 78)
print("SHORTLIST — claim before contact. Owner is asked, never assumed.\n")
for i, r in enumerate(cands[:a.count], 1):
    print(f"{i:3d}. {s(r.get('Name')):26s} {s(r.get('Profession'))[:22]:22s} "
          f"{s(r.get('City')):16s} {s(r.get('County'))}")
    print(f"     {s(r.get('SEGMENT'))}")
    if s(r.get("THE PLAY")): print(f"     play: {s(r.get('THE PLAY'))[:90]}")
    bits = [b for b in (s(r.get("Email")), s(r.get("Phone"))) if b]
    print(f"     {' · '.join(bits)}")
    print(f"     → registry Segment = {registry_segment(s(r.get('SEGMENT')))} · "
          f"Source Type = Lead Router · Source Detail = {os.path.basename(RESERVOIR)}")
    print()

print("=" * 78)
print("NEXT: Joe or Dell claims the ones they want. Only claimed rows become L-###")
print("rows — an unclaimed row stays in the reservoir rather than becoming an")
print("orphan with no owner (the 37 unowned sweep leads are exactly that failure).")
