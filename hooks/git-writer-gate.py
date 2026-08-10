#!/usr/bin/env python3
"""git-writer-gate.py — PreToolUse gate on destructive git in a SHARED tree.

WHY, IN ONE INCIDENT. On 2026-08-09 a concurrent session ran `git commit`
sweeping ANOTHER session's uncommitted in-flight work onto branch
dictation-phase-b-loop-243, then `git checkout main`. Six files vanished from
the working tree mid-build — council.sh, council-lib.sh, precheck.sh,
harden-gates.sh and both gate selftests. Nothing was destroyed (the commit is
recoverable) but the authoring session lost its files with no warning and spent
an hour rebuilding them, and the first diagnosis was "another session deleted
my work", which was wrong and reported to Joe as fact.

THE RULE ALREADY EXISTED AND DID NOT BIND. CLAUDE.md gained this the SAME DAY:

    "Background and unattended sessions never run `git commit`, `git push`, or
     `git checkout` ... ~/carr-system regularly holds another live session's
     uncommitted work, and a background agent preserving its own changes would
     sweep that writer's files into a commit on whatever branch HEAD happens to
     be on."  (rule 308ef1de)

Written before it happened, describing it exactly, violated anyway — the same
pattern as rules 14e0408b / e313a3ca / 179be4b8, which were all active, all
recited at session start, and all violated within hours. Prose does not bind.
This does.

WHAT IT BLOCKS, and only when the tree is genuinely SHARED (another writer has
uncommitted changes this session did not make):
  - `git checkout <branch>` / `git switch` — the move that silently removes
    files belonging to whoever else is mid-build
  - `git commit -a` / `git commit -A` / `git add -A` / `git add .` — the
    sweep that captures another writer's files into your commit
  - `git clean` — removes untracked files, which is where in-flight work lives
  - `git reset --hard` / `git restore .` / `git checkout .` — discards it

WHAT IT ALWAYS ALLOWS, because these cannot take another writer's work:
  - `git add <specific paths>` then `git commit` — the correct pattern, and the
    one this build should have used from the first hour
  - every read-only git verb (status, log, diff, show, branch, reflog...)
  - `git stash push -- <paths>` naming paths explicitly
  - anything at all when the tree holds no other writer's changes

JOE + DELL ARE NOT THE RISK, and this gate is not about them. Dell works from a
SEPARATE CLONE on his own machine; he cannot reach this working tree, and git
merge is precisely the tool for that case. The hazard is several of JOE'S OWN
sessions sharing one checkout of ~/carr-system, which is what actually happened.

FAILS OPEN on any error: a wedged session is worse than a risky commit, and the
work is recoverable either way.

Fixtures: ops/git-writer-gate-selftest.py
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.expanduser("~/carr-system")
LOG = os.path.join(REPO, "out", "conduct-gate.jsonl")
DEBUG = os.path.join(REPO, "out", "conduct-gate.log")

# Ordered: first match wins, so the specific safe forms are tested before the
# broad dangerous ones.
DANGEROUS = [
    ("checkout_branch", re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(?:checkout|switch)\s+(?!--?\s*$)(?!-- )(?!\.)\S", re.I)),
    ("checkout_paths",  re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?checkout\s+(?:--\s+)?\.", re.I)),
    ("commit_all",      re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?commit\b[^\n|;&]*\s-\w*[aA]", re.I)),
    ("add_all",         re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?add\s+(?:-\w*[Au]\b|\.(?:\s|$)|--all\b)", re.I)),
    ("clean",           re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?clean\b", re.I)),
    ("reset_hard",      re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?reset\s+[^\n|;&]*--hard", re.I)),
    ("restore_all",     re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?restore\s+(?:--\s+)?\.", re.I)),
    # \b not \s+ after `stash`: the bare `git stash` (no arguments at all) is
    # the most dangerous form — it pockets the WHOLE tree including another
    # writer's files — and a \s+ requirement missed it entirely because there is
    # nothing after the word. Caught by the selftest.
    ("stash_all",       re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?stash\b(?!\s+(?:list|show|push\s+[^\n]*--\s+\S))", re.I)),
]


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()}  git-writer-gate  {msg}\n")
    except Exception:
        pass


def audit(rec):
    if rec.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def dirty_files():
    """Uncommitted paths in the shared tree — modified, staged, or untracked."""
    try:
        out = subprocess.run(
            ["git", "-C", REPO, "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    files = []
    for line in out.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip().strip('"'))
    return files


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        dlog(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        if (payload.get("tool_name") or payload.get("toolName")) != "Bash":
            sys.exit(0)
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        cmd = ti.get("command", "") if isinstance(ti, dict) else ""
        if "git" not in cmd:
            sys.exit(0)

        hit = None
        for name, pat in DANGEROUS:
            if pat.search(cmd):
                hit = name
                break
        if not hit:
            sys.exit(0)

        dirty = dirty_files()
        if not dirty:
            dlog(f"ALLOW({hit}) tree clean :: {cmd[:120]}")
            sys.exit(0)

        shown = "\n".join(f"    {f}" for f in dirty[:12])
        more = f"\n    ... and {len(dirty) - 12} more" if len(dirty) > 12 else ""

        reason = (
            f"GIT WRITER GATE — refused `{hit}`.\n\n"
            f"~/carr-system has {len(dirty)} uncommitted path(s) right now, and this "
            f"machine runs several Claude sessions against ONE shared working tree. "
            f"Some of these are very likely another session's in-flight work:\n\n"
            f"{shown}{more}\n\n"
            "WHAT THIS PREVENTS, which already happened on 2026-08-09: a session ran "
            "`git commit` sweeping another session's files onto its own branch, then "
            "`git checkout main`, and six files vanished mid-build from the session "
            "that authored them. Recoverable, but an hour was lost rebuilding and the "
            "first diagnosis was a wrong accusation of deletion. Rule 308ef1de and "
            "CLAUDE.md both forbid exactly this, and both were active that day.\n\n"
            "DO THIS INSTEAD:\n"
            "  - `git add <specific paths you wrote>` then `git commit` — never -A, "
            "never -a, never `.`\n"
            "  - to change branch, use a WORKTREE so no other session's tree moves\n"
            "  - if you genuinely need the whole tree, confirm with Joe first and say "
            "which files belong to someone else\n\n"
            "Read-only git is unaffected. This gate is silent when the tree is clean."
        )

        audit({"ts": now(), "hook": "git-writer-gate", "classes": ["shared_tree_git"],
               "patterns": [f"git:{hit}"], "session": payload.get("session_id"),
               "dirty_count": len(dirty), "excerpt": cmd[:300]})
        dlog(f"DENY({hit}) dirty={len(dirty)} :: {cmd[:160]}")
        # Exit 2, not JSON: on any build that does not parse the structured
        # contract, exit 0 reads as ALLOW and the gate fails open silently.
        print(reason, file=sys.stderr)
        sys.exit(2)

    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
