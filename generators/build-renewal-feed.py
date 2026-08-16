#!/usr/bin/env python3
"""
build-renewal-feed.py — turn the latest lease-event MLS export into a board feed.
Reads the newest DNA/Leads/renewal-radar-*.xlsx (the processed CoStar list, per
DNA/Leads/renewal-radar-sop.md) and writes Automation/renewal-radar.json, which
build-lead-board.py merges as board segments.

THE REFRAME (Joe, 2026-07-14): a lease coming up is NOT a renewal. It means the doctor
is at a real-estate DECISION point — renew, relocate & lease, relocate & buy, buy the
current space, ground-up build, sale-leaseback, or exit. The job is to be in the room
BEFORE the decision and represent whichever way they go. So the segment is a LEASE EVENT
/ decision window, not a "renewal." Source-agnostic on purpose: as other MLS feeds
(Crexi, ECAR, Moody's, Bay/Tallahassee MLS) come online they append into the same
segments — CoStar only shows part of the market.

Routes the radar's sub-types into three board segments:
  T1/T2/T3 leased tenants -> LEASE EVENT (decision window)   [tier shown per lead]
  owner-occupiers         -> OWNER-OCCUPIER (2nd location)
  institutional tenants   -> INSTITUTIONAL (watch the physicians)

SUPPRESSOR (Joe, 2026-07-14): cross-check every row against the lead registry —
read live from the record layer by default since ORDER 29b (`v_export_leads`,
the same view lead-registry.xlsx is exported from); --files reads the xlsx
directly, and records mode falls back to it, loudly, wherever the record layer
is unreachable.
  - a match flagged do-not-contact / paused / closed  -> DROPPED (never resurface)
  - any other registry match                          -> KEPT but FLAGGED "already L-xxx · stage"
  - no match                                          -> normal lease-event lead
Every suppression/flag is printed, so a bad match is visible (stale beats silent).

Usage: python3 build-renewal-feed.py [--records|--files] [CARR_ROOT]
"""
import os, glob, json, re, sys
from datetime import date
import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# ORDER 29b: the registry suppressor reads the RECORD (v_export_leads through
# lib/record_sources.py) by default now, the same view the exporter renders
# lead-registry.xlsx from — this file gains a second read path, same as the
# ORDER 29a consumers. The radar xlsx this generator turns into board rows is a
# separate, external CoStar export and is untouched: it still only ever comes
# from a file, and the JSON this file writes (Automation/renewal-radar.json)
# still lands exactly where it always has.
from lib.record_sources import MODE_RECORDS, effective_mode, load_leads, resolve_mode, source_note

MODE, ARGS = resolve_mode(sys.argv[1:], default=MODE_RECORDS)
MODE = effective_mode(MODE, "renewal-feed")

ROOT = ARGS[0] if ARGS else os.path.abspath(os.path.join(HERE, ".."))
LEADS_DIR = os.path.join(ROOT, "DNA", "Leads")
AUTO = os.path.join(ROOT, "Automation")

SEG_LEASE = "\U0001F511 LEASE EVENT — decision window"
SEG_OWNER = "\U0001F3E2 OWNER-OCCUPIER — 2nd location"
SEG_INST  = "\U0001F3DB INSTITUTIONAL — watch the physicians"

CITY_COUNTY = {
 "PENSACOLA":"Escambia","CANTONMENT":"Escambia","PERDIDO KEY":"Escambia","MOLINO":"Escambia",
 "CENTURY":"Escambia","MCDAVID":"Escambia","GONZALEZ":"Escambia",
 "MILTON":"Santa Rosa","GULF BREEZE":"Santa Rosa","NAVARRE":"Santa Rosa","PACE":"Santa Rosa",
 "JAY":"Santa Rosa","BAGDAD":"Santa Rosa","MARY ESTHER":"Okaloosa","FORT WALTON BEACH":"Okaloosa",
}
def county_of(city): return CITY_COUNTY.get(str(city or "").strip().upper(), "")

def vertical(naics, industry):
    n = str(naics or "").strip()
    m = {"621210":"Dental practice","621111":"Physician practice","621112":"Physician (specialist)",
         "621320":"Optometry","621330":"Mental health","621340":"PT / OT / speech","621391":"Podiatry",
         "621399":"Health practitioner","621498":"Outpatient clinic","621492":"Dialysis / kidney",
         "621493":"Urgent care","621610":"Home health","623110":"Nursing facility","623311":"Assisted living",
         "623312":"Assisted living","621511":"Medical lab","621512":"Imaging center","621910":"Ambulance / EMS",
         "622110":"Hospital","621420":"Behavioral / substance"}
    for k,v in m.items():
        if n.startswith(k): return v
    ind = str(industry or "").strip()
    if ind == "Health Care and Social Assistance": return "Healthcare tenant"
    return ind if ind and ind.lower()!="none" else "Healthcare tenant"

def val(x):
    s = "" if x is None else str(x).strip()
    return "" if s.lower()=="none" else s

GENERIC = {"dr","md","do","dds","dmd","pa","arnp","aprn","the","of","and","llc","pllc","inc","pc","co",
 "center","centre","centers","clinic","clinics","group","associates","assoc","associate","health","healthcare",
 "medical","care","services","service","specialists","specialist","dermatology","dental","orthopedic","orthopaedic",
 "pediatric","pediatrics","family","internal","medicine","vision","eye","surgery","surgical","plastic","hearing",
 "skin","wellness","institute","physicians","physician","office","offices","laser","rehabilitation","aid","extended",
 "rehab","assisted","living","nursing","memory","home","st","saint","north","south","gulf","coast","oral","states",
 "cardiology","cardiac","urgent","primary","womens","women","mens","childrens","child","kids","behavioral","mental",
 "therapy","physical","occupational","imaging","radiology","oncology","cancer","urology","neurology","dermatologic",
 "pensacola","milton","navarre","pace","cantonment","breeze","perdido","jay","bagdad","gonzalez","molino","escambia",
 "santarosa","rosa","santa","county","fl","florida","al","alabama","area","nw","ne","sw","se","llp","pllcs","ppec"}
def distinct(s):
    return {t for t in re.sub(r'[^a-z0-9 ]',' ',str(s or '').lower()).split() if t not in GENERIC and len(t)>2}

def load_registry():
    """Return list of {id, owner, stage, last, first, ptoks, dnc}.

    ORDER 29b: reads through lib/record_sources.load_leads, which is
    records-mode by default (the same `v_export_leads` view the exporter
    renders lead-registry.xlsx from) and falls back to the file, loudly, when
    the record layer is unreachable. Both modes hand back dicts keyed by the
    exact column names in exporters/targets.py:REGISTRY_COLS ("Lead ID",
    "Owner", "Stage", "Contact Name", "Practice", "Next Action", "Notes"), so
    the field access below is mode-agnostic.
    """
    out=[]
    for r in load_leads(ROOT, MODE):
        lead_id = r.get("Lead ID")
        if not lead_id: continue
        contact = val(r.get("Contact Name")); practice = val(r.get("Practice")); stage = val(r.get("Stage"))
        nexta = val(r.get("Next Action")); notes = val(r.get("Notes"))
        cn = [t for t in re.sub(r'[^a-z ]',' ',contact.lower()).split() if t not in ("dr","mr","mrs","ms")]
        first = cn[0] if cn else ""; last = cn[-1] if len(cn)>1 else ""
        blob = (nexta+" "+notes).lower()
        dnc = bool(re.search(r"do not contact|do not nudge|no further outreach|inbound only|no nudges|sequence closed|no follow", blob)) or stage.strip().lower() in ("paused","closed","lost","dead","do not contact")
        out.append({"id":lead_id,"owner":val(r.get("Owner")),"stage":stage,"last":last,"first":first,
                    "ptoks":distinct(practice),"dnc":dnc})
    return out

def match_registry(tenant, contact, reg):
    tks = distinct(tenant) | distinct(contact)
    ctoks = set(re.sub(r'[^a-z ]',' ',(str(tenant)+" "+str(contact)).lower()).split())
    for g in reg:
        shared = {t for t in (g["ptoks"] & tks) if len(t)>=4}
        # practice match: 2+ shared distinctive tokens, OR the full distinctive sets are equal & non-trivial
        pmatch = (len(shared) >= 2) or (g["ptoks"] and g["ptoks"]==tks and len(g["ptoks"])>=1)
        # contact match: registry first AND last both present, last name >=4 chars (avoid short common ones)
        nmatch = bool(g["first"] and g["last"] and len(g["last"])>=4 and g["first"] in ctoks and g["last"] in ctoks)
        if pmatch or nmatch:
            return g
    return None

def latest_radar():
    c = sorted(glob.glob(os.path.join(LEADS_DIR, "renewal-radar-*.xlsx")))
    if not c: raise SystemExit("no renewal-radar-*.xlsx in "+LEADS_DIR)
    return c[-1]

TIER_T1 = "T1 (window <12mo)"
TIER_T2 = "T2 (12-24mo leverage)"
TIER_T3 = "T3 (nurture)"

def tier_from_date(le, today):
    """Recompute the tier band from EST LEASE EVENT and the RUN date (loop #204).

    The spreadsheet TIER column was computed once, on the July 13 pull, and never
    moved again: by 2026-08-07 eight of its 20 T1 windows were already behind the
    calendar while every T2 crept a month closer to being a T1. A tier is a claim
    about TIME REMAINING, so it has to be re-derived every run, from the date the
    radar actually holds.

    Returns (tier, note). A window already behind today's date stays T1 rather
    than vanishing (the decision may be happening right now, or the estimate was
    soft), but the note says so on the row. (None, None) means the date did not
    parse and the caller keeps the spreadsheet tier, flagged, never guessed.
    """
    m = re.fullmatch(r"(\d{4})-(\d{2})", le or "")
    if not m:
        return None, None
    months = (int(m.group(1)) - today.year) * 12 + (int(m.group(2)) - today.month)
    if months < 0:
        return TIER_T1, f"est window {le} already past, verify before outreach"
    if months <= 12:
        return TIER_T1, ""
    if months <= 24:
        return TIER_T2, ""
    return TIER_T3, ""

def main():
    path = latest_radar()
    reg = load_registry()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Renewal Radar"]; rows = list(ws.iter_rows(values_only=True)); wb.close()
    H = {str(c):i for i,c in enumerate(rows[0])}
    TODAY = date.today()
    out=[]; dist={}; dropped=[]; flagged=[]; moved=0; unparsed=0
    for r in rows[1:]:
        tenant = val(r[H['Tenant']])
        if not tenant: continue
        tier_raw = val(r[H['TIER']]); city = val(r[H['City']])
        le = val(r[H['EST LEASE EVENT']]); conf = val(r[H['Confidence']])
        rep = val(r[H['Tenant Rep (blank=unknown)']]); ll = val(r[H['Landlord']])
        phone = val(r[H['Phone']]); contact = val(r[H['Best Contact']])
        newll = val(r[H['NEW LANDLORD? (sold 2023+)']])
        addr = val(r[H['Address']]); suite = val(r[H['Suite']])
        pr = vertical(val(r[H['NAICS']]), val(r[H['Industry']]))
        # suppressor
        g = match_registry(tenant, contact, reg)
        flag = ""
        if g:
            if g["dnc"]:
                dropped.append((tenant, g["id"], g["stage"], g["owner"])); continue
            flag = f"already {g['id']} · {g['owner']} · {g['stage']}"
            flagged.append((tenant, g["id"], g["stage"], g["owner"]))
        if tier_raw.startswith("OWNER"): seg, tier = SEG_OWNER, ""
        elif tier_raw.startswith("INSTITUTIONAL"): seg, tier = SEG_INST, ""
        else:
            # Lease-event row. The tier is RECOMPUTED from EST LEASE EVENT against
            # the run date (loop #204) — the spreadsheet column is only a fallback
            # for a row whose date does not parse, and that fallback is flagged on
            # the row rather than silently trusted.
            seg = SEG_LEASE
            tier, note = tier_from_date(le, TODAY)
            if tier is None:
                if tier_raw.startswith("T1"): tier = TIER_T1
                elif tier_raw.startswith("T2"): tier = TIER_T2
                else: tier = TIER_T3
                if tier_raw.startswith(("T1", "T2")):
                    # A claimed window with no parseable date behind it is the one
                    # combination worth a flag; a dateless T3 is already the floor.
                    note = ("tier from the spreadsheet, "
                            + (f"lease event {le!r} is not a parseable YYYY-MM"
                               if le else "no lease-event date on file"))
                else:
                    note = ""
                unparsed += 1
            else:
                spread = (TIER_T1 if tier_raw.startswith("T1") else
                          TIER_T2 if tier_raw.startswith("T2") else TIER_T3)
                if tier != spread: moved += 1
            if note:
                flag = " · ".join(x for x in (flag, note) if x)
        ad = ", ".join([x for x in [addr+(" #"+suite if suite else ""), city] if x])
        out.append({"s":seg,"n":tenant,"pr":pr,"ly":"","ab":"","na":"",
                    "ad":ad,"ci":city,"co":county_of(city),"e":"","ph":phone,"own":"",
                    "le":le,"tier":tier,"conf":conf,"ll":ll,
                    "rep":("unrepresented" if not rep else ""),
                    "flag":flag,"newll":("new landlord (sold 2023+)" if newll else "")})
        dist[seg]=dist.get(seg,0)+1
    # --- GCCMLS lease-event feed (source-agnostic append; Gulf Coast MLS, added 2026-07-14) ---
    # Second MLS source per the reframe note above. Property-level decision-window signals
    # (address + term-inferred expiration, tenant not yet identified). Guarded: no-op if absent.
    gc = os.path.join(AUTO, "gccmls-lease-events.json")
    if os.path.exists(gc):
        try:
            extra = json.load(open(gc))
            out.extend(extra)
            for e in extra: dist[e.get("s","?")] = dist.get(e.get("s","?"),0)+1
            print(f"GCCMLS feed: +{len(extra)} lease-event rows appended")
        except Exception as ex:
            # Corrective #2 (2026-07-25): the file EXISTS but is unreadable — that is
            # corruption, not absence; absence is the guarded no-op above. Fail loudly.
            raise SystemExit(f"GCCMLS feed exists but is unreadable ({ex}) — fix or remove {gc}")
    json.dump(out, open(os.path.join(AUTO,"renewal-radar.json"),"w"), indent=1)
    print(f"source: {os.path.basename(path)} -> {len(out)} rows on the board | registry source: {source_note(MODE)}")
    for s,n in dist.items(): print(f"   {n:>4}  {s}")
    print(f"\nTIER RECOMPUTE (run date {TODAY.isoformat()}): {moved} CoStar rows moved band vs the "
          f"spreadsheet column, {unparsed} kept their spreadsheet tier (no parseable date; flagged "
          f"where they claim a window). GCCMLS rows keep their own feed's tiers: their windows are "
          f"multi-scenario estimates, and recomputing one would mean picking a scenario, which is "
          f"guessing.")
    print(f"\nSUPPRESSOR: {len(dropped)} dropped (do-not-contact/paused), {len(flagged)} flagged (already a known lead)")
    for t,i,st,o in dropped: print(f"   DROP  {t}  ->  {i} ({o}, {st})")
    for t,i,st,o in flagged: print(f"   FLAG  {t}  ->  {i} ({o}, {st})")

    # ── ORDER 26b: pool mapping, writer-side ──────────────────────────────
    # This run just wrote Automation/renewal-radar.json above. Map it into
    # candidate_pool NOW, in this same run, instead of leaving the pool to
    # wait on a separate hand-run of map_radar_lanes. `pipelines` is already
    # importable (HERE's parent went onto sys.path at module load, above) —
    # the only thing that can be missing here is psycopg itself or the DB
    # credential, both handled inside run_lane / the guard below, and neither
    # may take down a renewal-feed run that already succeeded at its real job.
    try:
        from pipelines.map_radar_lanes import run_lane
    except ImportError as e:
        print(f"[map-radar-lane SKIP] renewal-radar: {e} — pool mapping needs psycopg, "
              f"which this interpreter does not have. renewal-radar.json was still "
              f"written normally; catch the pool up by hand with the repo venv: "
              f".venv/bin/python -m pipelines.map_radar_lanes --lane renewal-radar",
              file=sys.stderr)
    else:
        run_lane("renewal-radar", rows=out)
if __name__=="__main__": main()
