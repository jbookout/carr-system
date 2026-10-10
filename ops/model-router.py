#!/usr/bin/env python3
"""Select a model, capture Claude statusLine telemetry, or emit wiring variables.

The route command never invokes a model. Use its result at the existing Model
Room transport. MODEL_ROUTER_BUDGET_DIR must identify one shared ledger for a PR.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib import model_router


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
        temp = stream.name
    os.replace(temp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-dir", default=os.environ.get("MODEL_ROUTER_BUDGET_DIR", REPO / "out/orch/budget"))
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("route")
    select.add_argument("task_kind")
    select.add_argument("--floor")
    select.add_argument("--pr", help="Stable repository:branch key, shared by build, fix and review")
    select.add_argument("--builder-model")
    select.add_argument("--usage-file", help="Normalized pool telemetry JSON, for offline validation")
    select.add_argument("--reserve-pct", type=float)
    select.add_argument("--shell", action="store_true", help="Quoted MODEL, EFFORT and POOL assignments")
    sub.add_parser("capture-claude", help="Read documented statusLine JSON on stdin; persist only rate limits")
    args = parser.parse_args(argv)
    try:
        if args.command == "capture-claude":
            snapshot = model_router.claude_snapshot(json.load(sys.stdin))
            atomic_json(Path(args.budget_dir) / "claude-usage.json", snapshot)
            print(f"Claude {snapshot['five_hour_pct']:g}% 5h / {snapshot['weekly_pct']:g}% weekly")
            return 0
        result = model_router.route(args.task_kind, args.floor, pr_id=args.pr, builder_model=args.builder_model,
                                    budget_dir=args.budget_dir, reserve_pct=args.reserve_pct,
                                    usage=model_router.load_json(args.usage_file) if args.usage_file else None)
        if args.shell:
            print(f"NATIVE_MODEL={shlex.quote(result['model'].replace('opus-5.5', 'claude-opus-5-5').replace('sonnet-5.5', 'claude-sonnet-5-5'))}")
            for key in ("model", "effort", "pool"):
                print(f"{key.upper()}={shlex.quote(result[key])}")
        else:
            print(json.dumps(result))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"model-router: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
