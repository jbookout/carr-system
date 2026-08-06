#!/usr/bin/env python3
"""
Vendor Intro Timeline — given a deal stage (and optional client state), list the
vendor categories to introduce the client to at that stage, with real candidate
vendors pulled from the vendor record (relationship-ready, state-matched,
referral-active surfaced first).

TWO SOURCE MODES (ORDER 29b, the ORDER 29a pattern; see lib/record_sources.py).
  --records  (default) reads load_vendors() off the record layer — the same
             v_export_vendors view the nightly exporter writes
             DNA/Network/vendors.xlsx from. Falls back to --files, loudly on
             stderr, when there is no exporter credential or psycopg is not
             importable by this interpreter (effective_mode's fallback rule).
  --files    the historical read: DNA/Network/vendors.xlsx directly, unchanged.

Usage:
  python3 vendor_intro_for_stage.py <CARR_ROOT> "<Initial Call|Post Tour|Legal|Execution/Closing>" [STATE] [--files|--records]
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.record_sources import MODE_RECORDS, effective_mode, load_vendors, resolve_mode

# deal stage -> vendor Category values (as they appear in the Vendors record)
STAGE_MAP = {
  "Initial Call":       ["Banker / Lender", "Marketing / Demographics", "Practice Broker / Consultant", "SBDC Consultant"],
  "Post Tour":          ["Attorney", "Supply / Equipment Rep", "General Contractor", "Architect / Design"],
  "Legal":              ["Insurance", "CPA / Financial", "Marketing / Demographics", "IT Services"],
  "Execution/Closing":  ["Association / Study Club"],
}
# vendors we should NOT put in front of a client
EXCLUDE_STAGE = {"Avoid (not a good partner)", "Target — not yet met", "Prospect (uncontacted)"}
# relationship-strength rank (higher = stronger, surface first)
REL_RANK = {"Fully aligned":5, "Important — strengthening":4, "Decent relationship":3,
            "Building (working on it)":2, "Follow-up needed":1, "Unrated":0}

def run(root, mode, stage, state=None):
    if stage not in STAGE_MAP:
        print("Unknown stage. Use one of:", list(STAGE_MAP)); return
    vendor_rows = load_vendors(root, mode)
    cats = STAGE_MAP[stage]
    print(f"== Deal stage: {stage} ==")
    if not cats:
        print("  No vendor categories mapped for this stage.")
        return
    print(f"  Introduce these categories to the client: {', '.join(cats)}")
    print(f"  (client state: {state or 'any'})\n")
    for cat in cats:
        rows=[]
        for r in vendor_rows:
            if str(r.get('Category')).strip()!=cat: continue
            st=str(r.get('Stage')).strip()
            if st in EXCLUDE_STAGE: continue
            vstate=str(r.get('State')).strip() if r.get('State') else ""
            if state and vstate and vstate.upper()!=state.upper(): continue  # wrong-state vendor skipped
            ref = str(r.get('Referral-active?')).strip().lower()=="yes"
            rows.append((ref, 1 if (state and vstate.upper()==state.upper()) else 0, REL_RANK.get(st,0),
                         r.get('Name'), r.get('Company'), vstate or "—", st, r.get('ID')))
        rows.sort(key=lambda x:(-x[0],-x[1],-x[2]))
        print(f"  {cat}  ({len(rows)} candidate{'s' if len(rows)!=1 else ''}):")
        for ref,_,_,name,co,vs,st,vid in rows[:5]:
            flag=" ★referral-active" if ref else ""
            print(f"     - {name or '?'} — {co or '?'}  [{vs}] · {st} · {vid}{flag}")
        if not rows: print("     (none relationship-ready — a gap to fill)")
        print()

if __name__=="__main__":
    MODE, ARGS = resolve_mode(sys.argv[1:], default=MODE_RECORDS)
    MODE = effective_mode(MODE, "vendor-intro")
    if len(ARGS) < 2:
        sys.exit('Usage: vendor_intro_for_stage.py <CARR_ROOT> "<stage>" [STATE] [--files|--records]')
    ROOT, STAGE = ARGS[0], ARGS[1]
    STATE = ARGS[2] if len(ARGS) > 2 else None
    run(ROOT, MODE, STAGE, STATE)
