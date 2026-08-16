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

from lib.control_plane_scheduler_cutover import (CutoverRefusal, observe_claude_scheduler_receipt,
                                                  observe_launchd, prepare_disable,
                                                  validate_registry, verify_disabled)
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


def surface(data: dict, surface_id: str) -> dict:
    item = next((row for row in data["surfaces"] if row["surface_id"] == surface_id), None)
    if not isinstance(item, dict):
        raise CutoverRefusal("scheduler surface is unknown")
    return item


def receipt_observation(data: dict, surface_id: str, receipt_ref: str, resolver) -> dict:
    item = surface(data, surface_id)
    if item["scheduler_kind"] != "claude-code":
        raise CutoverRefusal("provider receipt is valid only for a Claude scheduler surface")
    return observe_claude_scheduler_receipt(item, resolver.provider_observation_receipt(receipt_ref))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    observation = sub.add_parser("observe")
    observation.add_argument("--surface", required=True)
    observation.add_argument("--provider-receipt-ref")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--surface", required=True)
    prepare.add_argument("--observation-json")
    prepare.add_argument("--observation-ref")
    prepare.add_argument("--replacement-json", required=True)
    verify = sub.add_parser("verify-disabled")
    verify.add_argument("--prepared-json", required=True)
    verify.add_argument("--pre-observation-json")
    verify.add_argument("--post-observation-json")
    verify.add_argument("--pre-observation-ref")
    verify.add_argument("--post-observation-ref")
    verify.add_argument("--human-approval-ref", required=True)
    args = parser.parse_args()
    try:
        if args.command == "observe":
            data = registry()
            item = surface(data, args.surface)
            if item["scheduler_kind"] == "launchd":
                if args.provider_receipt_ref:
                    raise CutoverRefusal("launchd observation does not accept a provider receipt")
                result = launchd_observation(args.surface)
            else:
                if not args.provider_receipt_ref:
                    raise CutoverRefusal("Claude scheduler observation requires an immutable provider receipt ref")
                resolver = resolver_from_environment()
                try:
                    result = receipt_observation(data, args.surface, args.provider_receipt_ref, resolver)
                finally:
                    resolver.close()
        elif args.command == "prepare":
            data = registry()
            item = surface(data, args.surface)
            resolver = resolver_from_environment()
            try:
                if item["scheduler_kind"] == "claude-code":
                    if not args.observation_ref or args.observation_json:
                        raise CutoverRefusal("Claude prepare requires one immutable observation ref, never caller JSON")
                    observed = receipt_observation(data, args.surface, args.observation_ref, resolver)
                else:
                    if not args.observation_json or args.observation_ref:
                        raise CutoverRefusal("launchd prepare requires one local observation JSON document")
                    observed = json.loads(args.observation_json)
                result = prepare_disable(
                    data, surface_id=args.surface, observation=observed,
                    replacement=json.loads(args.replacement_json),
                    receipt_verifier=resolver.acceptance_receipt,
                )
            finally:
                resolver.close()
        else:
            data = registry()
            prepared = json.loads(args.prepared_json)
            prepared_binding = prepared.get("binding") if isinstance(prepared, dict) else None
            prepared_surface = prepared_binding.get("surface_id") if isinstance(prepared_binding, dict) else None
            prepared_item = surface(data, prepared_surface) if isinstance(prepared_surface, str) else None
            resolver = resolver_from_environment()
            try:
                if isinstance(prepared_item, dict) and prepared_item["scheduler_kind"] == "claude-code":
                    if (not args.pre_observation_ref or not args.post_observation_ref
                            or args.pre_observation_json or args.post_observation_json):
                        raise CutoverRefusal("Claude verification requires immutable pre/post observation refs")
                    assert isinstance(prepared_surface, str)
                    assert isinstance(args.pre_observation_ref, str)
                    assert isinstance(args.post_observation_ref, str)
                    pre_observation = receipt_observation(data, prepared_surface, args.pre_observation_ref, resolver)
                    post_observation = receipt_observation(data, prepared_surface, args.post_observation_ref, resolver)
                else:
                    if (not args.pre_observation_json or not args.post_observation_json
                            or args.pre_observation_ref or args.post_observation_ref):
                        raise CutoverRefusal("launchd verification requires local pre/post observation JSON")
                    pre_observation = json.loads(args.pre_observation_json)
                    post_observation = json.loads(args.post_observation_json)
                result = verify_disabled(
                    data, prepared=prepared, pre_disable_observation=pre_observation,
                    post_disable_observation=post_observation,
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
