#!/usr/bin/env python3
"""dso-match.py — address-match associate detector.
Reads DNA/Leads/dso-corporate-locations.json + a provider source and tags providers whose PRACTICE
address matches a corporate/DSO clinic, by brand+vertical. Writes Automation/dso-matches.json + prints a summary.
Default source = latest lead-router-*.xlsx (col 10). NOTE: that column is mostly the FL bulk MAILING
address, which UNDERCOUNTS. Point --src at a list carrying the FL MQA HealthCareProviders PRACTICE address
(https://mqa-internet.doh.state.fl.us/MQASearchServices/HealthCareProviders) for the real yield.
"""
import openpyxl, glob, os, re, json, sys
from collections import Counter
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),".."))
def norm(a):
    a=str(a or "").upper().strip(); a=re.sub(r"[.,]"," ",a)
    a=re.sub(r"\bSTE\b.*|\bSUITE\b.*|\b#.*","",a)
    for k,v in [("STREET","ST"),("AVENUE","AVE"),("ROAD","RD"),("BOULEVARD","BLVD"),("HIGHWAY","HWY"),("EAST","E"),("WEST","W"),("NORTH","N"),("SOUTH","S")]:
        a=re.sub(r"\b"+k+r"\b",v,a)
    return re.sub(r"\s+"," ",a).strip()
ref=json.load(open(os.path.join(ROOT,"DNA","Leads","dso-corporate-locations.json")))
targets={}
for vert,brands in ref.items():
    if vert.startswith("_") or not isinstance(brands,dict): continue
    for brand,locs in brands.items():
        if isinstance(locs,list):
            for L in locs: targets[norm(L["street"])]=(brand,vert)
src=sys.argv[1] if len(sys.argv)>1 else sorted(glob.glob(os.path.join(ROOT,"DNA","Leads","lead-router-*.xlsx")))[-1]
ws=openpyxl.load_workbook(src,read_only=True,data_only=True)["Lead Router"]
# Schema-validated (orchestrator-lane corrective #1, 2026-07-25): headers by name.
_d=os.path.dirname(os.path.abspath(__file__))
for _c in (os.path.join(_d,"..","lib"), _d):
    if os.path.isfile(os.path.join(_c,"sheets.py")): sys.path.insert(0,_c); break
from sheets import header_map, data_rows
c=header_map(ws,["SEGMENT","Name","Profession","Practice Address","City","Email","Phone"],f"{os.path.basename(src)}[Lead Router]")
out=[]
for r in data_rows(ws):
    if not r[c["Name"]] or not r[c["Practice Address"]]: continue
    t=targets.get(norm(r[c["Practice Address"]]))
    if t: out.append({"name":str(r[c["Name"]]),"prof":str(r[c["Profession"]] or ""),"brand":t[0],"vertical":t[1],"seg":str(r[c["SEGMENT"]] or ""),"addr":str(r[c["Practice Address"]]),"city":str(r[c["City"]] or ""),"email":str(r[c["Email"]] or ""),"phone":str(r[c["Phone"]] or "")})
json.dump(out,open(os.path.join(ROOT,"Automation","dso-matches.json"),"w"),indent=1)
print(f"source: {os.path.basename(src)} | {len(targets)} corporate addresses loaded | matches: {len(out)}")
for b,n in Counter(x["brand"] for x in out).most_common(): print(f"  {n:4}  {b}")
print("wrote Automation/dso-matches.json")
