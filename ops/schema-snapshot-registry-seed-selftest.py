#!/usr/bin/env python3
"""Registry seed rows must survive pg_dump's empty search_path on rebuild."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
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
RUNTIME_V11 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v11.generated.js").read_text(
    encoding="utf-8"
)
RUNTIME_V12 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v12.generated.js").read_text(
    encoding="utf-8"
)
RUNTIME_V13 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v13.generated.js").read_text(
    encoding="utf-8"
)
RUNTIME_V14 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v14.generated.js").read_text(
    encoding="utf-8"
)
RUNTIME_V15 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v15.generated.js").read_text(
    encoding="utf-8"
)
RUNTIME_V16 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v16.generated.js").read_text(
    encoding="utf-8"
)
RUNTIME_V17 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v17.generated.js").read_text(
    encoding="utf-8"
)
RUNTIME_V18 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v18.generated.js").read_text(
    encoding="utf-8"
)
RUNTIME_V19 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v19.generated.js").read_text(
    encoding="utf-8"
)
RUNTIME_V21 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v21.generated.js").read_text(
    encoding="utf-8"
)
RUNTIME_V23 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v23.generated.js").read_text(
    encoding="utf-8"
)
RUNTIME_V22 = (ROOT / "mcp-server" / "src" / "scac-mutation-registry.v22.generated.js").read_text(
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
assert set(FULL_SET_SEALS) == {f"scac-mutation-registry.v{version}" for version in range(1, 25)}
assert all(len(value) == 71 and value.startswith("sha256:") for value in FULL_SET_SEALS.values())
assert FULL_SET_SEALS["scac-mutation-registry.v10"] != "sha256:" + "0" * 64
assert FULL_SET_SEALS["scac-mutation-registry.v20"] == (
    "sha256:f5729a51a6e81684db7871ec1a604370c829d8402f7ed76e233eddbe966e8f6a"
)
assert FULL_SET_SEALS["scac-mutation-registry.v21"] == (
    "sha256:e687f2111d7c31feefde336a90a2413af0969b65bb8c0d43fe16c7d14d1dab91"
)
# v22 became readable only once 0497 sealed it. The digest is the exact
# full-entry-set value authenticated against a real database before the v22
# frontier was cleaned up, and it is deliberately NOT the v22 registry digest:
# the entry-set seal hashes the entry digests, the registry digest hashes the
# projection, and equating them would seal a frontier nobody measured.
assert FULL_SET_SEALS["scac-mutation-registry.v22"] == (
    "sha256:c5c01d95676ee51e691c01bb4d90d5e5fde18a2e634089d51974dbcdf8285b51"
)
assert (FULL_SET_SEALS["scac-mutation-registry.v22"]
        != "sha256:5bbe68942f2652523b52c024c07615b1718b59c7de4c0e41524a955566ba5f75")
# v23 became readable only once 0498 sealed it, on the same one-behind rule. The
# digest was measured against a real disposable database carrying every
# migration through 0497, and it is deliberately NOT the v23 registry digest.
assert FULL_SET_SEALS["scac-mutation-registry.v23"] == (
    "sha256:b6d1d0b0f72cc6ef3a6f6e2026bf1de479a39a066f03656a93f817c4246d87b2"
)
assert (FULL_SET_SEALS["scac-mutation-registry.v23"]
        != "sha256:d6633db96266ebd54bf6ade83cd35b587ee9f128f0f9db35ea557861e66f743b")
assert (FULL_SET_SEALS["scac-mutation-registry.v23"]
        != FULL_SET_SEALS["scac-mutation-registry.v22"])
# v24 became readable only once 0501 sealed it, on the same one-behind rule. The
# digest was measured against a real disposable database carrying every
# migration through 0500, and it is deliberately NOT the v24 registry digest.
assert FULL_SET_SEALS["scac-mutation-registry.v24"] == (
    "sha256:0740fee473c1ab6882ddf629372eaae39657dd3f37f2ace349dbebbf2bab9157"
)
assert (FULL_SET_SEALS["scac-mutation-registry.v24"]
        != "sha256:d280236b45e706ba6e2c642a526ffc827afdc0a1e2220331fb0424ea16758c23")
assert (FULL_SET_SEALS["scac-mutation-registry.v24"]
        != FULL_SET_SEALS["scac-mutation-registry.v23"])
assert FULL_SET_SEALS["scac-mutation-registry.v12"] == (
    "sha256:e0cae72f977332f93e02ce7c30f5b00a5438b13500dcc0e3d6d33db2f3685f9d"
)
assert FULL_SET_SEALS["scac-mutation-registry.v13"] == (
    "sha256:b21b1f23b674b0f15113961d5be020335e1742b0125aa211861d7877204003b0"
)
assert FULL_SET_SEALS["scac-mutation-registry.v14"] == (
    "sha256:91df326aea6172a2619e0e4582c6d19e10a63635d5ca67814f357278154008ba"
)
assert FULL_SET_SEALS["scac-mutation-registry.v15"] == (
    "sha256:72e4a6b57a9e9e085b1192873defcccc7772ceea3784a09ebaba0df8e5e5cd32"
)
assert GENERATOR.count("SCAC_FULL_SET_SQL") >= 3
assert "SCAC_EXPECTED_CURRENT_DIGEST" in GENERATOR
assert "registry_digest='${SCAC_EXPECTED_CURRENT_DIGEST}'" in GENERATOR
assert "SCAC_EXPECTED_CURRENT_SOURCE_SET" in GENERATOR
assert "SCAC_EXPECTED_CURRENT_CATALOG" in GENERATOR
# The v24 frontier the V5-F09 workflow-truth successor installs. Pinned BEFORE
# the v23 branch below so this file fails if the snapshot ever loses the current
# frontier while keeping its history -- the failure mode a substring-presence
# test is otherwise blind to.
assert "SCAC_CURRENT_NUMBER=25" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=37054" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1608" in GENERATOR
assert "SCAC_CURRENT_SOURCE_COUNT=839" in GENERATOR
assert "SCAC_FULL_SET_SEAL_COUNT=24" in GENERATOR
assert "ops.scac_mutation_catalog_v25_current()" in GENERATOR
assert "0501_scheduled_job_admission_and_scac_successor.sql" in GENERATOR
# The v24 frontier stays a selectable branch behind the new one.
assert "SCAC_CURRENT_NUMBER=24" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=35446" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1600" in GENERATOR
assert "SCAC_FULL_SET_SEAL_COUNT=23" in GENERATOR
assert "ops.scac_mutation_catalog_v24_current()" in GENERATOR
assert "0498_f09_workflow_truth_and_scac_successor.sql" in GENERATOR
# The predecessor probe stays reachable: v23 remains a selectable branch.
assert "SCAC_CURRENT_NUMBER=23" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=33846" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1596" in GENERATOR
assert "SCAC_CURRENT_SOURCE_COUNT=835" in GENERATOR
assert "SCAC_FULL_SET_SEAL_COUNT=22" in GENERATOR
assert "ops.scac_mutation_catalog_v23_current()" in GENERATOR
assert "0497_r07_repo_hygiene_janitor_and_scac_successor.sql" in GENERATOR
# The predecessor probe stays: the v22 arm still has to be reachable.
assert "0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql" in GENERATOR
assert "SCAC_CURRENT_NUMBER=21" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=30660" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1528" in GENERATOR
assert "SCAC_FULL_SET_SEAL_COUNT=20" in GENERATOR
assert "ops.scac_mutation_catalog_v21_current()" in GENERATOR
assert "0495_r06_hooks_correctness_scac_successor.sql" in GENERATOR
assert "SCAC_CURRENT_NUMBER=20" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=29132" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1524" in GENERATOR
assert "SCAC_CURRENT_SOURCE_COUNT=828" in GENERATOR
assert "ops.scac_mutation_catalog_v20_current()" in GENERATOR
assert "SCAC_CURRENT_NUMBER=19" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=27608" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1520" in GENERATOR
assert "SCAC_CURRENT_SOURCE_COUNT=828" in GENERATOR
assert "ops.scac_mutation_catalog_v19_current()" in GENERATOR
assert "SCAC_CURRENT_NUMBER=18" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=26088" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1515" in GENERATOR
assert "SCAC_CURRENT_SOURCE_COUNT=827" in GENERATOR
assert "ops.scac_mutation_catalog_v18_current()" in GENERATOR
assert "SCAC_CURRENT_NUMBER=17" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=24573" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1509" in GENERATOR
assert "ops.scac_mutation_catalog_v17_current()" in GENERATOR
assert "SCAC_CURRENT_NUMBER=16" in GENERATOR
assert "SCAC_CURRENT_NUMBER=15" in GENERATOR
assert "SCAC_CURRENT_NUMBER=14" in GENERATOR
assert "SCAC_CURRENT_NUMBER=13" in GENERATOR
assert "SCAC_CURRENT_NUMBER=12" in GENERATOR
assert "SCAC_CURRENT_NUMBER=11" in GENERATOR
assert "SCAC_CURRENT_NUMBER=10" in GENERATOR
assert "SCAC_CURRENT_NUMBER=9" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=23064" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=21561" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=20062" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=18567" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=17076" in GENERATOR
assert "SCAC_TOTAL_ENTRY_COUNT=15589" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1499" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1495" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1491" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1487" in GENERATOR
assert "SCAC_CURRENT_ENTRY_COUNT=1471" in GENERATOR
assert "SCAC_CURRENT_SOURCE_COUNT=825" in GENERATOR
assert "SCAC_CURRENT_SOURCE_COUNT=819" in GENERATOR
assert "ops.scac_mutation_catalog_v16_current()" in GENERATOR
assert "ops.scac_mutation_catalog_v15_current()" in GENERATOR
assert "ops.scac_mutation_catalog_v14_current()" in GENERATOR
assert "ops.scac_mutation_catalog_v13_current()" in GENERATOR
assert "ops.scac_mutation_catalog_v12_current()" in GENERATOR
assert "ops.scac_mutation_catalog_v11_current()" in GENERATOR
assert "ops.scac_mutation_catalog_v10_current()" in GENERATOR
assert GENERATOR.count("order by e.entry_digest collate") >= 2
assert "not ${SCAC_CURRENT_CATALOG_FUNCTION}" in GENERATOR
numeric_registry_order = (
    "array_agg(registry_version order by "
    "split_part(registry_version,'.v',2)::integer)"
)
assert numeric_registry_order in GENERATOR
versions = [f"scac-mutation-registry.v{version}" for version in range(1, 24)]
assert sorted(versions, key=lambda value: int(value.rsplit("v", 1)[1])) == versions
assert sorted(versions) != versions

loader_start = GENERATOR.index("SCAC_FULL_SET_SQL=\"$(node -e '\n") + len(
    "SCAC_FULL_SET_SQL=\"$(node -e '\n"
)
loader_end = GENERATOR.index("\n  ' \"$SCAC_FULL_SET_SEALS\" \"$SCAC_FULL_SET_SEAL_COUNT\")\"", loader_start)
loader = GENERATOR[loader_start:loader_end]
loaded_sql = subprocess.run(
    ["node", "-e", loader, str(ROOT / "ops" / "config" / "scac-registry-full-entry-set-seals.json"), "24"],
    check=True,
    capture_output=True,
    text=True,
).stdout
assert loaded_sql.count("scac-mutation-registry.v") == 24
assert loaded_sql.count("sha256:") == 24
assert FULL_SET_SEALS["scac-mutation-registry.v24"] in loaded_sql, (
    "the newest sealed history must actually reach the SQL the snapshot embeds"
)

# NEGATIVE HALF. The positive above only shows the loader renders 21 rows; it
# says nothing about whether a tampered seal file would be caught. These three
# feed the loader deliberately broken input and require a nonzero exit, so a
# seal set that lost v22, gained a stray version, or carried a malformed digest
# cannot be rendered into a snapshot as if it were sealed history.
def loader_rejects(seals: dict, count: str) -> bool:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(seals, handle)
        path = handle.name
    try:
        return subprocess.run(["node", "-e", loader, path, count],
                              capture_output=True, text=True).returncode != 0
    finally:
        os.unlink(path)


dropped = {k: v for k, v in FULL_SET_SEALS.items() if k != "scac-mutation-registry.v24"}
assert loader_rejects(dropped, "24"), "a seal file missing v24 must not load"
assert loader_rejects(dropped, "21"), (
    "lowering the count must not be a way to hide a missing v24 seal"
)
malformed = dict(FULL_SET_SEALS, **{"scac-mutation-registry.v24": "sha256:not-a-digest"})
assert loader_rejects(malformed, "24"), "a malformed v24 seal must not load"

def runtime_seal(source: str, name: str) -> str:
    match = re.search(rf'^export const {name} = "([0-9a-f]{{64}})";$', source, re.MULTILINE)
    assert match is not None, f"missing generated runtime seal {name}"
    return match.group(1)


runtime_seals = {name: runtime_seal(RUNTIME_V23, name) for name in (
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
    "SCAC_CURRENT_NUMBER": "22",
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

assert FULL_SET_SEALS["scac-mutation-registry.v16"] == "sha256:605e5566322523db7606375f01931e15379225cd26367d76581df17575c6eebc"
assert FULL_SET_SEALS["scac-mutation-registry.v17"] == (
    "sha256:bbcf8140e89da1b4b7f7cfced199ddd2fa11c110bdbad2852f562742625e2c16"
)
assert FULL_SET_SEALS["scac-mutation-registry.v18"] == (
    "sha256:c4666a4bf4e06ddb9254a319ae19ad67b3a6c10590a994da7aa26481b73bc0c4"
)
# v19's full entry set is only readable from a database that has applied 0493:
# it covers the DB-catalog-projected ACL rows as well as the 828 source rows.
# This value is the production post-image read back after 0493 landed.
assert FULL_SET_SEALS["scac-mutation-registry.v19"] == (
    "sha256:9f350292253eaf1d0b57f6c453b92330ceeee7f9c3a3c372a1d61968fc22c9f3"
)
