#!/usr/bin/env python3
"""
build-isometric-map.py — the CARR system as a runtime topology you can look at.

WHAT THIS IS
  A single self-contained HTML page: an isometric map of ~/carr-system where each
  MODULE is a building on a grid, each named FLOW is a traced path across those
  buildings, and animated payloads travel the paths. Every module and every flow
  cites the real files it is made of, and the citations are CHECKED AT BUILD TIME.

WHERE IT CAME FROM (provenance, 2026-08-14)
  Joe handed over an X post by @JayScambler quoting @fleetingbits. The outer post
  was a prompt, verbatim:

      "Analyze [repo] at latest main. Create an isometric system map with legend
       and explainer panel. Show infrastructure as varied 3D buildings on a grid,
       with dependencies and payloads tracing real control/data paths. Cite files."

  The quoted post named the actual purpose: turning a codebase into a visual so
  you can DISCUSS it with an assistant more easily. That purpose, not the
  prettiness, is what this file is built for.

WHY IT IS NOT `run.sh graph` OR `graphify-out/`
  We already had two graph surfaces and neither does this job.
    - `run.sh graph-system` emits Obsidian wikilink stubs so Obsidian's
      force-directed view can draw the VAULT. It draws documents, not runtime.
    - `graphify-out/graph.json` is a real typed AST graph of this repo (7,445
      nodes, 11,048 edges, relations `calls` / `imports` / `reads_from` /
      `triggers`), which is genuinely valuable and is NOT re-derived here. But it
      is pinned to commit 56f2b98, it has no spatial layout, no legend, no
      explainer, no animation, and 7,445 nodes is not a thing a human reads.
  The gap was never extraction. The gap was PRESENTATION at an altitude a person
  can hold in their head. That is the whole of what this adds.

THE HONEST SPLIT — read this before trusting anything on the page
  DERIVED (recomputed every build, cannot go stale silently):
    · the file counts on every module, from real globs over the working tree
    · the existence of every cited file — a missing one renders as a red MISSING
      chip on the page instead of quietly reading as fine
    · the commit the map was built at
  CURATED (written down by a human/session, and therefore CAN go stale):
    · which modules exist and what they mean
    · the four flows and the order of their steps
  This split is printed on the page itself, in the HOW IT'S BUILT tab. A map that
  hides which half is which is worse than no map (rule d5dcfe26 — a dated
  artifact is not self-updating; rule b01edd26 — no hardcoded count a later edit
  can falsify).

PLACEMENT (rule c1547ed1)
  Code lives here, in the repo, versioned. Output is derived and goes to out/ by
  default. Nothing is written into the vault by this script.

USAGE
  python3 pipelines/build-isometric-map.py [--out PATH] [--open]
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# THE TOPOLOGY — the curated half. Grid coords are (col, row) on the iso plane.
#
#   glob      : how the module's file count is DERIVED. Never a hardcoded number.
#   shape     : block | stack | prisms. Drives the 3-D form, and the mapping is
#               stated in the legend so the shape is readable, not decorative.
#               block  = one coherent surface        (a single dispatcher)
#               stack  = an accumulator, layered     (a store, a render engine)
#               prisms = many small independent units (40 gates, 56 pipelines)
#   cites     : real repo-relative paths. CHECKED at build time.
# ─────────────────────────────────────────────────────────────────────────────

GROUPS: list[tuple[str, list[str]]] = [
    ("ENTRY AND CONTROL", ["HK", "MC", "RS"]),
    ("THE RECORD LOOP", ["VB", "EN", "RU", "DB"]),
    ("WHAT COMES OUT", ["EX", "PL", "VA"]),
]

MODULES: dict[str, dict[str, Any]] = {
    "HK": dict(
        name="Hooks & gates", grid=(0, 0.6), foot=(1.15, 1.15), shape="prisms",
        glob=["hooks/*.py"],
        blurb="The only layer the model cannot reason its way around, because "
              "the harness runs it and not the model. Fires on SessionStart, "
              "PreToolUse and Stop. Some of these can DENY an action outright; "
              "most only inform.",
        cites=["hooks/guard-unattended.py", "hooks/session-brief.py",
               "hooks/gate-integrity.py", "hooks/git-writer-gate.py",
               "hooks/record-home-gate.py"],
        note="15 of these can actually deny. The rest observe. "
             "hooks/ledger-boundary-sweep.py looks like a gate on a file listing "
             "and is registered nowhere, so it blocks nothing.",
    ),
    "MC": dict(
        name="MCP server", grid=(3.0, 0), foot=(1.2, 1.2), shape="block",
        glob=["mcp-server/src/*.js"],
        blurb="The transport. A Cloudflare Worker speaking JSON-RPC over "
              "streamable HTTP against Neon Postgres. Resolves the caller's "
              "profile, then hands the verb name to the registry. Auth is not "
              "done here.",
        cites=["mcp-server/src/mcp.js", "mcp-server/src/tools.js",
               "mcp-server/src/doctrine.js"],
        note="Counted files are hand-written src only. A naive find under "
             "mcp-server/ returns 1,444 because node_modules and .wrangler "
             "build output live there too.",
    ),
    "RS": dict(
        name="run.sh", grid=(0, 3.6), foot=(1.0, 1.0), shape="block",
        glob=["run.sh"],
        blurb="The operator's front door. ~24 subcommands, each shelling out to "
              "one script. This is how a human or a launchd job starts work that "
              "is not a verb call.",
        cites=["run.sh", "bin/nightly.sh", "bin/refresh-rules.sh",
               "bin/worktree.sh"],
        note="Its own top-of-file comment undercounts its subcommands. The case "
             "block and the usage line are the truth; the header is stale.",
    ),
    "VB": dict(
        name="Verb registry", grid=(3.0, 2.6), foot=(1.25, 1.25), shape="stack",
        glob=["mcp-server/src/tools.js"],
        blurb="A flat name to handler map — every verb the system has. A call "
              "clears two gates before it reaches a handler: is this verb in the "
              "caller's profile, and is this specific payload allowed on that "
              "profile.",
        cites=["mcp-server/src/tools.js", "mcp-server/src/mcp.js"],
        note="call-verb has two definitions on purpose. The real dispatch "
             "intercepts by name in mcp.js; the registry entry exists only to "
             "publish a schema and throws if reached directly.",
    ),
    "EN": dict(
        name="Write envelope", grid=(5.4, 1.4), foot=(0.95, 0.95), shape="block",
        glob=[],
        blurb="Every write goes through one idempotency gate. It requires a key, "
              "hashes the canonical args, replays a prior identical call instead "
              "of repeating it, and refuses a reused key carrying different args. "
              "Then it writes the event row alongside the record.",
        cites=["mcp-server/src/tools.js"],
        note="Not a file of its own — it is withEnvelope() inside tools.js, and "
             "it is drawn as its own building because it is a distinct stage "
             "every single write passes through.",
    ),
    "RU": dict(
        name="Rule store", grid=(2.4, 5.2), foot=(1.05, 1.05), shape="stack",
        glob=[],
        blurb="Where taught rules live. A rule lands as PROPOSED and binds "
              "nobody; a human calls activate-rule to make it ACTIVE. This is "
              "the only path by which a lesson reaches the next session on its "
              "own.",
        cites=["mcp-server/src/tools.js", "mcp-server/src/doctrine.js"],
        note="A table in Postgres, not a folder. Drawn separately from the "
             "record layer because the rule loop is the system's own memory and "
             "reads differently from client records.",
    ),
    "DB": dict(
        name="Record layer", grid=(5.6, 3.8), foot=(1.4, 1.4), shape="stack",
        glob=["migrations/*.sql"],
        blurb="Postgres. The one source of truth for clients, deals, vendors, "
              "loops, decisions, rules and every event. Content goes in through "
              "verbs and never into a markdown file.",
        cites=["migrations/0001_init.sql", "db/schema.sql",
               "lib/record_sources.py"],
        note="Building height tracks the migration count, which is the honest "
             "measure of how much schema this system actually carries.",
    ),
    "EX": dict(
        name="Exporters", grid=(5.0, 6.2), foot=(1.1, 1.1), shape="stack",
        glob=["exporters/*.py"],
        blurb="The render engine. Every export runs the same poison gate: build "
              "to a temp file, validate it, keep the generation, atomically "
              "rename it into place, then record the run.",
        cites=["exporters/run_exports.py", "exporters/targets.py",
               "exporters/common.py"],
        note="DRAFT by default. Only CARR_EXPORT_LIVE=1 writes to the vault — "
             "which is exactly why a raw export once produced a rule that bound "
             "nobody.",
    ),
    "PL": dict(
        name="Pipelines", grid=(2.2, 7.4), foot=(1.2, 1.2), shape="prisms",
        glob=["pipelines/*.py", "generators/*.py"],
        blurb="Batch jobs that read the record layer and write a derived surface "
              "— boards, briefs, feeds, queues. They are surfaces, not pingers: "
              "no send path and no write path back into the record.",
        cites=["pipelines/review_queue.py", "pipelines/brief_pack.py",
               "generators/build-deal-room.py", "lib/record_sources.py"],
        note="",
    ),
    "VA": dict(
        name="The vault", grid=(7.4, 6.4), foot=(1.0, 1.0), shape="block",
        glob=[],
        blurb="Google Drive. Where a render finally becomes something Joe or "
              "Dell opens — the boards, the compiled rules, the Deal Room. "
              "Everything here is GENERATED and nothing here is hand-edited.",
        cites=["exporters/targets.py", "run.sh"],
        note="Outside the repo, so it has no file count. It is on the map "
             "because a render that never reaches it has not reached anybody.",
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# THE FLOWS — traced control paths. `steps` are module ids in execution order.
# `kind` per leg: flow (solid, normal control) · retry (dashed, refusal or
# re-entry) · feedback (solid, closes a loop back upstream).
# ─────────────────────────────────────────────────────────────────────────────

FLOWS: list[dict[str, Any]] = [
    dict(
        id="boot", name="Session boot", view="boot",
        tagline="Hook fires → rules read from the store → session "
                "recites what binds it.",
        body="A session starts and a SessionStart hook injects the directive to "
             "call standing-context before anything else. That verb reads the "
             "rule store directly and returns both rule sets with the counts, "
             "the open action-required items, and the doctrine pointer. The "
             "session states them back in its first response, so the human can "
             "see what is binding it.",
        steps=[("HK", "flow"), ("MC", "flow"), ("VB", "flow"), ("DB", "flow")],
        back=[("DB", "HK", "feedback")],
        sources=["hooks/session-brief.py", "mcp-server/src/doctrine.js"],
        payload="No named type — an inline JSON object assembled from three "
                "queries",
        caveat="The recitation is compliance-dependent. No gate enforces that "
               "the session actually calls the verb or actually recites it.",
    ),
    dict(
        id="teach", name="Taught rule loop", view="teach",
        tagline="Human says it once → stored proposed → activated "
                "→ rendered to both brains.",
        body="A partner states a standing lesson. The teach verb records it in "
             "his own words as PROPOSED, binding nobody. He calls activate-rule "
             "and it becomes ACTIVE. An hourly job then re-exports the compiled "
             "rules into the vault so the other brain sees them too. This is the "
             "only loop by which a correction outlives the session that heard it.",
        steps=[("MC", "flow"), ("VB", "flow"), ("EN", "flow"), ("RU", "flow"),
               ("EX", "flow"), ("VA", "flow")],
        back=[("RU", "DB", "retry")],
        sources=["mcp-server/src/tools.js", "bin/refresh-rules.sh",
                 "exporters/targets.py"],
        payload="rule row (SQL) · no named application type",
        caveat="refresh-rules.sh is the path, not a raw export. It sets "
               "CARR_EXPORT_LIVE=1; a plain export writes a draft that reaches "
               "nobody.",
    ),
    dict(
        id="write", name="Record write", view="write",
        tagline="Verb call → two gates → idempotency envelope → "
                "record and event, one transaction.",
        body="Any write verb resolves the caller's profile, checks the verb is "
             "in it, then checks this particular payload is allowed on it. It "
             "opens a transaction, passes through the idempotency envelope — "
             "which replays an identical prior call rather than repeating it — "
             "writes the record and its event together, and commits. A refused "
             "call never reaches a handler.",
        steps=[("MC", "flow"), ("VB", "flow"), ("EN", "flow"), ("DB", "flow")],
        back=[("VB", "MC", "retry")],
        sources=["mcp-server/src/mcp.js", "mcp-server/src/tools.js",
                 "migrations/0001_init.sql"],
        payload="tool_call envelope → record row + event row (all SQL "
                "shapes)",
        caveat="An ok:true confirms the call parsed. It never confirms the "
               "values landed in the right fields.",
    ),
    dict(
        id="render", name="Derived render", view="render",
        tagline="Job fires → pipeline reads the record → surface "
                "rewritten in the vault.",
        body="A launchd job or an operator runs a subcommand. The pipeline reads "
             "the record layer through a view rather than any file, builds the "
             "surface, and writes it into the vault atomically. The output is "
             "derived every time, which is why hand-editing one of these files "
             "is always the wrong move — the next run overwrites it.",
        steps=[("RS", "flow"), ("PL", "flow"), ("DB", "flow"), ("PL", "feedback"),
               ("VA", "flow")],
        back=[],
        sources=["run.sh", "generators/build-deal-room.py",
                 "lib/record_sources.py"],
        payload="plain dict of rows from a Postgres view · no named type",
        caveat="Reads records by default. There is a legacy file mode, and which "
               "one is live is a runtime decision, not a constant.",
    ),
]

HONESTY: list[str] = [
    "hooks/SETTINGS-BLOCK.md documents 6 hooks. The directory holds 40 and "
    "roughly 29 are actually registered. It is stale; read the live settings "
    "files instead.",
    "hooks/session-brief.py is wired ONLY into the two VAULT project settings "
    "files, not into this repo's .claude/settings.json. Reading the obvious "
    "settings file alone would tell you it never runs.",
    "hooks/ledger-boundary-sweep.py is written, is documented as a blocking "
    "gate, and is registered nowhere. It blocks nothing today.",
    "ops/launchd/*.plist are TEMPLATES carrying an unresolved placeholder. The "
    "jobs that actually run are the resolved copies in ~/Library/LaunchAgents.",
    "ops/launchd/com.carr.fetch-allowlist.plist has no installed counterpart. It "
    "is not scheduled, whatever its presence in the repo suggests.",
    "No flow on this map carries a named payload type. There is no Pydantic, no "
    "TypeScript type layer, no generated SQL types anywhere in these four paths "
    "— every payload is a raw JSON-Schema object inline in tools.js or a plain "
    "dict. That is a real property of the codebase, not a gap in the survey.",
]

# ─────────────────────────────────────────────────────────────────────────────
# Isometric geometry
# ─────────────────────────────────────────────────────────────────────────────

TILE_W, TILE_H = 128.0, 74.0
ORIGIN_X, ORIGIN_Y = 660.0, 74.0
FLOOR = 18.0          # px of building height per "storey" unit
GRID_N = 9

# Height is deliberately COMPRESSED. The record layer carries 131 migrations
# against run.sh's 1 file; drawn linearly it becomes a tower that occludes
# everything behind it and the map stops being readable. Log-shaped with a
# shallow coefficient keeps the ordering honest and the scene legible.
HEIGHT_COEF = 0.45


def iso(gx: float, gy: float) -> tuple[float, float]:
    """Grid coords to screen coords. Classic 2:1 isometric."""
    return (ORIGIN_X + (gx - gy) * TILE_W / 2.0,
            ORIGIN_Y + (gx + gy) * TILE_H / 2.0)


def height_units(n_files: int) -> float:
    """Storeys from file count, log-shaped so a 131-file module does not tower
    twenty times over a 3-file one. Floor of 1.1 so nothing is flat."""
    if n_files <= 0:
        return 1.1
    import math
    return 1.1 + math.log(1 + n_files, 2) * HEIGHT_COEF


def count_files(globs: list[str]) -> int:
    seen: set[Path] = set()
    for pat in globs:
        for p in REPO.glob(pat):
            if p.is_file() and "__pycache__" not in p.parts:
                seen.add(p)
    return len(seen)


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), *args],
                              capture_output=True, text=True,
                              timeout=15).stdout.strip()
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Build the model
# ─────────────────────────────────────────────────────────────────────────────

def build_model() -> dict[str, Any]:
    mods: dict[str, Any] = {}
    missing_total = 0
    for mid, spec in MODULES.items():
        n = count_files(spec["glob"]) if spec["glob"] else 0
        cites: list[dict[str, Any]] = []
        for c in spec["cites"]:
            ok = (REPO / c).exists()
            if not ok:
                missing_total += 1
            cites.append({"path": c, "ok": ok})
        gx, gy = spec["grid"]
        fw, fh = spec["foot"]
        h = height_units(n) * FLOOR if n else 1.35 * FLOOR
        cx, cy = iso(gx + fw / 2.0, gy + fh / 2.0)
        mods[mid] = {
            "id": mid, "name": spec["name"], "grid": [gx, gy], "foot": [fw, fh],
            "shape": spec["shape"], "files": n, "blurb": spec["blurb"],
            "note": spec.get("note", ""), "cites": cites,
            "h": round(h, 1), "cx": round(cx, 1), "cy": round(cy, 1),
        }

    flows: list[dict[str, Any]] = []
    for f in FLOWS:
        legs: list[dict[str, Any]] = []
        seq = f["steps"]
        for i in range(len(seq) - 1):
            a, _ = seq[i]
            b, kind = seq[i + 1]
            legs.append({"a": a, "b": b, "kind": kind})
        for (a, b, kind) in f["back"]:
            legs.append({"a": a, "b": b, "kind": kind})
        srcs = [{"path": s, "ok": (REPO / s).exists()} for s in f["sources"]]
        missing_total += sum(1 for s in srcs if not s["ok"])
        flows.append({**{k: f[k] for k in
                         ("id", "name", "tagline", "body", "payload", "caveat")},
                      "legs": legs,
                      "nodes": [s[0] for s in seq],
                      "sources": srcs})

    return {
        "modules": mods,
        "flows": flows,
        "groups": GROUPS,
        "honesty": HONESTY,
        "missing": missing_total,
        "commit": (git("rev-parse", "--short", "HEAD") or "unknown").upper(),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        "dirty": bool(git("status", "--porcelain")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SVG
# ─────────────────────────────────────────────────────────────────────────────

def svg_grid() -> str:
    out = []
    for i in range(GRID_N + 1):
        x1, y1 = iso(i, 0); x2, y2 = iso(i, GRID_N)
        out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                   f'y2="{y2:.1f}" class="g"/>')
        x1, y1 = iso(0, i); x2, y2 = iso(GRID_N, i)
        out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                   f'y2="{y2:.1f}" class="g"/>')
    return "".join(out)


def _prism(cx: float, cy: float, w: float, h: float) -> str:
    """One narrow diamond-topped spike, for the `prisms` form."""
    return (f'<polygon class="f-l" points="{cx-w:.1f},{cy-h*0.55:.1f} '
            f'{cx:.1f},{cy-h:.1f} {cx:.1f},{cy-h*0.16:.1f} '
            f'{cx:.1f},{cy:.1f} {cx-w:.1f},{cy-h*0.30:.1f}"/>'
            f'<polygon class="f-r" points="{cx+w:.1f},{cy-h*0.55:.1f} '
            f'{cx:.1f},{cy-h:.1f} {cx:.1f},{cy:.1f} '
            f'{cx+w:.1f},{cy-h*0.30:.1f}"/>')


def svg_building(m: dict[str, Any]) -> str:
    gx, gy = m["grid"]; fw, fh = m["foot"]; h = m["h"]
    n = iso(gx, gy); e = iso(gx + fw, gy)
    s = iso(gx + fw, gy + fh); w = iso(gx, gy + fh)
    parts = [f'<g class="bld" data-id="{m["id"]}">']

    # ground shadow, so a building reads as sitting on the plane
    parts.append(f'<polygon class="shadow" points="{n[0]:.1f},{n[1]:.1f} '
                 f'{e[0]:.1f},{e[1]:.1f} {s[0]:.1f},{s[1]:.1f} '
                 f'{w[0]:.1f},{w[1]:.1f}"/>')

    if m["shape"] == "prisms":
        # many small independent units — a cluster, not one mass
        cols = [(-0.62, -0.30), (-0.20, 0.10), (0.24, -0.16), (0.62, 0.26)]
        for k, (ox, oy) in enumerate(cols):
            px, py = iso(gx + fw / 2 + ox, gy + fh / 2 + oy)
            parts.append(_prism(px, py, 15.0, h * (0.72 + 0.14 * (k % 3))))
    else:
        top = [(p[0], p[1] - h) for p in (n, e, s, w)]
        parts.append('<polygon class="f-r" points="'
                     f'{e[0]:.1f},{e[1]:.1f} {s[0]:.1f},{s[1]:.1f} '
                     f'{top[2][0]:.1f},{top[2][1]:.1f} '
                     f'{top[1][0]:.1f},{top[1][1]:.1f}"/>')
        parts.append('<polygon class="f-l" points="'
                     f'{w[0]:.1f},{w[1]:.1f} {s[0]:.1f},{s[1]:.1f} '
                     f'{top[2][0]:.1f},{top[2][1]:.1f} '
                     f'{top[3][0]:.1f},{top[3][1]:.1f}"/>')
        if m["shape"] == "stack":
            # layered slabs — the accumulator read
            bands = max(2, min(6, int(h / FLOOR)))
            for b in range(1, bands):
                dy = h * b / bands
                parts.append(f'<line class="band" x1="{w[0]:.1f}" '
                             f'y1="{w[1]-dy:.1f}" x2="{s[0]:.1f}" '
                             f'y2="{s[1]-dy:.1f}"/>')
                parts.append(f'<line class="band" x1="{s[0]:.1f}" '
                             f'y1="{s[1]-dy:.1f}" x2="{e[0]:.1f}" '
                             f'y2="{e[1]-dy:.1f}"/>')
        parts.append('<polygon class="f-t" points="'
                     + " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in top) + '"/>')

    tx, ty = iso(gx + fw / 2.0, gy + fh / 2.0)
    parts.append(f'<text class="blab" x="{tx:.1f}" y="{ty - h - 9:.1f}">'
                 f'{m["id"]}</text>')
    parts.append("</g>")
    return "".join(parts)


def elbow(a: dict[str, Any], b: dict[str, Any],
          occupied: list[tuple[float, float, float, float, str]],
          ) -> list[tuple[float, float]]:
    """L-shaped route in grid space, choosing the variant that crosses fewer
    building footprints. Paths run on the ground plane, under the buildings."""
    ax, ay = a["grid"][0] + a["foot"][0] / 2, a["grid"][1] + a["foot"][1] / 2
    bx, by = b["grid"][0] + b["foot"][0] / 2, b["grid"][1] + b["foot"][1] / 2

    def crossings(pts):
        c = 0
        for (ox, oy, ow, oh, oid) in occupied:
            if oid in (a["id"], b["id"]):
                continue
            for i in range(len(pts) - 1):
                x1, y1 = pts[i]; x2, y2 = pts[i + 1]
                for t in range(1, 12):
                    px = x1 + (x2 - x1) * t / 12.0
                    py = y1 + (y2 - y1) * t / 12.0
                    if ox - .2 <= px <= ox + ow + .2 and oy - .2 <= py <= oy + oh + .2:
                        c += 1
                        break
        return c

    v1 = [(ax, ay), (ax, by), (bx, by)]
    v2 = [(ax, ay), (bx, ay), (bx, by)]
    pts = v1 if crossings(v1) <= crossings(v2) else v2
    return [iso(x, y) for (x, y) in pts]


def build_paths(model: dict[str, Any]) -> dict[str, Any]:
    occupied = [(m["grid"][0], m["grid"][1], m["foot"][0], m["foot"][1], m["id"])
                for m in model["modules"].values()]
    out: dict[str, Any] = {}
    for f in model["flows"]:
        legs = []
        for leg in f["legs"]:
            a = model["modules"][leg["a"]]; b = model["modules"][leg["b"]]
            pts = elbow(a, b, occupied)
            legs.append({"kind": leg["kind"], "a": leg["a"], "b": leg["b"],
                         "pts": [[round(x, 1), round(y, 1)] for x, y in pts]})
        out[f["id"]] = legs
    return out


# ─────────────────────────────────────────────────────────────────────────────
# HTML
# ─────────────────────────────────────────────────────────────────────────────

def esc(s: str) -> str:
    return html.escape(s, quote=True)


def render(model: dict[str, Any], paths: dict[str, Any]) -> str:
    m = model["modules"]

    rail = []
    for title, ids in model["groups"]:
        rail.append(f'<div class="grp">{esc(title)}</div>')
        for mid in ids:
            mod = m[mid]
            cnt = mod["files"] if mod["files"] else "—"
            rail.append(
                f'<button class="mrow" data-mod="{mid}">'
                f'<span class="mcode">{mid}</span>'
                f'<span class="mname">{esc(mod["name"])}</span>'
                f'<span class="mcount">{cnt}</span></button>')

    # depth order: farther (smaller gx+gy) drawn first
    order = sorted(m.values(), key=lambda x: x["grid"][0] + x["grid"][1])
    bldgs = "".join(svg_building(b) for b in order)

    warn = ""
    if model["missing"]:
        warn = (f'<div class="warn">{model["missing"]} cited file(s) '
                f'DID NOT RESOLVE at build time — shown in red below.</div>')

    dirty = ' · <span class="dirty">UNCOMMITTED</span>' if model["dirty"] else ""

    data = json.dumps({"modules": m, "flows": model["flows"],
                       "paths": paths, "honesty": model["honesty"]},
                      separators=(",", ":"))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CARR System — Runtime Topology</title>
<style>
:root{{
  --bg:#0a0b0e; --panel:#0d0f13; --line:#1e232c; --grid:#232935;
  --face-t:#2b3444; --face-l:#161c27; --face-r:#1f2735; --band:#39445a;
  --ink:#eef1f6; --dim:#828c9e; --accent:#3b82f6; --accent-dim:#1d4ed8;
  --bad:#f0616d; --good:#3ddc97;
}}
*{{box-sizing:border-box}}
html,body{{margin:0;height:100%;background:var(--bg);color:var(--ink);
  font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}}
body{{display:flex;flex-direction:column;overflow:hidden}}

.top{{display:flex;border-bottom:1px solid var(--line);flex:0 0 auto;
  overflow-x:auto}}
.cell{{padding:12px 20px;border-right:1px solid var(--line);white-space:nowrap}}
.cell .k{{color:var(--dim);font-size:10px;letter-spacing:.13em}}
.cell .v{{font-size:17px;margin-top:3px}}
.cell .sub{{color:var(--dim);font-size:10.5px;margin-top:3px}}
.ctrls{{margin-left:auto;display:flex;align-items:center;gap:9px;padding:0 16px}}
select,button.btn{{background:#12161d;color:var(--ink);border:1px solid #2b323f;
  border-radius:3px;padding:7px 12px;font:inherit;font-size:11px;cursor:pointer}}
button.btn:hover,select:hover{{border-color:#41506b}}
button.btn[aria-pressed=true]{{background:var(--accent);border-color:var(--accent);
  color:#fff}}

.main{{flex:1;display:flex;min-height:0}}

.rail{{width:270px;flex:0 0 270px;border-right:1px solid var(--line);
  overflow-y:auto;padding:16px 14px}}
.rail h2{{font-size:10px;letter-spacing:.16em;color:var(--dim);margin:0 0 14px;
  font-weight:400}}
.grp{{font-size:9.5px;letter-spacing:.15em;color:var(--dim);margin:18px 0 8px}}
.mrow{{display:flex;align-items:center;gap:10px;width:100%;text-align:left;
  background:#10141a;border:1px solid #222836;border-radius:3px;padding:9px 11px;
  margin-bottom:6px;color:var(--ink);font:inherit;cursor:pointer}}
.mrow:hover{{border-color:#3d4a63}}
.mrow[aria-current=true]{{border-color:var(--accent);background:#101a2c}}
.mcode{{font-size:9.5px;color:var(--dim);width:20px}}
.mname{{flex:1;font-size:12px}}
.mcount{{font-size:11px;color:var(--dim)}}

.stage{{flex:1;display:flex;flex-direction:column;min-width:0;position:relative}}
.stagehead{{padding:16px 24px 0}}
.stagehead .k{{font-size:9.5px;letter-spacing:.16em;color:var(--dim)}}
.stagehead h1{{font-size:25px;font-weight:400;margin:6px 0 0}}
.live{{position:absolute;right:26px;top:20px;font-size:10px;letter-spacing:.12em;
  color:var(--dim);display:flex;align-items:center;gap:7px}}
.dot{{width:7px;height:7px;border-radius:50%;background:var(--accent)}}
.dot.off{{background:#3a4356}}
svg{{flex:1;width:100%;min-height:0;display:block}}
.g{{stroke:var(--grid);stroke-width:.6}}
.shadow{{fill:#000;opacity:.34}}
.f-t{{fill:var(--face-t);stroke:#48566e;stroke-width:.7}}
.f-l{{fill:var(--face-l);stroke:#333d4f;stroke-width:.7}}
.f-r{{fill:var(--face-r);stroke:#333d4f;stroke-width:.7}}
.band{{stroke:var(--band);stroke-width:.8;opacity:.55}}
.blab{{fill:#fff;font-size:11.5px;text-anchor:middle;letter-spacing:.06em}}
.bld{{cursor:pointer}}
.bld.dim{{opacity:.42}}
.bld.hot .f-t{{fill:#31456a;stroke:var(--accent)}}
.bld.hot .f-l,.bld.hot .f-r{{stroke:var(--accent-dim)}}
.edge{{fill:none;stroke:var(--accent);stroke-width:1.7}}
.edge.retry{{stroke:#93a0b5;stroke-dasharray:6 5;stroke-width:1.3}}
.edge.feedback{{stroke:#7aa2f7;stroke-width:1.7}}
.pay{{fill:var(--accent)}}
.payring{{fill:none;stroke:var(--accent);stroke-width:1.4;opacity:.8}}

.legend{{display:flex;gap:26px;padding:12px 24px;border-top:1px solid var(--line);
  color:var(--dim);font-size:11px;flex-wrap:wrap;flex:0 0 auto}}
.legend b{{color:var(--ink);font-weight:400}}
.swatch{{display:inline-block;width:22px;height:0;border-top:2px solid var(--accent);
  vertical-align:middle;margin-right:6px}}
.swatch.d{{border-top:2px dashed #93a0b5}}
.swatch.dot{{width:8px;height:8px;border:0;border-radius:50%;
  background:var(--accent)}}
.foot{{padding:9px 24px 12px;color:#5d6678;font-size:10.5px;
  border-top:1px solid var(--line);flex:0 0 auto}}

.side{{width:390px;flex:0 0 390px;border-left:1px solid var(--line);
  display:flex;flex-direction:column;min-height:0}}
.tabs{{display:flex;border-bottom:1px solid var(--line);flex:0 0 auto}}
.tab{{flex:1;padding:13px 8px;text-align:center;font-size:10.5px;
  letter-spacing:.12em;color:var(--dim);cursor:pointer;background:none;
  border:0;font-family:inherit}}
.tab[aria-selected=true]{{background:#fff;color:#000}}
.pane{{padding:20px 22px;overflow-y:auto;flex:1}}
.pane[hidden]{{display:none}}
.lbl{{font-size:9.5px;letter-spacing:.15em;color:var(--dim);margin:22px 0 8px}}
.lbl:first-child{{margin-top:0}}
.pane h3{{font-size:21px;font-weight:400;margin:0 0 8px}}
.tag{{color:#9fb0c8;margin:0 0 16px}}
.body{{color:#d3dae6;margin:0 0 4px}}
.chip{{display:inline-block;background:#12161d;border:1px solid #2b323f;
  border-radius:3px;padding:5px 10px;margin:0 6px 6px 0;font-size:11px}}
.chip.bad{{border-color:var(--bad);color:var(--bad)}}
.chip.bad::after{{content:" — MISSING"}}
.caveat{{border-left:2px solid #4a5568;padding:9px 0 9px 13px;color:#9aa6b8;
  margin-top:8px}}
.warn{{background:#2a1216;border:1px solid var(--bad);color:#ffb3b9;
  padding:10px 13px;border-radius:3px;margin-bottom:16px;font-size:11.5px}}
.dirty{{color:#f0c674}}
ul.h{{margin:0;padding-left:17px;color:#aab4c4}}
ul.h li{{margin-bottom:11px}}
.split{{display:flex;gap:8px;margin-bottom:14px}}
.split div{{flex:1;background:#10141a;border:1px solid #222836;border-radius:3px;
  padding:11px}}
.split .t{{font-size:9.5px;letter-spacing:.13em;color:var(--dim);
  margin-bottom:6px}}
.split .d{{font-size:11px;color:#c2cbd8}}
@media (max-width:1200px){{.rail{{display:none}}.side{{width:320px;flex-basis:320px}}}}
</style></head><body>

<div class="top">
  <div class="cell"><div class="k">REPOSITORY</div>
    <div class="v">jbookout/carr-system</div>
    <div class="sub">{esc(model["branch"]).upper()} · {esc(model["commit"])}{dirty}</div></div>
  <div class="cell"><div class="k">RUNTIME FLOWS</div>
    <div class="v">{len(model["flows"])}</div></div>
  <div class="cell"><div class="k">MODULES</div>
    <div class="v">{len(m)}</div></div>
  <div class="cell"><div class="k">CITED FILES</div>
    <div class="v" id="citecount">—</div>
    <div class="sub">checked at build</div></div>
  <div class="ctrls">
    <select id="flowsel"></select>
    <button class="btn" id="pause" aria-pressed="false">PAUSE FLOW</button>
    <button class="btn" id="step">TRACE ONE STEP</button>
    <button class="btn" id="reset">RESET VIEW</button>
  </div>
</div>

<div class="main">
  <div class="rail"><h2>THE SYSTEM</h2>{"".join(rail)}</div>

  <div class="stage">
    <div class="stagehead">
      <div class="k">RUNTIME TOPOLOGY</div>
      <h1 id="stagetitle">—</h1>
    </div>
    <div class="live"><span class="dot" id="livedot"></span>
      <span id="livetext">PAYLOADS IN MOTION</span></div>
    <svg id="map" viewBox="0 0 1320 780" preserveAspectRatio="xMidYMid meet">
      <g id="grid">{svg_grid()}</g>
      <g id="edges"></g>
      <g id="blds">{bldgs}</g>
      <g id="pays"></g>
    </svg>
    <div class="legend">
      <span><i class="swatch"></i><b>flow</b> control passes</span>
      <span><i class="swatch d"></i><b>retry</b> refusal or re-entry</span>
      <span><i class="swatch dot"></i><b>payload</b> in transit</span>
      <span><b>height</b> = files in module</span>
      <span><b>stack</b> = accumulator ·
            <b>cluster</b> = many small units ·
            <b>block</b> = one surface</span>
    </div>
    <div class="foot">click a module for its files · change the flow to
      retrace · pause or step the payload · every path here is a real
      control path, not an illustration</div>
  </div>

  <div class="side">
    <div class="tabs">
      <button class="tab" id="t1" aria-selected="true">WHAT IT DOES</button>
      <button class="tab" id="t2" aria-selected="false">HOW IT'S BUILT</button>
    </div>
    <div class="pane" id="p1">{warn}<div id="paneA"></div></div>
    <div class="pane" id="p2" hidden>
      <div class="lbl">WHAT IS TRUE HERE</div>
      <div class="split">
        <div><div class="t">DERIVED</div><div class="d">File counts, every
          citation's existence, and the commit. Recomputed each build, so these
          cannot go stale without the page changing.</div></div>
        <div><div class="t">CURATED</div><div class="d">Which modules exist and
          the order of steps in each flow. Written by hand, so these CAN go
          stale.</div></div>
      </div>
      <div class="body">The page is generated by
        <span class="chip">pipelines/build-isometric-map.py</span>
        Re-run it and the derived half re-derives; the curated half changes only
        when someone edits the topology block at the top of that file.</div>
      <div class="lbl">WHAT A NAIVE READER WOULD GET WRONG</div>
      <ul class="h">{"".join(f"<li>{esc(x)}</li>" for x in model["honesty"])}</ul>
      <div class="lbl">WHAT THIS IS NOT</div>
      <div class="body">Not a dependency graph. <span class="chip">graphify-out/graph.json</span>
        already holds 7,445 nodes and 11,048 typed edges of this repo and is not
        re-derived here. This map is deliberately at an altitude a person can
        hold in their head — ten buildings and four paths.</div>
    </div>
  </div>
</div>

<script>
const D = {data};
let cur = D.flows[0].id, paused = false, t = 0, stepMode = false;

const sel = document.getElementById('flowsel');
D.flows.forEach(f => {{ const o = document.createElement('option');
  o.value = f.id; o.textContent = f.name; sel.appendChild(o); }});

document.getElementById('citecount').textContent =
  Object.values(D.modules).reduce((a,m)=>a+m.cites.length,0) +
  D.flows.reduce((a,f)=>a+f.sources.length,0);

function flow(id) {{ return D.flows.find(f => f.id === id); }}
const chips = a => a.map(c =>
  `<span class="chip${{c.ok?'':' bad'}}">${{c.path}}</span>`).join('');

function paneFlow(f) {{
  document.getElementById('paneA').innerHTML = `
    <div class="lbl">SELECTED FLOW</div>
    <h3>${{f.name}}</h3>
    <p class="tag">${{f.tagline}}</p>
    <p class="body">${{f.body}}</p>
    <div class="caveat">${{f.caveat}}</div>
    <div class="lbl">PATH</div>
    <div>${{f.nodes.map(n=>`<span class="chip">${{n}} ${{D.modules[n].name}}</span>`).join('')}}</div>
    <div class="lbl">SOURCE</div><div>${{chips(f.sources)}}</div>
    <div class="lbl">PAYLOAD</div><div><span class="chip">${{f.payload}}</span></div>`;
}}

function paneMod(id) {{
  const m = D.modules[id];
  document.getElementById('paneA').innerHTML = `
    <div class="lbl">SELECTED MODULE</div>
    <h3>${{m.name}}</h3>
    <p class="tag">${{m.files ? m.files + ' file(s) in the working tree'
      : 'no file count — lives outside the repo'}}</p>
    <p class="body">${{m.blurb}}</p>
    ${{m.note ? `<div class="caveat">${{m.note}}</div>` : ''}}
    <div class="lbl">FILES</div><div>${{chips(m.cites)}}</div>
    <div class="lbl">FORM</div>
    <div><span class="chip">${{m.shape}}</span>
      <span class="chip">height = ${{m.files||'n/a'}} files</span></div>`;
}}

function drawEdges() {{
  const legs = D.paths[cur], g = document.getElementById('edges');
  g.innerHTML = legs.map(l => {{
    const d = 'M' + l.pts.map(p=>p.join(' ')).join(' L');
    return `<path class="edge ${{l.kind}}" d="${{d}}"/>`;
  }}).join('');
  const on = new Set(flow(cur).nodes);
  document.querySelectorAll('.bld').forEach(b =>
    b.classList.toggle('dim', !on.has(b.dataset.id)));
}}

function lerp(legs, u) {{
  const total = legs.length; if (!total) return null;
  const i = Math.min(total - 1, Math.floor(u * total));
  const local = u * total - i, pts = legs[i].pts;
  const segs = pts.length - 1;
  const j = Math.min(segs - 1, Math.floor(local * segs));
  const lu = local * segs - j;
  return [pts[j][0] + (pts[j+1][0]-pts[j][0])*lu,
          pts[j][1] + (pts[j+1][1]-pts[j][1])*lu, i];
}}

function tick() {{
  if (!paused) t = (t + 0.0022) % 1;
  const legs = D.paths[cur], p = lerp(legs, t);
  const g = document.getElementById('pays');
  if (p) {{
    g.innerHTML = `<circle class="payring" cx="${{p[0]}}" cy="${{p[1]}}" r="7"/>
                   <circle class="pay" cx="${{p[0]}}" cy="${{p[1]}}" r="3.2"/>`;
    document.querySelectorAll('.bld').forEach(b =>
      b.classList.toggle('hot', b.dataset.id === legs[p[2]].b));
  }}
  requestAnimationFrame(tick);
}}

function setFlow(id) {{
  cur = id; sel.value = id; t = 0;
  document.getElementById('stagetitle').textContent = flow(id).name;
  drawEdges(); paneFlow(flow(id));
  document.querySelectorAll('.mrow').forEach(r =>
    r.setAttribute('aria-current','false'));
}}

sel.onchange = e => setFlow(e.target.value);
document.getElementById('pause').onclick = e => {{
  paused = !paused; e.target.setAttribute('aria-pressed', paused);
  document.getElementById('livedot').style.opacity = paused ? .3 : 1;
  document.getElementById('livetext').textContent =
    paused ? 'PAUSED' : 'PAYLOADS IN MOTION';
}};
document.getElementById('step').onclick = () => {{
  paused = true;
  document.getElementById('pause').setAttribute('aria-pressed', true);
  document.getElementById('livetext').textContent = 'PAUSED';
  const n = D.paths[cur].length;
  t = (Math.floor(t * n) + 1) % n / n + 0.0001;
}};
document.getElementById('reset').onclick = () => setFlow(D.flows[0].id);

document.querySelectorAll('.mrow').forEach(r => r.onclick = () => {{
  document.querySelectorAll('.mrow').forEach(x =>
    x.setAttribute('aria-current', String(x === r)));
  paneMod(r.dataset.mod); tab(1);
}});
document.querySelectorAll('.bld').forEach(b => b.onclick = () => {{
  paneMod(b.dataset.id); tab(1);
}});

function tab(n) {{
  document.getElementById('t1').setAttribute('aria-selected', n===1);
  document.getElementById('t2').setAttribute('aria-selected', n===2);
  document.getElementById('p1').hidden = n!==1;
  document.getElementById('p2').hidden = n!==2;
}}
document.getElementById('t1').onclick = () => tab(1);
document.getElementById('t2').onclick = () => tab(2);

setFlow(cur); tick();
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "out" / "isometric-map.html"))
    args = ap.parse_args()

    model = build_model()
    paths = build_paths(model)
    doc = render(model, paths)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")

    print(f"wrote {out}  ({len(doc):,} bytes)")
    print(f"  commit   {model['branch']} {model['commit']}"
          f"{' DIRTY' if model['dirty'] else ''}")
    print(f"  modules  {len(model['modules'])}   flows {len(model['flows'])}")
    for mid, mod in model["modules"].items():
        print(f"    {mid}  {mod['files']:>4} files  {mod['name']}")
    if model["missing"]:
        print(f"  !! {model['missing']} cited path(s) DID NOT RESOLVE")
        for mod in model["modules"].values():
            for c in mod["cites"]:
                if not c["ok"]:
                    print(f"       MISSING {c['path']}  (module {mod['id']})")
        for f in model["flows"]:
            for c in f["sources"]:
                if not c["ok"]:
                    print(f"       MISSING {c['path']}  (flow {f['id']})")
        return 1
    print("  all cited paths resolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
