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


def main():
    quiet = "--quiet" in sys.argv[1:]
    claims = {}

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
            print(f"ERROR: origin/main violates the migration-number contract: {exc}",
                  file=sys.stderr)
            return 1

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
            # WHOSE TREE IS IT. A violation in origin/main or in the tree the
            # caller is standing in is theirs to fix and genuinely blocks: the
            # number they are about to be handed may be wrong. A violation in
            # ANOTHER session's worktree is neither.
            #
            # This distinction cost most of a night on 2026-08-22. Two branches
            # merged 0248 twice; the collision was resolved on main by renumber,
            # and every OTHER worktree on the machine still held the old
            # filename on disk — as stale checkouts do, harmlessly. Because this
            # returned 1 for any tree, the allocator refused for everyone,
            # migration-number-contract-selftest.py failed, and the pre-push
            # gate refused pushes from branches touching no migration at all.
            # Three unrelated pushes were blocked by a file nobody was editing,
            # and the only way through was skipping CI entirely — which is worse
            # than the thing being guarded against.
            #
            # The council ruled this class on 2026-08-22: a machine-global
            # condition may open a loop, never veto unrelated work. So a foreign
            # tree now WARNS and its numbers are still merged into the claim set
            # below, which is the part that actually protects the caller — those
            # numbers stay reserved either way.
            if os.path.realpath(wt) == here:
                print(f"ERROR: this tree violates the migration-number contract: {exc}",
                      file=sys.stderr)
                return 1
            print(f"WARNING: {label} violates the migration-number contract: {exc}\n"
                  f"  It is another session's checkout, not yours, and it does not make the "
                  f"number below unsafe — its numbers are still counted as claimed. Most often "
                  f"this is a stale worktree that has not pulled since a renumber on main.",
                  file=sys.stderr)
        merge(claims, numbers_from_names(tree_names), label)

    # 4. THE RESERVATION LEDGER (2026-08-24, council cluster B). A number
    # another session has RESERVED at mint time is claimed whether or not any
    # file exists yet — the whole point of reserving. The ledger lives in the
    # canonical checkout's out/, which every worktree on this machine shares.
    # A torn or missing ledger line is skipped by read_reservations(), and a
    # ledger this tool cannot read degrades to today's behaviour (warn, don't
    # refuse) rather than blocking allocation.
    ledger = os.path.join(REPO, "out", "migration-reservations.jsonl")
    try:
        with open(ledger, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("number"), int):
                    merge(claims, {row["number"]: {"reserved"}}, "reservation")
    except OSError:
        pass  # no ledger yet — nothing reserved on this machine

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
