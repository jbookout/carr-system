#!/usr/bin/env python3
"""Extract plain-English one-pager data from a filled Purchase-vs-Lease workbook."""
import sys, json, openpyxl, datetime
def money(v): return None if v is None else round(float(v))
def run(xlsx, client, city, out):
    ws = openpyxl.load_workbook(xlsx, data_only=True).active
    g=lambda c: ws[c].value
    purchase_wins = (g('G62') not in (None,"")) and (g('G62') or 0) > 0
    saver = money(g('G62')) if purchase_wins else money(g('K62'))
    fv    = money(g('G64')) if purchase_wins else money(g('K64'))
    data = {
      "client": client, "city": city,
      "purchase": {
        "upfront": money(g('G23')), "after_tax_month": money(g('G46')),
        "after_tax_year": money(g('G45')), "net_equity_10yr": money(g('G59')),
      },
      "lease": {
        "upfront": money(g('K23')), "after_tax_month": money(g('K46')),
        "after_tax_year": money(g('K45')),
      },
      "winner": "Purchase" if purchase_wins else "Lease",
      "annual_cash_saved": saver,
      "invested_future_value_10yr": fv,
    }
    json.dump(data, open(out,'w'), indent=1)
    print(json.dumps(data, indent=1))
if __name__=="__main__":
    run(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
