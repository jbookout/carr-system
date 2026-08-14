#!/usr/bin/env python3
"""repo-config-selftest.py — the live repo's git config must survive the tests.

WHY THIS EXISTS (2026-08-14). A push reported "Everything up-to-date" while
carrying nothing, and the commit it should have pushed was not on main. The
cause was `core.bare = true` in ~/carr-system/.git/config on a checkout that
plainly has a working tree. In that state `git log` and `git show` keep working
normally while `status`, `diff`, `add` and `commit` all fail with "this
operation must be run in a work tree" — so the repo looks fine until you try to
save something. The same event left `user.email = selftest@example.invalid` in
the LOCAL config, which blocked the pre-push hook as a non-owner and
mis-attributed a commit to the test identity.

WHAT THIS DOES NOT CLAIM. It does not name the leaker. An attempt to bisect it
across every suite pointed at ops/ci-selftest.py, then failed to reproduce: the
leak fires on roughly one run in six, and several sessions run ops/ci.sh against
this same checkout concurrently, so a single-pass attribution is worthless here.
Rather than guess at a culprit and "fix" an innocent file, this makes the damage
impossible to miss and hands the next occurrence a timestamp and a named suite
to work from. The 2026-08-13 quarantine of the drift-watch suite is the standing
lesson: a wrong diagnosis of an intermittent fault costs more than no diagnosis.

WHY IT REPAIRS AND STILL FAILS. Leaving the repo broken would block every
session's commits, so it repairs. Repairing silently would hide a defect that
has already cost one lost push, so it also exits non-zero. Green means the
config was clean on arrival, never that it was cleaned up.
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A test identity has no business in a real checkout. Matched by suffix rather
# than by one literal address so a second fixture identity is caught too;
# .invalid is reserved by RFC 2606 precisely so it can never be a real address.
TEST_IDENTITY_SUFFIX = "@example.invalid"

failures: list[str] = []
repaired: list[str] = []


def git(*args):
    p = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    return p.returncode, (p.stdout or "").strip()


def check_not_bare():
    """A checkout with tracked files present is not a bare repo, ever."""
    rc, val = git("config", "--get", "core.bare")
    if val != "true":
        print("  ok   core.bare is not true")
        return
    # Confirm a working tree really is present before touching anything, so this
    # can never "repair" an intentionally bare mirror into a broken state.
    if not os.path.isdir(os.path.join(REPO, "ops")) or \
       not os.path.isfile(os.path.join(REPO, "run.sh")):
        print("  ok   core.bare is true and no working tree is present — genuinely bare")
        return
    git("config", "core.bare", "false")
    repaired.append("core.bare true -> false")
    failures.append(
        "core.bare was TRUE on a checkout that has a working tree. In that state "
        "status/diff/add/commit all fail while log/show keep working, so a push can "
        "report success and carry nothing. Repaired, but something set it."
    )


def check_no_test_identity():
    """The LOCAL identity must not be a fixture address."""
    for key in ("user.email", "user.name"):
        rc, val = git("config", "--local", "--get", key)
        if rc != 0 or not val:
            continue
        if key == "user.email" and val.endswith(TEST_IDENTITY_SUFFIX):
            git("config", "--local", "--unset", key)
            repaired.append(f"local {key}={val} unset")
            failures.append(
                f"local {key} was the fixture identity {val}. That blocks the pre-push "
                f"hook as a non-owner and mis-attributes commits. Unset, so the real "
                f"global identity applies again."
            )
        elif key == "user.name" and val == "selftest":
            git("config", "--local", "--unset", key)
            repaired.append(f"local {key}={val} unset")
            failures.append(f"local {key} was the fixture identity {val}. Unset.")
        else:
            print(f"  ok   local {key} is not a fixture identity")


def check_worktree_usable():
    """The end-to-end proof: the command that actually broke must work."""
    rc, _ = git("status", "--porcelain")
    if rc == 0:
        print("  ok   git status works — the working tree is usable")
    else:
        failures.append(
            "git status still fails after repair — the working tree is not usable, "
            "and no session can commit until this is resolved by hand."
        )


print("repo config invariants (the live checkout, not a fixture)")
check_not_bare()
check_no_test_identity()
check_worktree_usable()

print()
if failures:
    print(f"FAIL {len(failures)} invariant(s) violated:")
    for f in failures:
        print(f"  - {f}")
    if repaired:
        print(f"  repaired in place: {', '.join(repaired)}")
    print("  Green here means the config was CLEAN ON ARRIVAL, never that it was fixed.")
    print("  Something in this suite writes the live repo's git config. Note which")
    print("  suites ran alongside this one and when — that is the evidence the next")
    print("  occurrence needs, and it is why this fails loudly instead of healing quietly.")
    sys.exit(1)
print("OK all checks passed")
