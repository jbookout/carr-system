#!/usr/bin/env python3
"""inherited-from-main.py — is this red the branch's fault, or main's?

WHAT HAPPENED ON 2026-08-22. Between 01:38 and 02:17 UTC, six unrelated
branches — a tour packet, a cost gate, a retrieval fix, a CI experiment,
build-discipline and two others — all failed the SAME gates-class selftest.
Not one of them had touched it. Something that merged to main broke that
selftest, and because the required check builds refs/pull/N/merge (the PR head
merged into CURRENT main), main's break was inside every one of those runs.
Six sessions then spent six full CI runs and six sessions' worth of diagnostic
tokens reading a stack trace for a defect none of them wrote.

That is the failure this file exists to stop. It is the 2026-08-23 CI-failures
council's layer 1, the one both chairs marked SAFE and told us to ship
regardless of the other two: SuperGrok, "Ship this regardless of layers 2-3";
Codex, the same move as the attribution backstop.

WHAT IT DOES NOT DO: it does not make the run green. The run stays red and the
exit code stays nonzero, deliberately and on both chairs' explicit instruction
— a victim of a broken main must not merge onto that broken main. Codex put the
kill criterion on this exact point: "Kill any implementation under which a
skipped or neutral check accidentally satisfies branch protection." What
changes is the TIME (seconds instead of six minutes), the billed minutes, and
above all the victim session's tokens, because the run now says whose defect it
is looking at.

THE VERDICT COMES FROM RE-RUNNING THE CHECK AT THE MERGE BASE, not from the
diff. This is the whole safety argument and it is worth being precise about,
because the way this mechanism could do damage is by telling a session "not
your fault" about a defect that IS their fault.

  - The merge base contains NONE of this branch's changes. If the same check
    fails there, the branch cannot be the cause. That is a proof, not a heuristic.
  - The diff is only a CHEAP PRE-FILTER, and it is deliberately weak: it asks
    whether the change touched the check's own file, and skips the expensive
    re-run when it did. A branch can break a check without touching the check
    (change hooks/record-home-gate.py, watch its selftest go red), and in that
    case the pre-filter says "not touched", the re-run happens, the check PASSES
    at the merge base, and this file reports NOT INHERITED. Correct answer,
    reached by the re-run rather than by the diff. ops/inherited-from-main-selftest.py
    seeds exactly that case and fails if this file ever answers it wrong.

EVERY UNCERTAINTY RESOLVES TO "CANNOT TELL", never to "inherited". No merge
base, a shallow clone, a check that did not exist at the merge base, a check
that declined to run there (exit 78), a command that could not be executed
(126/127), a timeout, a worktree that would not materialise — all of them exit
2, and ops/ci.sh carries on with its normal full run. The failure mode of this
file is "you paid for a full run you could have skipped", which is the state we
are already in, and never "you were told to stop looking at a real defect".

Exit 0  INHERITED  — the same check fails at the merge base; this branch is a victim
Exit 1  NOT INHERITED — it passes at the merge base; this branch owns the failure
Exit 2  CANNOT TELL — refused to answer; the caller must behave as it did before

  ops/inherited-from-main.py --check <name> -- <cmd> [args...]
  ops/inherited-from-main.py --explain
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

INHERITED, NOT_INHERITED, CANNOT_TELL = 0, 1, 2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import scrubbed_env  # noqa: E402

# WHY scrubbed_env AND NOT A LIST OF OUR OWN. git reads GIT_DIR before it reads
# the working directory, and git EXPORTS GIT_DIR into every hook — so a
# subprocess started with cwd=<the merge-base tree> and GIT_DIR still set acts on
# whatever repository GIT_DIR names, not on the tree we materialised. That is the
# 2026-08-13 finding, and ops/git_env.py exists precisely so this list is not
# copied into a fifth file and left to drift. It matters twice here: for our own
# git calls, and for the check we re-run at the merge base, which may shell out
# to git itself.
#
# scrubbed_env rather than fixture_env: the merge-base tree is a REAL worktree of
# a real repository, and the check we run in it may legitimately want the
# caller's git identity and global config, exactly as it would in a normal run.


def _clean_env() -> dict:
    return scrubbed_env()


def git(repo: str, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", repo, *args],
                       capture_output=True, text=True, env=_clean_env())
    return p.returncode, (p.stdout or "").strip()


def refuse(reason: str) -> int:
    """Say why we are not answering. A silent decline is how a mechanism rots."""
    print(f"inherited-from-main: cannot tell — {reason}")
    return CANNOT_TELL


def resolve_base(repo: str) -> str | None:
    """The main-side ref to measure against, most trustworthy first."""
    for ref in (os.environ.get("CARR_INHERIT_BASE"), "origin/main", "main"):
        if ref and git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")[0] == 0:
            return ref
    return None


def check_file_of(cmd: list[str], repo: str) -> str | None:
    """The repo-relative path of the check being run, if one of the args is one.

    Used ONLY for the pre-filter and for the exists-at-the-merge-base test.
    Deliberately tolerant: an argument list we cannot read a path out of means
    no pre-filter, which costs one re-run and cannot produce a wrong verdict.
    """
    for arg in cmd:
        cand = os.path.normpath(os.path.join(repo, arg))
        if os.path.isfile(cand) and cand.startswith(repo + os.sep):
            return os.path.relpath(cand, repo)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--check", default="", help="the failing check's name, for the message")
    # 120s, not "however long the check takes". ops/ci.sh relies on this
    # default, and the point of the whole mechanism is failing in seconds
    # rather than six minutes; a merge-base re-run that outruns the budget
    # resolves to cannot-tell and the normal full run proceeds.
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    if a.help or a.explain:
        print(__doc__)
        return CANNOT_TELL

    cmd = [x for x in a.cmd if x != "--"]
    if not cmd:
        return refuse("no command to re-run was given")
    if os.environ.get("CARR_CI_NO_INHERIT_CHECK") == "1":
        return refuse("CARR_CI_NO_INHERIT_CHECK=1 — the short-circuit is switched off")

    rc, repo = git(os.getcwd(), "rev-parse", "--show-toplevel")
    if rc != 0 or not repo:
        return refuse("not inside a git repository")

    if git(repo, "rev-parse", "--is-shallow-repository")[1] == "true":
        return refuse("shallow clone — there is no merge base to compare against")

    base = resolve_base(repo)
    if not base:
        return refuse("no origin/main or main to compare against")

    rc, mb = git(repo, "merge-base", "HEAD", base)
    if rc != 0 or not mb:
        return refuse(f"HEAD and {base} share no merge base")

    rc, head = git(repo, "rev-parse", "HEAD")
    if rc != 0:
        return refuse("could not resolve HEAD")
    if head == mb:
        # Nothing sits between this tree and the base, so there is no "branch"
        # to exonerate. On main itself this is the normal case, and main's own
        # verdict is main's own — the canary names it, not this file.
        return refuse("HEAD is the merge base — this run IS the base; nothing to attribute")

    name = a.check or (check_file_of(cmd, repo) or " ".join(cmd))
    rel = check_file_of(cmd, repo)

    # PRE-FILTER (an optimisation, never the verdict — see the header).
    rc, diff = git(repo, "diff", "--name-only", mb, "HEAD")
    if rc != 0:
        return refuse(f"could not diff {mb[:8]}..HEAD")
    changed = set(diff.split("\n")) if diff else set()
    if rel and rel in changed:
        return refuse(f"the change touches {rel} — diagnose it here")

    # A check this branch ADDED cannot have failed at the merge base; without
    # this, python would exit 2 on the missing file and that would read as a
    # failure there, which is the exact misattribution this file must not make.
    if rel and git(repo, "cat-file", "-e", f"{mb}:{rel}")[0] != 0:
        return refuse(f"{rel} does not exist at the merge base — this branch added it")

    tmp = tempfile.mkdtemp(prefix=f"carr-mergebase-{os.getpid()}-")
    tree = os.path.join(tmp, "base")
    try:
        rc, out = git(repo, "worktree", "add", "--detach", "--quiet", tree, mb)
        if rc != 0:
            return refuse(f"could not materialise the merge base: {out[:160]}")
        try:
            p = subprocess.run(cmd, cwd=tree, capture_output=True, text=True,
                               env=_clean_env(), timeout=a.timeout)
        except subprocess.TimeoutExpired:
            return refuse(f"{name} did not finish within {a.timeout}s at the merge base")
        except OSError as exc:
            return refuse(f"could not execute {name} at the merge base: {exc}")

        if p.returncode == 0:
            print(f"inherited-from-main: NOT inherited — {name} passes at the merge base "
                  f"({mb[:8]}), so this branch introduced the failure.")
            return NOT_INHERITED
        if p.returncode == 78:
            return refuse(f"{name} declined to run at the merge base (exit 78, not configured)")
        if p.returncode in (126, 127):
            return refuse(f"{name} could not be executed at the merge base (exit {p.returncode})")

        # THE ANSWER. It fails on a tree that contains none of this branch.
        print(f"INHERITED FROM MAIN — do not diagnose this branch; "
              f"the break is {name} on main")
        print(f"  {name} exits {p.returncode} at the merge base {mb[:12]} "
              f"({base}), which carries none of this branch's {len(changed)} changed file(s).")
        print(f"  THE MOVE: nothing on this branch. Do not read the trace below as yours. "
              f"Wait for main to go green — the main canary names the break and the "
              f"merge freeze holds until it is fixed — then re-run this check.")
        print(f"  If you mean to be the one who fixes it, fix it ON MAIN in its own change; "
              f"a fix smuggled into this branch merges a second unrelated thing.")
        tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-6:]
        for line in tail:
            print(f"    | {line}")
        return INHERITED
    finally:
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", tree],
                       capture_output=True, text=True, env=_clean_env())
        subprocess.run(["git", "-C", repo, "worktree", "prune"],
                       capture_output=True, text=True, env=_clean_env())
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never let a crash here become an "inherited" verdict
        print(f"inherited-from-main: cannot tell — {exc!r}")
        sys.exit(CANNOT_TELL)
