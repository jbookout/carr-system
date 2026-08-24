#!/usr/bin/env python3
"""Exit audit: every active rule has a reviewed delivery tag, and shadow mode is
still doing its job.

The delivery twin of ops/rule-admission-audit.py. That audit answers "can
anything refuse this rule"; this one answers "will this rule ever reach a
session", which is the question the 2026-08-23 rules council found nobody was
asking. Split the same way for the same reason: counts() takes a cursor so the
nightly watch and this audit count once rather than twice (rule a8c55a47).

WHAT COUNTS AS A FAILURE, and each is a way the delivery half could go quiet:
  untagged   an active rule no layer covers — it would be omitted by omission
  orphaned   a tag naming a rule that is not active — dead law in a live pack
  wildcarded a pack tag of '*', the scoping-costume failure the chair named
  packless   a rule tagged for pack delivery that names no pack
  emptypack  a defined pack no active rule is in — a trigger that loads nothing
  layer0     the shared Layer 0 set over its reviewed cap

MODE IS REPORTED, NEVER FAILED ON. 'shadow' is the correct state for the first
week and 'enforced' is the correct state after it; a check that called either
one wrong would be asserting a calendar it cannot read.

  0  every active rule carries a deliverable tag
  1  a gap (the counts say which shape)
  2  could not run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import psycopg

DEFAULT_LAYER0_CAP = 35


def counts(cur: Any, cap: int = DEFAULT_LAYER0_CAP) -> dict[str, Any]:
    """Read the delivery contract's numbers. Read-only, and it reads no rule text."""
    cur.execute("""select count(*) filter (where l.rule_id is null),
                          count(*)
                     from rule r
                     left join ops.rule_load_layer l on l.rule_id = r.id
                    where r.status = 'active'""")
    untagged, total = cur.fetchone()
    cur.execute("""select count(*) from ops.rule_load_layer l
                    where not exists (select 1 from rule r
                                       where r.id = l.rule_id and r.status = 'active')""")
    orphaned = cur.fetchone()[0]
    cur.execute("""select
                     count(*) filter (where l.load_layer = 'layer0'),
                     count(*) filter (where l.load_layer = 'control'),
                     count(*) filter (where l.load_layer = 'pack'),
                     count(*) filter (where l.load_layer = 'layer0' and l.scope = 'shared'),
                     count(*) filter (where '*' = any(l.packs)),
                     count(*) filter (where l.load_layer = 'pack'
                                        and cardinality(l.packs) = 0)
                   from ops.rule_load_layer l
                   join rule r on r.id = l.rule_id and r.status = 'active'""")
    layer0, control, pack, layer0_shared, wildcarded, packless = cur.fetchone()
    cur.execute("""select count(*) from ops.rule_pack p
                    where not exists (
                      select 1 from ops.rule_load_layer l
                        join rule r on r.id = l.rule_id and r.status = 'active'
                       where p.pack = any(l.packs))""")
    emptypack = cur.fetchone()[0]
    cur.execute("select count(*) from ops.rule_pack")
    packs = cur.fetchone()[0]
    cur.execute("select mode from ops.rule_delivery_policy")
    row = cur.fetchone()
    return {"total": total, "untagged": untagged, "orphaned": orphaned,
            "layer0": layer0, "control": control, "pack": pack,
            "layer0_shared": layer0_shared, "layer0_shared_cap": cap,
            "wildcarded": wildcarded, "packless": packless,
            "packs": packs, "emptypack": emptypack,
            "mode": row[0] if row else "(none)"}


def failing(c: dict[str, Any], *, allow_empty_store: bool = False) -> bool:
    """The one place that decides whether these numbers are a failure."""
    if c["total"] == 0:
        return not allow_empty_store
    return bool(c["untagged"] or c["orphaned"] or c["wildcarded"] or c["packless"]
                or c["emptypack"] or c["mode"] == "(none)"
                or c["layer0_shared"] > c["layer0_shared_cap"])


def render(c: dict[str, Any]) -> str:
    return (f"total={c['total']} untagged={c['untagged']} orphaned={c['orphaned']} "
            f"layer0={c['layer0']}(shared {c['layer0_shared']}/{c['layer0_shared_cap']}) "
            f"control={c['control']} pack={c['pack']} packs={c['packs']} "
            f"wildcarded={c['wildcarded']} packless={c['packless']} "
            f"emptypack={c['emptypack']} mode={c['mode']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-empty-store", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("rule-delivery-audit: DATABASE_URL required", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            c = counts(cur)
    except psycopg.errors.UndefinedTable:
        # "NOT INSTALLED" AND "INCOMPLETE" ARE DIFFERENT FINDINGS (rule 88e9b5eb).
        # Absent tables mean migration 0288 has not been applied here, which has a
        # different remedy from tags that are missing rows — and letting the
        # traceback through would report the first as the second.
        print("rule-delivery-audit: the delivery tables are absent — migration "
              "0288_rule_delivery_layers.sql has not been applied to this database. "
              "That is not the same finding as incomplete tags: apply the migration "
              "first, then re-run.", file=sys.stderr)
        return 2
    bad = failing(c, allow_empty_store=args.allow_empty_store)
    if args.json:
        print(json.dumps({"ok": not bad, **c}, sort_keys=True))
    else:
        print("rule-delivery-audit: " + render(c))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
