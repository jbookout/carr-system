#!/usr/bin/env python3
"""bin/whose-work.py — is this mine, and has it landed? Measured against origin.

WHY THIS EXISTS. Rule 173119a8 binds at the moment a session is about to say
anything about uncommitted work, a dirty tree, or who is blocking whom: measure
against origin/<branch>, never against HEAD, and read the SIGN. The rule's own
incident was a session naming another session as the blocker off a HEAD-based
diff. The diff was accurate; the conclusion was backwards. A local HEAD that is
BEHIND origin makes work somebody else already landed look like work that has
gone missing.

Several sessions share the ~/carr-system checkout, so "is this mine, and is it
landed" comes up constantly and was answered by hand every time — badly at least
once, and slowly always.

AHEAD AND BEHIND ARE NOT SYMMETRIC and this tool never collapses them into one
"out of sync" number:

    AHEAD   commits that exist on this machine and nowhere else. Yours, and at
            risk. A correct fix that sat like this for an hour on 2026-08-14 is
            a filed defect: the pull request stayed red and kept emailing Joe
            while the fix sat locally.
    BEHIND  commits already on origin that this checkout has not pulled. Not
            yours, not missing, and nothing to fix — the state that got read
            backwards.

IT ALWAYS FETCHES FIRST. Measuring against a stale remote-tracking ref is the
same error one level down: the answer looks origin-based and is not.

REACHABILITY FOR THE UNLANDED QUESTION. `unlanded` asks whether any commit here
is reachable from NO remote ref, not whether the branch has an upstream. A push
under a different branch name — the normal shape of landing work in this repo,
where main is pull-request-only — leaves no upstream and nothing missing.

READ-ONLY. It fetches and reads. It never checks out, commits, pushes or resets.

RUN IT:
    ./bin/whose-work.py                # this repo, human-readable
    ./bin/whose-work.py --json         # machine-readable
    ./bin/whose-work.py --repo PATH
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


def git(repo: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, timeout=timeout)


def measure(repo: str) -> dict:
    out: dict = {"repo": repo}

    head = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    branch = (head.stdout or "").strip() or "HEAD"
    out["branch"] = branch

    remotes = [r for r in (git(repo, "remote").stdout or "").split() if r]
    if not remotes:
        # A local-only repository is a real answer, not an error. Saying "no
        # remote" is different from saying "nothing is missing" (rule 88e9b5eb).
        out.update({"no_remote": True, "ahead": 0, "behind": 0,
                    "unlanded": False, "loose": loose_files(repo),
                    "note": "no remote configured — nothing to measure against"})
        return out
    out["no_remote"] = False

    # Always fetch. Measuring against a stale remote-tracking ref is the same
    # mistake one level down, wearing an origin-shaped answer.
    fetched = git(repo, "fetch", "--quiet", remotes[0], timeout=180)
    out["fetched"] = fetched.returncode == 0

    upstream = f"{remotes[0]}/{branch}"
    exists = git(repo, "rev-parse", "--verify", "--quiet", upstream)
    if exists.returncode != 0:
        out.update({"ahead": None, "behind": None,
                    "note": f"{upstream} does not exist — this branch has never "
                            f"been pushed under this name"})
    else:
        counts = git(repo, "rev-list", "--left-right", "--count",
                     f"{upstream}...HEAD")
        behind, ahead = 0, 0
        parts = (counts.stdout or "").split()
        if len(parts) == 2:
            behind, ahead = int(parts[0]), int(parts[1])
        out["ahead"], out["behind"] = ahead, behind

    # Reachability, not upstream tracking. See the module docstring.
    unl = git(repo, "rev-list", "--count", "HEAD", "--not", "--remotes")
    try:
        out["unlanded_commits"] = int((unl.stdout or "0").strip())
    except ValueError:
        out["unlanded_commits"] = 0
    out["unlanded"] = out["unlanded_commits"] > 0

    out["loose"] = loose_files(repo)
    return out


def loose_files(repo: str) -> list[dict]:
    """Tracked files with uncommitted changes, each with its last author.

    The author is what turns a count into an attribution: in a shared checkout
    the useful question is never "how many files are dirty" but "whose are
    they", and the last committer of a file is the cheapest honest signal
    available without reading anyone's transcript.
    """
    p = git(repo, "status", "--porcelain", "--untracked-files=no")
    rows = []
    for line in (p.stdout or "").splitlines():
        if len(line) < 4:
            continue
        rel = line[3:].strip()
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        who = git(repo, "log", "-1", "--format=%an", "--", rel)
        rows.append({"path": rel, "status": line[:2].strip(),
                     "last_author": (who.stdout or "").strip() or "unknown"})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="is this mine, and has it landed?")
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(os.path.join(args.repo, ".git")):
        # A worktree carries a .git FILE rather than a directory; let git decide.
        if git(args.repo, "rev-parse", "--git-dir").returncode != 0:
            print(f"not a git repository: {args.repo}")
            return 1

    result = measure(args.repo)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"branch {result['branch']}")
    if result.get("no_remote"):
        print("  no remote configured — nothing to measure against")
    elif result.get("ahead") is None:
        print(f"  {result.get('note')}")
    else:
        a, b = result["ahead"], result["behind"]
        print(f"  AHEAD  {a:>3}  commit(s) on this machine and nowhere else"
              + ("" if a else "  (nothing at risk)"))
        print(f"  BEHIND {b:>3}  commit(s) already on origin, not pulled here"
              + ("" if b else "  (nothing waiting)"))
        if b and not a:
            print("\n  Reading this as your work going missing is the mistake rule "
                  "173119a8\n  exists for. Behind means somebody else landed "
                  "something. Pull it.")

    if result.get("unlanded"):
        print(f"\n  {result['unlanded_commits']} commit(s) reachable from NO remote "
              f"ref — push before this session ends.")

    loose = result.get("loose") or []
    if loose:
        print(f"\n  {len(loose)} tracked file(s) with uncommitted changes:")
        for row in loose:
            print(f"    {row['status']:<2} {row['path']}  (last committed by "
                  f"{row['last_author']})")
        print("\n  A file you did not write belongs to another session: leave it "
              "and say so.")
    else:
        print("\n  no uncommitted tracked changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
