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
                                                  observe_launchd, observe_launchd_scheduler_receipt,
                                                  prepare_disable, prepare_duplicate_disable,
                                                  validate_registry, verify_disabled,
                                                  verify_duplicate_disabled)
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
    if item["scheduler_kind"] == "claude-code":
        return observe_claude_scheduler_receipt(item, resolver.provider_observation_receipt(receipt_ref))
    return observe_launchd_scheduler_receipt(item, resolver.scheduler_observation_receipt(receipt_ref))


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
    prepare.add_argument("--sibling-observation-ref")
    prepare.add_argument("--replacement-json", required=True)
    verify = sub.add_parser("verify-disabled")
    verify.add_argument("--prepared-json", required=True)
    verify.add_argument("--pre-observation-json")
    verify.add_argument("--post-observation-json")
    verify.add_argument("--pre-observation-ref")
    verify.add_argument("--post-observation-ref")
    verify.add_argument("--sibling-pre-observation-ref")
    verify.add_argument("--sibling-post-observation-ref")
    verify.add_argument("--human-approval-ref", required=True)
    args = parser.parse_args()
    try:
        if args.command == "observe":
            data = registry()
            item = surface(data, args.surface)
            if not args.provider_receipt_ref:
                raise CutoverRefusal("scheduler observation requires an immutable native receipt ref")
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
                if not args.observation_ref or args.observation_json:
                    raise CutoverRefusal("scheduler prepare requires one immutable observation ref, never caller JSON")
                observed = receipt_observation(data, args.surface, args.observation_ref, resolver)
                if item.get("duplicate_group"):
                    sibling = next((row for row in data["surfaces"]
                                    if row.get("duplicate_group") == item["duplicate_group"]
                                    and row["surface_id"] != item["surface_id"]), None)
                    if not isinstance(sibling, dict) or not args.sibling_observation_ref:
                        raise CutoverRefusal("duplicate scheduler prepare requires both immutable observation refs")
                    sibling_observed = receipt_observation(
                        data, sibling["surface_id"], args.sibling_observation_ref, resolver)
                    result = prepare_duplicate_disable(
                        data, duplicate_group=item["duplicate_group"],
                        observations=[observed, sibling_observed],
                        replacement=json.loads(args.replacement_json),
                        receipt_verifier=resolver.acceptance_receipt,
                    )
                else:
                    if args.sibling_observation_ref:
                        raise CutoverRefusal("single scheduler prepare does not accept a sibling observation")
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
            duplicate_prepared = isinstance(prepared, dict) and prepared.get("kind") == "scheduler_duplicate_disable_prepare"
            if duplicate_prepared and isinstance(prepared_binding, dict):
                bound_surfaces = prepared_binding.get("surfaces")
                prepared_surface = (bound_surfaces[0].get("surface_id")
                                    if isinstance(bound_surfaces, list) and bound_surfaces
                                    and isinstance(bound_surfaces[0], dict) else None)
            else:
                prepared_surface = prepared_binding.get("surface_id") if isinstance(prepared_binding, dict) else None
            prepared_item = surface(data, prepared_surface) if isinstance(prepared_surface, str) else None
            resolver = resolver_from_environment()
            try:
                if (not isinstance(prepared_item, dict) or not args.pre_observation_ref
                        or not args.post_observation_ref
                        or args.pre_observation_json or args.post_observation_json):
                    raise CutoverRefusal("scheduler verification requires immutable pre/post observation refs")
                assert isinstance(prepared_surface, str)
                pre_observation = receipt_observation(data, prepared_surface, args.pre_observation_ref, resolver)
                post_observation = receipt_observation(data, prepared_surface, args.post_observation_ref, resolver)
                if duplicate_prepared:
                    bound_surfaces = prepared_binding.get("surfaces") if isinstance(prepared_binding, dict) else None
                    sibling_surface = (bound_surfaces[1].get("surface_id")
                                       if isinstance(bound_surfaces, list) and len(bound_surfaces) == 2
                                       and isinstance(bound_surfaces[1], dict) else None)
                    if (not isinstance(sibling_surface, str) or not args.sibling_pre_observation_ref
                            or not args.sibling_post_observation_ref):
                        raise CutoverRefusal("duplicate scheduler verification requires all four immutable observation refs")
                    sibling_pre = receipt_observation(
                        data, sibling_surface, args.sibling_pre_observation_ref, resolver)
                    sibling_post = receipt_observation(
                        data, sibling_surface, args.sibling_post_observation_ref, resolver)
                    result = verify_duplicate_disabled(
                        data, prepared=prepared,
                        pre_disable_observations=[pre_observation, sibling_pre],
                        post_disable_observations=[post_observation, sibling_post],
                        human_approval_ref=args.human_approval_ref,
                        approval_verifier=resolver.disable_authority_receipt,
                    )
                else:
                    if args.sibling_pre_observation_ref or args.sibling_post_observation_ref:
                        raise CutoverRefusal("single scheduler verification does not accept sibling observations")
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
