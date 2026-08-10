#!/usr/bin/env python3
"""Pure regressions for rule-enforcement-map-check validation."""
from __future__ import annotations

import importlib.util
import os

REPO = os.path.expanduser("~/carr-system")
spec = importlib.util.spec_from_file_location(
    "rule_enforcement_map_check", os.path.join(REPO, "ops", "rule-enforcement-map-check.py")
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(mod)

BASE = {
    "schema_version": 1,
    "default_category": "judgment_advisory",
    "categories": {key: {} for key in mod.CATEGORIES},
    "control_catalog": {"real": {"implementation": ["hooks/gate-integrity.py"], "test": ["ops/rule-enforcement-map-selftest.py"], "failure_mode": "deny"}},
    "active_rule_ids": {"shared": ["aaaaaaaa"], "joe": ["bbbbbbbb"]},
    "category_overrides": {"hard_pre_action": ["aaaaaaaa"]},
    "rule_controls": {"aaaaaaaa": {"category": "hard_pre_action", "binding_moment": "before write", "control": "real", "exceptions": "none"}},
}
SOURCE = {"shared": ["aaaaaaaa"], "joe": ["bbbbbbbb"]}


def case(name, data, passes):
    got = mod.validate(data, SOURCE)
    ok = (not got) == passes
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got or 'valid'}")
    return ok


def main():
    import copy
    cases = [
        case("exact coverage", copy.deepcopy(BASE), True),
        case("missing active id", {**copy.deepcopy(BASE), "active_rule_ids": {"shared": [], "joe": ["bbbbbbbb"]}}, False),
        case("duplicate scope id", {**copy.deepcopy(BASE), "active_rule_ids": {"shared": ["aaaaaaaa"], "joe": ["aaaaaaaa"]}}, False),
        case("unknown override", {**copy.deepcopy(BASE), "category_overrides": {"hard_pre_action": ["cccccccc"]}}, False),
        case("double classification", {**copy.deepcopy(BASE), "category_overrides": {"hard_pre_action": ["aaaaaaaa"], "session_task_rail": ["aaaaaaaa"]}}, False),
        case("non-advisory rule requires concrete control", {**copy.deepcopy(BASE), "rule_controls": {}}, False),
        case("missing local control path fails", {**copy.deepcopy(BASE), "control_catalog": {"real": {"implementation": ["hooks/missing.py"], "test": ["ops/rule-enforcement-map-selftest.py"], "failure_mode": "deny"}}}, False),
    ]
    print(f"rule-enforcement-map-selftest: {sum(cases)}/{len(cases)} passed")
    return 0 if all(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
