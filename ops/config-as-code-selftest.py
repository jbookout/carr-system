#!/usr/bin/env python3
"""Regression fixtures for non-destructive Codex global-hook reconciliation."""
from __future__ import annotations

import importlib.util
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "config_as_code", os.path.join(REPO, "ops", "config-as-code.py")
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(mod)

DESIRED = {"hooks": {
    "Stop": [{"hooks": [{
        "type": "command", "command": "/Users/booko/carr-system/hooks/completion-evidence-gate.py", "timeout": 15,
    }]}],
}}
LIVE = {
    "hooks": {
        "Stop": [
            {"hooks": [{"type": "command", "command": "/Users/booko/other/hooks/keep.py", "timeout": 5}]},
            {"hooks": [{"type": "command", "command": "/Users/booko/carr-system/hooks/old.py", "timeout": 10}]},
            {"hooks": [
                {"type": "command", "command": "/Users/booko/other/hooks/mixed.py", "timeout": 5},
                {"type": "command", "command": "/Users/booko/carr-system/hooks/old2.py", "timeout": 10},
            ]},
        ],
        "PostToolUse": [{"matcher": "Other", "hooks": [{
            "type": "command", "command": "/Users/booko/other/hooks/post.py", "timeout": 5,
        }]}],
    },
    "user_setting": {"keep": True},
}


def commands(doc):
    return [hook.get("command") for groups in doc["hooks"].values()
            for group in groups for hook in group.get("hooks", []) if isinstance(hook, dict)]


def main():
    merged = mod.merge_codex_carr_hooks(LIVE, DESIRED)
    names = commands(merged)
    again = mod.merge_codex_carr_hooks(merged, DESIRED)
    cases = [
        ("unrelated top-level key preserved", merged.get("user_setting") == {"keep": True}),
        ("unrelated event preserved", "PostToolUse" in merged["hooks"] and "/Users/booko/other/hooks/post.py" in commands(merged)),
        ("unrelated Stop hook preserved", "/Users/booko/other/hooks/keep.py" in names),
        ("mixed group keeps unrelated hook", "/Users/booko/other/hooks/mixed.py" in names),
        ("stale CARR hook removed", all("/carr-system/hooks/old" not in name for name in names)),
        ("desired CARR hook installed once", names.count("/Users/booko/carr-system/hooks/completion-evidence-gate.py") == 1),
        ("second merge is idempotent", again == merged),
    ]
    for label, passed in cases:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
    print(f"config-as-code-selftest: {sum(ok for _, ok in cases)}/{len(cases)} passed")
    return 0 if all(ok for _, ok in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
