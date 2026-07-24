#!/usr/bin/env python3
"""
corroborate.py — the Upstream Radar's corroboration engine (Radar lane 4).
Doctrine: DNA/Leads/upstream-radar.md · Mechanics: Automation/radar/upstream-sop.md

Joins weak pre-entity signals (tips, deeds, PECOS, NPPES moves, jobs, licenses,
domains) by person; surfaces a PRE-ENTITY candidate only when independent signals
corroborate. One signal is noise; two is a person packing boxes.

Usage: python3 corroborate.py [CARR_ROOT]
Reads:  Automation/radar/upstream/*.json   (each optional; missing = skipped, reported)
        Automation/entity-formation-leads.json (graduation check)
        DNA/Leads/lead-registry.xlsx          (suppressor: already-known people)
Writes: Automation/pre-entity-watch.json      (board feed; builder merges it)
        prints a digest block to stdout for the radar digest.
Never contacts anyone. Nothing enters the registry. GREEN.
"""
import sys, os, json, re, glob
from datetime import datetime, date

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UP   = os.path.join(ROOT, "Automation", "radar", "upstream")
AUTO = os.path.join(ROOT, "Automation")

WEIGHTS = {"human-tip":4, "deed":3, "pecos-enroll":3, "nppes-move":2, "job-post":2, "new-license":1, "domain":1}
POOL_FILES = {"tips.json":"human-tip","deeds.json":"deed","pecos.json":"pecos-enroll",
              "nppes-moves.json":"nppes-move","jobs.json":"job-post",
              "licenses-pool.json":"new-license","domains.json":"domain"}
THRESHOLD, MIN_CLASSES = 3, 2
PERSONAL_CLASSES = {"human-tip"}  # deeds flagged per-row via residential:true

# Territory anchor (added Jul 22, 2026 — the pecos pool exposed the gap: statewide
# pools name-join fine, but doctrine says the candidate needs a "here" signal;
# without this every statewide license+enrollment pair surfaced). A person is a
# candidate only if some signal carries territory geography. Two ways to anchor:
# a class that is territory-scoped by construction, or a row whose city/county
# is in territory. Sets follow date-founders.py + the deed-lane counties.
ANCHOR_CLASSES = {"human-tip", "deed", "nppes-move", "job-post", "domain"}
TERRITORY_COUNTIES = {"ESCAMBIA","SANTA ROSA","OKALOOSA","WALTON","BAY","LEON",
                      "MOBILE","BALDWIN","HOUSTON"}
TERRITORY_CITIES = {"PENSACOLA","MILTON","PACE","GULF BREEZE","CANTONMENT","NAVARRE","JAY",
    "CENTURY","TALLAHASSEE","PANAMA CITY","LYNN HAVEN","DESTIN","FORT WALTON BEACH","CRESTVIEW",
    "NICEVILLE","SANTA ROSA BEACH","MARY ESTHER","SHALIMAR","FREEPORT","DEFUNIAK SPRINGS",
    "MOBILE","DAPHNE","FAIRHOPE","SPANISH FORT","GULF SHORES","FOLEY","BONIFAY","CHIPLEY",
    "PANAMA CITY BEACH","DOTHAN","ENTERPRISE"}
def row_in_territory(r):
    return str(r.get("county","")).upper() in TERRITORY_COUNTIES or \
           str(r.get("city","")).upper() in TERRITORY_CITIES

TITLE_TOKENS = {"DR","MD","DO","DC","OD","DDS","DMD","PA","NP","ARNP","APRN","RN","LPC","PT","OT","OTR","PHD",
                "MR","MRS","MS","JR","SR","II","III","IV","CEO","OWNER","PHYSICIAN","THE"}
def key(name):
    s = str(name or "")
    s = re.sub(r"\(.*?\)", " ", s)          # drop parentheticals: "(physician owner)"
    s = re.split(r"[;—–]|--", s)[0]  # drop everything after ; or a dash separator
    s = re.sub(r"[^A-Za-z ]", " ", s).upper()
    p = [t for t in s.split() if t not in TITLE_TOKENS and len(t) > 1]
    return p[-1] + "|" + p[0] if len(p) >= 2 else None

def load_pools():
    pools, report = {}, []
    for fn, cls in POOL_FILES.items():
        path = os.path.join(UP, fn)
        if not os.path.exists(path):
            report.append(f"{fn}: absent (lane not live yet)"); continue
        try:
            rows = json.load(open(path))
            pools[cls] = rows; report.append(f"{fn}: {len(rows)} rows")
        except Exception as e:
            report.append(f"{fn}: UNREADABLE ({e}) — skipped, fix the file")
    return pools, report

def known_people():
    """Suppressor set: names already in the registry or the entity feed."""
    known = set()
    ef = os.path.join(AUTO, "entity-formation-leads.json")
    if os.path.exists(ef):
        for r in json.load(open(ef)):
            k = key(r.get("n")) ; known.add(("entity", k)) if k else None
    try:
        import openpyxl
        reg = os.path.join(ROOT, "DNA", "Leads", "lead-registry.xlsx")
        wb = openpyxl.load_workbook(reg, read_only=True, data_only=True)
        for r in wb["Registry"].iter_rows(min_row=2, values_only=True):
            if r and r[5]:
                k = key(r[5]); known.add(("registry", k)) if k else None
        wb.close()
    except Exception as e:
        print(f"[warn] registry unreadable ({e}) — suppressor runs on entity feed only")
    return known

def main():
    pools, report = load_pools()
    print("[pools] " + " · ".join(report))
    known = known_people()

    people = {}
    for cls, rows in pools.items():
        for r in rows:
            k = key(r.get("name") or r.get("n") or r.get("grantee") or r.get("contact"))
            if not k:  # domains often have no person name; they corroborate by token later
                continue
            p = people.setdefault(k, {"classes":{}, "rows":[]})
            p["classes"].setdefault(cls, []).append(r)
            p["rows"].append((cls, r))

    candidates, graduated = [], []
    unanchored = 0
    today = date.today().isoformat()
    for k, p in people.items():
        classes = p["classes"]
        score = sum(WEIGHTS[c] for c in classes)
        tip_only = ("human-tip" in classes) and len(classes) == 1
        if not (score >= THRESHOLD and len(classes) >= MIN_CLASSES) and not tip_only:
            continue
        anchored = any(c in ANCHOR_CLASSES for c in classes) or \
                   any(row_in_territory(r) for _, r in p["rows"])
        if not anchored:
            unanchored += 1; continue   # scores, but no "here" yet — stays in the pools
        if ("entity", k) in known:
            graduated.append(k); continue          # they filed — graduation, not a candidate
        if ("registry", k) in known:
            continue                               # suppressor: already a known/worked person
        rows = p["rows"]
        first = next((r for _, r in rows if row_in_territory(r)), rows[0][1])
        personal = any(c in PERSONAL_CLASSES for c in classes) or \
                   any(r.get("residential") for _, r in rows)
        name = (first.get("name") or first.get("n") or first.get("grantee") or "").title()
        evidence = "; ".join(f"{c}: {r.get('detail', r.get('city',''))}" for c, r in rows)[:220]

        # Geo-agreement flag (added Jul 22, 2026, once pecos rows carried real NPPES
        # practice locations). Which independent classes place them HERE, and does any
        # class place them provably ELSEWHERE (a city in another state)? A territory
        # license city + an out-of-state practice location is the tell of a common-name
        # false join (TAYLOR|JAMES); two classes agreeing on territory is a true anchor.
        terr_classes = {c for c, r in rows if row_in_territory(r)}
        elsewhere = {(r.get("state_practice") or r.get("state") or "").upper()
                     for c, r in rows
                     if (r.get("city") and not row_in_territory(r)
                         and (r.get("state_practice") or r.get("state")))}
        elsewhere.discard(""); elsewhere -= {"FL", "AL"}
        if len(terr_classes) >= 2:
            geo = "GEO-CORROBORATED"
        elif terr_classes and elsewhere:
            geo = "⚠ GEO-CONFLICT (likely name-join — verify identity)"
        elif "pecos-enroll" in terr_classes and "new-license" in classes and "new-license" not in terr_classes:
            geo = "NPPES-MOVER (practices here, licensed elsewhere)"
        else:
            geo = "GEO-SINGLE-SOURCE"
        candidates.append({
            "s": "\U0001F52D PRE-ENTITY — upstream watch",
            "n": name, "pr": first.get("profession",""), "ly":"", "ab":"", "na":"",
            "ad": ("TIP-ONLY · " if tip_only else "") + geo + " · " + evidence,
            "ci": first.get("city","").title(), "co": first.get("county","").title(),
            "e": first.get("email",""), "ph": first.get("phone",""), "own": "",
            "st": first.get("state",""), "in_territory": True,
            "signals": sorted(classes), "score": score, "geo": geo,
            "sensitivity": "PERSONAL-adjacent" if personal else "STANDARD",
            "surfaced": today,
        })
    # Rank: geo-corroborated first, conflicts last, score within.
    GEO_RANK = {"GEO-CORROBORATED": 0, "NPPES-MOVER (practices here, licensed elsewhere)": 1,
                "GEO-SINGLE-SOURCE": 2}
    candidates.sort(key=lambda c: (GEO_RANK.get(c["geo"], 3), -c["score"]))

    out = os.path.join(AUTO, "pre-entity-watch.json")
    json.dump(candidates, open(out, "w"), indent=1)
    print(f"[out] {len(candidates)} pre-entity candidate(s) -> {out}")
    if unanchored:
        print(f"[held] {unanchored} name-join(s) score >= {THRESHOLD} but carry no territory geography — held in the pools, not candidates")
    if graduated:
        print(f"[graduated] {len(graduated)} pool person(s) now in the entity feed: " + ", ".join(graduated))
    from collections import Counter
    gc = Counter(c["geo"].split(" (")[0] for c in candidates)
    print("[geo] " + " · ".join(f"{v} {k}" for k, v in gc.most_common()))
    print("\n===== DIGEST BLOCK (paste into the radar digest) =====")
    print(f"UPSTREAM (lane 4), {today}: {len(candidates)} candidate(s), {len(graduated)} graduation(s).")
    for c in candidates:
        flag = " ⚠PERSONAL" if c["sensitivity"] != "STANDARD" else ""
        print(f"  {c['score']:>2}  {c['n']} · {c['ci']} · {c['geo'].split(' (')[0]} · [{', '.join(c['signals'])}]{flag}")
    print("Nothing above enters the registry without a human. License-verify anyone not license-sourced.")

if __name__ == "__main__":
    main()
