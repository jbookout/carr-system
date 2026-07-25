#!/usr/bin/env python3
"""
build-system-graph.py — the SYSTEM-FLOW graph (companion to build-graph-notes.py).

WHY THIS EXISTS
  build-graph-notes.py renders PEOPLE (vendors, leads, clients, deals) from four
  spreadsheets. It answers "who referred whom". It cannot answer "how does my
  system flow through different areas", because the vault's own documents —
  playbooks, SOPs, doctrine, workflows — are not in it at all.

  This generator renders the DOCUMENTS and the AREAS they belong to, and draws an
  edge wherever one document references another. Those references already exist:
  the vault convention is to cite files by path ("DNA/writing-rules.md") or by
  bare filename ("templates.md"). That is real, human-authored structure, so the
  edges are real rather than inferred.

PLACEMENT (per the standing repo-vs-vault rule)
  Code lives in the repo (~/carr-system/pipelines) because it is durable and
  version-controlled. Output lives in the vault (CARR AI/Graph-System) because
  that is where Obsidian reads it. Output is DERIVED — never hand-edit it.

SEPARATION
  Writes to Graph-System/, NOT Graph/. Keeping people and system structure in
  separate folders is deliberate: merging them is how you get a hairball. In
  Obsidian, filter the graph with  path:Graph-System  to see this one alone.

IDENTITY RULE
  A reference resolves to a node by (1) exact relative path, else (2) unique
  basename. Ambiguous basenames (INDEX.md, README.md, SKILL.md, ...) resolve ONLY
  by full path — never guessed. Unresolved references are listed in the note as
  plain text so the gap stays visible instead of silently vanishing.
"""
import sys, os, re, json, shutil, unicodedata
from collections import defaultdict

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "Graph-System")

SKIP_DIRS = {"Graph", "Graph-System", ".obsidian", "_to_delete", "_asset_staging",
             ".git", "node_modules", ".trash"}
DOC_EXT = (".md", ".xlsx", ".json", ".html")

# ---------- areas: directory prefix -> (area name, tag slug) ----------
AREA_RULES = [
    ("DNA/Leads",            "Pipeline & Leads",     "leads"),
    ("DNA/Network",          "Vendor Network",       "network"),
    ("DNA/Deal Management",  "Deal Management",      "deals"),
    ("DNA/Clients",          "Clients & Prospects",  "clients"),
    ("DNA/Marketing",        "Marketing & Content",  "marketing"),
    ("DNA/Team",             "Team & Protocol",      "team"),
    ("DNA/Research",         "Research",             "research"),
    ("DNA/Reference",        "Reference",            "reference"),
    ("DNA",                  "DNA Doctrine",         "doctrine"),
    ("00_Context",           "Context & Governance", "context"),
    ("Automation",           "Automation",           "automation"),
    ("Marketing",            "Marketing & Content",  "marketing"),
    ("Prospects",            "Clients & Prospects",  "clients"),
    ("Outreach",             "Pipeline & Leads",     "leads"),
    ("Output",               "Marketing & Content",  "marketing"),
]

def area_of(relpath):
    for prefix, name, tag in AREA_RULES:
        if relpath == prefix or relpath.startswith(prefix + "/"):
            return name, tag
    return "Root & Entry Points", "root"

def slug(name, taken):
    s = unicodedata.normalize("NFKD", str(name or "unnamed"))
    s = re.sub(r'[\\/:*?"<>|#^\[\]{}]', "", s).strip()
    s = re.sub(r"\s+", " ", s) or "unnamed"
    base, n = s, 2
    while s.lower() in taken:
        s = f"{base} ({n})"; n += 1
    taken.add(s.lower())
    return s

# ---------- collect documents ----------
docs = {}   # relpath -> meta
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
    for fn in filenames:
        if not fn.endswith(DOC_EXT) or fn.startswith("."):
            continue
        full = os.path.join(dirpath, fn)
        rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
        if rel.split("/")[0] in SKIP_DIRS:
            continue
        area, tag = area_of(rel)
        docs[rel] = {"rel": rel, "base": fn, "area": area, "tag": tag,
                     "size": os.path.getsize(full), "refs": set(), "unresolved": set()}

# ---------- resolution index ----------
by_path = {r: r for r in docs}
base_map = defaultdict(list)
for r in docs:
    base_map[docs[r]["base"].lower()].append(r)
# a basename resolves only when unambiguous
by_base = {b: v[0] for b, v in base_map.items() if len(v) == 1}

REF_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_/&. -]*\.(?:md|xlsx|json|html)", re.I)

def resolve(ref):
    ref = ref.strip().lstrip("./")
    if ref in by_path:
        return by_path[ref]
    # try trailing-path match (e.g. "Social Media/x.md" for a deeper real path)
    for r in docs:
        if r.lower().endswith("/" + ref.lower()):
            return r
    return by_base.get(os.path.basename(ref).lower())

# ---------- scan markdown bodies for references ----------
for rel, meta in docs.items():
    if not rel.endswith(".md"):
        continue
    try:
        text = open(os.path.join(ROOT, rel), encoding="utf-8", errors="ignore").read()
    except OSError:
        continue
    for m in REF_RE.findall(text):
        tgt = resolve(m)
        if tgt and tgt != rel:
            meta["refs"].add(tgt)
        elif not tgt:
            meta["unresolved"].add(m.strip())

# ---------- build node titles ----------
taken = set()
title = {}
for rel in sorted(docs):
    stem = os.path.splitext(docs[rel]["base"])[0]
    # disambiguate the known colliding basenames with their parent folder
    if len(base_map[docs[rel]["base"].lower()]) > 1:
        parent = os.path.dirname(rel).split("/")[-1] or "root"
        stem = f"{stem} ({parent})"
    title[rel] = slug(stem, taken)

areas = sorted({d["area"] for d in docs.values()})
area_title = {a: slug(a, taken) for a in areas}
area_tag = {}
for d in docs.values():
    area_tag[d["area"]] = d["tag"]

# ---------- write ----------
if os.path.isdir(OUT):
    shutil.rmtree(OUT)
os.makedirs(os.path.join(OUT, "areas"), exist_ok=True)
os.makedirs(os.path.join(OUT, "docs"), exist_ok=True)

def first_line(rel):
    """A one-line human descriptor: first heading, else first italic note line."""
    if not rel.endswith(".md"):
        return ""
    try:
        for ln in open(os.path.join(ROOT, rel), encoding="utf-8", errors="ignore"):
            ln = ln.strip()
            if ln.startswith("#"):
                return ln.lstrip("# ").strip()[:160]
    except OSError:
        pass
    return ""

# area hubs
area_members = defaultdict(list)
for rel, d in docs.items():
    area_members[d["area"]].append(rel)

# cross-area traffic, for the hub notes
area_edges = defaultdict(int)
for rel, d in docs.items():
    for t in d["refs"]:
        a, b = d["area"], docs[t]["area"]
        if a != b:
            area_edges[(a, b)] += 1

for a in areas:
    tag = area_tag[a]
    members = sorted(area_members[a], key=lambda r: -docs[r]["size"])
    out_links = sorted({b for (x, b), n in area_edges.items() if x == a})
    in_links = sorted({x for (x, b), n in area_edges.items() if b == a})
    body = [
        "---",
        "type: area",
        f'area: "{a}"',
        f"tags: [sys-area, sys-{tag}]",
        "---",
        "",
        f"# {a}",
        "",
        f"**{len(members)} documents.** Area hub — every document below belongs to this area.",
        "",
    ]
    if out_links:
        body += ["## Feeds into", ""] + [f"- [[{area_title[b]}]] ({area_edges[(a,b)]} references)"
                                          for b in out_links] + [""]
    if in_links:
        body += ["## Fed by", ""] + [f"- [[{area_title[x]}]] ({area_edges[(x,a)]} references)"
                                      for x in in_links] + [""]
    body += ["## Documents", ""] + [f"- [[{title[r]}]]" for r in members] + [""]
    open(os.path.join(OUT, "areas", f"{area_title[a]}.md"), "w", encoding="utf-8").write("\n".join(body))

# doc notes
for rel, d in sorted(docs.items()):
    desc = first_line(rel)
    body = [
        "---",
        "type: doc",
        f'area: "{d["area"]}"',
        f'path: "{rel}"',
        f"size_kb: {round(d['size']/1024, 1)}",
        f"tags: [sys-doc, sys-{d['tag']}]",
        "---",
        "",
        f"# {title[rel]}",
        "",
        f"`{rel}`" + (f" — {desc}" if desc else ""),
        "",
        f"Area: [[{area_title[d['area']]}]]",
        "",
    ]
    # Link to the REAL file so the actual vault note joins the graph instead of
    # floating as an orphan. The vault cites files by path ("DNA/templates.md"),
    # which Obsidian does not resolve as a link, so 297 real documents rendered
    # as disconnected dots while their proxy node sat in the cluster. A path-form
    # wikilink resolves in Obsidian and is unambiguous across the 4 colliding
    # basenames (INDEX/README/SKILL/session-operating-style). Markdown only —
    # Obsidian does not graph .xlsx/.json/.html.
    if rel.endswith(".md"):
        body += [f"Source file: [[{rel[:-3]}]]", ""]
    if d["refs"]:
        body += ["## References", ""]
        for t in sorted(d["refs"], key=lambda r: title[r]):
            cross = " ⟶ *" + docs[t]["area"] + "*" if docs[t]["area"] != d["area"] else ""
            body.append(f"- [[{title[t]}]]{cross}")
        body.append("")
    if d["unresolved"]:
        body += ["## Referenced but not found in the vault", ""]
        body += [f"- `{u}`" for u in sorted(d["unresolved"])[:25]]
        body.append("")
    open(os.path.join(OUT, "docs", f"{title[rel]}.md"), "w", encoding="utf-8").write("\n".join(body))

# ---------- README + stats ----------
edge_count = sum(len(d["refs"]) for d in docs.values()) + sum(len(v) for v in area_members.values())
orphans = [r for r, d in docs.items() if not d["refs"]]
lines = [
    "# Graph-System — how the CARR AI system flows",
    "",
    "**DERIVED. Never hand-edit.** Regenerate with `run.sh graph-system`.",
    "",
    f"- **{len(docs)} document nodes** across **{len(areas)} areas**",
    f"- **{edge_count} edges** ({sum(len(d['refs']) for d in docs.values())} document-to-document "
    f"references + {sum(len(v) for v in area_members.values())} document-to-area)",
    f"- documents that reference nothing: **{len(orphans)}** "
    f"({round(100*len(orphans)/max(len(docs),1))}%) — still connected via their area hub",
    "",
    "In Obsidian, filter the graph with `path:Graph-System` to view this alone,",
    "or `path:Graph` for the people graph. They are deliberately separate.",
    "",
    "## Cross-area traffic",
    "",
    "| From | To | References |",
    "|---|---|---|",
]
for (a, b), n in sorted(area_edges.items(), key=lambda kv: -kv[1]):
    lines.append(f"| {a} | {b} | {n} |")
open(os.path.join(OUT, "README.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")

print(f"Graph-System: {len(docs)} docs, {len(areas)} areas, {edge_count} edges, "
      f"{len(orphans)} docs with no outbound reference")
