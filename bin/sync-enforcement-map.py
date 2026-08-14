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
import shutil
import subprocess
import sys
import tempfile
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

# The branch this job publishes on. STABLE rather than timestamped: an hourly job
# that minted a new branch name every run would leave a litter of them and a new
# pull request beside each. One branch, force-updated, one pull request reused.
GATES_BRANCH = "gates/enforcement-sync"


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


def git_in(worktree: str, *args, extra_env: dict | None = None):
    """git(), but rooted in the throwaway publish worktree.

    A separate function rather than a cwd argument on git(), because the two have
    genuinely different safety stories and blurring them is how one of them loses
    its guard. git() is pinned to REPO and must never drift off it. This one is
    pinned to a directory THIS PROCESS just created, does the committing, and is
    the only place a commit is allowed to happen at all. Both scrub the
    environment for the same reason: an inherited GIT_DIR outranks cwd, so
    without scrubbing this would commit into whatever repository invoked the
    hourly refresh.
    """
    env = scrubbed_env()
    if extra_env:
        env.update(extra_env)
    p = subprocess.run(["git", *args], cwd=worktree, capture_output=True,
                       text=True, env=env)
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


def publish_via_branch(summary: str, preexisting: list[str]) -> None:
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

    # ── PUBLISHED THROUGH A BRANCH, NEVER COMMITTED ON main ──────────────────
    #
    # CHANGED 2026-08-14, and the old behaviour is why. This used to
    # `git commit` the pair on whatever branch the canonical tree was on — which
    # is always main — and then `git push origin HEAD`. main is protected, so the
    # push was refused every time and the commit stayed local. Not a theory;
    # out/rules-refresh.log carries it verbatim:
    #
    #   sync-enforcement-map: committed the pair
    #   sync-enforcement-map: FAIL push refused, commit is local only — ... on main
    #
    # One stranded commit per run, hourly, unattended. That is the machine half of
    # how ~/carr-system reached nine unpushed commits by 2026-08-14, and it made
    # this job the largest single producer of the divergence it was meant to end.
    # ops/githooks/pre-commit now refuses a commit on main outright, so the old
    # path would not merely strand — it would fail.
    #
    # The pair is therefore built in a THROWAWAY WORKTREE on a stable branch cut
    # from origin/main, pushed there, and offered as a pull request. The canonical
    # tree is never committed to and is left exactly as clean as it was found,
    # which also keeps the next run's dirty_owned() check honest: leaving the
    # files modified here would make the following hour read them as another
    # writer's work and refuse for ever.
    rc, _ = git("fetch", "origin", "main")
    if rc != 0:
        print("sync-enforcement-map: FAIL could not fetch origin/main — nothing published")
        return

    # NOTHING TO PUBLISH is the common case once a PR has merged and this tree
    # has pulled. Checked against origin/main rather than local HEAD, because
    # local HEAD on a busy machine is not what the repository actually holds.
    rc, _ = git("diff", "--quiet", "origin/main", "--", *OWNED)
    if rc == 0:
        restore_owned()
        print("sync-enforcement-map: pair already matches origin/main — nothing to publish")
        return

    tmp = tempfile.mkdtemp(prefix="carr-gates-sync-")
    wt = os.path.join(tmp, "wt")
    try:
        # -B resets the branch to origin/main every run, so a stale attempt from
        # a previous hour is replaced rather than built upon. The branch name is
        # STABLE on purpose: repeated runs update one branch and reuse one pull
        # request instead of littering a new one every hour.
        rc, out = git("worktree", "add", "--quiet", "-B", GATES_BRANCH, wt, "origin/main")
        if rc != 0:
            print(f"sync-enforcement-map: FAIL could not create the publish worktree — {out}")
            return

        for path in OWNED:
            shutil.copyfile(os.path.join(REPO, path), os.path.join(wt, path))

        rc, out = git_in(wt, "add", *OWNED)
        if rc != 0:
            print(f"sync-enforcement-map: FAIL could not stage the pair — {out}")
            return
        rc, out = git_in(wt, "diff", "--cached", "--quiet")
        if rc == 0:
            print("sync-enforcement-map: the branch already carries this pair — nothing new")
            return

        rc, out = git_in(wt, "commit", "-m",
                         f"gates: sync enforcement map inventory ({summary}) and "
                         "re-stamp its baseline hash\n\n"
                         "Automated by bin/sync-enforcement-map.py on the hourly rules "
                         "refresh. Both paths land in ONE commit because a map change "
                         "without its baseline is what took the gates down on "
                         "2026-08-12.\n\n"
                         "Published on a branch rather than committed on main: main is "
                         "protected, so the old direct commit could never be pushed and "
                         "stranded on the canonical checkout once an hour.")
        if rc != 0:
            print(f"sync-enforcement-map: FAIL commit refused — {out}")
            return

        # CARR_SKIP_CI because the remote required check is the real gate and this
        # runs unattended once an hour; a full local CI pass per run would put
        # minutes of work on a schedule to re-prove what the PR proves anyway.
        rc, out = git_in(wt, "push", "--force", "origin",
                         f"HEAD:refs/heads/{GATES_BRANCH}",
                         extra_env={"CARR_SKIP_CI": "1"})
        if rc != 0:
            print(f"sync-enforcement-map: FAIL push refused — {out}")
            return
        print(f"sync-enforcement-map: pushed the pair to {GATES_BRANCH}")
        open_or_note_pr(summary)
    finally:
        git("worktree", "remove", "--force", wt)
        shutil.rmtree(tmp, ignore_errors=True)
        restore_owned()


def restore_owned() -> None:
    """Put the canonical tree's copies back to HEAD, leaving it clean.

    The content is not lost — it is on the branch and in the pull request. What
    this prevents is the canonical checkout sitting permanently dirty on two
    files, which would make the NEXT run's dirty_owned() read them as another
    writer's in-flight work and refuse to sync for ever after.
    """
    rc, out = git("checkout", "--", *OWNED)
    if rc != 0:
        print(f"sync-enforcement-map: WARN could not restore the working copies — {out}")


def open_or_note_pr(summary: str) -> None:
    """Offer the branch as a pull request, or say plainly that it is waiting.

    gh may not be authenticated in a launchd environment, and that is not a
    failure of the sync — the pair is already safe on the branch. Saying so
    beats a traceback in a log nobody reads.
    """
    if shutil.which("gh") is None:
        print(f"sync-enforcement-map: branch pushed, no gh on PATH — land it with "
              f"`gh pr create --base main --head {GATES_BRANCH}`")
        return
    p = subprocess.run(
        ["gh", "pr", "create", "--base", "main", "--head", GATES_BRANCH,
         "--title", f"gates: sync enforcement map inventory ({summary})",
         "--body",
         "Automated by `bin/sync-enforcement-map.py` on the hourly rules refresh.\n\n"
         "The enforcement map and its gate baseline move together — a map change "
         "without its baseline is what took the gates down on 2026-08-12.\n\n"
         "Published on a branch because `main` is protected: the previous version "
         "of this job committed directly on `main`, could never push, and stranded "
         "one commit per run on the canonical checkout."],
        cwd=REPO, capture_output=True, text=True, env=scrubbed_env())
    out = (p.stdout + p.stderr).strip()
    if p.returncode == 0:
        print(f"sync-enforcement-map: opened {out.splitlines()[-1] if out else 'a pull request'}")
    elif "already exists" in out:
        print("sync-enforcement-map: the open pull request for this branch was updated")
    else:
        print(f"sync-enforcement-map: branch pushed, PR not opened — {out}")
        print(f"sync-enforcement-map: land it with "
              f"`gh pr create --base main --head {GATES_BRANCH}`")


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
    publish_via_branch("; ".join(summary_bits), preexisting)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
