#!/usr/bin/env python3
"""
build-graph-notes.py — derived Obsidian graph notes for the CARR AI vault.

Emits one small .md note per entity into <vault>/Graph/ so Obsidian's graph
shows the REAL system: vendors, worked leads, clients, deals — with every
relationship the data actually encodes rendered as a [[wikilink]] edge.
DERIVED VIEW: regenerate any time; never hand-edit Graph/ (files are truth:
vendors.xlsx, lead-registry.xlsx, client-roster.xlsx, panhandle-team-deals.json).

Design bar (Joe, 2026-07-24): complete and truthful, legible via the semantic
palette (graph color groups live in .obsidian/graph.json, keyed to the tags
written here). Anomalies are the point: a vendor with no edges never referred;
a floating lead has no source; an unlinked referral string matched nobody.

Identity rule: edges link on exact IDs (V-###, C-###, L-###) or exact
case-insensitive full-name/company match ONLY. A partial or surname match is
never linked (standing rule: a lone surname never merges records) — the
unlinked text stays visible in the note as a "(no exact match)" line.

Usage: python3 build-graph-notes.py [CARR_ROOT]
"""
import sys, os, re, json, glob, shutil, unicodedata
import openpyxl

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT  = os.path.join(ROOT, "Graph")

VERT_TAGS = {"dental":"dental","dentist":"dental","oral":"dental","ortho":"dental",
             "medical":"medical","medicine":"medical","cardio":"medical","derm":"medical",
             "kidney":"medical","nephro":"medical","urgent":"medical","primary":"medical",
             "pediatric":"medical","psych":"medical","wound":"medical","dpc":"medical",
             "vet":"vet","chiro":"chiro","vision":"vision","opto":"vision","eye":"vision",
             "therapy":"therapy","pt ":"therapy","physical":"therapy","occupational":"therapy",
             "counsel":"therapy","behavioral":"therapy","wellness":"therapy","women":"therapy"}

def vert_tag(text):
    t = (text or "").lower()
    for k, v in VERT_TAGS.items():
        if k in t: return v
    return None

def sanitize(name, fallback_id=""):
    s = unicodedata.normalize("NFKD", str(name or fallback_id or "unnamed"))
    s = re.sub(r'[\\/:*?"<>|#^\[\]{}]', "", s).strip()
    return re.sub(r"\s+", " ", s) or (fallback_id or "unnamed")

def slug(name, taken, fallback_id=""):
    s = base = sanitize(name, fallback_id)
    n = 2
    while s.lower() in taken:
        s = f"{base} ({fallback_id or n})"; n += 1
    taken.add(s.lower())
    return s

def unique_node(name, kind, taken, fallback_id=""):
    """Node titles must be unique across ALL of Graph/, not per subfolder.

    Obsidian resolves [[Name]] by basename across the whole vault, so two notes
    sharing a basename in different folders make every link to that name
    ambiguous — Obsidian silently picks one and the other node becomes
    unreachable, rendering as an isolated dot no matter how many links point at
    the name. Measured 2026-07-25: 41 duplicate titles affecting 88 notes,
    because leads and deals each deduped against their own private set instead of
    a shared one.

    A client and their deal are legitimately separate records, so on collision we
    suffix with the record TYPE rather than a meaningless (2). Clients and vendors
    are registered first and keep the clean name as the canonical person record.
    """
    base = sanitize(name, fallback_id)
    s = base
    if s.lower() in taken:
        s, n = f"{base} ({kind})", 2
        while s.lower() in taken:
            s = f"{base} ({kind} {n})"; n += 1
    taken.add(s.lower())
    return s

def owner_tag(owner):
    """Owner is the biggest-picture separator in the graph (Joe, 2026-07-25):
    which half of the business a record belongs to. It lives in frontmatter but
    not in tags, and Obsidian colour groups key on tags — so emit it as one."""
    o = s(owner).strip().lower()
    if o.startswith("joe"):    return "owner-joe"
    if o.startswith("dell") or o.startswith("wayne"): return "owner-dell"
    if o.startswith("shared") or o.startswith("both"): return "owner-shared"
    return "owner-unassigned"

def fm(lines):
    return "---\n" + "\n".join(lines) + "\n---\n"

def esc(v):
    v = str(v if v is not None else "").strip()
    return f'"{v}"' if v and (":" in v or v.startswith(("'", '"', "[", "{"))) else (v or '""')

def rows_of(path, sheet):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]
    it = ws.iter_rows(values_only=True)
    hdr = [str(h).strip() if h else "" for h in next(it)]
    out = []
    for r in it:
        d = {hdr[i]: (r[i] if i < len(r) else None) for i in range(len(hdr))}
        if any(v not in (None, "") for v in d.values()): out.append(d)
    return out

def s(v):  # cell → clean str
    return str(v).strip() if v not in (None, "") else ""

def datestr(v):
    v = s(v)
    return v.split(" ")[0] if v else ""

# ---------- load sources ----------
vendors = rows_of(os.path.join(ROOT, "DNA/Network/vendors.xlsx"), "Vendors")
leads   = rows_of(os.path.join(ROOT, "DNA/Leads/lead-registry.xlsx"), "Registry")
clients = rows_of(os.path.join(ROOT, "DNA/Clients/client-roster.xlsx"), "Clients")
deals   = json.load(open(os.path.join(ROOT, "DNA/Deal Management/panhandle-team-deals.json")))["deals"]
detail_files = {os.path.splitext(f)[0].lower(): os.path.splitext(f)[0]
                for f in os.listdir(os.path.join(ROOT, "DNA/Clients/prospects")) if f.endswith(".md")}

# ---------- index for exact-match linking ----------
taken = set()
vend_by_id, vend_by_name = {}, {}
for v in vendors:
    vid = s(v.get("ID"))
    node = slug(s(v.get("Name")) or s(v.get("Company")), taken, vid)
    v["_node"] = node
    if vid: vend_by_id[vid.upper()] = node
    for key in (s(v.get("Name")), s(v.get("Company"))):
        if key: vend_by_name.setdefault(key.lower(), node)

cli_by_id, cli_by_name = {}, {}
for c in clients:
    cid = s(c.get("Client ID"))
    det = s(c.get("Detail File"))
    base = os.path.splitext(os.path.basename(det))[0].lower() if det else ""
    c["_detail"] = detail_files.get(base)          # hand note wins as the node
    c["_node"] = c["_detail"] or slug(s(c.get("Name")) or s(c.get("Practice / Entity")), taken, cid)
    # a hand-written dossier name bypasses slug(), so register it explicitly or
    # a later lead/deal can mint the same title and both become unreachable
    if c["_detail"]: taken.add(c["_detail"].lower())
    if cid: cli_by_id[cid.upper()] = c["_node"]
    for key in (s(c.get("Name")), s(c.get("Practice / Entity"))):
        if key: cli_by_name.setdefault(key.lower(), c["_node"])

def find_vendor(text):
    t = s(text)
    if not t: return None, ""
    m = re.search(r"V-[A-Z]+-\d+", t.upper())
    if m and m.group(0) in vend_by_id: return vend_by_id[m.group(0)], t
    return vend_by_name.get(t.lower()), t

def find_client(text):
    t = s(text)
    if not t: return None, ""
    m = re.search(r"C-\d+", t.upper())
    if m and m.group(0) in cli_by_id: return cli_by_id[m.group(0)], t
    return cli_by_name.get(t.lower()), t

# ---------- write ----------
# Temp-and-swap (orchestrator-lane corrective, 2026-07-25): build the whole tree in
# Graph.tmp first, swap in only on success — a mid-run crash (malformed source xlsx)
# used to leave Graph/ empty or half-built with no rollback.
FINAL_OUT = OUT
OUT = FINAL_OUT.rstrip("/\\") + ".tmp"
if os.path.isdir(OUT): shutil.rmtree(OUT)
for sub in ("vendors", "leads", "clients", "deals"): os.makedirs(os.path.join(OUT, sub))

# .txt not .md — Obsidian graphs every markdown file, so a README with no
# links renders as a floating dot in the middle of the graph.
open(os.path.join(OUT, "README.txt"), "w").write(
    "# Graph/ — derived Obsidian nodes (DO NOT HAND-EDIT)\n\n"
    "Generated by carr-system/pipelines/build-graph-notes.py from the sources of truth\n"
    "(vendors.xlsx, lead-registry.xlsx, client-roster.xlsx, panhandle-team-deals.json).\n"
    "Regenerate with `~/carr-system/run.sh graph`. Edits here are overwritten and never\n"
    "flow back to the data — fix the source, then regenerate.\n")

counts = {"vendors":0, "leads":0, "clients":0, "deals":0}

for v in vendors:
    vid, node = s(v.get("ID")), v["_node"]
    tags = ["vendor", "network", owner_tag(v.get("Owner"))]
    vt = vert_tag(s(v.get("Vertical"))+" "+s(v.get("Category")))
    if vt: tags.append(vt)
    stage = s(v.get("Stage")).lower()
    tags.append("active" if s(v.get("Referral-active?")).lower() in ("yes","y","true","active") else "dormant")
    body = [fm([f"type: vendor", f"id: {esc(vid)}", f"category: {esc(v.get('Category'))}",
                f"owner: {esc(v.get('Owner') or 'unassigned')}", f"stage: {esc(v.get('Stage'))}",
                f"last-touch: {esc(datestr(v.get('Last Touch')))}",
                "tags: [" + ", ".join(tags) + "]"])]
    body.append(f"**{s(v.get('Name')) or node}** — {s(v.get('Company'))} · {s(v.get('Category'))} · {s(v.get('Territory'))} {s(v.get('State'))}\n")
    links = []
    for m in re.findall(r"V-[A-Z]+-\d+", s(v.get("Links")).upper()):
        if m in vend_by_id and vend_by_id[m] != node: links.append(f"- knows [[{vend_by_id[m]}]]")
    if links: body.append("## Links\n" + "\n".join(sorted(set(links))) + "\n")
    open(os.path.join(OUT, "vendors", node + ".md"), "w").write("\n".join(body))
    counts["vendors"] += 1

for c in clients:
    if c["_detail"]: continue                      # hand-maintained note is the node
    cid, node = s(c.get("Client ID")), c["_node"]
    status = s(c.get("Status")).lower()
    stat_tag = "won" if "won" in status else ("active" if any(k in status for k in ("active","negotiat","outreach","research")) else ("dormant" if "paus" in status else "cold"))
    tags = ["client", stat_tag, owner_tag(c.get("Owner"))]
    vt = vert_tag(s(c.get("Specialty / Type"))+" "+s(c.get("Deal Type")))
    if vt: tags.append(vt)
    body = [fm([f"type: client", f"id: {esc(cid)}", f"status: {esc(c.get('Status'))}",
                f"owner: {esc(c.get('Owner') or 'unassigned')}", f"vertical: {esc(c.get('Specialty / Type'))}",
                "tags: [" + ", ".join(tags) + "]"])]
    body.append(f"**{s(c.get('Name')) or node}** — {s(c.get('Practice / Entity'))} · {s(c.get('Specialty / Type'))} · {s(c.get('Market / Location'))}\n")
    links = []
    ref_node, ref_txt = find_vendor(c.get("Referral Source"))
    if ref_node: links.append(f"- referred by [[{ref_node}]]")
    elif ref_txt: links.append(f"- referral source: {ref_txt} (no exact match)")
    if links: body.append("## Links\n" + "\n".join(links) + "\n")
    open(os.path.join(OUT, "clients", node + ".md"), "w").write("\n".join(body))
    counts["clients"] += 1

lead_taken = set()
for l in leads:
    lid = s(l.get("Lead ID"))
    det = s(l.get("Detail File"))
    det_node = detail_files.get(os.path.splitext(os.path.basename(det))[0].lower()) if det else None
    cname = s(l.get("Contact Name"))
    if not cname or cname.startswith("("):          # placeholder, not a name
        cname = s(l.get("Practice"))
    node = unique_node(cname, "lead", taken, lid)
    stage = s(l.get("Stage")).lower()
    stat_tag = "active" if any(k in stage for k in ("active","engaged","outreach")) else ("won" if "won" in stage else ("dormant" if any(k in stage for k in ("paus","hold","do-not")) else "cold"))
    tags = ["lead", stat_tag, owner_tag(l.get("Owner"))]
    vt = vert_tag(s(l.get("Specialty")))
    if vt: tags.append(vt)
    body = [fm([f"type: lead", f"id: {esc(lid)}", f"stage: {esc(l.get('Stage'))}",
                f"owner: {esc(l.get('Owner') or 'unassigned')}", f"segment: {esc(l.get('Segment'))}",
                f"last-touch: {esc(datestr(l.get('Last Touch')))}",
                "tags: [" + ", ".join(tags) + "]"])]
    body.append(f"**{s(l.get('Contact Name')) or node}** — {s(l.get('Practice'))} · {s(l.get('Specialty'))} · {s(l.get('City/Market'))}\n")
    links = []
    src_node, src_txt = find_vendor(l.get("Source Detail (V-ID / event / referrer)"))
    if src_node: links.append(f"- sourced by [[{src_node}]]")
    elif s(l.get("Source Type")) or src_txt:
        links.append(f"- source: {s(l.get('Source Type'))} {src_txt}".rstrip() + ("" if not src_txt else " (no exact vendor match)" if not src_node else ""))
    if det_node: links.append(f"- client dossier: [[{det_node}]]")
    else:
        c_node, _ = find_client(l.get("Contact Name"))
        if c_node: links.append(f"- possibly same person as [[{c_node}]] (name match — confirm)")
    if links: body.append("## Links\n" + "\n".join(links) + "\n")
    open(os.path.join(OUT, "leads", node + ".md"), "w").write("\n".join(body))
    counts["leads"] += 1

# Territory footprint (AL gulf coast + FL panhandle). Used ONLY to separate deals CARR
# represents directly (deep green) from out-of-market deals referred to another CARR agent
# for a referral fee (light green). A deal with no city recorded is neither — it is tagged
# #deal-unclassified and stays visibly unknown rather than being guessed into a lane.
TERRITORY = ["pensacola","navarre","freeport","gulf shores","destin","miramar","santa rosa","panama",
             "crestview","fort walton","mobile","dothan","tallahassee","foley","daphne","inlet beach",
             "loxley","30a","niceville","gulf breeze","pace","milton","spanish fort","fairhope",
             "enterprise","ozark","chipley","marianna","defuniak"]

def deal_lane(deal):
    """Prefer Salesforce's own Out-of-Market flag (written into the JSON as `lane`
    by diff-salesforce-deals.py). Fall back to the city heuristic only for a deal
    that has never been through a Salesforce read; a blank city stays unknown
    rather than being guessed into a lane."""
    lane = s(deal.get("lane")).lower()
    if lane in ("territory", "national"): return lane
    c = s(deal.get("city")).lower()
    if not c: return None
    return "territory" if any(t in c for t in TERRITORY) else "national"

deal_taken = set()
for d in deals:
    node = unique_node(s(d.get("name")) or s(d.get("company")), "deal", taken, s(d.get("txn")))
    phase = s(d.get("phase")).lower()
    tags = ["deal", "won" if "closed" in phase or "won" in phase else ("cold" if "pending" in phase else "active"),
            owner_tag(d.get("owner"))]
    lane = deal_lane(d)
    tags.append({"territory":"deal-territory", "national":"deal-national"}.get(lane, "deal-unclassified"))
    vt = vert_tag(s(d.get("seg"))+" "+s(d.get("ptype")))
    if vt: tags.append(vt)
    lane_txt = {"territory":"CARR represents (full commission)",
                "national":"referred out to a local CARR agent (referral fee back to Dell; Joe's share rides on it)",
                None:"LANE UNKNOWN — no city recorded, needs a verdict"}[lane]
    body = [fm([f"type: deal", f"phase: {esc(d.get('phase'))}", f"owner: {esc(d.get('owner') or 'unassigned')}",
                f"segment: {esc(d.get('seg'))}", f"lane: {lane or 'unclassified'}",
                "tags: [" + ", ".join(tags) + "]"])]
    body.append(f"**{s(d.get('name'))}** — {s(d.get('company'))} · {s(d.get('seg'))} · {s(d.get('city'))} · phase: {s(d.get('phase'))}\n")
    body.append(f"*Lane: {lane_txt}*\n")
    links = []
    c_node, _ = find_client(d.get("company"))
    if not c_node: c_node, _ = find_client(d.get("contact"))
    if not c_node: c_node, _ = find_client(d.get("name"))
    if c_node: links.append(f"- client: [[{c_node}]]")
    ref_node, ref_txt = find_vendor(d.get("referral"))
    if ref_node: links.append(f"- referred by [[{ref_node}]]")
    elif ref_txt: links.append(f"- referral: {ref_txt} (no exact match)")
    own = s(d.get("owner")).lower()
    if links: body.append("## Links\n" + "\n".join(links) + "\n")
    open(os.path.join(OUT, "deals", node + ".md"), "w").write("\n".join(body))
    counts["deals"] += 1

# success — swap the finished tree into place
if os.path.isdir(FINAL_OUT): shutil.rmtree(FINAL_OUT)
os.rename(OUT, FINAL_OUT)
print("Graph notes written:", counts, "→", FINAL_OUT)
