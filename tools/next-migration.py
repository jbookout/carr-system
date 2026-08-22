#!/usr/bin/env python3
"""next-migration.py — allocate the next migration number without colliding.

WHY THIS EXISTS (2026-08-13). Migration numbers are a single global ordered
namespace, and the method for picking the next one was `ls migrations/ | tail`.
That reads ONE directory: the tree you happen to be standing in. It cannot see a
number a concurrent session has already taken in a file it has not committed yet,
and this machine runs many sessions against one repository with NINETEEN linked
worktrees. Nine migrations landed on 2026-08-13 alone, from several sessions.

The collision is not hypothetical and it is not rare: on 2026-08-12 a session
wrote 0110, a peer took 0110 while it was writing, and it renumbered to 0111. The
same shape recurred the next day. Each one costs a rename plus every reference
that moved with it.

WORKTREES MAKE THIS WORSE, WHICH IS THE POINT WORTH UNDERSTANDING. Isolating each
session in its own tree is the right answer to sweeping another writer's FILES,
and it is already standard here. But it is the wrong shape for a SHARED namespace:
the more isolated the trees, the less any one session can see of what the others
have already claimed. Isolation and allocation pull in opposite directions, so the
allocation has to be done deliberately rather than by looking around.

WHAT THIS READS, and why each source is needed:
  1. origin/main            — every number already merged, including ones this
                              checkout has not fetched into its working tree.
  2. this tree's migrations/ — committed and uncommitted alike.
  3. EVERY OTHER WORKTREE's migrations/ — the source `ls` cannot see and the one
                              that actually causes the collisions. A peer's
                              uncommitted 0112 is a real claim on 0112.

It prints the next free number and, separately, names any number that is claimed
only by an uncommitted file somewhere — because that is a number that will look
free to anyone reading git alone, and is not.

Risk colour GREEN: reads only. Writes nothing, creates nothing, sends nothing.

Usage:
  ./run.sh next-migration            # the next free number, and what is in flight
  ./run.sh next-migration --quiet    # just the number, for scripting
"""

import os
import re
import subprocess
import sys

from migration_number_contract import (
    FROZEN_COLLISIONS,
    MigrationNumberError,
    collision_report,
    validate_migration_names,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NUM = re.compile(r"^(\d{4})[_a-z0-9]*.*\.sql$", re.IGNORECASE)


def run(args, cwd=REPO):
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def numbers_from_names(names):
    """Map every 4-digit-prefixed .sql name to its int. Ignores anything else —
    0013a_historical_client_status_vocabulary.sql is a real filename here, so the
    pattern deliberately tolerates a suffix after the digits."""
    out = {}
    for n in names:
        base = os.path.basename(n.strip())
        m = NUM.match(base)
        if m:
            out.setdefault(int(m.group(1)), set()).add(base)
    return out


def merge(into, more, source):
    for num, names in more.items():
        for name in names:
            into.setdefault(num, {}).setdefault(name, set()).add(source)


def worktree_paths():
    """Every checkout attached to this repository, the main one included."""
    paths = []
    for line in run(["git", "worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            paths.append(line.split(" ", 1)[1].strip())
    return paths or [REPO]


def _head_contains_origin_main() -> bool:
    """True only when this checkout includes the exact remote base it repairs."""
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
            cwd=REPO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


TRANSIENT_ORIGIN_REPAIR = {
    "0248_renewal_signed_source_ingress.sql": "0249_renewal_signed_source_ingress.sql",
    "0249_calendar_prebrief_joe_runtime.sql": "0250_calendar_prebrief_joe_runtime.sql",
}
TRANSIENT_APPLIED_IDENTITY = "0248_register_conduct_stop_control.sql"


def _repairs_exact_origin_collision(
    remote_names: list[str],
    current_names: list[str],
    schema_text: str,
    contents_match: bool,
) -> bool:
    """Admit only the exact, self-expiring 0248/0249 repair PR state.

    A repair PR necessarily sees the broken base through ``origin/main`` until
    it merges.  The exception retains the one production-snapshot identity,
    removes both old unapplied names, adds their ordered replacements, and
    requires the old/new SQL bytes to match. Any extra or different collision
    still refuses. Once merged, origin/main is valid and this path is inactive.
    """
    remote, current = set(remote_names), set(current_names)
    new_collisions = {
        slot: names for slot, names in collision_report(remote_names).items()
        if slot not in FROZEN_COLLISIONS
    }
    expected_collision = tuple(sorted((
        TRANSIENT_APPLIED_IDENTITY,
        "0248_renewal_signed_source_ingress.sql",
    )))
    if new_collisions != {"0248": expected_collision} or not contents_match:
        return False
    if f"{TRANSIENT_APPLIED_IDENTITY}\t" not in schema_text:
        return False
    if any(f"{old}\t" in schema_text for old in TRANSIENT_ORIGIN_REPAIR):
        return False
    if TRANSIENT_APPLIED_IDENTITY not in remote or TRANSIENT_APPLIED_IDENTITY not in current:
        return False
    return all(old in remote and old not in current and new in current
               for old, new in TRANSIENT_ORIGIN_REPAIR.items())


def _repair_contents_match() -> bool:
    for old, new in TRANSIENT_ORIGIN_REPAIR.items():
        try:
            old_blob = subprocess.run(
                ["git", "show", f"origin/main:migrations/{old}"],
                cwd=REPO,
                capture_output=True,
                timeout=30,
                check=True,
            ).stdout
            with open(os.path.join(REPO, "migrations", new), "rb") as fh:
                new_blob = fh.read()
        except (OSError, subprocess.SubprocessError):
            return False
        if old_blob != new_blob:
            return False
    return True


def _is_exact_broken_peer_tree(names: list[str]) -> bool:
    """Recognize only a peer checkout carrying the same transient 0248 pair."""
    expected = tuple(sorted((
        TRANSIENT_APPLIED_IDENTITY,
        "0248_renewal_signed_source_ingress.sql",
    )))
    unregistered = {
        slot: claimed for slot, claimed in collision_report(names).items()
        if slot not in FROZEN_COLLISIONS
    }
    if unregistered != {"0248": expected}:
        return False
    filtered = [name for name in names if name != "0248_renewal_signed_source_ingress.sql"]
    try:
        validate_migration_names(filtered, allow_frozen_subset=True)
    except MigrationNumberError:
        return False
    return True


def main():
    quiet = "--quiet" in sys.argv[1:]
    claims = {}
    transient_repair = False

    try:
        current_names = os.listdir(os.path.join(REPO, "migrations"))
        with open(os.path.join(REPO, "db", "schema.sql"), encoding="utf-8") as fh:
            schema_text = fh.read()
    except OSError:
        current_names, schema_text = [], ""

    # 1. everything merged on the remote
    remote = run(["git", "ls-tree", "--name-only", "origin/main", "migrations/"])
    remote_names = [os.path.basename(name.strip()) for name in remote.splitlines() if name.strip()]
    merge(claims, numbers_from_names(remote_names), "origin/main")
    if not remote:
        print("WARNING: could not read origin/main — the number below is based only on "
              "local trees and may collide with something already merged.", file=sys.stderr)
    else:
        try:
            validate_migration_names(remote_names, require_frozen=True)
        except MigrationNumberError as exc:
            try:
                validate_migration_names(current_names, require_frozen=True)
            except MigrationNumberError:
                current_tree_valid = False
            else:
                current_tree_valid = True
            transient_repair = (
                current_tree_valid
                and _head_contains_origin_main()
                and _repairs_exact_origin_collision(
                    remote_names, current_names, schema_text, _repair_contents_match()
                )
            )
            if not transient_repair:
                print(f"ERROR: origin/main violates the migration-number contract: {exc}",
                      file=sys.stderr)
                return 1
            print(
                "WARNING: origin/main has an applied-vs-pending migration collision; "
                "this descendant tree keeps the snapshot-ledger identity and renumbers "
                "only the unapplied file.",
                file=sys.stderr,
            )
            remote_names = current_names

    # 2 & 3. every worktree's migrations/ directory, on disk, committed or not
    here = os.path.realpath(REPO)
    for wt in worktree_paths():
        mdir = os.path.join(wt, "migrations")
        if not os.path.isdir(mdir):
            continue
        label = "this tree" if os.path.realpath(wt) == here else f"worktree {os.path.basename(wt)}"
        try:
            tree_names = os.listdir(mdir)
        except OSError:
            continue
        try:
            validate_migration_names(tree_names, allow_frozen_subset=True)
        except MigrationNumberError as exc:
            if transient_repair and _is_exact_broken_peer_tree(tree_names):
                # A clean peer checkout of the exact broken base is expected
                # while this repair PR is in flight. No other invalid tree is.
                merge(claims, numbers_from_names(current_names), label)
                continue
            print(f"ERROR: {label} violates the migration-number contract: {exc}",
                  file=sys.stderr)
            return 1
        merge(claims, numbers_from_names(tree_names), label)

    if not claims:
        print("0001", flush=True)
        return 0

    nxt = max(claims) + 1
    if quiet:
        print(f"{nxt:04d}")
        return 0

    print(f"next free migration number: {nxt:04d}")
    print(f"  highest claimed: {max(claims):04d}")

    frozen = collision_report(remote_names)
    if frozen:
        print("\n  frozen numeric collisions on origin/main — full filenames are distinct")
        print("  ledger identities; never rename, delete, edit, or add to these sets:")
        for slot, names in frozen.items():
            print(f"    {slot}: {', '.join(names)}")

    # A number absent from origin/main is the dangerous case. It may be genuinely
    # uncommitted, or committed on a branch that has not merged — the distinction
    # does not matter to the collision and IS NOT ASSERTED HERE, because this
    # function cannot tell them apart from a directory listing and a tool that
    # guesses which it is would be stating something it did not check. What both
    # cases share is the only thing that matters: origin/main does not show the
    # number, so it looks free to anyone reading git, and it is not.
    in_flight = []
    for num in sorted(claims):
        for name, sources in claims[num].items():
            if "origin/main" not in sources:
                in_flight.append((num, name, sorted(sources)))
    if in_flight:
        print("\n  CLAIMED BUT NOT ON origin/main — uncommitted, or on an unmerged")
        print("  branch. Either way it looks free to anyone reading git, and is not:")
        for num, name, sources in in_flight:
            print(f"    {num:04d}  {name}  — in {', '.join(sources)}")
        print("\n  Do not reuse those numbers. Re-run this immediately before you write "
              "the file; another session may claim one while you are typing.")
    else:
        print("  every claimed number is on origin/main — nothing in flight elsewhere")
    return 0


if __name__ == "__main__":
    sys.exit(main())
