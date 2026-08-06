#!/usr/bin/env python3
"""
diff-salesforce-deals.py — reconcile the Salesforce capture against the deal record.

Salesforce report export is DISABLED on Joe's profile, so the capture step is a
browser read (see DNA/Deal Management/salesforce-read-sop.md). That step writes
Automation/salesforce-deals-latest.tsv. THIS script is the deterministic half:
it diffs that capture against the deal record and prints exactly what changed.

READ SIDE (ORDER 29b, repointed from panhandle-team-deals.json): records mode is
the default and reads the deal record live through lib/record_sources.py's
load_deals_doc(), the same read build-deal-room.py and graph-health.py already
use (ORDER 29a). Records mode falls back to files, loudly on stderr, when there
is no exporter credential or psycopg is not importable (a plain `python3`, a
machine with no db.env) — see lib/record_sources.py's effective_mode(). Pass
--files to force the file read on purpose; panhandle-team-deals.json is still
generated nightly and is still the only path Dell's runtime and any machine
without a database credential can use.

WRITE SIDE: it never guesses and never writes anything unless --apply is passed.
  --files mode   unchanged: --apply still hand-writes the phase/city/lane fields
                 into panhandle-team-deals.json, the documented fallback path.
  --records mode panhandle-team-deals.json is now a GENERATED EXPORT of the
                 record layer (nightly). A hand-write to it here would be
                 overwritten by the next export and look applied, then silently
                 vanish. --apply therefore REFUSES to write anything in records
                 mode: it prints the exact changes found and the record-verb
                 calls (update-deal) a session should run instead, so a human or
                 a session can execute them deliberately. This script does not
                 write to the database; it never has and still does not.

Lane truth: Salesforce's own "Out of Market Deal" checkbox (the `oom` column,
N/T), NOT a city-string heuristic. A deal's lane decides the economics:
  T (territory)  — CARR represents:            Dell 70% / Joe 30%
  N (out-of-market) — referred to a local CARR agent: agent 70% / Dell 21% / Joe 9%
(verified 2026-07-25 against Salesforce Deal Splits on Trambadia and Nikki Cottis)

Usage:
  python3 diff-salesforce-deals.py [CARR_ROOT]                       # report only, records mode
  python3 diff-salesforce-deals.py [CARR_ROOT] --files                # report only, file mode
  python3 diff-salesforce-deals.py [CARR_ROOT] --apply                # records mode: refuses, prints the update-deal calls
  python3 diff-salesforce-deals.py [CARR_ROOT] --files --apply        # file mode: writes panhandle-team-deals.json (fallback path)
"""
import sys, os, json, re, difflib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.record_sources import MODE_RECORDS, effective_mode, load_deals_doc, resolve_mode

MODE, ARGS = resolve_mode(sys.argv[1:], default=MODE_RECORDS)
MODE = effective_mode(MODE, "salesforce-diff")
RECORDS_MODE = MODE == MODE_RECORDS

args = [a for a in ARGS if not a.startswith("--")]
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

# The capture step (pipelines/capture-salesforce-report.js) emits Salesforce's own column
# headers; a hand-built TSV uses the short names. Accept either, so the two halves of this
# workflow actually plug together no matter which produced the file.
ALIAS = {
    "deal name": "deal_name", "company name": "company", "deal owner": "owner",
    "total commission": "commission", "city of transaction": "city",
    "state of transaction": "state", "out of market deal": "oom",
    "out of market deal type": "oom_type", "phase": "phase", "close date": "close_date",
    "primary contact": "contact", "transaction type": "txn",
}
def colname(h):
    h = h.strip()
    return ALIAS.get(h.lower(), h.lower().replace(" ", "_"))

rows, hdr = [], None
for line in open(TSV, encoding="utf-8"):
    line = line.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"): continue
    parts = line.split("\t")
    if hdr is None:
        hdr = [colname(p) for p in parts]; continue
    rows.append(dict(zip(hdr, [p.strip() for p in parts])))

# Salesforce renders the Out-of-Market checkbox as prose; normalise to the N/T the rest
# of this script (and the graph's lane tags) speak.
for r in rows:
    v = (r.get("oom") or "").strip().lower()
    if "not included" in v: r["oom"] = "T"
    elif "included" in v:   r["oom"] = "N"
    r.setdefault("deal_name", ""); r.setdefault("company", "")
    for k in ("city", "state", "phase", "commission", "owner", "oom_type", "close_date"):
        r.setdefault(k, "")
    if r.get("owner", "").startswith("Wayne"): r["owner"] = "Dell"
rows = [r for r in rows if r.get("deal_name")]     # drops Salesforce's grand-total row

# ---------- load the deal record (ORDER 29b: records by default, --files falls back) ----------
data  = load_deals_doc(ROOT, MODE)
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
if APPLY and RECORDS_MODE:
    # ORDER 29b, ORDER 28 row 17. panhandle-team-deals.json is now a GENERATED
    # export of the record layer (nightly, CARR_EXPORT_LIVE, see lib/record_sources.py
    # and exporters/targets.py:build_deals). A hand-write straight into that file
    # would look applied and then be silently overwritten by the next export —
    # worse than refusing, because it reads as done and is not. This script does
    # not write to the database and is not growing a write path today (that is a
    # separate, deliberate scope decision, not an oversight): field-level deal
    # changes go through the record verbs, chiefly update-deal. So --apply in
    # records mode REFUSES the write and prints exactly what it would have
    # changed, so a session can run the verb calls by hand.
    print("\nAPPLY REFUSED (records mode): panhandle-team-deals.json is a GENERATED "
          "export now — writing to it here would be overwritten by the next nightly "
          "export and look applied when it is not. Nothing was written, in the file "
          "or the database.")
    if changed:
        print("\nThese are the changes Salesforce implies. Run them through the record "
              "verbs instead (each needs a fresh base_version from a `find` or "
              "`deal-board` read on the deal first):")
        for name, diffs, r in changed:
            for field, old, newv in diffs:
                if field == "phase":
                    # update-deal's `phase` field is the deal_phase SLUG
                    # (pending/research/site_selection/negotiation/closing/closed +
                    # imported), not the free-text label Salesforce and this legacy
                    # JSON carry (e.g. "Legal", "Due Diligence") — map it by hand
                    # before calling, do not paste `newv` in verbatim.
                    print(f"  update-deal(deal={name!r}, idempotency_key=<fresh-uuid>, "
                          f"base_version=<fresh>, fields={{\"phase\": <slug for {newv!r}>}})"
                          f"   # {field}: {old!r} -> {newv!r}")
                else:
                    print(f"  NO RECORD VERB YET for deal={name!r} field={field!r}: "
                          f"{old!r} -> {newv!r}  "
                          f"({'city is not a deal-table column, only prose inside source_row' if field == 'city' else 'deal.lane exists (migration 0061) but no verb currently writes it'} "
                          f"— update-deal's allowed fields are phase, segment, outcome, closed_on, "
                          f"won_value, notes_path, salesforce_id)")
    else:
        print("\nNo changes were found — nothing to apply.")
elif APPLY:
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
    print("\n(report only — pass --apply to write the phase/city/lane updates into the JSON in "
          "--files mode, or print the record-verb calls to run in records mode)")
