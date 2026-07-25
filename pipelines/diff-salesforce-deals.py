#!/usr/bin/env python3
"""
diff-salesforce-deals.py — reconcile the Salesforce capture against the Deal Room's JSON.

Salesforce report export is DISABLED on Joe's profile, so the capture step is a
browser read (see DNA/Deal Management/salesforce-read-sop.md). That step writes
Automation/salesforce-deals-latest.tsv. THIS script is the deterministic half:
it diffs that capture against DNA/Deal Management/panhandle-team-deals.json and
prints exactly what changed. It never guesses and never writes the JSON unless
--apply is passed.

Lane truth: Salesforce's own "Out of Market Deal" checkbox (the `oom` column,
N/T), NOT a city-string heuristic. A deal's lane decides the economics:
  T (territory)  — CARR represents:            Dell 70% / Joe 30%
  N (out-of-market) — referred to a local CARR agent: agent 70% / Dell 21% / Joe 9%
(verified 2026-07-25 against Salesforce Deal Splits on Trambadia and Nikki Cottis)

Usage:
  python3 diff-salesforce-deals.py [CARR_ROOT]            # report only
  python3 diff-salesforce-deals.py [CARR_ROOT] --apply    # also update the JSON
"""
import sys, os, json, re, difflib

args = [a for a in sys.argv[1:] if not a.startswith("--")]
APPLY = "--apply" in sys.argv
ROOT = args[0] if args else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

TSV  = os.path.join(ROOT, "Automation", "salesforce-deals-latest.tsv")
JSON = os.path.join(ROOT, "DNA", "Deal Management", "panhandle-team-deals.json")

# Joe's share of total commission, by lane (see header)
JOE_SHARE = {"T": 0.30, "N": 0.09}
DELL_SHARE = {"T": 0.70, "N": 0.21}

def norm(s):
    """Loose key for matching a Salesforce deal name to a JSON deal name."""
    s = (s or "").lower()
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())

def money(s):
    try: return float(re.sub(r"[^0-9.]", "", s or "")) or 0.0
    except ValueError: return 0.0

# ---------- load the capture ----------
if not os.path.exists(TSV):
    sys.exit(f"No capture found at {TSV}\nRun the browser capture first — see DNA/Deal Management/salesforce-read-sop.md")

rows, hdr = [], None
for line in open(TSV, encoding="utf-8"):
    line = line.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"): continue
    parts = line.split("\t")
    if hdr is None:
        hdr = [p.strip() for p in parts]; continue
    rows.append(dict(zip(hdr, [p.strip() for p in parts])))

# ---------- load the Deal Room JSON ----------
data  = json.load(open(JSON, encoding="utf-8"))
deals = data["deals"]
by_name = {norm(d.get("name")): d for d in deals}

new, changed, unmatched = [], [], []
for r in rows:
    key = norm(r["deal_name"])
    d = by_name.get(key)
    if d is None:
        # 1) exact company match, 2) close-spelling name match — before declaring it new.
        # Spelling drift is real (Salesforce "Erik Peterson" vs the JSON's "Erik Petersen"),
        # so a near-match is surfaced for confirmation, never silently merged and never
        # mislabelled as a brand-new deal.
        alt = next((x for x in deals if norm(x.get("company")) == norm(r["company"]) and norm(x.get("company"))), None)
        how = "company"
        if alt is None:
            close = difflib.get_close_matches(key, list(by_name.keys()), n=1, cutoff=0.82)
            if close:
                alt = by_name[close[0]]; how = "near-spelling"
        if alt is None:
            # Deal names carry different amounts of suffix on each side ("Erik Peterson, DO"
            # vs "Erik Petersen, DO – First Call DPC"), so compare only the leading
            # person-name tokens. Still a flagged suggestion, never an automatic merge.
            lead = " ".join(key.split()[:2])
            if lead:
                cand = [(difflib.SequenceMatcher(None, lead, " ".join(k.split()[:2])).ratio(), k)
                        for k in by_name if k]
                best = max(cand, default=(0, None))
                if best[0] >= 0.85:
                    alt = by_name[best[1]]; how = f"leading name {best[0]:.0%}"
        if alt is None:
            new.append(r); continue
        d = alt
        unmatched.append((r["deal_name"], d.get("name"), how))
    diffs = []
    if r["phase"] and r["phase"] != (d.get("phase") or ""):
        diffs.append(("phase", d.get("phase") or "(blank)", r["phase"]))
    if r["city"] not in ("", "-") and r["city"] != (d.get("city") or ""):
        diffs.append(("city", d.get("city") or "(blank)", r["city"]))
    lane_now = d.get("lane") or ""
    lane_sf  = "national" if r["oom"] == "N" else "territory"
    if lane_now != lane_sf:
        diffs.append(("lane", lane_now or "(unset)", lane_sf))
    if diffs:
        changed.append((d.get("name"), diffs, r))

# ---------- report ----------
print(f"Salesforce capture: {len(rows)} deals   |   Deal Room JSON: {len(deals)} deals\n")

if new:
    print(f"NEW IN SALESFORCE — not in the Deal Room ({len(new)}):")
    for r in new:
        print(f"  + {r['deal_name']}  [{r['owner']}, {r['phase']}, {r['city'] or '-'} {r['state']}, {r['commission']}]")
    print()

if changed:
    print(f"CHANGED ({len(changed)}):")
    for name, diffs, r in changed:
        print(f"  ~ {name}")
        for field, old, newv in diffs:
            print(f"      {field}: {old}  ->  {newv}")
    print()

if unmatched:
    print("SAME DEAL, DIFFERENT NAME — confirm before trusting (never auto-merged):")
    for sf, js, how in unmatched: print(f"  ? Salesforce '{sf}'  ~  JSON '{js}'   [matched on {how}]")
    print()

# ---------- the economics, which is the whole point of the lane field ----------
tot = {"T": 0.0, "N": 0.0}
cnt = {"T": 0, "N": 0}
placeholder = 0
for r in rows:
    lane = r["oom"] if r["oom"] in tot else "T"
    amt = money(r["commission"])
    if abs(amt - 15000.0) < 0.01: placeholder += 1
    tot[lane] += amt; cnt[lane] += 1

joe = tot["T"] * JOE_SHARE["T"] + tot["N"] * JOE_SHARE["N"]
dell = tot["T"] * DELL_SHARE["T"] + tot["N"] * DELL_SHARE["N"]
print("PIPELINE BY LANE (gross commission on the deal, before the split):")
print(f"  territory     {cnt['T']:>3} deals   ${tot['T']:>12,.2f}   -> Joe 30% = ${tot['T']*0.30:>11,.2f}")
print(f"  out-of-market {cnt['N']:>3} deals   ${tot['N']:>12,.2f}   -> Joe  9% = ${tot['N']*0.09:>11,.2f}")
print(f"  {'':<14}{sum(cnt.values()):>3} deals   ${sum(tot.values()):>12,.2f}   -> Joe     ${joe:>11,.2f}  (Dell ${dell:,.2f})")
print(f"\n  CAUTION: {placeholder} of {len(rows)} rows still carry the $15,000 placeholder, not a real figure.")
print("  These totals are therefore an upper-bound sketch, not a forecast. Never present them as projected revenue.")

# ---------- optional write-back ----------
if APPLY:
    n = 0
    for name, diffs, r in changed:
        d = by_name.get(norm(name)) or next(x for x in deals if x.get("name") == name)
        for field, _old, newv in diffs:
            if field == "phase": d["phase"] = newv
            elif field == "city": d["city"] = newv
            elif field == "lane": d["lane"] = newv
        n += 1
    data["notes"] = (data.get("notes", "") +
        f" | {len(rows)}-deal Salesforce re-read applied {os.popen('date +%Y-%m-%d').read().strip()}: "
        f"{n} deals updated, {len(new)} new deals reported (not auto-added — add via the SOP so each gets a real record).")
    json.dump(data, open(JSON, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"\nAPPLIED: {n} deals updated in panhandle-team-deals.json.")
    print("New deals were NOT auto-added — they need a real record (owner, C-ID, detail file) per the SOP.")
else:
    print("\n(report only — pass --apply to write the phase/city/lane updates into the JSON)")
