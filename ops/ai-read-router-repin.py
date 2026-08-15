#!/usr/bin/env python3
"""ops/ai-read-router-repin.py — re-pin the read router after an intended
mcp-server/src/tools.js change.

WHY THIS EXISTS. The read-router policy (evals/ai/function-router.v1.json) binds
mcp-server/src/tools.js by sha256, and ops/ai_read_router.py in turn binds the
POLICY file by sha256. That double pin is the point: a forged or drifted registry
cannot reach the router. But it means ANY edit to tools.js — a new verb, a new
guard, a typo fix — breaks ops/ai-read-router-selftest.py with
`router_policy_invalid`, and the failure names neither file that has to move.

Until this script existed the two hashes were hand-maintained with no path
written down. The first session to trip it (2026-08-15, adding a guard to
log-activity) spent the diagnosis working out that the router pins the registry
at all, and that the fix is TWO updates in a specific order, because updating the
policy changes the policy's own hash. Rule 5e89c211: never spend a cognition
token on something already expressible as code.

THIS IS NOT A BYPASS. It re-states what the pin should be for the tree AS IT
STANDS, which is only correct when the tools.js change was intended and reviewed.
That is the same contract as re-blessing the gate baseline, and it belongs in the
SAME COMMIT as the change that moved the file (rule c0b38d80) so a reader sees
the registry move and the pin move together.

RUN IT:
    ./.venv/bin/python ops/ai-read-router-repin.py           # show what would move
    ./.venv/bin/python ops/ai-read-router-repin.py --apply   # write the new pins
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "mcp-server", "src", "tools.js")
POLICY = os.path.join(REPO, "evals", "ai", "function-router.v1.json")
ROUTER = os.path.join(REPO, "ops", "ai_read_router.py")


def sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the new pins")
    args = ap.parse_args()

    for path in (TOOLS, POLICY, ROUTER):
        if not os.path.isfile(path):
            print(f"ai-read-router-repin: ERROR — missing {path}", file=sys.stderr)
            return 2

    with open(POLICY, encoding="utf-8") as fh:
        raw = fh.read()
    pinned_tools = json.loads(raw)["tool_registry"]["sha256"]
    actual_tools = sha256(TOOLS)

    with open(ROUTER, encoding="utf-8") as fh:
        router_src = fh.read()
    m = re.search(r'POLICY_SHA256 = "([0-9a-f]{64})"', router_src)
    if not m:
        print("ai-read-router-repin: ERROR — POLICY_SHA256 not found in ops/ai_read_router.py",
              file=sys.stderr)
        return 2
    pinned_policy = m.group(1)

    if pinned_tools == actual_tools and pinned_policy == sha256(POLICY):
        print("ai-read-router-repin: OK — both pins already match the tree; nothing to do")
        return 0

    print("ai-read-router-repin: the registry moved, so both pins move with it")
    print(f"  tool_registry.sha256  {pinned_tools[:12]} -> {actual_tools[:12]}  (tools.js)")
    if not args.apply:
        print("  POLICY_SHA256         recomputed after the policy is written")
        print("\nRe-read the tools.js diff first. This re-states the pin for the tree as it "
              "stands; it does not verify the change was intended.")
        print("Then: ./.venv/bin/python ops/ai-read-router-repin.py --apply")
        return 0

    # ORDER MATTERS: the policy carries the registry hash, so the policy's OWN
    # hash is only final once that write has landed.
    with open(POLICY, "w", encoding="utf-8") as fh:
        fh.write(raw.replace(pinned_tools, actual_tools))
    new_policy = sha256(POLICY)
    with open(ROUTER, "w", encoding="utf-8") as fh:
        fh.write(router_src.replace(pinned_policy, new_policy))
    print(f"  POLICY_SHA256         {pinned_policy[:12]} -> {new_policy[:12]}  (the policy file)")
    print("\nBoth pins written. Commit them WITH the tools.js change, never separately.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
