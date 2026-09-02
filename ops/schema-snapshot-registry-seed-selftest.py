#!/usr/bin/env python3
"""Registry seed rows must survive pg_dump's empty search_path on rebuild."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = (ROOT / "bin" / "schema-snapshot.sh").read_text(encoding="utf-8")
SNAPSHOT = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
FULL_SET_SEALS = json.loads(
    (ROOT / "ops" / "config" / "scac-registry-full-entry-set-seals.json").read_text(encoding="utf-8")
)
RUNTIME_V9 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v9.generated.js").read_text(
    encoding="utf-8"
)
RUNTIME_V10 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v10.generated.js").read_text(
    encoding="utf-8"
)

for table, key in (
    ("doctrine_gate_check", "check_key"),
    ("agent_profile", "profile_key"),
):
    insert = (
        f"insert into public.{table} select * from "
        f"jsonb_populate_record(null::public.{table}"
    )
    source = f"from public.{table} "
    assert insert in GENERATOR, (
        f"{table} seed INSERT must be schema-qualified because pg_dump "
        "sets search_path to the empty string"
    )
    assert source in GENERATOR, f"{table} seed source read must be schema-qualified"
    assert f"on conflict ({key}) do nothing" in GENERATOR
    if f"insert into public.{table}" in SNAPSHOT:
        assert insert in SNAPSHOT, f"generated {table} seed lost schema qualification"

# A restore must validate the canonical contract behind every carried digest,
# not merely trust a stored digest column and then re-aggregate it.  The same
# predicate is required before rendering and after restoring, so a snapshot
# whose contract JSON was tampered while its old entry_digest was retained is
# rejected on both sides of the boundary.
assert GENERATOR.count("e.entry_digest is distinct from 'sha256:'||encode(public.digest(") >= 2
assert GENERATOR.count("ops.scac_mutation_registry_seal_valid(historical.registry_version)") >= 2
for version in range(1, 9):
    assert GENERATOR.count(f"'scac-mutation-registry.v{version}'") >= 2
assert set(FULL_SET_SEALS) == {f"scac-mutation-registry.v{version}" for version in range(1, 11)}
assert all(len(value) == 71 and value.startswith("sha256:") for value in FULL_SET_SEALS.values())
assert FULL_SET_SEALS["scac-mutation-registry.v10"] != "sha256:" + "0" * 64
assert GENERATOR.count("SCAC_FULL_SET_SQL") >= 3
assert "SCAC_EXPECTED_CURRENT_DIGEST" in GENERATOR
assert "registry_digest='${SCAC_EXPECTED_CURRENT_DIGEST}'" in GENERATOR
assert "SCAC_EXPECTED_CURRENT_SOURCE_SET" in GENERATOR
assert "SCAC_EXPECTED_CURRENT_CATALOG" in GENERATOR
assert "SCAC_CURRENT_NUMBER=10" in GENERATOR
assert "SCAC_CURRENT_NUMBER=9" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=14115" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1455" in GENERATOR
assert "SCAC_CURRENT_SOURCE_COUNT=811" in GENERATOR
assert "ops.scac_mutation_catalog_v10_current()" in GENERATOR
assert GENERATOR.count("order by e.entry_digest collate") >= 2
assert "not ${SCAC_CURRENT_CATALOG_FUNCTION}" in GENERATOR
numeric_registry_order = (
    "array_agg(registry_version order by "
    "split_part(registry_version,'.v',2)::integer)"
)
assert numeric_registry_order in GENERATOR
versions = [f"scac-mutation-registry.v{version}" for version in range(1, 11)]
assert sorted(versions, key=lambda value: int(value.rsplit("v", 1)[1])) == versions
assert sorted(versions) != versions

loader_start = GENERATOR.index("SCAC_FULL_SET_SQL=\"$(node -e '\n") + len(
    "SCAC_FULL_SET_SQL=\"$(node -e '\n"
)
loader_end = GENERATOR.index("\n  ' \"$SCAC_FULL_SET_SEALS\" \"$SCAC_VERSION_COUNT\")\"", loader_start)
loader = GENERATOR[loader_start:loader_end]
loaded_sql = subprocess.run(
    ["node", "-e", loader, str(ROOT / "ops" / "config" / "scac-registry-full-entry-set-seals.json"), "10"],
    check=True,
    capture_output=True,
    text=True,
).stdout
assert loaded_sql.count("scac-mutation-registry.v") == 10
assert loaded_sql.count("sha256:") == 10

def runtime_seal(source: str, name: str) -> str:
    match = re.search(rf'^export const {name} = "([0-9a-f]{{64}})";$', source, re.MULTILINE)
    assert match is not None, f"missing generated runtime seal {name}"
    return match.group(1)


runtime_seals = {name: runtime_seal(RUNTIME_V10, name) for name in (
    "SCAC_MUTATION_REGISTRY_DIGEST",
    "SCAC_MUTATION_SOURCE_CONTRACT_SET_DIGEST",
    "SCAC_MUTATION_DB_CATALOG_BASELINE_DIGEST",
)}
validation_start = GENERATOR.index('  case "$SCAC_EXPECTED_CURRENT_DIGEST$SCAC_EXPECTED_CURRENT_SOURCE_SET')
validation_end = GENERATOR.index('  SCAC_EXPECTED_CURRENT_DIGEST="sha256:', validation_start)
validation = GENERATOR[validation_start:validation_end]
validation_env = {
    **os.environ,
    "SCAC_EXPECTED_CURRENT_DIGEST": runtime_seals["SCAC_MUTATION_REGISTRY_DIGEST"],
    "SCAC_EXPECTED_CURRENT_SOURCE_SET": runtime_seals["SCAC_MUTATION_SOURCE_CONTRACT_SET_DIGEST"],
    "SCAC_EXPECTED_CURRENT_CATALOG": runtime_seals["SCAC_MUTATION_DB_CATALOG_BASELINE_DIGEST"],
    "SCAC_CURRENT_NUMBER": "10",
}
subprocess.run(["sh", "-c", validation], check=True, env=validation_env)

# Model the exact attack the SQL predicate closes: changing the canonical
# contract necessarily invalidates the retained digest.
original = {"effect_class": "read", "ingress_key": "fixture"}
tampered = {"effect_class": "write", "ingress_key": "fixture"}
canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
retained_digest = "sha256:" + hashlib.sha256(canonical(original)).hexdigest()
tampered_digest = "sha256:" + hashlib.sha256(canonical(tampered)).hexdigest()
assert retained_digest != tampered_digest
immutable_full_set = "sha256:" + hashlib.sha256(retained_digest.encode()).hexdigest()
attacker_rewritten_header = "sha256:" + hashlib.sha256(tampered_digest.encode()).hexdigest()
assert attacker_rewritten_header != immutable_full_set

print("schema snapshot registry seeds: public-qualified and rebuild-safe")
