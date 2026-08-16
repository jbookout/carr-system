#!/usr/bin/env python3
"""Backfill active reviewed rules into the Phase 1 database admission contract."""
from __future__ import annotations
import argparse,hashlib,json,os,sys
from pathlib import Path
import psycopg

REPO=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(REPO))
from lib.rule_admission import admission_contract  # noqa:E402

MAP=REPO/"ops"/"config"/"rule-enforcement-map.json"


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-empty-store",action="store_true",
                        help="staging only: accept a sanitized store with zero active rules")
    args=parser.parse_args()
    dsn=os.environ.get("DATABASE_URL")
    if not dsn: raise SystemExit("DATABASE_URL is required")
    data=json.loads(MAP.read_text(encoding="utf-8"))
    scope_by_id={rid:scope for scope,ids in data["active_rule_ids"].items() for rid in ids}
    counts={"admitted":0,"needs_revision":0}
    with psycopg.connect(dsn) as conn,conn.cursor() as cur:
        cur.execute("select id,statement,taught_by,coalesce(activated_by,taught_by) from rule where status='active'")
        rules=cur.fetchall()
        if not rules and args.allow_empty_store:
            print(json.dumps({"active_rules":0,"admitted":0,"needs_revision":0,
                              "note":"sanitized empty store; contract covered by rollback gate"},sort_keys=True))
            return 0
        matched={}
        for rid,statement,taught_by,authority_actor in rules:
            short=str(rid)[:8]
            if short not in scope_by_id or short not in data["rule_controls"]:
                raise RuntimeError(f"active rule {short} is absent from reviewed enforcement map")
            contract=admission_contract(short,scope_by_id[short],data["rule_controls"][short],data["control_catalog"])
            contract_json=json.dumps(contract,sort_keys=True,separators=(",",":"))
            contract_hash=hashlib.sha256(contract_json.encode()).hexdigest()
            matched[short]=True
            source=f"rule:{rid}"
            cur.execute("""insert into ops.guidance_intake
                         (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
                       values ('rule','system',%s,%s,%s,%s,%s)
                       on conflict (lane,source_ref) where lane='rule' and source_ref like 'rule:%%'
                       do update set state=excluded.state,normalized_contract=excluded.normalized_contract,
                                     updated_at=now(),version=ops.guidance_intake.version+1
                       returning id""",
                       (source,statement,contract["state"],contract_json,taught_by))
            intake_row=cur.fetchone()
            if intake_row is None:
                raise RuntimeError(f"admission intake write for active rule {short} returned no id")
            intake=intake_row[0]
            cur.execute("""insert into ops.rule_admission
                         (rule_id,guidance_intake_id,enforcement_class,binding_moment,
                          applicability,projection,reachability,input_contract,fixture_refs,
                          state,admitted_by,admitted_at,reason)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               case when %s='admitted' then %s else null end,
                               case when %s='admitted' then now() else null end,%s)
                       on conflict(rule_id) do update set
                         guidance_intake_id=excluded.guidance_intake_id,
                         enforcement_class=excluded.enforcement_class,binding_moment=excluded.binding_moment,
                         applicability=excluded.applicability,projection=excluded.projection,
                         reachability=excluded.reachability,input_contract=excluded.input_contract,
                         fixture_refs=excluded.fixture_refs,state=excluded.state,
                         admitted_by=excluded.admitted_by,admitted_at=excluded.admitted_at,
                         reason=excluded.reason,updated_at=now(),version=ops.rule_admission.version+1""",
                       (rid,intake,contract["enforcement_class"],contract["binding_moment"],
                        json.dumps(contract["applicability"]),json.dumps(contract["projection"]),
                        json.dumps(contract["reachability"]),json.dumps(contract["input_contract"]),
                        contract["fixture_refs"],contract["state"],contract["state"],authority_actor,
                        contract["state"],contract["reason"]))
            cur.execute("delete from ops.rule_enforcement_point where rule_id=%s",(rid,))
            for point in contract["enforcement_points"]:
                cur.execute("""insert into ops.rule_enforcement_point
                             (rule_id,control_key,implementation_ref,test_ref,enforcement_class,
                              installed,verified_at)
                           values (%s,%s,%s,%s,%s,%s,case when %s then now() else null end)""",
                           (rid,point["control_key"],point["implementation_ref"],point["test_ref"],
                            point["enforcement_class"],point["installed"],point["installed"]))
            cur.execute("""insert into ops.authority_receipt
                         (idempotency_key,kind,subject_type,subject_id,actor_id,decision,
                          contract_hash,evidence_refs)
                       values (%s,'admission','rule',%s,%s,%s,
                               encode(digest(%s::text,'sha256'),'hex'),%s)
                       on conflict(idempotency_key) do nothing""",
                       (f"control-plane-backfill:{contract_hash}:{rid}",rid,authority_actor,
                        f"active rule backfill: {contract['state']}",contract_json,
                        contract["fixture_refs"]))
            counts[contract["state"]]+=1
        if set(matched)!=set(scope_by_id):
            raise RuntimeError(f"map/store active parity differs missing={sorted(set(scope_by_id)-set(matched))}")
        conn.commit()
    print(json.dumps({"active_rules":len(rules),**counts},sort_keys=True))
    return 0

if __name__=="__main__": raise SystemExit(main())
