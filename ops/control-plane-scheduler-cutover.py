#!/usr/bin/env python3
"""Read-only launchd cutover observations and evidence verification."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.control_plane_scheduler_cutover import (CutoverRefusal, observe_launchd,
                                                  prepare_disable, validate_registry,
                                                  verify_disabled)
from lib.control_plane_scheduler_cutover_db import resolver_from_environment
from lib.loadpy import load_module_from_path


def registry() -> dict:
    data = json.loads((REPO / "ops/config/control-plane-scheduler-cutover.v1.json").read_text(encoding="utf-8"))
    manifest = json.loads((REPO / "ops/config/control-plane-workflows.v1.json").read_text(encoding="utf-8"))
    errors = validate_registry(data, manifest=manifest)
    if errors:
        raise CutoverRefusal("invalid scheduler cutover registry: " + "; ".join(errors))
    return data


def launchd_observation(surface_id: str) -> dict:
    data = registry()
    surface = next((item for item in data["surfaces"] if item["surface_id"] == surface_id), None)
    if not isinstance(surface, dict):
        raise CutoverRefusal("scheduler surface is unknown")
    truth = load_module_from_path("scheduler_truth", str(REPO / "tools/scheduler-truth.py"))
    config_as_code = load_module_from_path("config_as_code", str(REPO / "ops" / "config-as-code.py"))
    if (Path(config_as_code.LAUNCHD_REPO) != Path(truth.REPO_PLISTS)
            or Path(config_as_code.LAUNCHD_SRC) != Path(truth.INSTALLED)):
        raise CutoverRefusal("scheduler-truth and config-as-code launchd read seams disagree")
    locator = surface["locator"]
    repo_plist = truth.read_plist(str(Path(truth.REPO_PLISTS) / f"{locator}.plist"))
    installed_plist = truth.read_plist(str(Path(truth.INSTALLED) / f"{locator}.plist"))
    return observe_launchd(surface, repo_plist=repo_plist, installed_plist=installed_plist,
                           repo_path=str(Path("ops") / "launchd" / f"{locator}.plist"),
                           installed_path=f"{locator}.plist",
                           loaded_labels=truth.loaded_labels(),
                           observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    observation = sub.add_parser("observe")
    observation.add_argument("--surface", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--surface", required=True)
    prepare.add_argument("--observation-json", required=True)
    prepare.add_argument("--replacement-json", required=True)
    verify = sub.add_parser("verify-disabled")
    verify.add_argument("--prepared-json", required=True)
    verify.add_argument("--pre-observation-json", required=True)
    verify.add_argument("--post-observation-json", required=True)
    verify.add_argument("--human-approval-ref", required=True)
    args = parser.parse_args()
    try:
        if args.command == "observe":
            result = launchd_observation(args.surface)
        elif args.command == "prepare":
            resolver = resolver_from_environment()
            try:
                result = prepare_disable(
                    registry(), surface_id=args.surface, observation=json.loads(args.observation_json),
                    replacement=json.loads(args.replacement_json),
                    receipt_verifier=resolver.acceptance_receipt,
                )
            finally:
                resolver.close()
        else:
            data = registry()
            prepared = json.loads(args.prepared_json)
            resolver = resolver_from_environment()
            try:
                result = verify_disabled(
                    data, prepared=prepared, pre_disable_observation=json.loads(args.pre_observation_json),
                    post_disable_observation=json.loads(args.post_observation_json),
                    human_approval_ref=args.human_approval_ref,
                    approval_verifier=resolver.disable_authority_receipt,
                )
            finally:
                resolver.close()
    except (CutoverRefusal, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "refusal": str(exc)}))
        return 2
    print(json.dumps({"ok": True, "evidence": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
