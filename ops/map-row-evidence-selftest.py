#!/usr/bin/env python3
"""
map-row-evidence-selftest.py — fixtures for ops/map-row-evidence-check.py,
written before it (rule e65efc68).

WHAT IT GUARDS. ops/config/rule-enforcement-map.json is the last enforcement
inventory nothing reads. Two checks landed 2026-08-15 and neither looks at its
rule rows: ops/enforcement-coverage-check.py runs gate -> control, and
ops/audit-queue-freshness-check.py runs audit row -> tree. The map's own rows
sat unchecked, and they are the ones a session opens first.

Measured the hour this was written: fourteen rules where the map and the audit
table state different things about the same rule, in both directions.

THREE PREDICATES, none of them a judgment:

  A. A row classed as BUILT must name a control that exists in the catalog and
     whose implementation files exist on disk. This is the inverse lie — a row
     marked enforced with nothing behind it — and it is what rule ab814a26 is
     about. Hard fail.

  B. The table says ENFORCED with evidence naming a live artifact, and the map
     still says unbuilt. The table carries the positive claim, so the map is
     simply behind and the fix is one edit. Hard fail.

  C. The map claims a BUILT class while the table has the rule in the work
     queue. These are not the map being newer. They are the auditor's judgment
     that the assigned control does not COVER this rule's content — the map
     derives enforcement_class mechanically from category, so a rule sorted
     into a bucket reads as enforced whether or not the control in that bucket
     addresses it. Overturning that judgment is not a check's business, so
     these are RECORDED DEBT, in the same shape ops/enforcement-coverage-check.py
     uses: a new one fails immediately, and the recorded set may only shrink.

WHY C IS A BACKLOG AND NOT A FAILURE. Eight existed on day one. A check that
fails on day one gets muted on day one, and a muted check is worse than none.

WHAT MUST STAY TRUE:
  1. Each predicate refuses its own violation AND permits its own clean case.
  2. A rule the audit table does not carry at all is skipped, not guessed at.
     Rules activated after the audit are exactly that, and there are already two.
  3. external:, glob: and command: catalog entries are not filesystem paths and
     must never be probed as though they were.
  4. The recorded debt may only shrink: an entry since reconciled FAILS, and so
     does one naming a rule that is no longer in that state.
  5. The escape hatch works, and the exit code is the contract.

Trees are synthetic, so this measures the CHECK and not today's repo.

RUNNING IT. No database, no network, no vault:

    .venv/bin/python ops/map-row-evidence-selftest.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "ops" / "map-row-evidence-check.py"

passed = 0
failures: list[str] = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


HEADER = "id\tplain_name\tbucket\tenforcement_or_sketch\tevidence\n"


def build_tree(rows, table, catalog=None, backlog=None, files=()):
    """A synthetic repo: a map, an audit table, a backlog, and real files."""
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "ops", "config"), exist_ok=True)
    os.makedirs(os.path.join(d, "audits"), exist_ok=True)
    os.makedirs(os.path.join(d, "hooks"), exist_ok=True)
    for rel in files:
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write("# fixture\n")
    mapping = {
        "control_catalog": catalog if catalog is not None else {},
        "rule_controls": rows,
    }
    with open(os.path.join(d, "ops/config/rule-enforcement-map.json"), "w") as fh:
        json.dump(mapping, fh)
    tsv = os.path.join(d, "audits/rule-enforceability-audit-2026-08-14.tsv")
    with open(tsv, "w") as fh:
        fh.write(HEADER)
        for rid, (bucket, ev) in table.items():
            fh.write(f"{rid}\tname\t{bucket}\tsketch\t{ev}\n")
    with open(os.path.join(d, "ops/config/map-row-overstatement-backlog.json"), "w") as fh:
        json.dump(backlog if backlog is not None else {"overstated_rows": []}, fh)
    return d


def run(tree, env=None):
    p = subprocess.run([sys.executable, str(CHECK), "--repo", tree],
                       capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})))
    return p.returncode, (p.stdout or "") + (p.stderr or "")


print("\nops/map-row-evidence-check.py — the map's own rule rows must agree "
      "with the tree and the audit table")

if not CHECK.exists():
    print(f"  FAIL  the check does not exist at {CHECK}")
    print("\n1 check(s) failed: not implemented")
    sys.exit(1)

CATALOG = {"real_gate": {"implementation": ["hooks/real-gate.py"]},
           "ghost_gate": {"implementation": ["hooks/ghost-gate.py"]},
           "mixed": {"implementation": ["external:database constraints",
                                        "glob:mcp-server/test/*.test.js",
                                        "command:python3 x.py",
                                        "hooks/real-gate.py"]}}
FILES = ["hooks/real-gate.py"]

# ── A. a BUILT row must name a control backed by files that exist ───────────
rc, out = run(build_tree(
    {"aaaaaaaa": {"enforcement_class": "deny_gate", "control": "ghost_gate"}},
    {"aaaaaaaa": ("E", "built")}, CATALOG, files=FILES))
check("A: built row naming a control whose file is missing FAILS", rc != 0, out[:150])
check("A: the failure names the missing file", "ghost-gate.py" in out, out[:200])

rc, _ = run(build_tree(
    {"aaaaaaaa": {"enforcement_class": "deny_gate", "control": "real_gate"}},
    {"aaaaaaaa": ("E", "built")}, CATALOG, files=FILES))
check("A: built row whose control file exists PASSES", rc == 0)

rc, out = run(build_tree(
    {"aaaaaaaa": {"enforcement_class": "deny_gate"}},
    {"aaaaaaaa": ("E", "built")}, CATALOG, files=FILES))
check("A: built row naming NO control at all FAILS", rc != 0, out[:150])

rc, out = run(build_tree(
    {"aaaaaaaa": {"enforcement_class": "deny_gate", "control": "not_in_catalog"}},
    {"aaaaaaaa": ("E", "built")}, CATALOG, files=FILES))
check("A: built row naming a control absent from the catalog FAILS", rc != 0)

# non-path prefixes are not filesystem paths and must not be probed
rc, _ = run(build_tree(
    {"aaaaaaaa": {"enforcement_class": "schema", "control": "mixed"}},
    {"aaaaaaaa": ("E", "built")}, CATALOG, files=FILES))
check("A: external:/glob:/command: entries are never probed as paths", rc == 0,
      "a database constraint is not a file on disk")

# an unbuilt row names no control BY DEFINITION and must not trip A
rc, _ = run(build_tree(
    {"aaaaaaaa": {"enforcement_class": "unbuilt",
                  "planned_control": "someday"}},
    {"aaaaaaaa": ("U", "nothing yet")}, CATALOG, files=FILES))
check("A: an unbuilt row naming no control is fine", rc == 0,
      "unbuilt means exactly that; punishing it is how a check gets deleted")

# ── B. table says enforced, map still says unbuilt ──────────────────────────
rc, out = run(build_tree(
    {"bbbbbbbb": {"enforcement_class": "unbuilt", "planned_control": "someday"}},
    {"bbbbbbbb": ("E", "BUILT. hooks/real-gate.py does it.")}, CATALOG, files=FILES))
check("B: table=E while map=unbuilt FAILS", rc != 0, out[:150])
check("B: the failure quotes the table's evidence", "real-gate.py" in out, out[:250])

rc, _ = run(build_tree(
    {"bbbbbbbb": {"enforcement_class": "deny_gate", "control": "real_gate"}},
    {"bbbbbbbb": ("E", "BUILT.")}, CATALOG, files=FILES))
check("B: table=E and map built AGREE, so it passes", rc == 0)

rc, _ = run(build_tree(
    {"bbbbbbbb": {"enforcement_class": "unbuilt", "planned_control": "someday"}},
    {"bbbbbbbb": ("U", "nothing yet")}, CATALOG, files=FILES))
check("B: table=U and map=unbuilt AGREE, so it passes", rc == 0)

# ── C. map claims built while the table still queues the rule ───────────────
OVERSTATED = {"cccccccc": {"enforcement_class": "stop_gate", "control": "real_gate"}}
QUEUED = {"cccccccc": ("U", "the assigned control does not cover this rule")}

rc, out = run(build_tree(OVERSTATED, QUEUED, CATALOG, files=FILES))
check("C: an UNRECORDED overstatement FAILS", rc != 0, out[:150])

rc, _ = run(build_tree(OVERSTATED, QUEUED, CATALOG,
                       backlog={"overstated_rows": [{"rule": "cccccccc",
                                                     "reason": "auditor judged mismatch"}]},
                       files=FILES))
check("C: a RECORDED overstatement passes", rc == 0,
      "eight existed on day one; a check that fails on day one gets muted")

# BUCKET P IS NOT A CONTRADICTION, and this is the case that decides whether the
# check is usable. P means partial coverage WITH a documented hole, so a control
# exists — which is what the map is saying too. Counting P reported 52 rows
# against the real tree instead of 8, mostly honest partials.
rc, out = run(build_tree(
    {"cccccccc": {"enforcement_class": "surfacing", "control": "real_gate"}},
    {"cccccccc": ("P", "rail only; the specific test this rule demands is not checked")},
    CATALOG, files=FILES))
check("C: bucket P with a built class is NOT flagged", rc == 0, out[:200])

rc, out = run(build_tree(
    {"cccccccc": {"enforcement_class": "unbuilt", "planned_control": "x"}},
    QUEUED, CATALOG,
    backlog={"overstated_rows": [{"rule": "cccccccc", "reason": "r"}]},
    files=FILES))
check("C: a recorded row SINCE RECONCILED fails, so the debt only shrinks",
      rc != 0, out[:200])

rc, out = run(build_tree(
    {"aaaaaaaa": {"enforcement_class": "deny_gate", "control": "real_gate"}},
    {"aaaaaaaa": ("E", "built")}, CATALOG,
    backlog={"overstated_rows": [{"rule": "dddddddd", "reason": "r"}]},
    files=FILES))
check("C: a recorded row for a rule no longer in that state fails", rc != 0,
      "otherwise the backlog is where things go to be forgotten")

# ── 2. a rule the table does not carry is skipped, never guessed at ─────────
rc, _ = run(build_tree(
    {"eeeeeeee": {"enforcement_class": "unbuilt",
                  "planned_control": "pending classification"}},
    {}, CATALOG, files=FILES))
check("a rule absent from the audit table is skipped", rc == 0,
      "rules activated after the audit are exactly this, and there are two")

# ── 5. escape hatch and exit code ───────────────────────────────────────────
tree = build_tree(
    {"bbbbbbbb": {"enforcement_class": "unbuilt", "planned_control": "someday"}},
    {"bbbbbbbb": ("E", "BUILT. hooks/real-gate.py does it.")}, CATALOG, files=FILES)
rc, _ = run(tree)
check("the exit code is the contract: a violation exits nonzero", rc != 0)
rc, out = run(tree, env={"CARR_ALLOW_MAP_ROW_DRIFT": "1"})
check("the escape hatch lands", rc == 0, out[:150])

# ── clean tree says so ──────────────────────────────────────────────────────
rc, out = run(build_tree(
    {"aaaaaaaa": {"enforcement_class": "deny_gate", "control": "real_gate"},
     "bbbbbbbb": {"enforcement_class": "unbuilt", "planned_control": "someday"},
     "ffffffff": {"enforcement_class": "judgment_ambient",
                  "why_unenforceable": "judgment"}},
    {"aaaaaaaa": ("E", "built"), "bbbbbbbb": ("U", "nothing"),
     "ffffffff": ("J", "judgment")}, CATALOG, files=FILES))
check("a fully consistent tree passes and says so", rc == 0 and "OK" in out, out[:150])

# ── fail loudly on a broken input rather than silently passing ──────────────
d = tempfile.mkdtemp()
rc, out = run(d)
check("a tree with no map at all fails loudly, never silently passes", rc != 0,
      out[:150])

print(f"\n{passed} check(s) passed"
      + (f", {len(failures)} FAILED: {failures}" if failures else ""))
sys.exit(1 if failures else 0)
