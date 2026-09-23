"""Read the SCAC registry successor list (the delta seal, ruling 4 of 8, 2026-09-23).

ops/config/scac-registry-successors.v1.json is the one place a registry
successor from v55 on is declared. The Python gates and selftests that used to
carry their own `v55`/`v56` literals (siep11, siep18, the schema-snapshot seed
selftest) derive the frontier from here, so advancing a seal is a config entry
and the measured numbers, never a hand edit of a dozen scattered ordinals.

Every number returned is read from a committed artifact, never guessed: the
predecessor's digest and entry counts come out of the live migration's own
preflight, which is the pin the database enforces.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "ops" / "config" / "scac-registry-successors.v1.json"
SCHEMA_VERSION = "scac-registry-successors.v1"
FIRST_GENERIC_VERSION = 55
_REGISTRY_PREFIX = "scac-mutation-registry.v"


def load_config(path: Path = CONFIG_PATH) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(f"unsupported successor config schema: {config.get('schema_version')!r}")
    successors = config.get("successors")
    if not isinstance(successors, list) or not successors:
        raise RuntimeError("successor config declares no successors")
    expected = FIRST_GENERIC_VERSION
    previous_migration = None
    for entry in successors:
        if entry["version"] != expected:
            raise RuntimeError(f"successor versions must be contiguous from v{FIRST_GENERIC_VERSION}: found v{entry['version']}, expected v{expected}")
        if previous_migration is not None and entry["predecessor_migration"] != previous_migration:
            raise RuntimeError(f"v{entry['version']} names predecessor {entry['predecessor_migration']!r}, but v{expected - 1} rendered {previous_migration!r}")
        if not re.fullmatch(r"migrations/0\d{3}_[a-z0-9_]+_scac_successor\.sql", entry["migration"]):
            raise RuntimeError(f"v{entry['version']} migration path is malformed: {entry['migration']!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", entry["predecessor_sha256"]):
            raise RuntimeError(f"v{entry['version']} predecessor digest is malformed")
        previous_migration = entry["migration"]
        expected += 1
    return config


def successors(config: dict | None = None) -> list[dict]:
    return list((config or load_config())["successors"])


def registry_version(ordinal: int) -> str:
    return f"{_REGISTRY_PREFIX}{ordinal}"


def successor_registry_versions(config: dict | None = None) -> set[str]:
    return {registry_version(entry["version"]) for entry in successors(config)}


def live_ordinal(config: dict | None = None) -> int:
    return successors(config)[-1]["version"]


def frontier(config: dict | None = None, repo: Path = REPO) -> dict:
    """The live successor and the sealed predecessor it pins, read from the live migration."""
    live = successors(config)[-1]
    live_sql = (repo / live["migration"]).read_text(encoding="utf-8")
    preflight_end = live_sql.index("\ndrop trigger scac_mutation_registry_version_sealed")
    preflight = live_sql[:preflight_end]
    digest = re.search(r"v\.registry_digest is distinct from '(sha256:[0-9a-f]{64})'", preflight)
    entry_count = re.search(r"v\.entry_count<>(\d+)", preflight)
    source_count = re.search(r"v\.source_entry_count<>(\d+)", preflight)
    if digest is None or entry_count is None or source_count is None:
        raise RuntimeError(f"{live['migration']} preflight no longer pins its predecessor where this reader looks")
    return {
        "live_ordinal": live["version"],
        "live_version": registry_version(live["version"]),
        "live_migration": live["migration"],
        "predecessor_ordinal": live["version"] - 1,
        "predecessor_version": registry_version(live["version"] - 1),
        "predecessor_migration": live["predecessor_migration"],
        "predecessor_digest": digest.group(1),
        "predecessor_entry_counts": (int(entry_count.group(1)), int(source_count.group(1))),
    }
