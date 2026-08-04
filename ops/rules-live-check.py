#!/usr/bin/env python3
"""rules-live-check.py — is the rule store's ACTIVE set actually the one binding sessions?

WHY THIS EXISTS (2026-08-04, Joe's go). `teach` returned ok. `activate-rule`
returned ok. `run.sh export --only compiled-rules` returned ok and reported 56
rows. All three were true, and the rule was still absent from the file sessions
actually read — because `run.sh export` writes to out/exports/ STAGING by
default and only `CARR_EXPORT_LIVE=1` reaches the vault (exporters/common.py:32).
Three green results and a dormant rule.

The window is narrower than it first looked: bin/refresh-rules.sh runs the export
WITH the live flag hourly from 07:00 to 20:00 (com.carr.rules-refresh), so a rule
taught in business hours lands within the hour on its own. The real exposure is
20:00-07:00, when only the 2am chain sweeps — and the 2am chain has already been
observed both failing and sitting six hours behind a permission prompt.

But the window was never the point. NOTHING REPORTED THE GAP. A narrower silent
window is still a silent window, and this system has now produced the same defect
three times in one day: a smoke suite green while structurally dead, DMARC reports
that would never arrive read as "no spoofing", and an export that stages silently.
So this check does not try to shrink the window. It makes the gap VISIBLE.

WHAT IT COMPARES. The count of active rules in the store, per audience, against
the count each live vault file DECLARES IN ITS OWN HEADER. Both numbers already
exist; nothing was reading them together.

WHY IT IMPORTS THE EXPORTER'S OWN FETCH rather than writing its own query: a
second query would be a second contract, and the two would drift exactly the way
the doc and the script drifted to produce this bug. `_fetch_rules` carries the
non-obvious parts — the intro-scope exclusion (ORDER 37) and the personal_to
split — and a reimplementation that missed either would raise a false alarm every
hour, which is worse than no check because it trains the eye to ignore the row.
Same code, one contract.

READ-ONLY. Opens the DB with the exporter's read credential, writes nothing.

  ./.venv/bin/python ops/rules-live-check.py

Exit 0 = every audience's live file matches the store.
Exit 1 = at least one is behind; the message names which and by how many.
Exit 0 with SKIP = no credential available (house convention: missing env is a
skip, not a failure, so an unconfigured machine does not cry wolf).
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from exporters.common import VAULT, connect          # noqa: E402
from exporters.targets import _fetch_rules           # noqa: E402

# audience label, personal_to slug (None = shared), vault-relative path
AUDIENCES = [
    ("shared", None,  "DNA/compiled-rules-shared.md"),
    ("joe",    "joe", "00_Context/compiled-rules-joe.md"),
]

# The header line each file writes about itself, e.g. "**56 active rule(s), by section.**"
# The footer carries the same number; the header is the one a reader sees first
# and the one the session-start recitation is read from, so it is the one checked.
DECLARED = re.compile(r"\*\*(\d+)\s+active rule\(s\)")


def declared_count(path: Path):
    """The count the file claims about itself, or None if it does not say."""
    if not path.exists():
        return None
    m = DECLARED.search(path.read_text())
    return int(m.group(1)) if m else None


def main():
    try:
        conn = connect()
    except SystemExit as e:
        # connect() sys.exit()s with a message when the credential is absent.
        print(f"SKIP rules-live-check: {e}")
        return 0

    rc = 0
    findings = []
    with conn, conn.cursor() as cur:
        for label, slug, rel in AUDIENCES:
            store_n = len(_fetch_rules(cur, slug))
            path = VAULT / rel
            file_n = declared_count(path)

            if file_n is None:
                findings.append(
                    f"{label}: live file missing or declares no count ({rel}) — "
                    f"store has {store_n}")
                rc = 1
            elif file_n != store_n:
                behind = store_n - file_n
                direction = ("behind by %d" % behind if behind > 0
                             else "AHEAD by %d" % -behind)
                findings.append(
                    f"{label}: store {store_n}, live file {file_n} ({direction}) — "
                    f"{rel}")
                rc = 1
            else:
                findings.append(f"{label}: {store_n} active, live file agrees")

    if rc == 0:
        print("OK rules live — " + "; ".join(findings))
    else:
        print("STALE rules not live — " + "; ".join(findings))
        # The remedy, named here so nobody has to remember the flag. This is the
        # same script the hourly job runs, which is the point: one code path.
        print("  remedy: ~/carr-system/bin/refresh-rules.sh")
        print("  note: a rule that is taught but not exported binds NOBODY, and the "
              "session-start recitation will state the stale count as fact.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
