#!/usr/bin/env python3
"""sync-enforcement-map.py — keep the enforcement map's rule inventory in step
with the compiled-rules renders, so activating a rule can never again leave the
gates reporting themselves out of force.

WHY THIS EXISTS. Twice in two days (2026-08-12 and 2026-08-13) a rule was
activated, the compiled-rules renders picked it up on the hourly refresh, and
the enforcement map did not. The map's `active_rule_ids` must match the render
order exactly, so each time the parity checker failed, and every session that
booted afterwards was told all enforcing gates were not in force. On 2026-08-13
the covering commit even asserted "re-bless in the same commit" while touching
only one file, so the miss survived review.

`active_rule_ids` is DERIVED DATA: its only correct value is the render order.
Nothing is decided here, which is exactly why no model may be spent on it (Joe's
2026-08-13 council rule on never spending a cognition token on anything already
expressible as a tested predicate). This is that predicate.

WHAT IT DELIBERATELY DOES NOT DO. It never runs a full `gate-integrity.py
--bless`. A full bless re-hashes every gate SCRIPT, so an automated job calling
it would happily bless a tampered gate and destroy the detection the baseline
exists to provide. This script rewrites exactly one baseline entry — the
contract hash of the map file it just derived — and leaves all 20 gate-script
hashes frozen. Tampering with a gate is still detected on the next boot.

SKIP-not-FAIL, per house convention: a missing render or baseline is a SKIP at
exit 0, not a failure that would take the hourly refresh down with it.
"""

import hashlib
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")
BASELINE = os.path.join(REPO, "ops", "config", "gate-baseline.json")

# The map keys its inventory by scope; each scope is rendered to its own file.
RENDERS = {
    "shared": os.path.join("DNA", "compiled-rules-shared.md"),
    "joe": os.path.join("00_Context", "compiled-rules-joe.md"),
}

ID_RE = re.compile(r"^`#?([0-9a-f]{8})`|^#### .*`#([0-9a-f]{8})`", re.M)


def find_vault() -> str | None:
    """Locate the Drive vault the renders live in.

    Mirrors ops/rule-enforcement-map-check.py rather than inventing a second
    search order — a manual path and an automated path that do the same job
    must be the same code, and two different vault searches would drift.
    """
    sys.path.insert(0, os.path.join(REPO, "ops"))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "map_check", os.path.join(REPO, "ops", "rule-enforcement-map-check.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.find_vault(), mod


def main() -> int:
    if not (os.path.exists(MAP) and os.path.exists(BASELINE)):
        print("sync-enforcement-map: SKIP map or baseline missing")
        return 0

    try:
        vault, checker = find_vault()
    except Exception as exc:
        print(f"sync-enforcement-map: SKIP cannot locate vault ({exc})")
        return 0

    if not vault:
        print("sync-enforcement-map: SKIP vault not reachable")
        return 0

    data = json.load(open(MAP))
    inventory = data.get("active_rule_ids") or {}

    changed = []
    for scope, rel in RENDERS.items():
        path = os.path.join(vault, rel)
        if not os.path.exists(path):
            print(f"sync-enforcement-map: SKIP {scope} render missing")
            return 0
        rendered = checker.ids(path)
        if not rendered:
            # An empty parse means the render format moved, not that every rule
            # was retired. Refuse to write an empty inventory over a good one.
            print(f"sync-enforcement-map: SKIP {scope} render parsed 0 ids")
            return 0
        if inventory.get(scope) != rendered:
            added = [r for r in rendered if r not in (inventory.get(scope) or [])]
            dropped = [r for r in (inventory.get(scope) or []) if r not in rendered]
            changed.append((scope, added, dropped))
            inventory[scope] = rendered

    if not changed:
        print("sync-enforcement-map: OK already in parity")
        return 0

    # Rewrite the inventory IN PLACE, preserving the file's one-line-per-scope
    # array style. A json.dump would reformat every array in the file and bury
    # a one-rule change in hundreds of lines of noise, which makes the diff
    # unreviewable — and an unreviewable gate diff is how this bug shipped.
    text = open(MAP).read()
    for scope, _added, _dropped in changed:
        rendered = inventory[scope]
        block = re.compile(
            r'(^(?P<indent> *)"' + re.escape(scope) + r'": \[\n)(?P<body>.*?)(\n *\])',
            re.M | re.S,
        )
        m = block.search(text)
        if not m:
            print(f"sync-enforcement-map: SKIP cannot locate {scope} array in map")
            return 0
        line = m.group("indent") + "  " + ", ".join(f'"{r}"' for r in rendered)
        text = text[: m.start("body")] + line + text[m.end("body") :]

    with open(MAP, "w") as fh:
        fh.write(text)

    # Sanity: the file must still parse and still carry what we intended.
    reread = json.load(open(MAP))
    for scope, _a, _d in changed:
        if reread["active_rule_ids"][scope] != inventory[scope]:
            print(f"sync-enforcement-map: FAIL {scope} did not land; leaving as-is")
            return 0

    # Re-stamp ONLY this file's contract hash, by targeted replacement so the
    # rest of the baseline is byte-identical. Gate-SCRIPT hashes stay frozen:
    # an automated full bless would launder a tampered gate into the baseline.
    new_hash = hashlib.sha256(open(MAP, "rb").read()).hexdigest()
    btext = open(BASELINE).read()
    old_hash = json.load(open(BASELINE)).get("contracts", {}).get(
        "rule-enforcement-map.json"
    )
    if not old_hash or old_hash not in btext:
        print("sync-enforcement-map: SKIP no map contract hash in baseline")
        return 0
    with open(BASELINE, "w") as fh:
        fh.write(btext.replace(old_hash, new_hash, 1))

    for scope, added, dropped in changed:
        bits = []
        if added:
            bits.append(f"+{','.join(added)}")
        if dropped:
            bits.append(f"-{','.join(dropped)}")
        print(f"sync-enforcement-map: SYNCED {scope} {' '.join(bits)}")
    print("sync-enforcement-map: contract hash re-stamped; gate hashes untouched")
    print("sync-enforcement-map: COMMIT NEEDED — both config files are modified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
