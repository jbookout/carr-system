#!/usr/bin/env python3
"""dso-match.py — address-match corporate/DSO associate detector.

Reads DNA/Leads/dso-corporate-locations.json + a provider source and tags providers whose PRACTICE
address matches a corporate/DSO clinic, by brand+vertical. Writes Automation/dso-matches.json.

WHAT CHANGED 2026-07-27 (Joe: "seems highly unlikely that there is only one"). The lead board showed
exactly ONE DSO associate. Three independent causes, all fixed or recorded here:

  1. The reference file was a 6-address Aspen-only stub. Rebuilt by a verified sweep to 93 addresses
     across 9 brands and 4 verticals. Yield against the same router went 4 matched providers -> 103.

  2. The associate test was "corporate email domain proves it". Only THREE rows in 9,320 carry a
     corporate email domain, because associates use personal email. That gate could never have
     produced a real number. It is now CORROBORATION that raises confidence, never the gate.
     The positive test is: practice address matches a corporate site AND the provider is not a
     Sunbiz-confirmed owner.

  3. The docstring used to say "point --src at the FL MQA practice-address list for the real yield."
     That list is NOT available and by policy is not retained (upstream pools hold minimal joinable
     fields only; raw statewide files are discarded the same session). The router's Practice Address
     column is fully populated (0 blanks in 9,320 rows) and is the source. Removing a standing
     instruction that cannot be followed. MQA would still improve precision if it were ever acquired,
     and it only ever covers Florida — Mobile, Baldwin and Dothan are Alabama.

READ THE OWNERSHIP FIELD. A match only implies "employee" for corporate/PE-backed brands. Vision
Source is deliberately excluded from the reference file for exactly this reason: its members are
INDEPENDENTLY OWNED practices, and matching them would label owners as associates, inverting the
lead. Any brand added to that file must carry an ownership classification.

Default source = latest lead-router-*.xlsx.

SOURCE MODE (ORDER 29b, the ORDER 29a pattern). The provider source defaults to
RECORDS — the router rows come off the pool (`v_export_pool` for the router
source alone, same read lib/record_sources.py's other pool consumers use) —
and falls back to the generated router xlsx, LOUDLY on stderr, when the pool is
unreachable. --files forces the file read outright. Passing an explicit source
path (the historical `python3 dso-match.py <path>` override) is itself a
file-mode request — there is no records equivalent of "read THIS xlsx" — so it
forces file mode too.
"""
import openpyxl, glob, os, re, json, sys
from collections import Counter, defaultdict

# ROOT is the VAULT, not the script's parent. The vault copy of this script sits in
# Automation/ so "parent" happened to be right there; the repo copy sits in pipelines/
# and that assumption made it unrunnable from the repo. CARR_VAULT is how every other
# repo pipeline resolves this.
ROOT = os.environ.get("CARR_VAULT") or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# lib/record_sources.py lives one level up from pipelines/ in the repo; the vault
# copy of this script has no lib/ beside it (manifest.tsv), so the import is
# guarded exactly like every other repointed consumer.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from lib.record_sources import (MODE_FILES, MODE_RECORDS, ROUTER_SOURCE, load_pool,
                                     pool_reach, resolve_mode, source_note)
    _HAVE_RECORDS = True
except ImportError:
    MODE_FILES, MODE_RECORDS, _HAVE_RECORDS = "files", "records", False

_argv = sys.argv[1:]
_explicit_src = next((x for x in _argv if not x.startswith("--")), None)

if _HAVE_RECORDS:
    MODE, _ = resolve_mode(_argv, default=MODE_RECORDS)
else:
    MODE = MODE_FILES
    if "--records" in _argv:
        print("[dso-match] this copy has no lib/record_sources.py — running file mode",
              file=sys.stderr)

if _explicit_src and MODE == MODE_RECORDS:
    print(f"[dso-match] explicit source path given ({_explicit_src}) — running file mode",
          file=sys.stderr)
    MODE = MODE_FILES

if MODE == MODE_RECORDS:
    _ok, _why, _, _ = pool_reach((ROUTER_SOURCE,))
    if not _ok:
        print(f"[dso-match] records mode unavailable ({_why}) — falling back to the "
              f"generated files", file=sys.stderr)
        MODE = MODE_FILES

def norm(a):
    a = str(a or "").upper().strip(); a = re.sub(r"[.,]", " ", a)
    a = re.sub(r"\bSTE\b.*|\bSUITE\b.*|\b#.*", "", a)
    for k, v in [("STREET","ST"),("AVENUE","AVE"),("ROAD","RD"),("BOULEVARD","BLVD"),
                 ("HIGHWAY","HWY"),("PARKWAY","PKWY"),("DRIVE","DR"),("COURT","CT"),
                 ("EAST","E"),("WEST","W"),("NORTH","N"),("SOUTH","S")]:
        a = re.sub(r"\b"+k+r"\b", v, a)
    return re.sub(r"\s+", " ", a).strip()

# CARR_DSO_REF points at an alternate reference file so a proposed one can be
# measured against the live router before it replaces anything.
REF_PATH = os.environ.get("CARR_DSO_REF") or os.path.join(ROOT, "DNA", "Leads", "dso-corporate-locations.json")
ref = json.load(open(REF_PATH))
OWNERSHIP = ref.get("_ownership", {})
targets = {}
for vert, brands in ref.items():
    if vert.startswith("_") or not isinstance(brands, dict): continue
    for brand, locs in brands.items():
        if isinstance(locs, list):
            for L in locs: targets[norm(L["street"])] = (brand, vert)

ROUTER_COLS = ["SEGMENT", "Name", "Profession", "Practice Address", "City", "Email", "Phone"]

def router_rows_from_file(path):
    """One dict per sheet row, keyed by header name — the historical read."""
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)["Lead Router"]
    # Schema-validated (orchestrator-lane corrective #1, 2026-07-25): headers by name.
    _d = os.path.dirname(os.path.abspath(__file__))
    for _c in (os.path.join(_d, "..", "lib"), _d):
        if os.path.isfile(os.path.join(_c, "sheets.py")): sys.path.insert(0, _c); break
    from sheets import header_map, data_rows
    c = header_map(ws, ROUTER_COLS, f"{os.path.basename(path)}[Lead Router]")
    out = [{h: r[c[h]] for h in ROUTER_COLS} for r in data_rows(ws)]
    ws.parent.close()
    return out

if MODE == MODE_RECORDS:
    rows = load_pool((ROUTER_SOURCE,))[ROUTER_SOURCE]
    src_label = source_note(MODE)
else:
    src = _explicit_src or sorted(glob.glob(os.path.join(ROOT, "DNA", "Leads", "lead-router-*.xlsx")))[-1]
    rows = router_rows_from_file(src)
    src_label = os.path.basename(src)

# A corporate email is now corroboration, not the gate.
CORP_DOMAIN = re.compile(r"@(aspendental|heartland|pacificdental|smilebrands|sagedental|"
                         r"greatexpressions|mb2dental|vca|myeyedr|forefrontderm|dermsoutheast|"
                         r"mydermspecialists)", re.I)
OWNER_SEG = "PRACTICE OWNER (Sunbiz-confirmed)"

# Vertical guard (added 2026-07-27 after the first live run put a PODIATRIST inside
# Heartland Dental). norm() strips suite numbers, so two separate tenants of one
# building collapse onto a single street address — the dentist really is Heartland's,
# the podiatrist down the hall is not. 12 of the first 103 matches were this. A
# provider whose profession cannot plausibly work for the brand's vertical is not a
# match; it is evidence the address is shared.
VERT_OK = {
    "dental":      ("dent",),
    "veterinary":  ("vet",),
    "optometry":   ("optom", "ophthal", "optic"),
    # derm groups genuinely employ MDs, DOs, APRNs and PAs, so this one is broad
    "dermatology": ("medical doctor", "osteopathic", "physician", "aprn", "nurse",
                    "derm", "assistant"),
}
def vertical_ok(prof, vert):
    p = str(prof or "").lower()
    keys = VERT_OK.get(vert)
    return True if not keys else any(k in p for k in keys)

out, density, shared = [], defaultdict(list), []
for r in rows:
    if not r["Name"] or not r["Practice Address"]: continue
    key = norm(r["Practice Address"])
    density[(key, str(r["City"] or "").upper())].append(r)
    t = targets.get(key)
    if not t: continue
    brand, vert = t
    seg = str(r["SEGMENT"] or "")
    if not vertical_ok(r["Profession"], vert):
        # kept, but OFF the board: it is a shared-address signal, not an employee
        shared.append({"name": str(r["Name"]), "prof": str(r["Profession"] or ""),
                       "brand": brand, "vertical": vert, "addr": str(r["Practice Address"]),
                       "city": str(r["City"] or ""),
                       "why": "profession incompatible with the brand's vertical — the address is "
                              "almost certainly shared between separate tenants"})
        continue
    email = str(r["Email"] or "")
    own = OWNERSHIP.get(brand, "unclassified")

    # An owner sitting at a corporate address is NOT an associate. It is usually the
    # dentist-of-record a state requires the group to name, or an independent practice
    # sharing a building. Surfaced for review rather than labelled either way.
    if OWNER_SEG in seg:
        cls, conf = "owner-at-corporate-address (REVIEW)", "n/a"
    else:
        cls = "dso-associate"
        conf = "HIGH" if CORP_DOMAIN.search(email) else "MEDIUM"
        if own.startswith(("dso-partner-equity", "physician-owned", "regional group")):
            conf = "LOW"   # employment is likelier than ownership here, but not proven

    out.append({"name": str(r["Name"]), "prof": str(r["Profession"] or ""),
                "brand": brand, "vertical": vert, "ownership": own,
                "class": cls, "confidence": conf,
                "seg": seg, "addr": str(r["Practice Address"]), "city": str(r["City"] or ""),
                "email": email, "phone": str(r["Phone"] or "")})

# Density is a CANDIDATE signal, never a classification. norm() strips suites, so a
# medical office building with 30 independent practices collapses into one address —
# the largest bucket in the July router was 116 providers, which is a hospital or an
# MOB, not a clinic. These are written out for brand research, not for the board.
dense = sorted(((k, v) for k, v in density.items() if len(v) >= 5 and k[0] not in targets),
               key=lambda kv: -len(kv[1]))[:40]
unmatched = [{"addr": k[0], "city": k[1], "providers": len(v),
              "sample": [str(x["Name"]) for x in v[:3]]} for k, v in dense]

json.dump(out, open(os.path.join(ROOT, "Automation", "dso-matches.json"), "w"), indent=1)
json.dump(unmatched, open(os.path.join(ROOT, "Automation", "dso-density-candidates.json"), "w"), indent=1)
json.dump(shared, open(os.path.join(ROOT, "Automation", "dso-shared-address-flags.json"), "w"), indent=1)

assoc = [x for x in out if x["class"] == "dso-associate"]
print(f"source: {src_label} | {len(targets)} corporate addresses | {len(rows)} providers")
print(f"matched: {len(out)}  ->  {len(assoc)} associates, {len(out)-len(assoc)} owners-at-corporate (review)")
for b, n in Counter(x["brand"] for x in out).most_common(): print(f"  {n:4}  {b}")
print("confidence: " + ", ".join(f"{k}={v}" for k, v in Counter(x['confidence'] for x in assoc).most_common()))
print(f"vertical guard dropped {len(shared)} shared-address false positives "
      f"(-> dso-shared-address-flags.json)")
print(f"wrote Automation/dso-matches.json + dso-density-candidates.json "
      f"({len(unmatched)} dense unmatched addresses for brand research)")
