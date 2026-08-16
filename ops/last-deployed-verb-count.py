#!/usr/bin/env python3
"""last-deployed-verb-count.py — what the last deploy of this service to this
environment actually shipped, read from the ledger.

WHY THIS EXISTS. bin/deploy-worker.sh refuses a deploy that would remove verbs,
and it needs a previous number to compare against. That number used to live in
mcp-server/.last-deployed-verb-count.<env>, a file the deploy wrote and the
postflight told you to commit. Committing it fails ops/release-manifest-selftest.py
(defect d737c09c): manifest artifact_paths are ['mcp-server','dealroom'], so the
file sits inside the digested artifact while the digest skips dotfiles — moving
the deployed tree without moving its digest, which is the exact condition that
test guards.

THE FILE WAS ALWAYS A DUPLICATE. Every deploy already records `--verb-count`
into ops.deployment, and since the 2026-08-16 ledger fix it does so for staging
as well as production. Write law 14181e60 settles which copy survives: the
database is the source, a file exists only where a machine requires one, and
nothing requires this one.

ONE CODE PATH, TWO CALLERS (rule a8c55a47): the deploy asks this, and so can CI
or a human. A second copy of the query in shell would be free to drift.

EXIT CODES, and the shell caller depends on telling them apart:
  0   a previous deployment exists; its verb count is printed on stdout
  3   no prior deployment for this service+environment — a genuine first deploy,
      which must be allowed to establish the baseline rather than refused forever
  78  no credential in this environment (EX_CONFIG, the convention bin/nightly.sh
      and bin/run-scheduled.sh already use for "not configured here")
  1   the ledger exists but could not be read — the caller must FAIL CLOSED

WHY FAIL CLOSED IS CHEAP HERE, stated so nobody softens it later: the Worker
being deployed needs Postgres for every verb it serves. A deploy attempted while
the ledger is unreachable would ship something that cannot work anyway. Refusing
costs nothing real, and a loss guard that cannot check must never wave a deploy
through — that is how the 2026-08-09 verb loss happened, when production went
from 75 verbs to 66 and nothing objected.

Usage:  last-deployed-verb-count.py <service> <environment>
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Only states that represent something that actually SHIPPED. A planned or
# aborted row never reached the edge, so its count is not a baseline. `verifying`
# counts: the code is live, only the golden suite is unproven.
SHIPPED_STATES = ("complete", "verifying", "rolled_back", "superseded")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: last-deployed-verb-count.py <service> <environment>",
              file=sys.stderr)
        return 64
    service, environment = sys.argv[1], sys.argv[2]

    spec = importlib.util.spec_from_file_location(
        "ops_record", REPO / "tools" / "ops-record.py")
    if spec is None or spec.loader is None:
        print("last-deployed-verb-count: could not load tools/ops-record.py",
              file=sys.stderr)
        return 1
    ops = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(ops)
    except Exception as exc:
        print(f"last-deployed-verb-count: {exc}", file=sys.stderr)
        return 1

    try:
        conn = ops.connect("read")
    except SystemExit as exc:
        # ops-record raises SystemExit with a credential message when no DSN is
        # set. That is "not configured here", never "the ledger is broken".
        msg = str(exc)
        if "no credential" in msg or "psycopg not installed" in msg:
            print(f"last-deployed-verb-count: {msg}", file=sys.stderr)
            return 78
        print(f"last-deployed-verb-count: {msg}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"last-deployed-verb-count: could not reach the ledger: {exc}",
              file=sys.stderr)
        return 1

    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """select d.verb_count
                     from ops.deployment d
                     join ops.service s on s.id = d.service_id
                    where s.key = %s
                      and d.environment = %s
                      and d.verb_count is not null
                      and d.state = any(%s)
                    order by d.observed_at desc
                    limit 1""",
                (service, environment, list(SHIPPED_STATES)))
            row = cur.fetchone()
    except Exception as exc:
        print(f"last-deployed-verb-count: could not read the ledger: {exc}",
              file=sys.stderr)
        return 1

    if not row or row[0] is None:
        print(f"last-deployed-verb-count: no prior deployment of {service} to "
              f"{environment} carries a verb count", file=sys.stderr)
        return 3

    print(int(row[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
