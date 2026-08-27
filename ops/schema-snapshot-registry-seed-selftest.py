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

print("schema snapshot registry seeds: public-qualified and rebuild-safe")
