#!/usr/bin/env python3
"""V5-A05: daily cadence sweep -- detect a missed 14-day assurance receipt and
raise the "replan on miss" escalation.

Read-only against the record layer except for the ONE write this job owns:
raise-delivery-cadence-alert, called only when cadence-status (read) reports
status "missed". A subject that is "current" or "no_receipt_on_record" is
left alone -- "no_receipt_on_record" is not itself a miss (nothing has ever
been promised yet for that subject), matching evaluateCadenceReceipt/
ops.v5_a05_cadence_status's own distinction.

Same call path ops/timebomb-audit.py and tools/cutover-watch.py use: a
subprocess to `run.sh call <verb> '<json>'`, never the generic MCP call-verb
passthrough. No seal is owed for this script or its launchd plist (decision
05e144eb, 2026-09-24, PR #1174): script and launchd edits no longer reseal.

Bounded v1 scope: ONE subject, (engineering_program, doctorcre-v5) -- the
whole v5 delivery program's assurance cadence. Nothing in this repository yet
calls record-cadence-receipt to issue a receipt for that subject; this sweep
detects and escalates a miss honestly even while no receipt has ever been
issued (status: no_receipt_on_record is reported as such, never escalated,
since it is not a broken promise). Extending this to per-slice subjects, and
wiring a receipt-issuing producer, is a follow-up, named here rather than
silently assumed.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
RUN_SH = REPO / "run.sh"

# Bounded v1 subject list. Extend here as producers exist to issue receipts
# for a narrower subject; do not invent subjects nothing issues a receipt for.
SUBJECTS = [
    {"subject_type": "engineering_program", "subject_ref": "doctorcre-v5"},
]


def call_verb(verb: str, args: dict) -> tuple[bool, Any]:
    """Never raises -- a verb call that fails is a finding, not a crash."""
    if not RUN_SH.exists():
        return False, f"no such file: {RUN_SH}"
    child_env = {"HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", ""),
                 "LANG": os.environ.get("LANG", "C")}
    try:
        proc = subprocess.run([str(RUN_SH), "call", verb, json.dumps(args)],
                               cwd=str(REPO), env=child_env, capture_output=True,
                               text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001
        return False, f"subprocess failed: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"run.sh call {verb} exit {proc.returncode}: {tail[-1] if tail else '(no output)'}"
    try:
        return True, json.loads(proc.stdout)
    except ValueError:
        return False, f"non-JSON stdout from {verb}: {proc.stdout[:200]!r}"


def sweep_subject(subject: dict) -> dict:
    ok, status = call_verb("cadence-status", subject)
    if not ok:
        return {**subject, "outcome": "status_read_failed", "detail": status}
    if status.get("status") != "missed":
        return {**subject, "outcome": "no_action", "status": status.get("status")}

    idempotency_key = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"delivery-cadence-a05-sweep:{subject['subject_type']}:{subject['subject_ref']}:"
        f"{status.get('expires_at')}"))
    ok, raised = call_verb("raise-delivery-cadence-alert", {
        "idempotency_key": idempotency_key,
        "reason_id": "cadence_miss_replan_required",
        "subject_type": subject["subject_type"],
        "subject_ref": subject["subject_ref"],
        "requires_joe_authority": True,
        "unresolved_intent": False,
        "detail": f"expired {status.get('expires_at')}; sweep detected {status.get('days_since_last_receipt')} days since last receipt",
    })
    if not ok:
        return {**subject, "outcome": "escalation_failed", "detail": raised}
    return {**subject, "outcome": "escalated", "duplicate": raised.get("duplicate"),
            "routing": raised.get("routing", {}).get("routing")}


def main() -> int:
    results = [sweep_subject(subject) for subject in SUBJECTS]
    failed = [r for r in results if r["outcome"] in ("status_read_failed", "escalation_failed")]
    print(json.dumps({"schema_version": "delivery-cadence-a05-sweep.v1", "results": results}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
