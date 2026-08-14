#!/usr/bin/env python3
"""abilities-manifest.py — the machine half of abilities.md (2026-08-06 stitch).

Scans the REPO for code-backed capabilities and emits a markdown section. No
database, no vault reads — pure repo introspection, so the inventory can never
drift from the code it describes (the #214 audit found three source comments
asserting states the code disproved; a scanned manifest is the antidote for
the whole class).

Consumed by exporters/targets.py's build_abilities (the stitched render). Also
runnable standalone: prints the section to stdout.
"""

import json
import os
import re
import sys

# Script-relative, NOT expanduser("~/carr-system") — same fix as commit fad87a4
# (tests) and c4d040d (gates). exporters/targets.py already locates THIS script
# script-relative and then runs it, so on a clone outside $HOME the caller found
# the file and the file crashed on open(REPO/mcp-server/src/tools.js), taking
# the abilities export down with it.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def verbs():
    """(name, first sentence of description) from mcp-server/src/tools.js."""
    src = open(os.path.join(REPO, "mcp-server/src/tools.js")).read()
    out = []
    for m in re.finditer(r'^  "([a-z-]+)": \{\s*\n\s*write:.*?\n\s*description:\s*"((?:[^"\\]|\\.)*)"',
                         src, re.M | re.S):
        name = m.group(1)
        desc = m.group(2).replace('\\"', '"')
        first = re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0]
        out.append((name, first[:160]))
    return out


def run_sh_commands():
    """Subcommand names parsed from run.sh's usage line."""
    src = open(os.path.join(REPO, "run.sh")).read()
    m = re.search(r'usage: run\.sh ([^"]+)"', src)
    if not m:
        return []
    names = []
    for part in m.group(1).split("|"):
        tok = part.strip().split(" ")[0].split("[")[0].strip()
        if tok and tok not in names:
            names.append(tok)
    return names


def scheduled_tasks():
    """(name, description) from ops/scheduled-tasks/*.SKILL.md frontmatter."""
    d = os.path.join(REPO, "ops/scheduled-tasks")
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".SKILL.md"):
            continue
        head = open(os.path.join(d, fn)).read(2000)
        name = re.search(r"^name:\s*(.+)$", head, re.M)
        desc = re.search(r"^description:\s*(.+)$", head, re.M)
        out.append((name.group(1).strip() if name else fn.replace(".SKILL.md", ""),
                    (desc.group(1).strip() if desc else "")[:140]))
    return out


def hooks():
    """(filename, docstring first line) for hooks/*.py."""
    d = os.path.join(REPO, "hooks")
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        src = open(os.path.join(d, fn)).read(1500)
        m = re.search(r'"""(.+?)(?:\n|""")', src)
        out.append((fn, (m.group(1).strip() if m else "")[:140]))
    return out


def export_targets():
    """Generated render paths, from record-home-gate's own parser — the same
    list the gate enforces, so this section and the enforcement can't disagree."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_rhg", os.path.join(REPO, "hooks/record-home-gate.py"))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    exact, _dirs = g.generated_paths()
    return sorted(exact)


def render_section():
    v = verbs()
    r = run_sh_commands()
    t = scheduled_tasks()
    h = hooks()
    e = export_targets()
    lines = [
        "## MACHINE INVENTORY (generated from the repo — never edit; regenerated on every export)",
        "",
        f"*Scanned live from `~/carr-system` so these counts cannot drift from the code: "
        f"**{len(v)} record verbs · {len(r)} run.sh commands · {len(t)} scheduled tasks · "
        f"{len(h)} enforcement hooks · {len(e)} generated renders**.*",
        "",
        f"### Record verbs ({len(v)}) — the write/read surface every session shares",
        "| Verb | What it does |",
        "|---|---|",
    ]
    lines += [f"| `{n}` | {d} |" for n, d in v]
    lines += ["", f"### run.sh commands ({len(r)})", "",
              "`" + "` · `".join(r) + "`", "",
              f"### Scheduled tasks ({len(t)}) — the clock summons",
              "| Task | What it does |", "|---|---|"]
    lines += [f"| `{n}` | {d} |" for n, d in t]
    lines += ["", f"### Enforcement hooks ({len(h)}) — the rails every session runs inside",
              "| Hook | Job |", "|---|---|"]
    lines += [f"| `{n}` | {d} |" for n, d in h]
    lines += ["", f"### Generated renders ({len(e)}) — files the record layer owns "
              "(never hand-edit; the gate denies it)", "",
              "`" + "` · `".join(e) + "`", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_section())
