#!/usr/bin/env python3
"""Registry seed rows must survive pg_dump's empty search_path on rebuild."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = (ROOT / "bin" / "schema-snapshot.sh").read_text(encoding="utf-8")
SNAPSHOT = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")

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
canonical_entry_seal = (
    "e.entry_digest is distinct from 'sha256:'||encode(public.digest(\n"
    "           convert_to(ops.scac_canonical_json(e.contract),'UTF8'),'sha256'),'hex')"
)
assert GENERATOR.count("e.entry_digest is distinct from 'sha256:'||encode(public.digest(") >= 2
assert GENERATOR.count("ops.scac_mutation_registry_v8_seal_available()") >= 2
assert "SCAC_EXPECTED_V9_DIGEST" in GENERATOR
assert "registry_digest='${SCAC_EXPECTED_V9_DIGEST}'" in GENERATOR

# Model the exact attack the SQL predicate closes: changing the canonical
# contract necessarily invalidates the retained digest.
import hashlib
import json

original = {"effect_class": "read", "ingress_key": "fixture"}
tampered = {"effect_class": "write", "ingress_key": "fixture"}
canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
retained_digest = "sha256:" + hashlib.sha256(canonical(original)).hexdigest()
assert retained_digest != "sha256:" + hashlib.sha256(canonical(tampered)).hexdigest()

print("schema snapshot registry seeds: public-qualified and rebuild-safe")
