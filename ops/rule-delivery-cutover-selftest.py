#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location("cutover",REPO/"ops/rule-delivery-cutover.py")
assert spec and spec.loader
cutover=importlib.util.module_from_spec(spec); spec.loader.exec_module(cutover)
py=(REPO/"ops/rule-delivery-cutover.py").read_text()
sh=(REPO/"bin/rule-delivery-cutover-prod.sh").read_text()
sql=(REPO/"migrations/0317_atomic_rule_delivery_cutover.sql").read_text()
checks={
 "exact 38 reviewed proposal ids":len(cutover.curation_ids())==38,
 "human reviewer is checked":"p.status='approved' and a.kind='human'" in py,
 "seven-day eligibility is checked":"shadow_eligible(identity)" in py and "eligibility[\"eligible\"]" in py,
 "live policy identity binds eligibility":"current_identity(REPO, row)" in py,
 "production is pinned":"steep-field-48688294" in sh and "connection-string production" in sh,
 "dry run is default":"--apply" in sh and "APPLY=0" in sh,
 "direct policy update is guarded":"rule_delivery_policy_cutover_only" in sql,
 "cutover and controls share one function":"set_rule_delivery_mode" in sql and "delete from ops.rule_enforcement_point" in sql,
 "receipt is append-only":"rule_delivery_activation_receipt_append_only" in sql,
}
bad=[name for name,passed in checks.items() if not passed]
if bad:
 print("rule-delivery-cutover-selftest: FAIL")
 for name in bad: print("  "+name)
 raise SystemExit(1)
print(f"rule-delivery-cutover-selftest: {len(checks)} cases passed")
