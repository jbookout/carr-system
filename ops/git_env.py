#!/usr/bin/env python3
"""git_env.py — the one place that knows how to stop git escaping its directory.

WHY THIS FILE EXISTS. On 2026-08-13 the shared checkout's `main` was found
pointing at a commit authored by `selftest@example.invalid`, with 801 files
staged. The cause was not a bad test. It was a wrong belief, held in several
files at once:

    subprocess.run(["git", ...], cwd=repo)      # <- believed to be isolation

It is not. Git reads GIT_DIR before it reads the working directory, and GIT_DIR
IS EXPORTED BY EVERY GIT HOOK. `ops/githooks/pre-push` runs `ops/ci.sh`, which
runs the selftests, so an ordinary `git push` from this machine handed every
"throwaway fixture" git call a pointer straight back at the real repository.
`git config user.email selftest@...` then rewrote the shared identity and
`git commit` moved the shared branch.

`git -C <dir>` does not help either: -C changes the directory, and GIT_DIR still
outranks the directory.

The fix is to remove the variables. That was done per-file for
sync-enforcement-map in PR #15, which left the list of variables duplicated in
two places and about to be duplicated in two more. A scrub list that exists in
four copies is a scrub list that will drift, and drift here is silent — nothing
fails, the wrong repository is simply modified. So it lives here once, and
rule a8c55a47 applies: the manual path and the automated path doing the same job
must be the same code.

WHAT CALLS THIS, and why the two functions differ:

  scrubbed_env()  for REAL work that must act on a KNOWN repo — bin/sync-
                  enforcement-map.py commits and pushes two gate files on the
                  hourly refresh, and must do that to its own REPO regardless of
                  who invoked it. Keeps the caller's identity and global config,
                  because a real commit needs a real author.

  fixture_env()   for THROWAWAY repos in tests. Everything scrubbed_env() drops,
                  plus system and global config, so a fixture cannot read Joe's
                  identity and a stray `git config` has nowhere real to land.

Fixtures: ops/git-env-selftest.py, which also fails if ops/githooks/pre-push
stops covering GIT_LOCATION_VARS — the hook keeps its own literal list on
purpose (see the note there) and that case is what stops the two drifting.
"""
import os

# Every variable git consults BEFORE the working directory. Sourced from
# git(1)'s environment section rather than from memory, and deliberately
# over-inclusive: an extra name in this tuple costs nothing, a missing one costs
# a shared repository.
GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
    "GIT_PREFIX",
    "GIT_WORK_TREE_OVERRIDE",
)

# Config discovery. Scrubbed only for fixtures: real runs want the real author.
GIT_CONFIG_VARS = (
    "GIT_CONFIG",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_COUNT",
)


def scrubbed_env(base=None):
    """Environment for a git call that must act on the directory it is given.

    Drops only the location variables, so identity and global config survive —
    a real commit needs a real author.
    """
    env = dict(os.environ if base is None else base)
    for var in GIT_LOCATION_VARS:
        env.pop(var, None)
    # GIT_CONFIG_COUNT drives the GIT_CONFIG_KEY_<n>/GIT_CONFIG_VALUE_<n> pairs,
    # which can carry core.worktree and relocate the call as surely as GIT_DIR.
    count = env.pop("GIT_CONFIG_COUNT", None)
    if count:
        try:
            for i in range(int(count)):
                env.pop(f"GIT_CONFIG_KEY_{i}", None)
                env.pop(f"GIT_CONFIG_VALUE_{i}", None)
        except ValueError:
            pass
    return env


def fixture_env(base=None):
    """Environment for a git call against a THROWAWAY repo in a test.

    Everything scrubbed_env() removes, plus system and global config discovery,
    so the fixture cannot inherit a real identity and a `git config` that
    escaped --local has nowhere real to land.
    """
    env = scrubbed_env(base)
    for var in GIT_CONFIG_VARS:
        env.pop(var, None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env
