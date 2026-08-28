#!/usr/bin/env python3
"""Static and state-machine checks for the one-time staging 0382 repair."""

import hashlib
import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "staging-ledger-repair-0382.py"
spec = importlib.util.spec_from_file_location("staging_repair_0382", TOOL)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


migration_bytes = module.MIGRATION.read_bytes()
check(
    "checked-in 0382 matches Production's recorded immutable digest",
    hashlib.sha256(migration_bytes).hexdigest() == module.EXPECTED_SHA256,
)
check(
    "exact genuine hole applies and records",
    module.classify_state({module.LATER_NAME: module.LATER_SHA256}, "legacy")
    == "apply_and_record",
)
check(
    "exact crash-recovery state records only after boundary verification",
    module.classify_state({module.LATER_NAME: module.LATER_SHA256}, "repaired")
    == "record_verified_recovery",
)
check(
    "already-recorded exact digest is permanently idempotent",
    module.classify_state(
        {
            module.MIGRATION_NAME: module.EXPECTED_SHA256,
            module.LATER_NAME: module.LATER_SHA256,
        },
        "repaired",
    )
    == "already_recorded",
)

for label, ledger, boundary in [
    ("missing later marker refuses", {}, "legacy"),
    ("mismatched later digest refuses", {module.LATER_NAME: "bad"}, "legacy"),
    ("unknown function state refuses", {module.LATER_NAME: module.LATER_SHA256}, "unknown"),
    (
        "mismatched 0382 digest refuses",
        {module.MIGRATION_NAME: "bad", module.LATER_NAME: module.LATER_SHA256},
        "repaired",
    ),
]:
    try:
        module.classify_state(ledger, boundary)
    except ValueError:
        check(label, True)
    else:
        check(label, False)

source = TOOL.read_text()
check("base-table SELECT remains forbidden", "not reader_rule" in source)
check("repair uses exact migration bytes", "cur.execute(sql_text)" in source)
check("ledger record follows repaired-boundary verification", source.index("cur.execute(sql_text)") < source.index("insert into public.schema_migrations"))
