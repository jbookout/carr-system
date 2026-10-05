#!/usr/bin/env python3
"""Nightly bounded fit calibration from attributed orchestration outcomes.

Usage: python3 ops/model-router-nightly.py --orch-dir /path/out/orch
Loop logs need a sibling .meta.json with {model, task_kind, attempt_id?}.
Optional --outcomes JSONL contains {id, model, task_kind, first_pass_approve?,
ci_first_push?, rounds_to_approve?, no_progress?, attempt_id?}. It is an
explicit orchestrator projection of existing logs/receipts, never authority.
When attempt_id is supplied, read-attempt-reliability must bind that ID before
the row is admitted. Persist original receipts through record-attempt-receipt
at their producer; this script never invents receipt bindings or attestations.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib import model_router


def read_reliability(attempt_id):
    result = subprocess.run([str(REPO / "run.sh"), "call", "read-attempt-reliability",
                             json.dumps({"attempt_id": attempt_id})], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("read-attempt-reliability failed; retry next nightly run")
    response = json.loads(result.stdout)
    canonical = response.get("reliability", {}).get("canonical_binding", {})
    if not response.get("ok") or canonical.get("attempt_id") != attempt_id:
        raise ValueError("attempt reliability binding mismatch")
    return response["reliability"]


def outcomes_from(orch_dir, outcomes_path=None, reader=read_reliability):
    outcomes, skipped = [], []
    for path in sorted(Path(orch_dir).glob("loop-*.log")):
        try:
            attribution = model_router.load_json(path.with_suffix(".meta.json"))
            row = model_router.log_outcome(path, attribution)
            if row:
                outcomes.append(row)
        except (OSError, ValueError):
            skipped.append({"source": path.name, "reason": "missing model/task attribution; write sibling .meta.json"})
    if outcomes_path:
        with Path(outcomes_path).open(encoding="utf-8") as stream:
            outcomes.extend(json.loads(line) for line in stream if line.strip())
    admitted = []
    for row in outcomes:
        try:
            if attempt := row.get("attempt_id"):
                reliability = reader(attempt)
                row["reliability_state"] = reliability["reliability"]["state"]
                # Canonical evidence gaps are not model failures; keep observable log metrics only.
                row["id"] = "attempt:" + attempt
            admitted.append(row)
        except (ValueError, KeyError, OSError, subprocess.TimeoutExpired):
            skipped.append({"source": row.get("id"), "reason": "canonical attempt readback failed; retry next run"})
    return admitted, skipped


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
        temporary = stream.name
    os.replace(temporary, path)


def run(orch_dir, outcomes_path=None, day=None, reader=read_reliability):
    day = day or dt.datetime.now(dt.timezone.utc).date().isoformat()
    dt.date.fromisoformat(day)
    budget = Path(orch_dir) / "budget"
    budget.mkdir(parents=True, exist_ok=True)
    outcomes, skipped = outcomes_from(orch_dir, outcomes_path, reader)
    with (budget / "router.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fit = model_router.current_fit(budget)
        candidate = model_router.learn(fit, outcomes, day=day)
        report = {"day": day, "inputs": len(outcomes), "changes": candidate["learning_changes"], "skipped": skipped}
        if candidate["learning_changes"]:
            digest = hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest()[:12]
            name = f"model-fit.{day}.{digest}.json"
            write_json(budget / name, candidate)
            write_json(budget / "model-fit-current.json", {"revision_file": name})
            report["revision_file"] = name
        with (budget / "learning.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(report) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orch-dir", type=Path, default=REPO / "out/orch")
    parser.add_argument("--outcomes", type=Path)
    parser.add_argument("--day")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.orch_dir, args.outcomes, args.day)))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"model-router-nightly: {exc}; retain previous fit and retry next run", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
