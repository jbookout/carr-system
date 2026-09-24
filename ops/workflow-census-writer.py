#!/usr/bin/env python3
# doctrine: doctorcre-v5-astra-integration-review
"""The single writer of the V5-F09 workflow census store.

WHAT IT DOES, in order, once per run:
  1. Reads the control plane's own rows in ONE read-only statement through the
     canonical tap (tools/db-tap.py sql): ops.job_definition, ops.workflow_acceptance,
     and every registered native scheduler surface with its latest observation
     receipt and any disable receipt that covers it.
  2. Runs the F09 classifier (lib/control_plane_workflow_truth.workflow_truth)
     over those rows plus the checked-in workflow manifest.
  3. Records the result through ``./run.sh call record-workflow-census`` -- the
     one write door, reached through the deployed Worker, which stamps the
     principal and the server time, extends the database hash chain
     (migration 0595) and then advances the external anchor to the new head.
     This script supplies neither the principal nor the time.

WHAT IT DOES NOT CLAIM.  The Completion Register (ops.completion_projection)
needs a server-derived tenant this tap does not carry, so completion is passed
to the classifier as UNREADABLE and every row is capped accordingly -- an unread
input is recorded as unread, never defaulted.  Native scheduler observations are
whatever the latest receipts say; the store attests what this run recorded, not
that those observations are true.

FAIL-CLOSED.  A tap that cannot be read, rows the classifier refuses, or a write
door refusal exits non-zero with one line naming which; nothing partial is
recorded.  bin/run-scheduled.sh (the launchd wrapper) makes that exit durable.

Runs daily from ops/launchd/com.carr.workflow-census-writer.plist
(StartCalendarInterval, not StartInterval: interval jobs have been seen never
firing on this Mac).  ``--dry-run`` classifies and prints the census summary
without recording it.

RISK: YELLOW.  Read-only against the database; one append through the record
layer's write door per run.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.control_plane_workflow_truth import UNREADABLE, workflow_truth  # noqa: E402

MANIFEST_PATH = REPO / "ops" / "config" / "control-plane-workflows.v1.json"
REGISTRY_PATH = REPO / "ops" / "config" / "control-plane-scheduler-cutover.v1.json"
RUN_SH = REPO / "run.sh"

# ONE STATEMENT, therefore one read-only transaction (the tap opens its session
# with default_transaction_read_only=on), so the three row sets are one snapshot.
CENSUS_INPUT_STATEMENT = """select json_build_object(
  'definitions', coalesce((select json_agg(json_build_object(
       'key', d.key, 'version', d.version, 'enabled', d.enabled,
       'execution_contract', d.execution_contract,
       'legacy_disabled_at', d.legacy_disabled_at)
     order by d.key, d.version) from ops.job_definition d), '[]'::json),
  'acceptances', coalesce((select json_agg(json_build_object(
       'workflow_key', a.workflow_key, 'workflow_version', a.workflow_version,
       'mode', a.mode, 'status', a.status)
     order by a.workflow_key, a.workflow_version, a.mode, a.status)
     from ops.workflow_acceptance a), '[]'::json),
  'surfaces', coalesce((select json_agg(json_build_object(
       'workflow_key', s.workflow_key, 'workflow_version', s.workflow_version,
       'surface_id', s.surface_id, 'locator', s.locator,
       'scheduler_kind', s.scheduler_kind, 'duplicate_group', s.duplicate_group,
       'disable_receipt_ref', (select r.receipt_ref
            from ops.legacy_schedule_disable_receipt r
           where r.workflow_key = s.workflow_key and r.workflow_version = s.workflow_version
             and (r.surface_id = s.surface_id or r.sibling_surface_id = s.surface_id)
           order by r.approved_at desc, r.id desc limit 1),
       'observation', (select json_build_object('scheduler_state', o.scheduler_state,
                                                'observed_at', o.observed_at)
            from ops.legacy_schedule_observation_receipt o
           where o.surface_id = s.surface_id
           order by o.observed_at desc, o.id desc limit 1))
     order by s.surface_id) from ops.legacy_schedule_surface_registry s), '[]'::json)
)::text"""


class WriterRefusal(RuntimeError):
    """One named reason this run records nothing."""


def read_census_inputs() -> dict:
    venv = REPO / ".venv" / "bin" / "python"
    try:
        proc = subprocess.run(
            [str(venv if venv.exists() else sys.executable), str(REPO / "tools" / "db-tap.py"),
             "sql", "/dev/stdin"],
            input=CENSUS_INPUT_STATEMENT, cwd=str(REPO), text=True, capture_output=True,
            timeout=180, env={k: v for k, v in os.environ.items()
                              if k != "CARR_BREAK_GLASS"})
    except Exception as exc:  # noqa: BLE001 - reported as the refusal
        raise WriterRefusal(f"tap_unreachable: {type(exc).__name__}") from None
    if proc.returncode != 0:
        raise WriterRefusal("tap_unreachable: the canonical tap exited non-zero")
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                rows = json.loads(line)
            except ValueError:
                break
            if isinstance(rows, dict):
                return rows
    raise WriterRefusal("tap_answer_unparseable")


def build_census(rows: dict, *, now: datetime) -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    for name in ("definitions", "acceptances", "surfaces"):
        if not isinstance(rows.get(name), list):
            raise WriterRefusal(f"tap_answer_shape_refused: {name}")
    return workflow_truth(
        declarations=manifest["workflows"],
        definitions=rows["definitions"],
        acceptances=rows["acceptances"],
        surfaces=rows["surfaces"],
        # The Completion Register needs a server-derived tenant; unread, never defaulted.
        completion=UNREADABLE,
        observation_max_age_seconds=int(registry["observation_max_age_seconds"]),
        now=now,
    )


def record_census(census: dict) -> dict:
    """Append through the one write door, and see the external anchor advanced.

    The Worker advances the census anchor (a Durable Object outside the
    database) after the row commits.  If that advance fails the verb says
    ``workflow_census_anchor_not_advanced`` with the row already committed; the
    SAME idempotency key is sent once more, which replays the row and
    re-advances the anchor.  A second failure exits non-zero, so the run is
    recorded as failed and the reader shows the mismatch until the next run.
    """
    args = {"idempotency_key": f"workflow-census-writer:{uuid.uuid4()}", "census": census}
    try:
        return _record_once(args)
    except WriterRefusal as refusal:
        if "workflow_census_anchor_not_advanced" not in str(refusal):
            raise
    return _record_once(args)


def _record_once(args: dict) -> dict:
    """One call of the write door.  The child carries no DATABASE_URL."""
    child_env = {"HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", ""),
                 "LANG": os.environ.get("LANG", "C")}
    try:
        proc = subprocess.run([str(RUN_SH), "call", "record-workflow-census", json.dumps(args)],
                              cwd=str(REPO), env=child_env, capture_output=True, text=True,
                              timeout=120)
    except Exception as exc:  # noqa: BLE001
        raise WriterRefusal(f"write_door_unreachable: {type(exc).__name__}") from None
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise WriterRefusal(f"write_door_refused: {tail[-1][:300] if tail else '(no output)'}")
    try:
        answer = json.loads(proc.stdout)
    except ValueError:
        raise WriterRefusal("write_door_answer_unparseable") from None
    if not isinstance(answer, dict) or answer.get("ok") is not True or not answer.get("row_hash"):
        raise WriterRefusal(f"write_door_refused: {json.dumps(answer)[:300]}")
    if answer.get("anchor") not in ("advanced", "replayed"):
        raise WriterRefusal(f"anchor_unconfirmed: {json.dumps(answer.get('anchor'))[:100]}")
    return answer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="classify and print the summary; record nothing")
    options = parser.parse_args(argv)
    try:
        census = build_census(read_census_inputs(), now=datetime.now(timezone.utc))
        if options.dry_run:
            print(json.dumps({"dry_run": True, "workflows": census["summary"]["workflows"],
                              "summary": census["summary"]}, sort_keys=True))
            return 0
        answer = record_census(census)
    except WriterRefusal as refusal:
        print(f"workflow-census-writer: REFUSED — {refusal}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - classifier refusals and anything else
        print(f"workflow-census-writer: REFUSED — census_unbuildable: {type(exc).__name__}: "
              f"{str(exc)[:300]}", file=sys.stderr)
        return 1
    print(f"workflow-census-writer: recorded seq {answer['seq']} at {answer['recorded_at']} "
          f"by {answer['principal']} ({census['summary']['workflows']} workflows; "
          f"row_hash {answer['row_hash'][:16]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
