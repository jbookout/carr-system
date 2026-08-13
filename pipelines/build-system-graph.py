#!/usr/bin/env python3
"""
build-system-graph.py — the CARR AI system as its own shape.

THE MODEL (Joe, 2026-07-25 — this is the spec, quoted):
  "You'd have DNA and all its internal folders and files, then JOE's Brain
   connected on one side and DELL's Brain connected on the other side. Then
   you'd have CONTEXT with all its files orbiting, then INDEX would connect out
   of that folder to all the other folders like it does in the actual system.
   Then each folder would have its own orbit of files. And they would all
   connect to other areas where they do in real life."

        ★ JOE'S BRAIN ──▶ ★ DNA (shared tier) ◀── ★ DELL'S BRAIN
         (everything            the single             (reads DNA,
          outside DNA/)         share to Dell)          writes back)

        📇 INDEX ──▶ every top-level folder      (the router, as in real life)
        📁 folder ──▶ its own files              (each folder's orbit)
        📁 folder ──▶ 📁 folder                   where their files cite each other

WHY THERE ARE NO PROXY NODES (v1 had them; they were duplication)
  v1 emitted one proxy node per document to carry file-to-file edges without
  editing real files. Obsidian graphs every .md in the vault, so each document
  then existed TWICE — once connected, once orphaned — and Joe called it, fairly,
  duplication. This version links FOLDERS directly to the REAL files and rolls
  file-to-file references up into FOLDER-to-FOLDER edges. Nothing is duplicated,
  every real document is connected to its folder, and cross-area flow is still
  visible — at the altitude you can actually read it.

  Path-form wikilinks ([[DNA/templates]]) because four basenames collide
  (INDEX, README, SKILL, session-operating-style).

PLACEMENT: code in the repo (durable, versioned); output in the vault (where
Obsidian reads it). Output is DERIVED — never hand-edit Graph-System/.

PHASE 1 (2026-08-13, doctrine-store build, gate for the Aug 21 retirement
start): folder-to-folder edges are rolled up from references found IN a
document's text. For any .md file recorded on a VERIFIED doctrine_migration_
batch, that text now comes from ONE read-only query (lib/record_sources.
doctrine_sections) instead of an open() — the file is never read. The node
itself (the file existing under some folder) still comes from the plain
os.walk below; only the CONTENT READ that reference-extraction depends on
moves to the store for migrated files. doctrine_edge/doctrine_link exist for
this purpose too but carry only 5/0 rows respectively as of this build — too
sparse to be a primary signal — so wikilink-style references are extracted
from the section plain_text the same way REF already mines a file's raw
markdown, per the build's own fallback instruction. FAIL-SOFT: any store error
falls back to opening every file exactly as before (no graph blindness).
"""
import sys, os, re, shutil, unicodedata
from collections import defaultdict

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "Graph-System")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Backups added 2026-08-13. Backups/portability-mirror held 224 .md copies that
# the walk treated as live vault content, so a reference could resolve to a
# BACKUP COPY instead of the real document. Measured on the folder-pair diff
# behind the determinism fix: of 65 folder-pairs that fix removed, 55 touched
# the mirror and 53 had it as the target. Same class as Output/_to_delete/.trash
# — derived, not live. The 5 doctrine documents whose only on-disk trace was the
# mirror are emitted from the store instead, in the pass below.
SKIP = {"Graph", "Graph-System", ".obsidian", "_to_delete", "_asset_staging",
        ".git", "node_modules", ".trash", "Output", "Backups"}
DOC_EXT = (".md", ".xlsx", ".json", ".html")

# ---------- collect real documents ----------
docs = {}
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if d not in SKIP and not d.startswith(".")]
    for f in fn:
        if not f.endswith(DOC_EXT) or f.startswith("."):
            continue
        rel = os.path.relpath(os.path.join(dp, f), ROOT).replace(os.sep, "/")
        if rel.split("/")[0] in SKIP:
            continue
        docs[rel] = os.path.dirname(rel) or "."

# ---------- doctrine store pass: which .md files are store-held, and their text ----------
# MOVED here 2026-08-13 (it used to sit after resolve()). It has to run before
# the folder list and the reference index are built, because doctrine that lives
# ONLY in the store now contributes real nodes — and because those nodes must be
# resolvable as reference TARGETS like any other document.
STORE_ONLY_FOLDER = "DNA/Doctrine (store-only)"
slug_by_path = {}
store_text = {}   # rel path -> concatenated plain_text (ordinal order), store-held files only
try:
    sys.path.insert(0, REPO)
    from lib.record_sources import doctrine_slug_by_path, doctrine_sections
    slug_by_path = doctrine_slug_by_path(ROOT)
    by_slug = defaultdict(list)
    for s in doctrine_sections():
        by_slug[s["slug"]].append(s)
    for rel, slug in slug_by_path.items():
        secs = sorted(by_slug.get(slug, []), key=lambda s: s["ordinal"])
        if secs:
            store_text[rel] = "\n\n".join(s["plain_text"] for s in secs)
    # Doctrine with NO live vault file. Before Backups/ was skipped, the backup
    # mirror was the only thing putting these on the graph at all — which meant
    # the graph was showing them from a backup rather than from the store that
    # actually holds them. Give them a node of their own instead of hanging them
    # off a folder they do not live in.
    for slug in sorted(set(by_slug) - set(slug_by_path.values())):
        secs = sorted(by_slug[slug], key=lambda s: s["ordinal"])
        if not secs:
            continue
        rel = f"{STORE_ONLY_FOLDER}/{slug}.md"
        docs[rel] = STORE_ONLY_FOLDER
        store_text[rel] = "\n\n".join(s["plain_text"] for s in secs)
except Exception as exc:  # noqa: BLE001 — fail-soft, same posture as retrieve.py
    print(f"build-system-graph: store pass skipped ({type(exc).__name__}) — "
          f"reading every file from disk (no store-held skip)", file=sys.stderr)

folders = sorted({d for d in docs.values()})

# ---------- resolve file-to-file references ----------
by_path = set(docs)
base_map = defaultdict(list)
for r in docs:
    base_map[os.path.basename(r).lower()].append(r)
by_base = {b: v[0] for b, v in base_map.items() if len(v) == 1}
REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_/&. -]*\.(?:md|xlsx|json|html)", re.I)

def resolve(ref):
    # Deterministic tie-break (fixed 2026-08-13): by_path is a set, so a plain
    # `for r in by_path` walked it in Python's per-process hash-randomized
    # order — proven today by running the unmodified script twice and getting
    # different folder-flow counts on the SAME vault content. When more than
    # one file ends with the same suffix, pick shortest-path-then-lexicographic
    # instead of whichever the set handed out first.
    ref = ref.strip().lstrip("./")
    if ref in by_path:
        return ref
    suffix = "/" + ref.lower()
    candidates = sorted(
        (r for r in by_path if r.lower().endswith(suffix)),
        key=lambda r: (len(r), r),
    )
    if candidates:
        return candidates[0]
    return by_base.get(os.path.basename(ref).lower())

folder_edges: defaultdict[tuple[str, str], int] = defaultdict(int)  # (folder A, folder B) -> reference count
for rel, fold in docs.items():
    if not rel.endswith(".md"):
        continue
    if rel in store_text:
        text = store_text[rel]
    else:
        try:
            text = open(os.path.join(ROOT, rel), encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
    for m in REF.findall(text):
        tgt = resolve(m)
        if tgt and docs[tgt] != fold:
            folder_edges[(fold, docs[tgt])] += 1

# ---------- tiers ----------
def tier_of(folder):
    """CLAUDE.md: the entire SHARED tier lives under DNA/ — the single share to
    Dell. Everything outside DNA/ is Joe-personal and never shared."""
    return "DNA" if folder == "DNA" or folder.startswith("DNA/") else "JOE"

def title_for(folder):
    return "📁 " + (folder if folder != "." else "root")

def safe(n):
    x = unicodedata.normalize("NFKD", n)
    return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|#^\[\]{}]', "", x)).strip() or "unnamed"

POLE_JOE  = "★ JOE'S BRAIN (personal tier)"
POLE_DNA  = "★ DNA (shared tier)"
POLE_DELL = "★ DELL'S BRAIN (his twin)"
INDEX     = "📇 INDEX — the router"

if os.path.isdir(OUT):
    shutil.rmtree(OUT)
os.makedirs(OUT)

def w(title, lines, ext=".md"):
    # README goes out as .txt: Obsidian graphs every .md, and a linkless
    # README renders as a floating dot with nothing attached to it.
    open(os.path.join(OUT, f"{safe(title)}{ext}"), "w", encoding="utf-8").write("\n".join(lines))

# ---------- folder nodes: each with its own orbit of real files ----------
edges = 0
for fold in folders:
    files = sorted(r for r, f in docs.items() if f == fold)
    tier = tier_of(fold)
    parent = os.path.dirname(fold)
    out_f = sorted({b for (a, b), n in folder_edges.items() if a == fold})
    body = ["---", "type: folder", f'folder: "{fold}"', f"tier: {tier}",
            f"files: {len(files)}", f"tags: [sys-folder, sys-tier-{tier.lower()}]",
            "---", "", f"# {fold if fold != '.' else 'root'}", "",
            f"Tier: [[{safe(POLE_DNA if tier == 'DNA' else POLE_JOE)}]]", ""]
    edges += 1
    if parent and parent in folders:
        body += [f"Parent: [[{safe(title_for(parent))}]]", ""]; edges += 1
    if out_f:
        body += ["## Feeds into", ""]
        for b in out_f:
            body.append(f"- [[{safe(title_for(b))}]] — {folder_edges[(fold, b)]} references")
            edges += 1
        body.append("")
    body += ["## Files", ""]
    for r in files:
        body.append(f"- [[{r[:-3]}]]" if r.endswith(".md") else f"- `{r}`")
        if r.endswith(".md"):
            edges += 1
    w(title_for(fold), body + [""])

# ---------- the router ----------
tops = sorted({f.split("/")[0] for f in folders if f != "."})
w(INDEX, ["---", "type: router", "tags: [sys-router]", "---", "",
          "# INDEX — the router", "",
          "*`INDEX.md` is read first every session and routes to every area, "
          "so it links out to all of them here exactly as it does in the system.*", ""]
         + [f"- [[{safe(title_for(t))}]]" for t in tops] + [""])
edges += len(tops)

# ---------- tier poles ----------
for pole, tier, blurb in (
    (POLE_JOE, "JOE", "Everything outside `DNA/`. Joe-personal, never shared."),
    (POLE_DNA, "DNA", "The single share to Dell. Both brains read and write this tier, "
                      "so the two-writer protocol applies to every file below."),
):
    mine = sorted(f for f in folders if tier_of(f) == tier)
    w(pole, ["---", "type: pole", f"tier: {tier}", f"tags: [sys-pole, sys-tier-{tier.lower()}]",
             "---", "", f"# {pole}", "", f"*{blurb}*", "",
             f"**{len(mine)} folders.**", ""]
            + [f"- [[{safe(title_for(f))}]]" for f in mine] + [""])
    edges += len(mine)

w(POLE_DELL, ["---", "type: pole", "tier: DELL", "tags: [sys-pole, sys-tier-dell]", "---", "",
              f"# {POLE_DELL}", "",
              "*Dell's twin runs on the shared tier. He has no files in this vault — "
              "he reads and writes `DNA/`, which is exactly why the two-writer "
              "protocol exists. This node marks the share boundary.*", "",
              f"- [[{safe(POLE_DNA)}]]", ""])
edges += 1

LEGEND = "📖 LEGEND — system graph"
w(LEGEND, ["---", "type: legend", "tags: [sys-legend]", "---", "",
           f"# {LEGEND}", "",
           "**DERIVED. Never hand-edit `Graph-System/`.** "
           "Regenerate: `~/carr-system/run.sh graph-system`.", "",
           "| Symbol | Meaning |", "|---|---|",
           "| ★ | the three brains — Joe's personal tier, the shared DNA tier, Dell's twin |",
           "| 📇 | INDEX, the router read first every session |",
           "| 📁 | a real folder, with its own files orbiting it |", "",
           "Folder-to-folder arrows are rolled up from the file references that "
           "already exist in the documents. Colour: cyan = Joe-personal · "
           "blue = shared DNA · orange = Dell · gold = INDEX.", "",
           "## Start here", "",
           f"- [[{safe(POLE_JOE)}]]", f"- [[{safe(POLE_DNA)}]]",
           f"- [[{safe(POLE_DELL)}]]", f"- [[{safe(INDEX)}]]", "",
           "## Busiest flows", "", "| From | To | Refs |", "|---|---|---|"]
          + [f"| {a} | {b} | {n} |" for (a, b), n in
             sorted(folder_edges.items(), key=lambda kv: -kv[1])[:20]])
edges += 4

print(f"Graph-System: {len(folders)} folders, {len(docs)} real docs linked directly "
      f"(no proxies), {len(folder_edges)} folder flows, ~{edges} edges "
      f"({len(store_text)} store-read, {sum(1 for r in docs if r.endswith('.md')) - len(store_text)} disk-read)")
