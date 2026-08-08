#!/usr/bin/env python3
"""doctrine_inventory.py — the remaining-corpus tracker for the doctrine-store
migration (P5 bounded batches; design §6, decisions 82a2fb62 + import-door).

ONE JOB: answer "which hand-authored vault .md files are not yet in a verified
migration batch," with a suggested content_class per file. This is the ONE home
of that answer — the doctrine health row calls it with --count, the batch
planner calls it bare, and both see identical logic (rule a8c55a47).

WHAT IS EXCLUDED, and why each class leaves the corpus:
  generated renders    — retire with their exporters at cutoff (never migrate);
                         the set is PARSED from record-home-gate (single source)
  machine manifest     — CLAUDE.md/AGENTS.md bootstrap stubs + compiled rules,
                         permanent files (or verb-replaced), never store rows
  briefs               — job outputs; delivery is email/heartbeat, they retire
  _to_delete/          — already staged for deletion
  archived trees       — frozen history; migrate only on a named need
  already migrated     — any path inside a VERIFIED doctrine_migration_batch

Usage:
  .venv/bin/python pipelines/doctrine_inventory.py            # the batch plan
  .venv/bin/python pipelines/doctrine_inventory.py --count    # one number
"""
import importlib.util
import json
import os
import sys

VAULT = os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {"_to_delete", ".archived", "Output", "out"}
SKIP_PREFIXES = ("DNA/Network/briefs/",)

# ARCHIVE, not corpus (council exemption: immutable history keeps its
# byte-exact file form as evidence; it stops being WRITTEN at cutoff but never
# migrates). Anything matching these is reported separately, never planned
# into a batch:
#   .generations/          Drive-versioning backup snapshots of renders
#   _processed/            idea-inbox rows already absorbed into doctrine
#   Source Material        capture-log artifacts (the INDEX render is the log)
#   Social Media week/batch archives — published output history
#   Automation/Learning    job-output snapshots, latest-N files
ARCHIVE_MARKERS = (".generations/", "/_processed/",
                   "Marketing/Source Material/",
                   "DNA/Marketing/Source Material/")
ARCHIVE_PREFIXES = ("Automation/Learning/",)
# RENDER TREES built by repo pipelines OUTSIDE exporters/targets.py (the graph
# pages and radar feeds are run.sh graph / renewal-feed output) — renders
# retire with their generators at cutoff; they never migrate:
RENDER_PREFIXES = ("Graph/", "Automation/radar/")
import re as _re
ARCHIVE_PATTERNS = (_re.compile(r"(^|/)(batch|week)-20\d\d-"),
                    _re.compile(r"/week-20\d\d-\d\d-\d\d/"),
                    _re.compile(r"-latest\.md$"))
MANIFEST_EXACT = {"CLAUDE.md", "AGENTS.md",
                  "00_Context/compiled-rules-joe.md",
                  "DNA/compiled-rules-shared.md", "DNA/compiled-rules-dell.md"}

# folder → suggested content_class; first prefix match wins, else 'reference'
CLASS_BY_PREFIX = [
    ("00_Context/idea-inbox/", "distillation"),
    ("00_Context/", "sop"),
    ("DNA/Clients/prospects/", "dossier_narrative"),
    ("DNA/Clients/", "sop"),
    ("DNA/Reference/", "reference"),
    ("DNA/Team/", "sop"),
    ("DNA/Leads/", "playbook"),
    ("DNA/Network/", "playbook"),
    ("DNA/Deal Management/", "sop"),
    ("DNA/Marketing/", "playbook"),
    ("Marketing/", "playbook"),
    ("Automation/", "sop"),
    ("DNA/", "playbook"),
]


def generated_set():
    spec = importlib.util.spec_from_file_location(
        "_rhg", os.path.join(REPO, "hooks", "record-home-gate.py"))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    exact, _dirs = g.generated_paths()
    return exact


def migrated_set():
    try:
        sys.path.insert(0, REPO)
        from lib.record_sources import _connect
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("select source_paths from doctrine_migration_batch "
                        "where state='verified'")
            out = set()
            for (paths,) in cur.fetchall():
                for p in paths or []:
                    rel = p
                    for pref in (VAULT + os.sep,
                                 VAULT.replace("/Library/CloudStorage/"
                                               "GoogleDrive-joe.bookout.carr.us@gmail.com",
                                               "") + os.sep):
                        if rel.startswith(pref):
                            rel = rel[len(pref):]
                    out.add(rel)
            return out, None
    except Exception as exc:
        return set(), f"{type(exc).__name__}: store unreachable — migrated set UNKNOWN"


def classify(rel):
    for pref, cls in CLASS_BY_PREFIX:
        if rel.startswith(pref):
            return cls
    return "reference"


def main():
    count_only = "--count" in sys.argv
    gen = generated_set()
    migrated, mig_err = migrated_set()
    remaining, archive = [], []
    for root, dirs, files in os.walk(VAULT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if not f.lower().endswith(".md"):
                continue
            rel = os.path.relpath(os.path.join(root, f), VAULT).replace(os.sep, "/")
            if rel in gen or rel in MANIFEST_EXACT or rel in migrated:
                continue
            if any(rel.startswith(p) for p in SKIP_PREFIXES):
                continue
            if any(rel.startswith(p) for p in RENDER_PREFIXES):
                continue
            if (any(m in rel for m in ARCHIVE_MARKERS)
                    or any(rel.startswith(p) for p in ARCHIVE_PREFIXES)
                    or any(p.search(rel) for p in ARCHIVE_PATTERNS)):
                archive.append(rel)
                continue
            remaining.append((classify(rel), rel))

    if mig_err and count_only:
        # A failed read is not an empty register (0034 doctrine): the count is
        # unknowable, and -1 says so louder than a plausible number.
        print(json.dumps({"remaining": -1, "error": mig_err}))
        return
    if count_only:
        by = {}
        for cls, _ in remaining:
            by[cls] = by.get(cls, 0) + 1
        print(json.dumps({"remaining": len(remaining), "archive": len(archive),
                          "by_class": by}))
        return

    if mig_err:
        print(f"WARNING: {mig_err} — plan may repeat already-migrated files")
    remaining.sort()
    cur = None
    for cls, rel in remaining:
        if cls != cur:
            n = sum(1 for c, _ in remaining if c == cls)
            print(f"\n== {cls} ({n}) ==")
            cur = cls
        print(f"  {rel}")
    print(f"\ntotal corpus remaining: {len(remaining)} "
          f"(batches of ~10 → {(len(remaining) + 9) // 10} batches)")
    print(f"archive class (stays as files, stops being written at cutoff): {len(archive)}")


if __name__ == "__main__":
    main()
