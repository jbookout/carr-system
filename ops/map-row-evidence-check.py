#!/usr/bin/env python3
"""ops/map-row-evidence-check.py — the coverage map's own rule rows must agree
with the tree and with the audit table.

WHY THIS EXISTS. ops/config/rule-enforcement-map.json was the last enforcement
inventory nothing read. Two checks landed on 2026-08-15 and neither looks at its
rule rows: ops/enforcement-coverage-check.py runs gate -> control, and
ops/audit-queue-freshness-check.py runs audit row -> tree. The map's rows sat
between them unchecked, and they are the ones a session opens first, because the
hourly sync job writes that file and every gate-integrity boot hashes it.

MEASURED, the hour this was written: fourteen rules where the map and the audit
table state different things about the same rule, in both directions. Three of
them (412d37d3, 24e10ee8, c0b38d80) had been confirmed built by two independent
sessions while the map still called them unbuilt. No count is quoted as a
standing figure — that is the `dated-artifact-read-as-present-state` failure
this family of checks exists to end, and a file about stale numbers must not
carry one.

THREE PREDICATES, none of them a judgment about whether a rule is "really"
enforced. That judgment is what a check must never make; a gate that tried
would be wrong often enough to be deleted. Each of these instead finds two
recorded claims that CANNOT BOTH BE CURRENT.

  A. NAMED CONTROL MUST EXIST. A row classed as built names a control; that
     control must be in the catalog and its implementation files must be on
     disk. This is the inverse lie — a row reading as enforced with nothing
     behind it — and it is exactly what rule ab814a26 is about. Zero of these
     existed when this shipped; it guards forward rather than fixing a backlog.

  B. THE TABLE SAYS ENFORCED, THE MAP SAYS UNBUILT. The audit row carries a
     POSITIVE claim naming a live artifact, so the map is simply behind. The
     remedy is a one-line edit and is always available, which is what keeps
     this a hard failure rather than recorded debt.

  C. THE MAP CLAIMS BUILT WHILE THE TABLE STILL QUEUES THE RULE. These are not
     the map being newer than the table. The map derives enforcement_class
     mechanically from `category` — a rule sorted into one of the four
     non-advisory buckets reads as backed by that bucket's control whether or
     not the control ADDRESSES it. The audit rows say so in their own evidence
     text ("checks task-completion claims, not metric-authoring shape --
     mismatch"). Overturning an auditor's judgment is not a check's business,
     so these are RECORDED DEBT rather than a failure.

WHY C IS A BACKLOG. Eight existed on day one. A check that fails on day one
gets muted on day one, and a muted check is worse than none — the same reasoning
ops/enforcement-coverage-check.py used for its orphan backlog, and the same
shape: a NEW overstatement fails immediately, which is where the bleeding stops.

THE DEBT MAY ONLY SHRINK, enforced in both directions. An entry whose row has
since been reconciled FAILS, and so does one naming a rule that is no longer in
that state. Without both, the backlog becomes the place things go to be
forgotten, which is the failure this exists to end.

A RULE THE TABLE DOES NOT CARRY IS SKIPPED, never guessed at. Rules activated
after the 2026-08-14 audit have no row, and inventing a bucket for them would
be the check making up the fact it is supposed to be verifying.

RUNNING IT:  .venv/bin/python ops/map-row-evidence-check.py
Escape hatch: CARR_ALLOW_MAP_ROW_DRIFT=1, same idiom as its neighbours.
Fixtures: ops/map-row-evidence-selftest.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# The classes that ASSERT something concrete backs the rule. `unbuilt` asserts
# the opposite and `judgment_ambient` asserts the question does not apply.
BUILT_CLASSES = {"deny_gate", "stop_gate", "surfacing", "schema"}

# Catalog entries that are not filesystem paths. Probing these as though they
# were is how a check invents a failure it cannot actually observe.
NON_PATH_PREFIXES = ("external:", "glob:", "command:")

# The one audit bucket that CONTRADICTS a built class. U means "enforcement
# specified and NOT BUILT", so a built class and a U row cannot both be current.
#
# BUCKET P IS DELIBERATELY NOT HERE, and that exclusion is the difference
# between a usable check and a muted one. P means partial coverage WITH a
# documented hole — a control exists and does not fully cover the rule. That is
# the same thing the map says when it names a control, so the two AGREE; the
# row is recording where the seam is, not denying the control. Including P
# reported 52 rows on the first run against the real tree instead of 8, most of
# them honest partials, which is precisely the day-one flood that gets a check
# switched off.
QUEUED_BUCKETS = {"U"}

AUDIT_TSV = os.path.join("audits", "rule-enforceability-audit-2026-08-14.tsv")
MAP_JSON = os.path.join("ops", "config", "rule-enforcement-map.json")
BACKLOG_JSON = os.path.join("ops", "config", "map-row-overstatement-backlog.json")


def read_json(path):
    with open(path) as fh:
        return json.load(fh)


def read_table(path):
    """{short_rule_id: (bucket, evidence)} from the audit table."""
    out = {}
    with open(path) as fh:
        next(fh, None)
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) >= 5 and f[0].strip():
                out[f[0].strip()] = (f[2].strip(), f[4].strip())
    return out


def control_problem(row, catalog, repo):
    """Why this built row's named control is not real, or None."""
    name = row.get("control")
    if not name:
        return "it names no control at all"
    control = catalog.get(name)
    if control is None:
        return f"it names control {name!r}, which is not in the control catalog"
    missing = [
        impl for impl in control.get("implementation", [])
        if not impl.startswith(NON_PATH_PREFIXES)
        and not os.path.exists(os.path.join(repo, impl.split(":")[-1]))
    ]
    if missing:
        return (f"control {name!r} names {', '.join(missing)}, "
                f"which {'do' if len(missing) > 1 else 'does'} not exist")
    return None


def audit(repo):
    mapping = read_json(os.path.join(repo, MAP_JSON))
    table = read_table(os.path.join(repo, AUDIT_TSV))
    try:
        backlog = read_json(os.path.join(repo, BACKLOG_JSON))
    except FileNotFoundError:
        backlog = {"overstated_rows": []}

    catalog = mapping.get("control_catalog") or {}
    rows = mapping.get("rule_controls") or {}
    recorded = {e["rule"]: e.get("reason", "") for e in
                (backlog.get("overstated_rows") or [])}

    ghosts, behind, overstated = [], [], []

    for key, row in sorted(rows.items()):
        rid = key[:8]
        cls = row.get("enforcement_class")

        if cls in BUILT_CLASSES:
            problem = control_problem(row, catalog, repo)
            if problem:
                ghosts.append((rid, cls, problem))

        bucket, evidence = table.get(rid, (None, ""))
        if bucket is None:
            continue                      # activated after the audit; not ours
        if bucket == "E" and cls == "unbuilt":
            behind.append((rid, evidence))
        elif bucket in QUEUED_BUCKETS and cls in BUILT_CLASSES:
            overstated.append((rid, cls, row.get("control"), evidence))

    live = {r[0] for r in overstated}
    return {
        "ghosts": ghosts,
        "behind": behind,
        "overstated": overstated,
        "new_overstated": [o for o in overstated if o[0] not in recorded],
        "reconciled": sorted(r for r in recorded if r not in live),
        "rows": len(rows),
        "recorded": len(recorded),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    args = ap.parse_args()

    try:
        result = audit(args.repo)
    except (OSError, ValueError, KeyError) as exc:
        # Loud, never silent: a check that cannot read its inputs and exits 0
        # reads exactly like a check that passed.
        print(f"map-row-evidence: CANNOT READ its inputs — {exc}", file=sys.stderr)
        return 1

    problems = 0

    if result["ghosts"]:
        problems += len(result["ghosts"])
        print("MAP ROWS CLAIMING A CONTROL THAT IS NOT THERE — "
              f"{len(result['ghosts'])} row(s)\n")
        print("Each row below reads as enforced. The control it names is not on")
        print("disk, so nothing refuses anything (rule ab814a26).\n")
        for rid, cls, problem in result["ghosts"]:
            print(f"  {rid}  class={cls}: {problem}")
        print()

    if result["behind"]:
        problems += len(result["behind"])
        print("MAP ROWS THE AUDIT TABLE HAS ALREADY OVERTAKEN — "
              f"{len(result['behind'])} row(s)\n")
        print("The table records this rule ENFORCED and names the artifact. The")
        print("map still says unbuilt. Both cannot be current, and the map is the")
        print("stale one — correct its row to the class and control that exist.\n")
        for rid, evidence in result["behind"]:
            print(f"  {rid}  table says: {evidence[:150]}")
        print()

    if result["new_overstated"]:
        problems += len(result["new_overstated"])
        print("NEW MAP ROWS OVERSTATING THEIR COVERAGE — "
              f"{len(result['new_overstated'])} row(s)\n")
        print("The map classes these as built because of the CATEGORY they sit")
        print("in, while the audit row says the control there does not cover this")
        print("rule. Either reconcile the row, or record it in")
        print(f"{BACKLOG_JSON} with the reason.\n")
        for rid, cls, control, evidence in result["new_overstated"]:
            print(f"  {rid}  map={cls} via {control!r}")
            print(f"        table says: {evidence[:150]}")
        print()

    if result["reconciled"]:
        problems += len(result["reconciled"])
        print("RECORDED DEBT THAT IS NO LONGER TRUE — "
              f"{len(result['reconciled'])} entr(y/ies)\n")
        print("These are recorded as overstatements and are not overstatements")
        print("any more. Remove them: the recorded set may only shrink, or it")
        print("becomes the place things go to be forgotten.\n")
        for rid in result["reconciled"]:
            print(f"  {rid}")
        print()

    if not problems:
        print(f"map-row-evidence: OK — {result['rows']} map rows agree with the "
              f"tree and the audit table; {result['recorded']} overstatement(s) "
              f"held in the recorded backlog")
        return 0

    if os.environ.get("CARR_ALLOW_MAP_ROW_DRIFT"):
        print("CARR_ALLOW_MAP_ROW_DRIFT set — reporting only, not failing.")
        return 0

    print("Escape hatch, for a genuinely mid-flight tree: "
          "CARR_ALLOW_MAP_ROW_DRIFT=1")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
