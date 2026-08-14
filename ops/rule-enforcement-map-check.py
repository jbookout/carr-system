#!/usr/bin/env python3
"""Validate that every active CARR rule has one honest enforcement category."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from glob import glob
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")
RULE_ID = re.compile(r"`#([0-9a-f]{8})`")
CATEGORIES = {
    "hard_pre_action", "transactional_schema", "post_action_verification",
    "session_task_rail", "judgment_advisory",
}

# enforcement_class: the finer-grained, per-rule axis added 2026-08-14 after the
# rule-enforceability audit found the gap in this file's own name — a rule with
# NO rule_controls entry at all fell to default_category silently, reading
# identically to a rule someone had actually reviewed and judged advisory.
# `category` still says WHICH of the five house buckets a rule sits in;
# `enforcement_class` says WHETHER something concrete backs that placement yet.
ENFORCEMENT_CLASSES = {
    "deny_gate", "stop_gate", "surfacing", "schema", "judgment_ambient", "unbuilt",
}

# The mechanical half of the backfill: a rule already sorted into one of the
# four non-advisory categories is, by construction, backed by SOME concrete
# control (the old check already required that). This is simply which shape of
# control each category implies, read off the category_overrides' own control
# catalog: hard_pre_action controls all fail with a block/deny, schema controls
# are the record layer's own constraints, post_action_verification controls are
# Stop-hook checks, and session_task_rail controls are advisory rails that
# surface context rather than refuse anything.
BUILT_CLASS_BY_CATEGORY = {
    "hard_pre_action": "deny_gate",
    "transactional_schema": "schema",
    "post_action_verification": "stop_gate",
    "session_task_rail": "surfacing",
}

# AN UNASSIGNED RULE MAY EXIST BRIEFLY AND MAY NOT PERSIST (rule ab814a26).
#
# bin/sync-enforcement-map.py mints a placeholder entry for every newly
# ACTIVATED rule, on purpose and correctly: classifying a rule is a judgment
# call a mechanical hourly job must not make on a human's behalf. So refusing
# every placeholder outright would put every session on the machine into a
# gate-integrity failure within an hour of the next taught rule, and the fix
# would be to delete this check. A guard that punishes the honest interim state
# gets removed, and then nothing is checked at all.
#
# The placeholder therefore carries the date it was minted and ages out. Before
# 2026-08-14 nothing aged it: 132 rules — two thirds of the whole rule set —
# sat at the same placeholder, and no check anywhere refused a single one of
# them. "Pending classification" had become the resting state rather than a
# transition, which is precisely what recitation-is-not-enforcement means.
PLACEHOLDER_MARK = "pending classification"
PLACEHOLDER_GRACE_DAYS = 14
_PLACEHOLDER_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def stale_placeholder(planned: str, today: date | None = None) -> bool:
    """True when a placeholder has outlived its grace window.

    An UNDATED placeholder is stale immediately: it could never age out, so
    leaving it unrefused would make the permanent state permanent by
    construction — the exact shape being repaired here. Text that is not a
    placeholder at all is never stale, however old the work it describes.
    """
    if not isinstance(planned, str) or PLACEHOLDER_MARK not in planned.lower():
        return False
    match = _PLACEHOLDER_DATE.search(planned)
    if not match:
        return True
    try:
        minted = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return True
    return ((today or date.today()) - minted).days > PLACEHOLDER_GRACE_DAYS


def find_vault() -> str:
    """Resolve the transitional render root without assuming Joe's account."""
    configured = os.environ.get("CARR_VAULT")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    home = os.path.expanduser("~")
    hits = sorted(glob(os.path.join(
        home, "Library", "CloudStorage", "GoogleDrive-*", "My Drive", "CARR AI"
    )))
    legacy = os.path.join(home, "My Drive", "CARR AI")
    if os.path.isdir(legacy):
        hits.append(legacy)
    return hits[0] if hits else ""


def source_paths(vault: str) -> dict[str, str]:
    return {
        "shared": os.path.join(vault, "DNA", "compiled-rules-shared.md"),
        "joe": os.path.join(vault, "00_Context", "compiled-rules-joe.md"),
    }


def ids(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return RULE_ID.findall(fh.read())


def reference_errors(control_name: str, kind: str, refs) -> list[str]:
    """Require named local paths to exist; non-local evidence is explicit.

    `external:`, `glob:`, and `command:` are intentionally descriptive rather
    than pretending a database probe or an executed command is a local path.
    Bare strings remain local repository paths for backward-compatible, strict
    validation.  A `path:` prefix permits a short annotation to be moved out of
    the path itself without weakening existence checking.
    """
    errors = []
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, str) or not ref.strip():
            errors.append(f"{control_name} {kind} contains an invalid reference")
            continue
        value = ref.strip()
        if value.startswith(("external:", "command:")):
            if not value.partition(":")[2].strip():
                errors.append(f"{control_name} {kind} has an empty descriptor")
            continue
        if value.startswith("glob:"):
            pattern = value.partition(":")[2].strip()
            if not pattern or not glob(os.path.join(REPO, pattern), recursive=True):
                errors.append(f"{control_name} {kind} glob matches no local path: {pattern or '(empty)'}")
            continue
        path = value.partition(":")[2].strip() if value.startswith("path:") else value
        if not path or os.path.isabs(path) or not os.path.exists(os.path.join(REPO, path)):
            errors.append(f"{control_name} {kind} local path is missing: {path or '(empty)'}")
    return errors


def validate(data: dict, source_ids: dict[str, list[str]] | None = None) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if data.get("default_category") not in CATEGORIES:
        errors.append("default_category is invalid")
    controls = data.get("categories")
    if not isinstance(controls, dict) or set(controls) != CATEGORIES:
        errors.append("categories must define the five canonical categories exactly")
    listed = data.get("active_rule_ids")
    if not isinstance(listed, dict):
        return errors + ["active_rule_ids must be an object"]
    if not listed:
        errors.append("active_rule_ids must not be empty")
    flat: list[str] = []
    for scope, got in listed.items():
        if not isinstance(scope, str) or not scope:
            errors.append("active_rule_ids contains an invalid scope")
        if not isinstance(got, list) or not all(
                isinstance(rule_id, str) and re.fullmatch(r"[0-9a-f]{8}", rule_id)
                for rule_id in got):
            errors.append(f"{scope} inventory must contain eight-character rule ids")
            continue
        if len(got) != len(set(got)):
            errors.append(f"{scope} inventory contains duplicate ids")
        flat.extend(got)
    if source_ids is not None:
        if set(listed) != set(source_ids):
            errors.append("inventory scopes do not exactly match active render scopes")
        for scope, expected in source_ids.items():
            got = listed.get(scope)
            if not isinstance(got, list):
                errors.append(f"{scope} inventory is missing")
            elif got != expected:
                errors.append(f"{scope} inventory does not exactly match active render order")
    if len(flat) != len(set(flat)):
        errors.append("a rule id appears in more than one scope inventory")
    overrides = data.get("category_overrides", {})
    if not isinstance(overrides, dict):
        return errors + ["category_overrides must be an object"]
    seen: list[str] = []
    for category, rule_ids in overrides.items():
        if category not in CATEGORIES or category == "judgment_advisory":
            errors.append(f"invalid override category {category!r}")
            continue
        if not isinstance(rule_ids, list):
            errors.append(f"{category} overrides must be a list")
            continue
        seen.extend(rule_ids)
    counts = Counter(seen)
    for rule_id, n in counts.items():
        if rule_id not in flat:
            errors.append(f"override references unknown rule {rule_id}")
        if n != 1:
            errors.append(f"override assigns {rule_id} {n} times")
    catalog = data.get("control_catalog", {})
    rule_controls = data.get("rule_controls", {})
    if not isinstance(catalog, dict) or not isinstance(rule_controls, dict):
        return errors + ["control_catalog and rule_controls must be objects"]
    expected_categories = {rule_id: category for category, rule_ids in overrides.items()
                           if isinstance(rule_ids, list) for rule_id in rule_ids}

    # EVERY active rule needs an entry now, not just the ones some category
    # override happened to name. `expected_all` maps every id in `flat` to the
    # category it should carry: whatever override named it, or default_category
    # when nothing did — the exact case the audit found silently falling through
    # to "advisory" with nothing recording that the fall was ever decided.
    default_category = data.get("default_category")
    expected_all = {rule_id: expected_categories.get(rule_id, default_category)
                    for rule_id in flat}
    if set(rule_controls) != set(expected_all):
        missing = sorted(set(expected_all) - set(rule_controls))
        extra = sorted(set(rule_controls) - set(expected_all))
        if missing:
            errors.append("active rule(s) with no enforcement-map entry at all: "
                          + ", ".join(missing))
        if extra:
            errors.append("enforcement-map entries reference inactive/unknown rule(s): "
                          + ", ".join(extra))

    for rule_id, category in expected_all.items():
        detail = rule_controls.get(rule_id)
        if not isinstance(detail, dict):
            continue  # already reported above as missing
        if detail.get("category") != category:
            errors.append(f"{rule_id} metadata category does not match override")

        enforcement_class = detail.get("enforcement_class")
        if enforcement_class not in ENFORCEMENT_CLASSES:
            errors.append(f"{rule_id} enforcement_class is missing or invalid")
            continue

        if enforcement_class == "judgment_ambient":
            if category != "judgment_advisory":
                errors.append(f"{rule_id} is judgment_ambient but categorized {category}")
            why = detail.get("why_unenforceable")
            if not isinstance(why, str) or not why.strip():
                errors.append(f"{rule_id} judgment_ambient lacks why_unenforceable")
            continue

        if enforcement_class == "unbuilt":
            planned = detail.get("planned_control")
            if not isinstance(planned, str) or not planned.strip():
                errors.append(f"{rule_id} unbuilt lacks planned_control")
            elif stale_placeholder(planned):
                errors.append(
                    f"{rule_id} has sat unclassified past the {PLACEHOLDER_GRACE_DAYS}-day "
                    "window — assign its trigger (a stop-gate check, binding-moment "
                    "surfacing, or recorded-ambient with a written reason) per rule "
                    "ab814a26, or give it a real planned_control")
            continue

        # The four BUILT classes: something concrete already refuses, verifies,
        # surfaces, or schema-guards this rule, so it needs the same control
        # metadata the old check always required — plus, now, agreement between
        # the category it sits in and the shape of enforcement that backs it.
        if category == "judgment_advisory":
            errors.append(f"{rule_id} enforcement_class {enforcement_class} "
                          "requires a non-advisory category override")
        expected_built = BUILT_CLASS_BY_CATEGORY.get(category)
        if expected_built and enforcement_class != expected_built:
            errors.append(f"{rule_id} enforcement_class {enforcement_class} does not "
                          f"match category {category} (expected {expected_built})")
        if not isinstance(detail.get("binding_moment"), str) or not detail["binding_moment"].strip():
            errors.append(f"{rule_id} lacks a concrete binding_moment")
        if not isinstance(detail.get("exceptions"), str):
            errors.append(f"{rule_id} lacks exceptions metadata")
        control = detail.get("control")
        control_detail = catalog.get(control) if isinstance(control, str) else None
        if not isinstance(control_detail, dict):
            errors.append(f"{rule_id} names no concrete control")
            continue
        if not isinstance(control_detail.get("implementation"), list) or not control_detail["implementation"]:
            errors.append(f"{rule_id} control has no implementation")
        if not isinstance(control_detail.get("test"), list) or not control_detail["test"]:
            errors.append(f"{rule_id} control has no test")
        if not isinstance(control_detail.get("failure_mode"), str) or not control_detail["failure_mode"]:
            errors.append(f"{rule_id} control has no failure_mode")
    # Validate every declared concrete control once. Rule metadata references
    # are checked above; orphan catalog entries still matter because they could
    # otherwise make a health green line look more complete than the files.
    for control_name, detail in catalog.items():
        if not isinstance(detail, dict):
            continue
        for kind in ("implementation", "test"):
            refs = detail.get(kind)
            if isinstance(refs, list) and refs:
                errors.extend(reference_errors(control_name, kind, refs))
    return errors


def counts(data: dict) -> dict[str, int]:
    total = sum(len(v) for v in data["active_rule_ids"].values())
    out = Counter({data["default_category"]: total})
    for category, rule_ids in data.get("category_overrides", {}).items():
        out[data["default_category"]] -= len(rule_ids)
        out[category] += len(rule_ids)
    return {category: out[category] for category in sorted(CATEGORIES)}


def enforcement_class_counts(data: dict) -> dict[str, int]:
    """How many active rules sit in each enforcement_class — chiefly so `unbuilt`
    (a rule everyone agrees needs a gate that does not exist yet) stays a number
    a session sees every time, rather than a fact only visible by reading 199
    JSON entries by hand.
    """
    out: Counter[str] = Counter()
    rule_controls = data.get("rule_controls", {})
    for detail in rule_controls.values():
        if isinstance(detail, dict):
            out[detail.get("enforcement_class", "?")] += 1
    return {cls: out[cls] for cls in sorted(out)}


def main() -> int:
    try:
        with open(MAP, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        print(f"rule-enforcement-map: ERROR — {exc}")
        return 2

    vault = find_vault()
    source_ids = None
    parity = ("local contract exact; store parity belongs to standing-context "
              "(no Markdown dependency)")
    if vault:
        if not os.path.isdir(vault):
            print(f"rule-enforcement-map: ERROR — configured CARR vault is missing: {vault}")
            return 2
        sources = source_paths(vault)
        present = {scope: os.path.isfile(path) for scope, path in sources.items()}
        if any(present.values()) and not all(present.values()):
            missing = ", ".join(scope for scope, exists in present.items() if not exists)
            print("rule-enforcement-map: ERROR — transitional rule renders are partial; "
                  f"missing {missing}")
            return 2
        if all(present.values()):
            try:
                source_ids = {scope: ids(path) for scope, path in sources.items()}
            except Exception as exc:
                print(f"rule-enforcement-map: ERROR — {exc}")
                return 2
            parity = "active render parity exact"
    errors = validate(data, source_ids)
    if errors:
        print("rule-enforcement-map: FAIL — " + "; ".join(errors))
        return 1
    c = counts(data)
    ec = enforcement_class_counts(data)
    print(f"rule-enforcement-map: OK ({parity}) — " + ", ".join(
        f"{name}={c[name]}" for name in sorted(c)
    ) + " | enforcement_class " + ", ".join(
        f"{name}={ec[name]}" for name in sorted(ec)
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
