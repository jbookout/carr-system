#!/usr/bin/env python3
"""
Vendor Intro Timeline — given a deal stage (and optional client state), list the
vendor categories to introduce the client to at that stage, with real candidate
vendors pulled from DNA/Network/vendors.xlsx (relationship-ready, state-matched,
referral-active surfaced first).

Usage:
  python3 vendor_intro_for_stage.py <vendors.xlsx> "<Initial Call|Post Tour|Legal|Execution/Closing>" [STATE]
"""
import sys, openpyxl

# deal stage -> vendor Category values (as they appear in vendors.xlsx)
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

def run(xlsx, stage, state=None):
    if stage not in STAGE_MAP:
        print("Unknown stage. Use one of:", list(STAGE_MAP)); return
    ws = openpyxl.load_workbook(xlsx, data_only=True)["Vendors"]
    hdr=[c.value for c in ws[1]]; ix={h:i for i,h in enumerate(hdr)}
    cats = STAGE_MAP[stage]
    print(f"== Deal stage: {stage} ==")
    if not cats:
        print("  No vendor categories mapped for this stage.")
        return
    print(f"  Introduce these categories to the client: {', '.join(cats)}")
    print(f"  (client state: {state or 'any'})\n")
    for cat in cats:
        rows=[]
        for r in ws.iter_rows(min_row=2, values_only=True):
            if str(r[ix['Category']]).strip()!=cat: continue
            st=str(r[ix['Stage']]).strip()
            if st in EXCLUDE_STAGE: continue
            vstate=str(r[ix['State']]).strip() if r[ix['State']] else ""
            if state and vstate and vstate.upper()!=state.upper(): continue  # wrong-state vendor skipped
            ref = str(r[ix['Referral-active?']]).strip().lower()=="yes"
            rows.append((ref, 1 if (state and vstate.upper()==state.upper()) else 0, REL_RANK.get(st,0),
                         r[ix['Name']], r[ix['Company']], vstate or "—", st, r[ix['ID']]))
        rows.sort(key=lambda x:(-x[0],-x[1],-x[2]))
        print(f"  {cat}  ({len(rows)} candidate{'s' if len(rows)!=1 else ''}):")
        for ref,_,_,name,co,vs,st,vid in rows[:5]:
            flag=" ★referral-active" if ref else ""
            print(f"     - {name or '?'} — {co or '?'}  [{vs}] · {st} · {vid}{flag}")
        if not rows: print("     (none relationship-ready — a gap to fill)")
        print()

if __name__=="__main__":
    run(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv)>3 else None)
