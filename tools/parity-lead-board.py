#!/usr/bin/env python3
"""parity-lead-board.py — ORDER 26(c)'s done-test, run rather than asserted.

The Lead Board is built TWICE on the same day's data — once from the generated
files, once from the record layer — and the payload the template is driven by is
diffed. The HTML is never compared: a template tweak or a stamp would make two
identical boards look different, and a dropped field inside an identical-looking
page would not. What is compared is `L` (every board row), the registry list, the
unowned list, the per-segment counts and the header numbers.

The gate ORDER 26 sets: IDENTICAL segment counts and IDENTICAL row membership per
segment. This checks that, and then checks more — full row equality, in order —
because a board whose rows match by name while a phone number moved is not the
same board. Both results are reported separately so a failure says which bar it
missed.

Neither run touches the vault. Each builds inside a scratch root whose inputs are
symlinks to the real files and whose outputs (lead-board.html and the shared-tier
copy) land in the scratch directory.

Usage:
  ./run.sh export --only lead-registry          # so both sides read the same day
  .venv/bin/python tools/db-tap.py run tools/parity-lead-board.py --fresh-registry [--keep]
  CARR_DB_POOL_URL=... .venv/bin/python tools/parity-lead-board.py
Exit 0 = exact parity. Exit 1 = a difference, printed in full.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = str(REPO / ".venv" / "bin" / "python")
ART = REPO / "out" / "parity-lead-board"
VAULT = Path(os.environ.get(
    "CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"))

# Everything the board reads, and nothing it does not. Missing files are fine —
# the board treats each lane as optional and so does this.
LINK_IN = [
    "Automation/lead-board-template.html",
    "Automation/dso-matches.json",
    "Automation/entity-formation-leads.json",
    "Automation/pre-entity-watch.json",
    "Automation/renewal-radar.json",
    "Automation/relocating-owner-leads.json",
    "Automation/lead-board-decisions.json",
    "Automation/lead-board-hot.json",
    "DNA/Team/national-accounts.json",
]


def scratch_root(tmp, name, fresh_registry=False):
    root = Path(tmp) / name
    for d in ("Automation", "DNA/Leads", "DNA/Team/live-boards"):
        (root / d).mkdir(parents=True, exist_ok=True)
    for rel in LINK_IN:
        src = VAULT / rel
        if src.exists():
            (root / rel).symlink_to(src)
    for x in (VAULT / "DNA/Leads").glob("lead-router-*.xlsx"):
        (root / "DNA/Leads" / x.name).symlink_to(x)
    # SAME DAY'S DATA IS THE PREMISE, and the vault copy is not always it. On
    # 2026-07-31 a backfill wrote L-206's lease event at 21:07Z while the vault's
    # lead-registry.xlsx had last been exported at 14:50 — so file mode read
    # '2029-01' where the record said 2029-01-01, and that is the export being
    # six hours behind, not the two paths deriving different boards.
    # --fresh-registry points file mode at a registry exported NOW (draft, no
    # vault write) so the comparison is actually same-day. Run
    # `./run.sh export --only lead-registry` first.
    draft = REPO / "out/exports/lead-registry.xlsx"
    reg = draft if (fresh_registry and draft.exists()) else VAULT / "DNA/Leads/lead-registry.xlsx"
    (root / "DNA/Leads/lead-registry.xlsx").symlink_to(reg)
    return root


def build(tmp, mode, fresh_registry=False):
    root = scratch_root(tmp, mode, fresh_registry)
    payload = Path(tmp) / f"payload-{mode}.json"
    env = {**os.environ, "CARR_BOARD_PAYLOAD": str(payload)}
    p = subprocess.run([PY, str(REPO / "generators" / "build-lead-board.py"),
                        str(root), f"--{mode}"],
                       capture_output=True, text=True, env=env)
    print(f"--- {mode} ---")
    print(p.stdout.strip())
    if p.stderr.strip():
        print(p.stderr.strip(), file=sys.stderr)
    if p.returncode != 0:
        sys.exit(f"{mode} build failed (rc={p.returncode})")
    if not payload.exists():
        sys.exit(f"{mode} build wrote no payload")
    d = json.load(open(payload))
    if d["mode"] != mode:
        sys.exit(f"{mode} build actually ran in {d['mode']} mode — the record path was "
                 "unreachable, so there is nothing to compare. Fix the credential first.")
    return d


def rowkey(r):
    """Identity of a board row for the membership test: who, and where."""
    return (str(r.get("n", "")), str(r.get("ad", "")))


def main():
    keep = "--keep" in sys.argv[1:]
    fresh = "--fresh-registry" in sys.argv[1:]
    ART.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="parity-board-")
    try:
        F = build(tmp, "files", fresh)
        R = build(tmp, "records")
        for nm, d in (("files", F), ("records", R)):
            json.dump(d, open(ART / f"payload-{nm}.json", "w"), sort_keys=True, indent=0)
    finally:
        if keep:
            print(f"scratch kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    fails = []

    # ── the order's own bar: segment counts, then row membership per segment ──
    fc, rc = Counter(x["s"] for x in F["leads"]), Counter(x["s"] for x in R["leads"])
    if fc != rc:
        fails.append("SEGMENT COUNTS DIFFER")
        for s in sorted(set(fc) | set(rc)):
            if fc[s] != rc[s]:
                fails.append(f"    {s!r}: files {fc[s]} vs records {rc[s]}")
    else:
        print(f"segment counts: IDENTICAL across {len(fc)} segments "
              f"({sum(fc.values()):,} rows)")

    memb_ok = True
    for s in sorted(set(fc) | set(rc)):
        fm = Counter(rowkey(x) for x in F["leads"] if x["s"] == s)
        rm = Counter(rowkey(x) for x in R["leads"] if x["s"] == s)
        if fm != rm:
            memb_ok = False
            only_f = sorted((fm - rm).elements())[:10]
            only_r = sorted((rm - fm).elements())[:10]
            fails.append(f"MEMBERSHIP DIFFERS in {s!r}: "
                         f"{sum((fm - rm).values())} only-files, {sum((rm - fm).values())} only-records")
            for k in only_f:
                fails.append(f"    only in files:   {k}")
            for k in only_r:
                fails.append(f"    only in records: {k}")
    if memb_ok:
        print("row membership per segment: IDENTICAL")

    # ── stricter than the order asks: every field, in order ──────────────────
    if F["leads"] != R["leads"]:
        n = 0
        if len(F["leads"]) != len(R["leads"]):
            fails.append(f"ROW COUNT: files {len(F['leads'])} vs records {len(R['leads'])}")
        for i, (a, b) in enumerate(zip(F["leads"], R["leads"])):
            if a != b:
                n += 1
                if n <= 8:
                    diff = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
                    fails.append(f"ROW {i} differs ({a.get('n')!r}): {diff}")
        if n:
            fails.append(f"ROW-LEVEL DIFFERENCES: {n} of {len(F['leads'])}")
    else:
        print(f"full row equality (every field, in order): EXACT — {len(F['leads']):,} rows")

    for key in ("registry", "unowned", "segmeta", "counties", "emailcov", "queue",
                "worked_n", "dso_n", "ef_n", "ro_n", "na_n", "segcount"):
        if F[key] == R[key]:
            continue
        if isinstance(F[key], list) and len(F[key]) == len(R[key]):
            for i, (a, b) in enumerate(zip(F[key], R[key])):
                if a != b:
                    diff = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
                    fails.append(f"{key}[{i}] ({a.get('id') or a.get('name')}): {diff}")
        else:
            fails.append(f"{key}: files {F[key]!r} vs records {R[key]!r}")
    else:
        pass
    if not any(f.split("[")[0].split(":")[0] in
               ("registry", "unowned", "segmeta", "counties", "emailcov", "queue",
                "worked_n", "dso_n", "ef_n", "ro_n", "na_n", "segcount") for f in fails):
        print("registry, unowned, segment metadata and every header number: EXACT")

    print()
    if fails:
        print("PARITY FAILED")
        for f in fails:
            print("  " + f)
        print(f"\npayloads kept: {ART}")
        sys.exit(1)
    print("PARITY EXACT — records mode and file mode derive the same board.")
    print(f"payloads kept: {ART}")


if __name__ == "__main__":
    main()
