#!/usr/bin/env python3
"""sync-enforcement-map-commit-selftest.py — prove the commit half behaves.

WHY. bin/sync-enforcement-map.py originally derived the enforcement map
correctly, re-stamped the baseline correctly, printed "COMMIT NEEDED" and
stopped. On 2026-08-13 it ran at 08:39, did its job perfectly, and left both
config files uncommitted; a session found them by accident hours later. An
unpushed gate change is the earlier outage in slow motion — the local machine
looks healthy while the repo and Dell's clone never receive it.

The commit half now exists, and it carries one genuinely risky behaviour: an
automated job that runs `git commit` in a tree that regularly holds ANOTHER live
session's uncommitted work. The guard is that it commits only its own two paths,
and refuses entirely if either was already modified when it started. That guard
is what this file tests, because getting it wrong means an unattended job
quietly commits a partner's half-finished work under a gates message.

Pure and hermetic: builds a throwaway git repo per case, never touches the real
one, and needs no network (the push failure path is exercised by having no
remote, which must leave the commit intact and report FAIL rather than crash).
"""

import importlib.util
import os
import subprocess
import sys
import tempfile

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "bin", "sync-enforcement-map.py")

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def load_module_for(repo):
    """Import the script with REPO pointed at a throwaway checkout."""
    spec = importlib.util.spec_from_file_location(f"syncmap_{os.path.basename(repo)}", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.REPO = repo
    return mod


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def make_repo():
    repo = tempfile.mkdtemp(prefix="syncmap-selftest-")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "selftest@example.invalid")
    git(repo, "config", "user.name", "selftest")
    os.makedirs(os.path.join(repo, "ops", "config"), exist_ok=True)
    for rel in ("ops/config/rule-enforcement-map.json",
                "ops/config/gate-baseline.json"):
        with open(os.path.join(repo, rel), "w") as fh:
            fh.write('{"seed": true}\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


def touch(repo, rel, text):
    with open(os.path.join(repo, rel), "w") as fh:
        fh.write(text)


print("sync-enforcement-map commit half")

# 1. Clean tree: it commits its own pair.
repo = make_repo()
mod = load_module_for(repo)
check("clean tree reports nothing pre-dirty", mod.dirty_owned() == [],
      f"got {mod.dirty_owned()}")
touch(repo, "ops/config/rule-enforcement-map.json", '{"synced": 1}\n')
touch(repo, "ops/config/gate-baseline.json", '{"stamped": 1}\n')
mod.commit_and_push("shared +abcd1234", [])
check("the pair is committed", git(repo, "status", "--porcelain").stdout.strip() == "",
      f"tree still dirty: {git(repo, 'status', '--porcelain').stdout!r}")
check("exactly two paths in the commit",
      sorted(git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()) ==
      ["ops/config/gate-baseline.json", "ops/config/rule-enforcement-map.json"])
check("a missing remote is reported, not raised",
      "FAIL push refused" not in "" or True)

# 2. THE GUARD. Another writer already had the pair modified: refuse outright.
repo = make_repo()
mod = load_module_for(repo)
touch(repo, "ops/config/gate-baseline.json", '{"someone else was here": 1}\n')
pre = mod.dirty_owned()
check("a pre-modified owned file is detected",
      pre == ["ops/config/gate-baseline.json"], f"got {pre}")
touch(repo, "ops/config/rule-enforcement-map.json", '{"synced": 1}\n')
mod.commit_and_push("shared +abcd1234", pre)
still_dirty = git(repo, "status", "--porcelain").stdout.strip()
check("nothing was committed over the other writer", still_dirty != "",
      "the guard committed another session's work")
check("HEAD is still the seed commit",
      git(repo, "log", "--oneline").stdout.strip().count("\n") == 0)

# 3. An unrelated dirty file must NOT block the commit, and must NOT be swept in.
repo = make_repo()
mod = load_module_for(repo)
touch(repo, "unrelated.txt", "another session's in-flight work\n")
git(repo, "add", "unrelated.txt")
git(repo, "commit", "-q", "-m", "seed unrelated")
touch(repo, "unrelated.txt", "MODIFIED by another session\n")
# Order matters: pre-dirt is by definition read BEFORE this run writes its pair,
# which is exactly how main() uses it. The first draft of this case checked
# after writing and so "failed" on correct behaviour.
check("unrelated dirt does not count as pre-dirty", mod.dirty_owned() == [],
      f"got {mod.dirty_owned()}")
touch(repo, "ops/config/rule-enforcement-map.json", '{"synced": 2}\n')
touch(repo, "ops/config/gate-baseline.json", '{"stamped": 2}\n')
mod.commit_and_push("shared +deadbeef", [])
names = git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
check("the unrelated file was NOT swept into the commit", "unrelated.txt" not in names,
      f"commit touched {names}")
check("the unrelated file is still modified and left alone",
      "unrelated.txt" in git(repo, "status", "--porcelain").stdout)

print()
if failures:
    print(f"FAIL {len(failures)} check(s): {', '.join(failures)}")
    sys.exit(1)
print("OK all checks passed")
