#!/usr/bin/env python3
"""Read one native launchd surface and append its immutable receipt."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.control_plane_scheduler_cutover import (CutoverRefusal, scheduler_launchd_rows)
from lib.device_evidence_submit import Submission, SubmissionRefused
from lib.launchd_scheduler_native import read_native_launchd
from lib.loadpy import load_module_from_path

DEVICE = load_module_from_path("device_evidence_submit_cli", str(REPO / "tools/device-evidence-submit.py"))


def submission_for_surface(surface_id: str, *, home: Path = Path.home()) -> Submission:
    registry = json.loads((REPO / "ops/config/control-plane-scheduler-cutover.v1.json").read_text(encoding="utf-8"))
    manifest = json.loads((REPO / "ops/config/control-plane-workflows.v1.json").read_text(encoding="utf-8"))
    matches = [row for row in scheduler_launchd_rows(registry, manifest=manifest, repo=REPO)
               if row[2] == surface_id]
    if len(matches) != 1:
        raise CutoverRefusal("surface is not one registered launchd scheduler task")
    (_workflow, _version, _surface, locator, relpath, installed_name, arguments_json,
     plist_sha, schedule_sha, timezone_name) = matches[0]
    native = read_native_launchd(
        home=home, repo=REPO, locator=locator, repo_plist_relpath=relpath,
        installed_plist_name=installed_name,
        expected_program_arguments=json.loads(arguments_json), plist_sha256=plist_sha,
        schedule_sha256=schedule_sha, expected_timezone=timezone_name,
    )
    return DEVICE.validate_submission({
        "schema_version": 1, "kind": "launchd_scheduler_observation",
        "surface_id": surface_id, **native,
    })


def main() -> int:
    parser = argparse.ArgumentParser(description="capture one provider-native launchd scheduler observation")
    parser.add_argument("--surface", required=True)
    parser.add_argument("--credential-file", type=Path, default=DEVICE.DEFAULT_CREDENTIAL_FILE)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        DEVICE.reject_broad_environment(dict(os.environ))
        submission = submission_for_surface(args.surface)
        if args.validate_only:
            print(json.dumps({"ok": True, "validated_native_read": True,
                              "surface_id": args.surface}, sort_keys=True))
            return 0
        dsn = DEVICE.load_dedicated_dsn(args.credential_file)
        receipt_ref = DEVICE.execute_submission(dsn, submission)
    except (CutoverRefusal, SubmissionRefused, OSError, ValueError, json.JSONDecodeError):
        print("FAIL launchd-scheduler-observe: native launchd read refused", file=sys.stderr)
        return 78
    except Exception:
        print("FAIL launchd-scheduler-observe: database submission unavailable", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "surface_id": args.surface,
                      "receipt_ref": receipt_ref}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
