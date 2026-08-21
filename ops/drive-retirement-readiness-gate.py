#!/usr/bin/env python3
"""Phase 4 preflight: inventory completeness is necessary, never retirement proof.

This gate deliberately has no ``--evidence`` argument.  JSON supplied by a
caller is not an immutable receipt, so it cannot prove that a reader was
repointed, recovery was exercised, or Joe approved a retirement batch.  Those
facts must be resolved by a separate least-privilege record-layer verifier.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("drive_inventory_gate", ROOT / "ops" / "drive-dependency-inventory.py")
assert SPEC and SPEC.loader
inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inventory
SPEC.loader.exec_module(inventory)


def registry_preflight(root: Path, registry_path: Path) -> dict[str, Any]:
    registry = inventory._read_json(registry_path)
    entries = inventory.validate_registry(
        registry, schema=inventory._read_json(root / inventory.DEFAULT_SCHEMA))
    refs, uncovered, multiple = inventory.audit(root, registry)
    if uncovered or multiple:
        raise inventory.InventoryError(
            f"inventory incomplete: refs={len(refs)} uncovered={len(uncovered)} multiple={len(multiple)}")
    operational = [entry for entry in entries if entry["class"] in inventory.OPERATIONAL_CLASSES]
    unrepointed = [entry["id"] for entry in operational
                   if not isinstance(entry.get("replacement"), dict)
                   or entry["replacement"].get("status") != "accepted"]
    return {"references": len(refs), "entries": len(entries), "operational_entries": len(operational),
            "unrepointed": unrepointed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--phase4-exit", action="store_true",
                        help="ask whether static inventory alone may close Phase 4 (always refuses)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    registry_path = args.registry or root / inventory.DEFAULT_REGISTRY
    try:
        preflight = registry_preflight(root, registry_path)
    except inventory.InventoryError as exc:
        print(f"NOT READY: {exc}", file=sys.stderr)
        return 2
    print(f"Drive inventory complete: {preflight['references']} references / {preflight['entries']} classifications; "
          f"{len(preflight['unrepointed'])} operational dependencies not accepted for retirement.")
    if args.phase4_exit:
        print("NOT READY: static registry cannot resolve immutable repoint receipts, recovery receipts, "
              "or Joe's authority receipt. Use the record-layer verifier; caller JSON is refused.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
