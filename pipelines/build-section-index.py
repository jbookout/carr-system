#!/usr/bin/env python3
"""
build-section-index.py — derived section-level index of the CARR AI vault.

Walks the vault's knowledge tier and emits one TSV row per markdown section:
path, start line, end line, header level, header text, parent breadcrumb, and
(for file-level rows) a one-line gist pulled from the file's own preamble.
This is the machine layer retrieve.py scores against so a session can decide
WHERE an answer lives without opening files (graph-engineering build,
2026-07-25; doctrine: the index is for the model, the graph view is for Joe).

DERIVED VIEW: regenerate any time with `./run.sh section-index`; never
hand-edit the output. Truth is the markdown files themselves.

Excluded on purpose: cold storage (Source Material, Output, archives),
app internals (.obsidian, .claude), and any file self-marked SUPERSEDED
(e.g. DNA/Network/vendors.md — frozen backup; retrieval must never route a
session into stale data). Graph/ IS indexed: its per-entity notes are derived
from the live xlsx/JSON sources of truth, so they are the entity-level nodes
retrieval should land on.

Usage: python3 build-section-index.py [CARR_ROOT]
Output: <CARR_ROOT>/Automation/section-index.tsv
"""
import sys, os, re

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT  = os.path.join(ROOT, "Automation", "section-index.tsv")

SKIP_DIRS  = {"Source Material", "Output", ".obsidian", ".claude", ".git",
              "source-exports", "photos", "Prospects"}
SKIP_FILES = {"decision-history-archive.md", "open-loops-closed.md", "section-index.tsv"}
SUPERSEDED = re.compile(r"SUPERSEDED|RETIRED\b", re.IGNORECASE)

HDR = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")

def clean(text):
    """Strip markdown noise so the TSV stays one line per row."""
    return re.sub(r"\s+", " ", text.replace("\t", " ").replace("*", "").replace("`", "")).strip()

def gist_of(lines):
    """First real prose line after the title — this vault's files open with an
    italic purpose line, which is exactly the description retrieval wants."""
    for ln in lines[1:40]:
        s = ln.strip()
        if not s or HDR.match(s) or s.startswith(("|", "-", ">", "```", "<!--")):
            continue
        return clean(s)[:200]
    return ""

def index_file(abspath, relpath, rows):
    with open(abspath, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    n = len(lines)
    if n == 0:
        return
    if any(SUPERSEDED.search(ln) for ln in lines[:3]):
        return  # frozen backups never enter the retrieval layer
    # file-level row (level 0) so headerless files still score
    rows.append((relpath, 1, n, 0, os.path.splitext(os.path.basename(relpath))[0],
                 "", gist_of(lines)))
    heads, fence = [], False
    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        m = None if fence else HDR.match(ln)
        if m:
            heads.append((i, len(m.group(1)), clean(m.group(2))))
    for j, (start, level, text) in enumerate(heads):
        end = n
        for k in range(j + 1, len(heads)):
            if heads[k][1] <= level:
                end = heads[k][0] - 1
                break
        crumbs = []
        lvl = level
        for k in range(j - 1, -1, -1):
            if heads[k][1] < lvl:
                crumbs.append(heads[k][2])
                lvl = heads[k][1]
        rows.append((relpath, start, end, level, text, " > ".join(reversed(crumbs)), ""))

def main():
    rows = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if not name.endswith(".md") or name in SKIP_FILES:
                continue
            abspath = os.path.join(dirpath, name)
            relpath = os.path.relpath(abspath, ROOT)
            index_file(abspath, relpath, rows)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# section-index.tsv — DERIVED, never hand-edit. Rebuild: ./run.sh section-index\n")
        f.write("# path\tstart\tend\tlevel\theader\tparents\tgist\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    files = len({r[0] for r in rows})
    print(f"section-index: {files} files, {len(rows)} rows -> {os.path.relpath(OUT, ROOT)}")

if __name__ == "__main__":
    main()
