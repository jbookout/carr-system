#!/usr/bin/env python3
"""Pin scoped rule-delivery configuration to the bounded snapshot vocabulary."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = (ROOT / "bin" / "schema-snapshot.sh").read_text(encoding="utf-8")
SNAPSHOT = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")

match = re.search(r'VOCAB_TABLES="(?P<body>.*?)"', GENERATOR, re.S)
assert match, "schema snapshot generator has no bounded vocabulary declaration"
tables = set(match.group("body").replace("\\\n", " ").split())

required = {"ops.rule_delivery_policy", "ops.rule_delivery_activation_target"}
assert required <= tables, (
    "the scoped rule-delivery policy and exact activation target map must ride "
    "in the bounded snapshot vocabulary"
)
for forbidden in (
    "ops.rule_delivery_observation",
    "ops.rule_delivery_activation_receipt",
):
    assert forbidden not in tables, f"runtime/evidence table leaked into snapshot vocabulary: {forbidden}"

# A checked-in snapshot may legitimately predate the repair migration. Once its
# ledger says 0321 has been absorbed, however, the rows must be present because
# neither 0291, 0317, nor 0321 will replay on a fresh database.
ledger_applied = "0321_rule_delivery_policy_seed_repair.sql" in SNAPSHOT
policy_seeded = bool(re.search(
    r"COPY ops\.rule_delivery_policy\b|INSERT INTO ops\.rule_delivery_policy\b",
    SNAPSHOT,
    re.I,
))
targets_seeded = bool(re.search(
    r"COPY ops\.rule_delivery_activation_target\b|INSERT INTO ops\.rule_delivery_activation_target\b",
    SNAPSHOT,
    re.I,
))
assert not ledger_applied or (policy_seeded and targets_seeded), (
    "a snapshot whose ledger includes 0321 must also carry the scoped "
    "rule-delivery policy and activation targets"
)

print("schema snapshot rule-delivery seed selftest: bounded reconstruction pinned")
