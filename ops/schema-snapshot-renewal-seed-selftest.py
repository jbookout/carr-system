#!/usr/bin/env python3
"""Pin the 0230 renewal seed to the source snapshot's applied ledger."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = (ROOT / "bin/schema-snapshot.sh").read_text(encoding="utf-8")
SNAPSHOT = (ROOT / "db/schema.sql").read_text(encoding="utf-8")
MIGRATION = "0230_renewal_decision_delivery.sql"
SEED_KEY = "renewal-radar-source-daily"

ledger_applied = bool(re.search(rf"^{re.escape(MIGRATION)}\t", SNAPSHOT, re.M))
seed_present = bool(re.search(
    rf"^\s*\('{re.escape(SEED_KEY)}',1,false,", SNAPSHOT, re.M
))

assert "RENEWAL_SOURCE_APPLIED" in GENERATOR
assert f"filename='{MIGRATION}'" in GENERATOR
assert 'if [ "$RENEWAL_SOURCE_APPLIED" = t ]; then' in GENERATOR
assert ledger_applied == seed_present, (
    "the disabled renewal source seed must appear exactly when 0230 is in "
    "the source snapshot ledger"
)

print("schema snapshot renewal seed selftest: pending/applied boundary pinned")
