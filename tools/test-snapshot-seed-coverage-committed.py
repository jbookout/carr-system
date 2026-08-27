#!/usr/bin/env python3
"""Run the seed-coverage check against the COMMITTED db/schema.sql.

WHY THIS EXISTS, and it is the gap the sixth review named. ops/snapshot-seed-
coverage.py has exactly one call site: bin/schema-snapshot.sh, against the
freshly composed candidate, just before the write. That is the right place to
refuse a bad snapshot, and it is the wrong place to notice a bad one that is
already committed -- because reaching it needs production read access, so nobody
running CI ever reaches it at all.

The consequence was not hypothetical. Twice in eight days a commit reclassified a
table to carried and added the generator's emit block WITHOUT regenerating the
snapshot, because regenerating needs credentials the author did not have in that
moment. #756 did it for doctrine_gate_check, the doctrine validation registry, so
a rebuilt database ran no doctrine gates at all. #762 did it for agent_profile, so
the bot brief would fail for every named profile. Both landed green. Both were
found later by hand. Nothing in CI could have said a word, because nothing in CI
ever read the committed file.

This closes that. It reads the artifact on disk and the classification beside it,
which is all the check needs -- no database, no credentials, no network. The same
function bin/schema-snapshot.sh calls, pointed at the file that is actually in the
repository.

WHAT A FAILURE HERE MEANS. Not that the check is broken. Either the snapshot needs
regenerating against production (bin/migrate-prod.sh does this, and bin/schema-
snapshot.sh --check reports it), or a classification entry was changed without the
matching emit block. The message names the table either way.
"""
from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "db" / "schema.sql"
CHECKER = ROOT / "ops" / "snapshot-seed-coverage.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("snapshot_seed_coverage", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    if not SNAPSHOT.exists():
        print(f"  FAIL {SNAPSHOT} does not exist")
        return 1
    module = load_checker()
    artifact = SNAPSHOT.read_text(encoding="utf-8", errors="replace")

    # A guard that passes because it read nothing is the failure mode this whole
    # file exists to prevent, so the inputs are asserted before the verdict is
    # trusted. An empty ledger would make every seeded table undetectable and the
    # check would report clean on a file that carries nothing.
    applied = module.applied_migrations(artifact)
    if len(applied) < 100:
        print(f"  FAIL the committed snapshot's ledger holds only {len(applied)} migrations; "
              f"the check would be reading almost nothing")
        return 1
    seeds, _missing = module.seeded_tables(str(ROOT), applied)
    if len(seeds) < 50:
        print(f"  FAIL only {len(seeds)} seeded tables detected from {len(applied)} applied "
              f"migrations; detection has collapsed and a clean verdict would mean nothing")
        return 1

    failures = module.check(str(ROOT), artifact)
    if failures:
        print(f"  FAIL the committed db/schema.sql does not pass its own seed-coverage check "
              f"({len(failures)} finding(s)). A database rebuilt from this file would come up "
              f"short. Regenerate it with bin/schema-snapshot.sh, or fix the classification.\n")
        for failure in failures:
            print("  " + failure.replace("\n", "\n  ") + "\n")
        return 1

    print(f"  ok   the committed db/schema.sql passes seed coverage "
          f"({len(applied)} applied migrations, {len(seeds)} seeded tables classified)")
    print("snapshot seed-coverage committed-artifact selftest: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
