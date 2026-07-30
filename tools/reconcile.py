"""Cell-level reconciliation: staged exports vs the live vault files (A8 fidelity).

Fidelity = extracted-cell equality on the columns consumers read, keyed by
record ID — never file bytes. Output is the reconciliation report Joe signs
at freeze; before freeze it is the drift-finder for the rehearsal loop.

Usage: .venv/bin/python tools/reconcile.py [--vault PATH] [--staged PATH]
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

VAULT_DEFAULT = ("/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/"
                 "My Drive/CARR AI")
REPO = Path(__file__).resolve().parent.parent


def sheet_dict(path, sheet, key_col):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    header = [str(h).strip() if h else None for h in rows[0]]
    ki = header.index(key_col)
    out = {}
    for r in rows[1:]:
        if r[ki] is None or not str(r[ki]).strip():
            continue
        out[str(r[ki]).strip()] = {header[i]: r[i] for i in range(len(header)) if header[i]}
    return out


def norm(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, default=str)   # jsonb reorders keys; content equality
    if hasattr(v, "date"):
        v = v.date() if hasattr(v, "hour") else v
    s = str(v).strip()
    return {"TRUE": "Yes", "FALSE": "No", "True": "Yes", "False": "No", "Y": "Yes", "N": "No"}.get(s, s)


def diff_target(name, live_rows, staged_rows, ignore=()):
    only_live = sorted(set(live_rows) - set(staged_rows))
    only_staged = sorted(set(staged_rows) - set(live_rows))
    cell_diffs = []
    for k in sorted(set(live_rows) & set(staged_rows)):
        lr, sr = live_rows[k], staged_rows[k]
        for col in lr:
            if col in ignore or col not in sr:
                continue
            if norm(lr[col]) != norm(sr[col]):
                cell_diffs.append((k, col, norm(lr[col])[:60], norm(sr[col])[:60]))
    return {"target": name, "live": len(live_rows), "staged": len(staged_rows),
            "only_live": only_live, "only_staged": only_staged, "cell_diffs": cell_diffs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=VAULT_DEFAULT)
    ap.add_argument("--staged", default=str(REPO / "out" / "exports"))
    a = ap.parse_args()
    vault, staged = Path(a.vault), Path(a.staged)

    results = []
    results.append(diff_target(
        "lead-registry.xlsx",
        sheet_dict(vault / "DNA/Leads/lead-registry.xlsx", "Registry", "Lead ID"),
        sheet_dict(staged / "lead-registry.xlsx", "Registry", "Lead ID")))
    results.append(diff_target(
        "client-roster.xlsx",
        sheet_dict(vault / "DNA/Clients/client-roster.xlsx", "Clients", "Client ID"),
        sheet_dict(staged / "client-roster.xlsx", "Clients", "Client ID")))
    results.append(diff_target(
        "vendors.xlsx",
        sheet_dict(vault / "DNA/Network/vendors.xlsx", "Vendors", "ID"),
        sheet_dict(staged / "vendors.xlsx", "Vendors", "ID")))
    live_deals = {d["name"]: d for d in json.loads(
        (vault / "DNA/Deal Management/panhandle-team-deals.json").read_text())["deals"]}
    staged_deals = {d["name"]: d for d in json.loads(
        (staged / "panhandle-team-deals.json").read_text())["deals"]}
    results.append(diff_target("panhandle-team-deals.json", live_deals, staged_deals))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [f"# Reconciliation — staged exports vs live vault files — {stamp}", ""]
    total_diffs = 0
    for r in results:
        lines += [f"## {r['target']}: live {r['live']} rows, staged {r['staged']}"]
        if r["only_live"]:
            lines += [f"- ONLY IN LIVE ({len(r['only_live'])}): {', '.join(r['only_live'][:20])}"]
        if r["only_staged"]:
            lines += [f"- ONLY IN STAGED ({len(r['only_staged'])}): {', '.join(map(str, r['only_staged'][:20]))}"]
        lines += [f"- cell differences: {len(r['cell_diffs'])}"]
        for k, col, lv, sv in r["cell_diffs"][:80]:
            lines += [f"    - {k} · {col}: live '{lv}' -> staged '{sv}'"]
        if len(r["cell_diffs"]) > 80:
            lines += [f"    - ... and {len(r['cell_diffs']) - 80} more"]
        lines += [""]
        total_diffs += len(r["cell_diffs"]) + len(r["only_live"]) + len(r["only_staged"])
    out = REPO / "out" / f"reconciliation-{stamp}.md"
    out.write_text("\n".join(lines))
    print(f"report -> {out}")
    for r in results:
        print(f"  {r['target']}: live {r['live']} / staged {r['staged']} / "
              f"cell diffs {len(r['cell_diffs'])} / only-live {len(r['only_live'])} / "
              f"only-staged {len(r['only_staged'])}")
    print(f"TOTAL discrepancies: {total_diffs}")


if __name__ == "__main__":
    main()
