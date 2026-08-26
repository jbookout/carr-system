#!/usr/bin/env python3
"""Guard and execute the atomic Production rule-delivery transition."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_activation import load_validated  # noqa:E402
from lib.rule_delivery_shadow import current_identity, locked_read  # noqa:E402

CURATION_BATCH = REPO / "audits" / "guidance-situation-curation-approval-batch.v1.json"
ELIGIBILITY = REPO / "ops" / "rule-delivery-shadow-eligibility.py"


def curation_ids() -> set[str]:
    batch = json.loads(CURATION_BATCH.read_text(encoding="utf-8"))
    ids = set(batch.get("proposal_ids", []))
    excluded = set(batch.get("explicitly_excluded_pending_proposal_ids", []))
    if len(ids) != 38 or ids & excluded or len(excluded) != 2:
        raise RuntimeError("curation approval batch is not exact 38 plus two exclusions")
    if batch.get("golden_suite_digest") != \
            "b1a5a61945c5e5fc5f7c74f45c3403f2c5df3e61db29e58f281d49015f63dae3":
        raise RuntimeError("curation approval batch golden digest drifted")
    return ids


def load_eligibility():
    import importlib.util
    spec = importlib.util.spec_from_file_location("shadow_eligibility", ELIGIBILITY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shadow_eligible(module, rows: list[dict], identity: dict) -> dict:
    return module.evaluate(rows, identity=identity)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("shadow","enforced"))
    parser.add_argument("--changed-by", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("rule-delivery-cutover: DATABASE_URL required", file=sys.stderr)
        return 78
    _base, overlay = load_validated()
    digest = overlay["base_map_sha256"]

    eligibility_module = load_eligibility()
    with locked_read(eligibility_module.DEFAULT_LOG) as ledger_rows, \
            psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if not args.apply:
            cur.execute("set transaction read only")
        cur.execute("""select mode,changed_by,reason,changed_at
                         from ops.rule_delivery_policy where singleton""")
        row = cur.fetchone()
        current = row[0] if row else None
        identity = current_identity(REPO, row)
        cur.execute("""select count(*),count(*) filter(where p.status='approved'),
                              count(*) filter(where p.status='approved' and a.kind='human')
                         from retrieval_proposal p left join actor a on a.id=p.reviewer_id
                        where p.id=any(%s::uuid[])""",
                    (sorted(curation_ids()),))
        curation = cur.fetchone()
        if curation is None:
            raise RuntimeError("curation approval query returned no aggregate row")
        cur.execute("select count(*) from ops.rule_delivery_activation_target")
        target_row = cur.fetchone()
        if target_row is None:
            raise RuntimeError("activation target query returned no row")
        target_count = target_row[0]
        cur.execute("select count(*) from ops.rule_delivery_activation_receipt")
        receipt_row = cur.fetchone()
        if receipt_row is None:
            raise RuntimeError("activation receipt query returned no row")
        receipt_count = receipt_row[0]
        eligibility = shadow_eligible(eligibility_module, ledger_rows, identity) \
            if args.mode == "enforced" else {"eligible": True}
        preflight = {"current_mode": current,"requested_mode": args.mode,
                     "targets": target_count,"prior_receipts": receipt_count,
                     "curation":{"found":curation[0],"approved":curation[1],
                                  "human_reviewed":curation[2]},
                     "shadow":eligibility,"map_digest":digest}
        print(json.dumps(preflight,sort_keys=True))
        if target_count != 9:
            print("rule-delivery-cutover: migration 0317 exact target set is absent",file=sys.stderr)
            return 1
        if args.mode == "enforced" and tuple(curation) != (38,38,38):
            print("rule-delivery-cutover: exact 38-item human curation approval is absent",file=sys.stderr)
            return 1
        if args.mode == "enforced" and not eligibility["eligible"]:
            print("rule-delivery-cutover: seven-day scoped shadow gate is not eligible",file=sys.stderr)
            return 1
        if not args.apply:
            print("rule-delivery-cutover: dry run only; pass --apply after reading the preflight")
            return 0

        # The ledger lock is still held. Re-lock and re-read policy inside this
        # same write transaction immediately before the atomic transition.
        cur.execute("""select mode,changed_by,reason,changed_at
                         from ops.rule_delivery_policy where singleton for update""")
        final_policy = cur.fetchone()
        final_identity = current_identity(REPO, final_policy)
        final_eligibility = shadow_eligible(
            eligibility_module, ledger_rows, final_identity) \
            if args.mode == "enforced" else {"eligible": True}
        if final_identity != identity or not final_eligibility["eligible"]:
            print("rule-delivery-cutover: policy/evidence identity changed before write",
                  file=sys.stderr)
            return 1
        cur.execute("select * from ops.set_rule_delivery_mode(%s,%s,%s,%s)",
                    (args.mode,args.changed_by,args.reason,digest))
        result = cur.fetchone()
        if not result or result[0] != args.mode or result[1] != 9:
            raise RuntimeError(f"atomic cutover returned an invalid receipt: {result}")
        conn.commit()
    print(json.dumps({"mode":result[0],"changed_controls":result[1],
                      "receipt_id":str(result[2])},sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
