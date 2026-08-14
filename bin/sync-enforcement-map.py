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
import subprocess
import sys
from types import ModuleType

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# RESOLVED AT CALL TIME, NOT IMPORT TIME, and that is the whole point.
#
# These were `MAP = os.path.join(REPO, ...)` constants until 2026-08-13. The
# selftest redirects this module at a throwaway repo by setting `mod.REPO` after
# importing it, which works for git() below (it reads REPO when it runs) and did
# NOT work for the constants (already frozen to the real checkout). The module
# was split-brained for the whole run: git operations went to the fixture while
# every read and write of the map and the baseline went to the LIVE repo, so a
# test whose docstring promises "never touches the real one" silently modified
# ops/config/rule-enforcement-map.json and ops/config/gate-baseline.json on
# every run. A module whose paths freeze at import cannot be pointed at a
# fixture at all — functions can.
def map_path() -> str:
    return os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")


def baseline_path() -> str:
    return os.path.join(REPO, "ops", "config", "gate-baseline.json")

# The map keys its inventory by scope; each scope is rendered to its own file.
RENDERS = {
    "shared": os.path.join("DNA", "compiled-rules-shared.md"),
    "joe": os.path.join("00_Context", "compiled-rules-joe.md"),
}

ID_RE = re.compile(r"^`#?([0-9a-f]{8})`|^#### .*`#([0-9a-f]{8})`", re.M)

# The two files this script is allowed to touch, and the ONLY two it may ever
# commit. Named explicitly because the house rule is to name every path and
# never `git add -A` in a tree that regularly holds another session's work.
OWNED = ["ops/config/rule-enforcement-map.json", "ops/config/gate-baseline.json"]


sys.path.insert(0, os.path.join(REPO, "ops"))
sys.path.insert(0, REPO)
from git_env import scrubbed_env  # noqa: E402
from lib.loadpy import load_module_from_path  # noqa: E402


def git(*args, check=False):
    """Run git in the repo and return (returncode, combined output).

    THE ENVIRONMENT IS SCRUBBED, and that is load-bearing rather than tidy.
    `cwd=REPO` does not pin git to REPO: GIT_DIR outranks the working directory,
    and every git hook exports it. This script runs on the hourly rules refresh
    and COMMITS AND PUSHES two gate files, so inheriting a stray GIT_DIR means
    committing the gate pair into whatever repository invoked us. Observed
    2026-08-13 through the selftest, which drove this function under a hook-like
    GIT_DIR and watched the commit land in the outer repo, sweeping an unrelated
    file with it — the exact two-writer accident rule 308ef1de exists to stop.
    """
    p = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                       env=scrubbed_env())
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip())
    return p.returncode, (p.stdout + p.stderr).strip()


def dirty_owned() -> list[str]:
    """Which of OWNED already have uncommitted changes, before this run writes.

    Asks git once per path and returns OUR canonical path strings rather than
    slicing them out of porcelain output. The first version sliced `line[3:]`,
    which is correct for a plain " M path" line and wrong for other status
    shapes — the selftest caught it returning "ps/config/gate-baseline.json",
    an off-by-one that would have made the guard compare a mangled name and
    silently decide nobody owned the file. Comparing against a path we already
    hold removes the parsing step that could be wrong at all.
    """
    out = []
    for path in OWNED:
        rc, res = git("status", "--porcelain", "--", path)
        if rc == 0 and res.strip():
            out.append(path)
    return out


def commit_and_push(summary: str, preexisting: list[str]) -> None:
    """Commit the synced pair, or say loudly why it was not committed.

    THIS IS THE HALF THAT WAS MISSING. The first version of this script derived
    the map correctly, re-stamped the baseline correctly, printed "COMMIT NEEDED"
    and stopped — into a log nobody reads. So on 2026-08-13 the sync ran at
    08:39, did its job perfectly, and left both config files sitting uncommitted;
    a session found them by accident hours later. An unpushed gate change is the
    same outage in slow motion: the local machine looks healthy while the repo
    and Dell's clone never receive it. Committing is not decoration on this job,
    it is the job (Joe, 2026-08-11: "you need to start committing your work").
    """
    if preexisting:
        # Another writer was already mid-edit on these exact files. Committing
        # would sweep their work into our commit, which the two-writer rule
        # forbids outright. Leave it and be loud.
        print("sync-enforcement-map: NOT COMMITTED — these were already modified "
              "before this run, so another writer owns them: "
              + ", ".join(preexisting))
        print("sync-enforcement-map: FAIL a human must commit the pair")
        return

    rc, out = git("commit", *OWNED, "-m",
                  f"gates: sync enforcement map inventory ({summary}) and "
                  "re-stamp its baseline hash\n\n"
                  "Automated by bin/sync-enforcement-map.py on the hourly rules "
                  "refresh. Both paths land in ONE commit because a map change "
                  "without its baseline is what took the gates down on "
                  "2026-08-12.")
    if rc != 0:
        print(f"sync-enforcement-map: FAIL commit refused — {out}")
        return
    print("sync-enforcement-map: committed the pair")

    rc, out = git("push", "origin", "HEAD")
    if rc != 0:
        # Deliberately does NOT pull/rebase. Moving shared HEAD unattended is
        # the hazard the two-writer rule names; a local commit that still needs
        # a push is recoverable, a bad rebase is not.
        print(f"sync-enforcement-map: FAIL push refused, commit is local only — {out}")
        return
    print("sync-enforcement-map: pushed")


def find_vault() -> tuple[str | None, ModuleType]:
    """Locate the Drive vault the renders live in, and hand back the checker
    module that found it (main() needs its `ids` parser too).

    Mirrors ops/rule-enforcement-map-check.py rather than inventing a second
    search order — a manual path and an automated path that do the same job
    must be the same code, and two different vault searches would drift.

    The return annotation said `str | None` while the body returned a pair. Both
    callers already unpacked two values, so nothing was broken at runtime, but it
    is what the type-check tripwire was reporting on 2026-08-14 and it made the
    signature a lie about the only thing a signature is for.
    """
    sys.path.insert(0, os.path.join(REPO, "ops"))
    mod = load_module_from_path(
        "map_check", os.path.join(REPO, "ops", "rule-enforcement-map-check.py"))
    return mod.find_vault(), mod


def main() -> int:
    if not (os.path.exists(map_path()) and os.path.exists(baseline_path())):
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

    # Captured BEFORE anything is written: once this script edits the pair they
    # are dirty by definition, so the only moment we can tell our own change
    # apart from another writer's is right now.
    preexisting = dirty_owned()

    data = json.load(open(map_path()))
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
    text = open(map_path()).read()
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

    with open(map_path(), "w") as fh:
        fh.write(text)

    # Sanity: the file must still parse and still carry what we intended.
    reread = json.load(open(map_path()))
    for scope, _a, _d in changed:
        if reread["active_rule_ids"][scope] != inventory[scope]:
            print(f"sync-enforcement-map: FAIL {scope} did not land; leaving as-is")
            return 0

    # Re-stamp ONLY this file's contract hash, by targeted replacement so the
    # rest of the baseline is byte-identical. Gate-SCRIPT hashes stay frozen:
    # an automated full bless would launder a tampered gate into the baseline.
    new_hash = hashlib.sha256(open(map_path(), "rb").read()).hexdigest()
    btext = open(baseline_path()).read()
    old_hash = json.load(open(baseline_path())).get("contracts", {}).get(
        "rule-enforcement-map.json"
    )
    if not old_hash or old_hash not in btext:
        print("sync-enforcement-map: SKIP no map contract hash in baseline")
        return 0
    with open(baseline_path(), "w") as fh:
        fh.write(btext.replace(old_hash, new_hash, 1))

    summary_bits = []
    for scope, added, dropped in changed:
        bits = []
        if added:
            bits.append(f"+{','.join(added)}")
        if dropped:
            bits.append(f"-{','.join(dropped)}")
        print(f"sync-enforcement-map: SYNCED {scope} {' '.join(bits)}")
        summary_bits.append(f"{scope} {' '.join(bits)}")
    print("sync-enforcement-map: contract hash re-stamped; gate hashes untouched")

    if "--no-commit" in sys.argv:
        print("sync-enforcement-map: --no-commit given; leaving the pair modified")
        return 0
    commit_and_push("; ".join(summary_bits), preexisting)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
