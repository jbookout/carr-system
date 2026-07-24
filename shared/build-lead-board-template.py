#!/usr/bin/env python3
"""
build-lead-board.py — regenerate The Lead Board operating surface.
Derived VIEW. Reads the two sources of truth and writes lead-board.html.
Run by the weekly lead-system run (see DNA/Leads/lead-system-weekly.md) and any
session that moves a worked lead (refresh-on-change law, mirrors the Deal Room).

Usage:
  python3 build-lead-board.py [CARR_ROOT]
CARR_ROOT defaults to the folder two levels up from this script (…/CARR AI).
Sources:
  <root>/DNA/Leads/lead-router-*.xlsx   (latest by name = the 9,320 reservoir)
  <root>/DNA/Leads/lead-registry.xlsx   (Registry tab = the worked call queue)
  <root>/Automation/lead-board-decisions.json  (optional; the curated decision queue)
  <root>/Automation/renewal-radar.json  (optional; CoStar renewal feed, built by build-renewal-feed.py)
  <root>/Automation/entity-formation-leads.json + pre-entity-watch.json  (optional segment feeds)
  <root>/Automation/lead-board-template.html   (the shell)
Writes:
  <root>/Automation/lead-board.html
Then: update the `the-lead-board` artifact from that file if the desktop app is
connected (update_artifact); otherwise note it and move on. The file is the record.
"""
import sys, os, glob, json
from collections import Counter
from datetime import datetime, date
import openpyxl

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LEADS_DIR = os.path.join(ROOT, "DNA", "Leads")
AUTO = os.path.join(ROOT, "Automation")

def latest_router():
    cands = sorted(glob.glob(os.path.join(LEADS_DIR, "lead-router-*.xlsx")))
    if not cands: raise SystemExit("No lead-router-*.xlsx found in " + LEADS_DIR)
    return cands[-1]

def norm(d):
    if isinstance(d,(datetime,date)): return d.strftime("%Y-%m-%d")
    return str(d).strip() if d not in (None,"") else ""

SEG_ORDER = [
 ("🏥 NATIONAL ACCOUNT — multi-location / expanding","National Account","multi-location · expanding · portfolio rep","hot"),
 ("\U0001F52D PRE-ENTITY — upstream watch","Pre-Entity Watch","corroborated, BEFORE the filing","hot"),
 ("\U0001F195 NEW ENTITY — corp-filings","New Entity","just filed here · FL + AL","hot"),
 ("🔑 LEASE EVENT — decision window","Lease Event","a real-estate decision is coming","hot"),
 ("\U0001F9ED RELOCATING OWNER — moving to territory","Relocating Owner","out-of-state licensee owns FL property here","hot"),
 ("\U0001F3AF PRACTICE RIPE FOR SALE","Ripe for Sale","refer to practice brokers","hot"),
 ("⭐⭐ POST-SALE FOUNDER (owns nothing)","Post-Sale Founder","the hottest class","hot"),
 ("⭐ ASSOCIATE — going-independent window","Associate in Window","2-5 yr nurture","warm"),
 ("⭐ DSO ASSOCIATE (email domain proves it)","DSO Associate","nurture now","warm"),
 ("PRACTICE OWNER (Sunbiz-confirmed)","Practice Owner","expansion / buy-vs-lease","warm"),
 ("🏢 OWNER-OCCUPIER — 2nd location","Owner-Occupier","owns the building · 2nd location","warm"),
 ("SOLO — owner not yet confirmed","Solo, Unconfirmed","confirm on Sunbiz","early"),
 ("NEW GRAD — watch","New Grad","revisit in 3 yr","hold"),
 ("WINDING DOWN","Winding Down","courtesy only","hold"),
 ("🏛 INSTITUTIONAL — watch the physicians","Institutional","watch the doctors, not the building","hold"),
 ("UNCLASSIFIED","Unclassified","needs a pass","hold"),
]

def load_router(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Lead Router"]
    leads=[]; segPlay={}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r[4]: continue
        if r[0] and r[0] not in segPlay: segPlay[r[0]] = r[1] or ""
        leads.append({"s":r[0],"n":str(r[4]).title() if r[4] else "","pr":r[5] or "",
            "ly":(round(float(r[6]),0) if isinstance(r[6],(int,float)) else ""),
            "ab":r[8] or "","na":r[9] or "","ad":r[10] or "",
            "ci":(str(r[11]).title() if r[11] else ""),"co":(str(r[12]).title() if r[12] else ""),
            "e":r[14] or "","ph":r[15] or "","own":r[2] or ""})
    wb.close()
    return leads, segPlay

def load_registry(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Registry"]
    out=[]
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r[0] or r[2] != "Joe": continue
        out.append({"id":r[0],"stage":r[3],"seg":r[4],"name":r[5],"practice":r[6],"spec":r[7],
            "city":r[8],"county":r[9],"email":r[10] or "","phone":r[11] or "",
            "next":str(r[17] or ""),"nextdate":norm(r[18]),"lasttouch":norm(r[19]),
            "detail":r[21] or "","lease":norm(r[23]) if len(r)>23 else "","leaseconf":(r[25] if len(r)>25 else "") or ""})
    wb.close()
    return out

router_path = latest_router()
L, segPlay = load_router(router_path)
# Entity-formation feed (the corp-filings lane output: new territory business filings,
# FL Sunbiz + AL SOS — the leads the licensure-based router structurally misses).
ef_path = os.path.join(AUTO, "entity-formation-leads.json")
ef_n = 0
if os.path.exists(ef_path):
    ef = json.load(open(ef_path))
    NEW_SEG = "\U0001F195 NEW ENTITY — corp-filings"
    for x in ef:
        if x.get("in_territory") is False:
            continue  # out-of-territory rows are logged in the file but not shown on the board
        L.append({"s":NEW_SEG,"n":x.get("n",""),"pr":x.get("pr",""),"ly":"","ab":"","na":"",
                  "ad":x.get("ad",""),"ci":x.get("ci",""),"co":x.get("co",""),
                  "e":x.get("e",""),"ph":x.get("ph",""),"own":x.get("own","YES")})
        ef_n += 1
segPlay["\U0001F195 NEW ENTITY — corp-filings"] = "New business entities filed in territory (FL Sunbiz + Alabama SOS). This is the entity-formation lane, the detector that catches relocators and Alabama leads the licensure list misses. Owner-confirmed by the filing itself."
# Pre-entity watch feed (Radar lane 4, the corroboration engine: corroborate.py output).
pe_path = os.path.join(AUTO, "pre-entity-watch.json")
if os.path.exists(pe_path):
    PE_SEG = "\U0001F52D PRE-ENTITY — upstream watch"
    for x in json.load(open(pe_path)):
        if x.get("in_territory") is False: continue
        sens = " ⚠" if x.get("sensitivity","").startswith("PERSONAL") else ""
        L.append({"s":PE_SEG,"n":x.get("n","")+sens,"pr":x.get("pr",""),"ly":"","ab":"","na":"",
                  "ad":x.get("ad",""),"ci":x.get("ci",""),"co":x.get("co",""),
                  "e":x.get("e",""),"ph":x.get("ph",""),"own":""})
segPlay["\U0001F52D PRE-ENTITY — upstream watch"] = "Corroborated upstream signals: two or more independent pre-entity signals (tip, deed, Medicare enrollment, license, address move) pointing at the same person BEFORE any entity files. The earliest the system can see. ⚠ rows are PERSONAL-adjacent: the detection is never the opener; every touch passes the how-did-you-know-to-call-me test."
# Renewal Radar feed (CoStar tenant renewal leads; doctrine DNA/Leads/renewal-radar-sop.md,
# built by build-renewal-feed.py from the latest DNA/Leads/renewal-radar-*.xlsx). Segment set
# by the feed (LEASE RENEWAL / OWNER-OCCUPIER / INSTITUTIONAL). Carries lease-event + tier fields.
rr_path = os.path.join(AUTO, "renewal-radar.json")
if os.path.exists(rr_path):
    for x in json.load(open(rr_path)):
        L.append({"s":x.get("s",""),"n":x.get("n",""),"pr":x.get("pr",""),"ly":"","ab":"","na":"",
                  "ad":x.get("ad",""),"ci":x.get("ci",""),"co":x.get("co",""),
                  "e":x.get("e",""),"ph":x.get("ph",""),"own":"",
                  "le":x.get("le",""),"tier":x.get("tier",""),"conf":x.get("conf",""),
                  "ll":x.get("ll",""),"rep":x.get("rep",""),"flag":x.get("flag",""),"newll":x.get("newll","")})
segPlay["🔑 LEASE EVENT — decision window"] = "A lease coming up is NOT a renewal — it means the doctor has reached a real-estate DECISION point: renew, relocate and lease, relocate and buy, buy the space they are in, build ground-up, sale-leaseback, or exit. The job is to be in the room BEFORE they decide and represent whichever way they go. Expiry data is blank in this market, so every date is DERIVED (commencement / move-in / permit) and the confidence tag says how firm. Leverage is built 12-24 months out, not at the deadline. A blank tenant rep = unrepresented = the CARR pitch, pre-qualified. CoStar shows only part of the market; other MLS feeds append into this segment as they come online."
segPlay["🏢 OWNER-OCCUPIER — 2nd location"] = "The practice OWNS its building (CoStar Occupancy=Owned, tax-roll confirmed). Never a renewal lead, but a proven transactor with a balance sheet: a second location, expansion, sale-leaseback, eventual sale, and the best referral source alive. The biggest hole in the old model; never delete these."
segPlay["🏛 INSTITUTIONAL — watch the physicians"] = "Hospital-system, Navy, or county tenants. Do not pitch the building, watch the physicians inside it (D5 team-diff). Institutions are the farm system: where independents come from (Brown filed Serenity while employed at HCA Florida West)."

# Relocating-owner feed (tax-roll NAL owners x out-of-state DOH new-licensee join; built by
# parse-tax-rolls.py -> relocating-owner-candidates, deduped+scored into relocating-owner-leads.json).
# An out-of-state doctor licensed in FL in the last ~12 months who ALSO owns a territory parcel is
# very likely relocating here, knowable months before any entity files. Rows carry their own board
# schema (incl. conf/newll); each is scored so Joe qualifies on the board, none pre-filtered out.
ro_path = os.path.join(AUTO, "relocating-owner-leads.json")
ro_n = 0
if os.path.exists(ro_path):
    for x in json.load(open(ro_path)):
        L.append(x); ro_n += 1
segPlay["\U0001F9ED RELOCATING OWNER — moving to territory"] = "An out-of-state clinician licensed in FL in the last ~12 months who ALSO owns a territory parcel is almost certainly moving here — the earliest ownership-backed relocation signal the system has, months before any entity filing. Source: FL DOR tax-roll NAL owners joined to the out-of-state FL DOH new-licensee pool by name. HIGH = the new licensee's state matches the parcel owner's mailing state (act on these); MEDIUM = name match with differing states (a common name can be a different person — verify identity first); LOW = likely a namesake owning many blank-address/non-residential parcels. A name match is not a person match: corroborate the person and the intent before any touch, and never reference how you knew to call."

NA_SEG = "🏥 NATIONAL ACCOUNT — multi-location / expanding"
na_path = os.path.join(ROOT, "DNA", "Team", "national-accounts.json")
if os.path.exists(na_path):
    for x in json.load(open(na_path)):
        summary = " · ".join([q for q in [x.get("type",""), x.get("locations",""), x.get("status","")] if q])
        loc = ", ".join([q for q in [x.get("ci",""), x.get("co","")] if q])
        pathline = ("  ·  warm path: " + x.get("path","")) if x.get("path") else ""
        L.append({"s":NA_SEG,"n":x.get("n",""),"pr":summary,"ly":"","ab":"","na":"","ad":loc + pathline,"ci":x.get("ci",""),"co":x.get("co",""),"e":x.get("email",""),"ph":x.get("phone",""),"own":x.get("own","")})
segPlay[NA_SEG] = "National Accounts: multi-location / expanding healthcare orgs (groups, DSOs, franchise/emerging, regional systems, surgical/ASC + acute care), curated + human-review, never auto-scored. No facility type/size auto-excluded. Shared feed: DNA/Team/national-accounts.json. Seed: AltaPointe Health."

registry = load_registry(os.path.join(LEADS_DIR, "lead-registry.xlsx"))
decisions_path = os.path.join(AUTO, "lead-board-decisions.json")
DECISIONS = json.load(open(decisions_path)) if os.path.exists(decisions_path) else []
hot_path = os.path.join(AUTO, "lead-board-hot.json")
HOT = json.load(open(hot_path)) if os.path.exists(hot_path) else {"ranked":[],"proposed":[],"holds":[],"signals":[]}
hot_n = len(HOT.get("ranked",[])) + len(HOT.get("proposed",[]))

segCount = Counter(x["s"] for x in L)
emailCov = sum(1 for x in L if x["e"] and "@" in str(x["e"]))
counties_n = len({str(x["co"]).strip() for x in L if str(x.get("co","")).strip()})
segmeta = [[m[0],m[1],m[2],m[3],segPlay.get(m[0],"")] for m in SEG_ORDER]
queue_n = len([r for r in registry if r["stage"] != "Nurture (Drip)"])

template = open(os.path.join(AUTO, "lead-board-template.html"), encoding="utf-8").read()
stamp = date.today().strftime("%B %-d, %Y") if hasattr(date.today(),'strftime') else "today"

html = template
html = html.replace("__STAMP__", stamp)
html = html.replace("__COUNTIES__", str(counties_n))
html = html.replace("__TOTAL__", f"{len(L):,}").replace("__EMAIL__", f"{emailCov:,}")
html = html.replace("__QUEUE__", str(queue_n)).replace("__DEC__", str(len(DECISIONS)))
html = html.replace("__LEADS__", json.dumps(L,separators=(",",":")))
html = html.replace("__REG__", json.dumps(registry,separators=(",",":")))
html = html.replace("__SEGMETA__", json.dumps(segmeta,separators=(",",":")))
html = html.replace("__SEGCOUNT__", json.dumps(dict(segCount),separators=(",",":")))
html = html.replace("__DECISIONS__", json.dumps(DECISIONS,separators=(",",":")))
html = html.replace("__HOTN__", str(hot_n))
html = html.replace("__HOT__", json.dumps(HOT,separators=(",",":")))

out_path = os.path.join(AUTO, "lead-board.html")
open(out_path,"w",encoding="utf-8").write(html)
print(f"Wrote {out_path}  ({os.path.getsize(out_path):,} bytes)")

# Shared-tier publish (added 2026-07-20, Joe's brain): Dell's brain cannot see
# Automation/, so every rebuild also drops the built board into the shared
# DNA/Team/live-boards/ for his side to re-persist. Derived view; overwrite is correct.
shared_path = os.path.join(ROOT, "DNA", "Team", "live-boards", "lead-board-latest.html")
try:
    os.makedirs(os.path.dirname(shared_path), exist_ok=True)
    open(shared_path,"w",encoding="utf-8").write(html)
    print(f"Published shared copy: {shared_path}")
except Exception as e:
    print(f"WARNING: shared-tier publish failed ({e}) — Dell's copy will go stale; fix before session end.")
print(f"Router: {os.path.basename(router_path)} | {len(L):,} leads | queue {queue_n} | decisions {len(DECISIONS)}")
