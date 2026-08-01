#!/usr/bin/env python3
"""parity-records.py — prove the record path and the file path derive the same surface.

ORDER 29a's gate. Each repointed consumer is run TWICE on the same day's data,
once with `--files` and once with `--records`, and the DERIVED DATA is diffed —
never the rendering. A consumer only keeps its records-mode default while this
reports PARITY EXACT for it.

  deal-room  ... the `var DEALS = [...]` payload the page is driven by. The
                 "Pulled <stamp>" line is expected to differ: file mode reports
                 when the export ran, records mode when the room was built. It is
                 reported separately and is not part of the data diff.
  graph      ... the whole Graph/ tree (notes + hubs): every node file and every
                 [[wikilink]] edge in it, compared path by path and byte by byte.
  graph-health . the anomaly report, verbatim.

Nothing is written into the vault: the graph runs happen in a scratch root whose
DNA/ is a symlink to the vault's, so Graph/ lands in the scratch dir. Artifacts
are kept under out/parity-records/ for the execution log.

Usage:  ./.venv/bin/python tools/parity-records.py [--vault PATH] [--keep]
Exit 0 = every consumer exact. Exit 1 = at least one difference, printed in full.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = str(REPO / ".venv" / "bin" / "python")
ART = REPO / "out" / "parity-records"
VAULT = Path(os.environ.get(
    "CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"))

MODES = ("files", "records")


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode != 0:
        print(f"COMMAND FAILED ({p.returncode}): {' '.join(str(c) for c in cmd)}")
        print(p.stdout)
        print(p.stderr, file=sys.stderr)
        sys.exit(2)
    return p


def scratch_root(tmp, name):
    root = Path(tmp) / name
    root.mkdir()
    (root / "DNA").symlink_to(VAULT / "DNA")
    return root


# ---------------- deal room ----------------

def deal_room_payload(tmp, mode):
    out = Path(tmp) / f"deal-room-{mode}.html"
    run([PY, str(REPO / "generators" / "build-deal-room.py"),
         str(VAULT / "DNA/Deal Management/panhandle-team-deals.json"), str(out), f"--{mode}"])
    html = out.read_text()
    m = re.search(r"^var DEALS = (.*);$", html, re.M)
    if not m:
        sys.exit("deal-room: could not find the DEALS payload in the built page")
    stamp = re.search(r"Pulled (.*?)\.</div>", html)
    return json.loads(m.group(1)), (stamp.group(1) if stamp else "")


# ---------------- graph ----------------

def graph_tree(tmp, mode):
    root = scratch_root(tmp, f"graph-{mode}")
    run([PY, str(REPO / "pipelines" / "build-graph-notes.py"), str(root), f"--{mode}"])
    run([PY, str(REPO / "pipelines" / "build-graph-structure.py"), str(root), f"--{mode}"])
    tree = {}
    g = root / "Graph"
    for p in sorted(g.rglob("*")):
        if p.is_file():
            tree[str(p.relative_to(g))] = p.read_text(encoding="utf-8", errors="replace")
    return tree, root


def health_report(root, mode):
    p = run([PY, str(REPO / "pipelines" / "graph-health.py"), str(root), f"--{mode}", "--verbose"])
    # the header carries the mode by design; the findings must not
    return "\n".join(l for l in p.stdout.splitlines() if not l.startswith("GRAPH HEALTH"))


# ---------------- diffs ----------------

def diff_json(a, b, label):
    if a == b:
        return []
    out = [f"{label}: MISMATCH"]
    if len(a) != len(b):
        out.append(f"  row count {len(a)} (files) vs {len(b)} (records)")
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            for k in sorted(set(x) | set(y)):
                if x.get(k) != y.get(k):
                    out.append(f"  row {i} {x.get('name') or ''} · {k}: "
                               f"{x.get(k)!r} (files) vs {y.get(k)!r} (records)")
    return out


def diff_tree(a, b, label):
    out = []
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    for p in only_a:
        out.append(f"  only in files mode:   {p}")
    for p in only_b:
        out.append(f"  only in records mode: {p}")
    for p in sorted(set(a) & set(b)):
        if a[p] != b[p]:
            out.append(f"  content differs: {p}")
    return [f"{label}: MISMATCH"] + out if out else []


def main():
    keep = "--keep" in sys.argv
    ART.mkdir(parents=True, exist_ok=True)
    results, report = {}, []
    tmp = tempfile.mkdtemp(prefix="parity-records-")
    try:
        payload, stamp = {}, {}
        for m in MODES:
            payload[m], stamp[m] = deal_room_payload(tmp, m)
        d = diff_json(payload["files"], payload["records"], "deal-room")
        results["deal-room"] = not d
        report += d or [f"deal-room: PARITY EXACT — {len(payload['files'])} deals, "
                        f"every field identical"]
        report.append(f"  (stamp, expected to differ: files {stamp['files']!r} / "
                      f"records {stamp['records']!r})")
        (ART / "deal-room-records.json").write_text(json.dumps(payload["records"], indent=2))

        tree, root = {}, {}
        for m in MODES:
            tree[m], root[m] = graph_tree(tmp, m)
        d = diff_tree(tree["files"], tree["records"], "graph")
        results["graph"] = not d
        nodes = len(tree["files"])
        edges = sum(t.count("[[") for t in tree["files"].values())
        report += d or [f"graph: PARITY EXACT — {nodes} node files, {edges} wikilink edges, "
                        f"byte-identical"]

        h = {m: health_report(root[m], m) for m in MODES}
        results["graph-health"] = h["files"] == h["records"]
        if results["graph-health"]:
            report.append(f"graph-health: PARITY EXACT — {len(h['files'].splitlines())} "
                          f"report lines identical")
        else:
            report.append("graph-health: MISMATCH")
            import difflib
            report += ["  " + x for x in difflib.unified_diff(
                h["files"].splitlines(), h["records"].splitlines(),
                "files", "records", lineterm="", n=1)]
        (ART / "graph-health-records.txt").write_text(h["records"])
    finally:
        if keep:
            print(f"scratch kept at {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    text = "\n".join(report)
    (ART / "parity-report.txt").write_text(text + "\n")
    print(text)
    print(f"\nartifacts: {ART}")
    ok = all(results.values())
    print("ALL CONSUMERS EXACT" if ok else "PARITY FAILED for: "
          + ", ".join(k for k, v in results.items() if not v))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
