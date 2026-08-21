#!/usr/bin/env python3
"""Pin the doctrine generation singleton in both rebuild and upgrade paths."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0212_doctrine_meta_singleton.sql"
SNAPSHOTTER = ROOT / "bin" / "schema-snapshot.sh"
DB_GATE = ROOT / "ops" / "staging-retrieval-doctrine-seed-gate.py"


def main() -> int:
    migration = MIGRATION.read_text(encoding="utf-8")
    snapshotter = SNAPSHOTTER.read_text(encoding="utf-8")
    db_gate = DB_GATE.read_text(encoding="utf-8")
    vocab_tables = re.search(r'^VOCAB_TABLES="(.*?)"$', snapshotter, re.MULTILINE | re.DOTALL)
    failures: list[str] = []

    def check(label: str, condition: bool) -> None:
        if condition:
            print(f"  ok    {label}")
        else:
            print(f"  FAIL  {label}")
            failures.append(label)

    canonical_insert = "insert into public.doctrine_meta (id, generation) values (1, 0);"
    check("snapshotter emits canonical doctrine_meta generation zero",
          canonical_insert in snapshotter)
    check("snapshotter does not dump production doctrine_meta state",
          vocab_tables is not None and "doctrine_meta" not in vocab_tables.group(1))
    check("forward migration restores only a missing singleton",
          "insert into public.doctrine_meta (id, generation)" in migration
          and "values (1, 0)" in migration
          and "on conflict (id) do nothing" in migration
          and "update public.doctrine_meta" not in migration.lower())
    check("disposable snapshot/migration gate requires singleton generation zero",
          "select generation from doctrine_meta where id=1" in db_gate
          and "doctrine_meta != 0" in db_gate
          and "on conflict (id) do nothing" not in db_gate)

    print(f"\npassed {4 - len(failures)} · failed {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
