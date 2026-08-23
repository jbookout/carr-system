#!/usr/bin/env python3
"""Nightly watch: has Production's control-plane registry drifted from the manifest?

WHY THIS EXISTS (2026-08-23). ops/control-plane-registry-gate.py proves the
committed manifest and the committed schema agree, on a throwaway database, on
every proposed change. It says nothing about Production. On 2026-08-23 the first
run of that gate against Production found 5 job definitions where the manifest
declares 25, zero cognition contracts where it declares 8, and the only enabled
definition carrying a downgraded risk colour. Nobody had done anything wrong;
there was simply no path by which the reviewed configuration reached Production,
and so no drift anyone could see.

bin/sync-control-plane-prod.sh is now that path, and running it bare reports the
same comparison. But a door only reports when a human opens it. This is the same
comparison on the nightly chain, so a manifest that is committed and never
installed is noticed the following morning rather than at the next audit.

WHAT IT MAY READ, and why that is the whole design. It connects with
CARR_DB_JOBS_URL — the routine-scoped role bin/routine-credential-env.sh already
carries across the unattended boundary — never the owner credential the doors
derive. That role can already select ops.job_definition and ops.cognition_job, so
this watch needs no new grant. It opens a READ ONLY transaction before it issues
anything, and it asserts it is running as carr_jobs afterwards, so a
misconfigured environment fails loudly instead of quietly reading Production
under a writer.

WHAT IT CANNOT WATCH, said out loud rather than left as a silent hole. The other
half of the same problem is the rule-admission contract, and its audit joins the
`rule` table, which the jobs role deliberately cannot select — that table is
authority-scoped. Watching it needs either a grant or a routine credential that
does not exist, which is a decision for Joe and is held as open loop 523.

EXIT CONTRACT, the same one bin/nightly.sh's other checks use:
  78  no routine credential configured — a SKIP, not a failed night
  0   ran; drift is PRINTED, not raised, because drift is a prompt for a human
      and a step that fails every night until someone acts trains people to stop
      reading the alarm (the same reasoning ops/vendor-level-drift-check.py
      states for its own always-zero exit)
  1   the watch itself could not run — a wrong role, an unreadable manifest
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MANIFEST = REPO / "ops" / "config" / "control-plane-workflows.v1.json"
EX_CONFIG = 78


def load_gate_compare():
    """Import compare() from the gate, which is not an importable module name."""
    import importlib.util

    path = REPO / "ops" / "control-plane-registry-gate.py"
    spec = importlib.util.spec_from_file_location("control_plane_registry_gate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the registry gate from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.compare


def routine_dsn() -> str | None:
    value = os.environ.get("CARR_DB_JOBS_URL", "").strip()
    if not value:
        return None
    login = ""
    try:
        login = unquote(urlsplit(value).username or "").strip().lower()
    except ValueError:
        login = ""
    if login in {"carr_writer", "carr_owner", "owner", "writer", "postgres"}:
        print("control-plane-registry-drift: CARR_DB_JOBS_URL names an owner or "
              "writer login; refusing to watch Production under it", file=sys.stderr)
        raise SystemExit(1)
    return value


def main() -> int:
    dsn = routine_dsn()
    if not dsn:
        print("control-plane-registry-drift: NOT CONFIGURED (no CARR_DB_JOBS_URL)",
              file=sys.stderr)
        return EX_CONFIG

    import psycopg

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    compare = load_gate_compare()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # BEGIN must be the first statement of the session. psycopg's default
        # transactional mode opens a read-write transaction on the first query,
        # and a later BEGIN READ ONLY does not retrofit it — the same ordering
        # tools/control-plane.py states for its own collector reads.
        cur.execute("begin transaction read only")
        cur.execute("select session_user, current_user")
        row = cur.fetchone()
        if not row or set(str(v) for v in row) != {"carr_jobs"}:
            print("control-plane-registry-drift: not a provisioned jobs identity "
                  f"({row}); refusing to read Production", file=sys.stderr)
            return 1
        failures, workflows, cognition, not_checked = compare(
            cur, manifest, resolve_managed_enabled=False)

    for skipped in not_checked:
        print(f"control-plane-registry-drift: NOT CHECKED — {skipped}")
    if failures:
        print("control-plane-registry-drift: PRODUCTION HAS DRIFTED from "
              f"{MANIFEST.relative_to(REPO)} — {len(failures)} difference(s):")
        for failure in failures:
            print(f"  {failure}")
        print("  Reconcile with ./bin/sync-control-plane-prod.sh (bare to read, "
              "--apply to install the reviewed manifest).")
        return 0
    print(f"control-plane-registry-drift: production matches the reviewed manifest — "
          f"{workflows} workflows and {cognition} cognition contracts exact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
