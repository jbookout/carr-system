#!/usr/bin/env python3
"""Pin the bounded internal control catalog carried by schema snapshots.

WHAT THIS PROTECTS. ops.enforcement_control_catalog is what approve-rule
consults, so a row in it is a claim about what counts as enforcement. Carrying
the table into a tracked snapshot as a plain --table dump would let whatever
happens to be in Production — including free-text implementation prose nobody
reviewed — ride into the repository. That must not happen, and the assertions
below are what stop it.

WIDENED 2026-08-22, from two hand-listed keys to the full declared set. The
boundary that matters was never the number two: it is that the key list comes
from the REPOSITORY rather than from the source database. bin/schema-snapshot.sh
now compiles the list with ops/sync_control_catalog.py — the same module that
generates the seeding migrations, from ops/config/rule-enforcement-map.json and
its companion class file — so a row can only ride along if a reviewed repository
change put its key there. A control present in the source and absent from the
declarations is still NOT carried; it is left for
ops/control-catalog-parity-gate.py to report, which is how ci_gates was found.

WHY IT HAD TO WIDEN, and it is a trap worth remembering. Migrations 0274 and
0275 seeded the catalog in Production. The moment they entered Production's
migration ledger they entered this snapshot too, so a database rebuilt from the
snapshot considered them applied, never ran them, and came up with an empty
catalog — failing the parity gate on 60 absent controls. A snapshot that carries
a migration's LEDGER ROW but not the rows it seeded describes a database nobody
can rebuild, which is Program 1's rebuild clause failing quietly.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "bin" / "schema-snapshot.sh").read_text(encoding="utf-8")
match = re.search(r'VOCAB_TABLES="(?P<tables>.*?)"', SCRIPT, re.DOTALL)
assert match, "schema snapshot vocabulary list is missing"
tables = set(match.group("tables").replace("\\\n", " ").split())

# STILL NOT A TABLE DUMP. This is the assertion the whole file exists for: the
# catalog and the rule-authority tables must never be swept in wholesale.
assert "ops.enforcement_control_catalog" not in tables
assert "ops.rule_control_binding" not in tables
assert "ops.rule_approval_receipt" not in tables
assert "ops.rule_retirement_receipt" not in tables
assert "ops.rule_control_binding" not in match.group("tables")

# The verify-then-render shape, unchanged.
assert "CONTROL_CATALOG_VERIFY" in SCRIPT
assert "CARR REVIEWED CONTROL CATALOG" in SCRIPT
assert "schema snapshot refused: exact reviewed control catalog is missing or drifted" in SCRIPT
assert "never dump arbitrary ops.enforcement_control_catalog rows" in SCRIPT
assert "on conflict (control_key) do nothing;" in SCRIPT

# Timestamps come from the SOURCE ROW, never from the clock at snapshot time —
# otherwise every snapshot would show a fresh verification that never happened.
assert "verified_at,now()" not in SCRIPT
assert "updated_at,now()" not in SCRIPT
assert "to_char(verified_at at time zone 'UTC'" in SCRIPT
assert "to_char(updated_at at time zone 'UTC'" in SCRIPT

# THE KEY LIST IS COMPILED FROM THE REPOSITORY, not typed here and not read from
# whatever the source happens to hold. This is what keeps the widening honest.
assert "ops/sync_control_catalog.py" in SCRIPT or "sync_control_catalog" in SCRIPT
assert "compile_catalog()" in SCRIPT
assert "DECLARED_KEYS" in SCRIPT
assert "could not compile the declared control keys" in SCRIPT
# An empty or truncated list must refuse rather than silently carry nothing.
assert "declared control key list is implausibly short" in SCRIPT
assert "__DECLARED_KEYS__" in SCRIPT and "__DECLARED_COUNT__" in SCRIPT
# Every declared key must be present in the source before any row is rendered.
assert "present <> __DECLARED_COUNT__" in SCRIPT
assert "where control_key in (__DECLARED_KEYS__)" in SCRIPT
# Deterministic order, so two snapshots of the same database are byte-identical.
assert "order by control_key;" in SCRIPT

# The two controls that predate the declaration files are still pinned by their
# FULL identity, because no repository file generates them, so a drift in either
# would otherwise be silent.
# Once each now, in the verify block, rather than twice: the render no longer
# names any key, because it selects the compiled declared list. Pinning the full
# identity is what matters, and that is asserted on the next two lines.
assert SCRIPT.count("'human_authority_runtime'") >= 1
assert SCRIPT.count("'platform_metering_pre_dispatch'") >= 1
assert "migrations/0161_control_plane_authority_boundary.sql; mcp-server/src/mcp.js" in SCRIPT
assert "lib/platform_metering.py; ops/platform-metering-gate.py; hooks/guard-unattended.py" in SCRIPT

# And the snapshot it produces must actually carry more than those two, or the
# widening did not take and the rebuild trap is still open.
SNAPSHOT = ROOT / "db" / "schema.sql"
if SNAPSHOT.is_file():
    carried = SNAPSHOT.read_text(encoding="utf-8").count(
        "insert into ops.enforcement_control_catalog")
    assert carried > 2, (
        f"the committed snapshot carries {carried} control rows; a database rebuilt from it "
        "would fail ops/control-catalog-parity-gate.py on the rest")

print("schema snapshot control catalog selftest: repository-declared, source-verified, "
      "source-timestamped inclusion/exclusion pinned")
