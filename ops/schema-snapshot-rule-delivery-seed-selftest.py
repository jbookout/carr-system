#!/usr/bin/env python3
"""Pin scoped rule-delivery reconstruction to bounded, exact source data."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = (ROOT / "bin" / "schema-snapshot.sh").read_text(encoding="utf-8")
SNAPSHOT = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
MIGRATION_SOURCE = (ROOT / "migrations" / "0317_atomic_rule_delivery_cutover.sql").read_text(
    encoding="utf-8"
)
POLICY_MIGRATION = "0291_rule_delivery_layers.sql"
CUTOVER_MIGRATION = "0317_atomic_rule_delivery_cutover.sql"
POLICY_MARKER = "-- CARR RULE DELIVERY POLICY (bin/schema-snapshot.sh)"
TARGET_MARKER = "-- CARR RULE DELIVERY ACTIVATION TARGETS (bin/schema-snapshot.sh)"
DIGEST = "266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a"
EXPECTED_TARGETS = [
    "25fcddee", "3fa17fa0", "72e06bdf", "581cb3fe", "113b3833",
    "57d13061", "c66dc739", "49533583", "557838a5",
]


def target_ids(source: str, marker: str) -> list[str]:
    block = source.split(marker, 1)[1].split(
        "on conflict (short_id) do nothing;", 1
    )[0]
    return re.findall(r"^\s*\('([0-9a-f]{8})'", block, re.M)


def target_digests(source: str, marker: str) -> list[str]:
    block = source.split(marker, 1)[1].split(
        "on conflict (short_id) do nothing;", 1
    )[0]
    return re.findall(r"'([0-9a-f]{64})'\)\s*,?$", block, re.M)


def snapshot_target_rows(source: str) -> list[list[str]]:
    marker = "COPY ops.rule_delivery_activation_target "
    if marker not in source:
        return []
    block = source.split(marker, 1)[1].split("\n", 1)[1].split("\n\\.\n", 1)[0]
    return [line.split("\t") for line in block.splitlines() if line]

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

policy_ledger_applied = bool(
    re.search(rf"^{re.escape(POLICY_MIGRATION)}\t", SNAPSHOT, re.M)
)
cutover_ledger_applied = bool(
    re.search(rf"^{re.escape(CUTOVER_MIGRATION)}\t", SNAPSHOT, re.M)
)
snapshot_targets = snapshot_target_rows(SNAPSHOT)

assert "RULE_DELIVERY_APPLIED" in GENERATOR
assert f"filename='{POLICY_MIGRATION}'" in GENERATOR
assert 'if [ "$RULE_DELIVERY_APPLIED" = t ]; then' in GENERATOR
assert "RULE_DELIVERY_CUTOVER_APPLIED" in GENERATOR
assert f"filename='{CUTOVER_MIGRATION}'" in GENERATOR
assert 'if [ "$RULE_DELIVERY_CUTOVER_APPLIED" = t ]; then' in GENERATOR
assert "insert into ops.rule_delivery_policy (singleton,mode,changed_by,reason)" in GENERATOR
assert "values (true,'shadow','schema-snapshot'," in GENERATOR
assert "on conflict (singleton) do nothing;" in GENERATOR
assert target_ids(GENERATOR, TARGET_MARKER) == EXPECTED_TARGETS
assert target_digests(GENERATOR, TARGET_MARKER) == [DIGEST] * 9
assert [row[0] for row in snapshot_targets] == EXPECTED_TARGETS
assert [row[-1] for row in snapshot_targets] == [DIGEST] * 9
assert re.findall(r"^\s*\('([0-9a-f]{8})'", MIGRATION_SOURCE, re.M)[:9] == EXPECTED_TARGETS
assert policy_ledger_applied == policy_seeded, (
    "the shadow rule-delivery bootstrap must appear exactly when 0291 is in "
    "the source snapshot ledger"
)
assert cutover_ledger_applied == targets_seeded, (
    "the nine rule-delivery activation targets must appear exactly when 0317 "
    "is in the source snapshot ledger"
)

print("schema snapshot rule-delivery seeds selftest: bounded exact reconstruction pinned")
