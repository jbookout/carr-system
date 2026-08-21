#!/usr/bin/env python3
"""Pin the bounded internal control catalog carried by schema snapshots."""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "bin" / "schema-snapshot.sh").read_text(encoding="utf-8")
match = re.search(r'VOCAB_TABLES="(?P<tables>.*?)"', SCRIPT, re.DOTALL)
assert match, "schema snapshot vocabulary list is missing"
tables = set(match.group("tables").replace("\\\n", " ").split())

assert "ops.enforcement_control_catalog" not in tables
assert "ops.rule_control_binding" not in tables
assert "ops.rule_approval_receipt" not in tables
assert "ops.rule_retirement_receipt" not in tables
assert "CONTROL_CATALOG_VERIFY" in SCRIPT
assert "CARR REVIEWED CONTROL CATALOG" in SCRIPT
assert "schema snapshot refused: exact reviewed control catalog is missing or drifted" in SCRIPT
assert "never dump arbitrary ops.enforcement_control_catalog rows" in SCRIPT
assert "on conflict (control_key) do nothing;" in SCRIPT
assert "verified_at,now()" not in SCRIPT
assert "updated_at,now()" not in SCRIPT
assert "to_char(verified_at at time zone 'UTC'" in SCRIPT
assert "to_char(updated_at at time zone 'UTC'" in SCRIPT
assert "order by array_position(array['human_authority_runtime','platform_metering_pre_dispatch'],control_key)" in SCRIPT
assert SCRIPT.count("'human_authority_runtime'") >= 2
assert SCRIPT.count("'platform_metering_pre_dispatch'") >= 2
assert "ops.rule_control_binding" not in match.group("tables")

print("schema snapshot control catalog selftest: source-timestamped two-row inclusion/exclusion pinned")
