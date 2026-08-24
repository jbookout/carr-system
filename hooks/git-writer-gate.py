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

WHICH TREE IT JUDGES: the one the command is aimed at — a worktree the command
names, else the worktree the session is STANDING IN (the payload's cwd), else
the shared canonical checkout. Never "always canonical", which is how this gate
spent 2026-08-14 to 08-22 refusing clean worktrees over other sessions' files.
See the long comment above target_tree().

WHAT IT BLOCKS, and only when THAT tree is genuinely SHARED (another writer has
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

# Script-relative, NOT expanduser("~/carr-system") — same fix as
# hooks/record-home-gate.py and the tools/test-*.py suites (commit fad87a4).
# A clone outside $HOME (CI checks out to /home/runner/work/carr-system/
# carr-system; Dell's clone need not sit at ~) made REPO a directory that does
# not exist, which here is worse than a bad log path: the `git -C REPO status`
# below silently returns nothing, so the gate reads a dirty tree as clean.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
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
    #
    # `drop <ref>` JOINED THE EXEMPTIONS 2026-08-14, after this gate refused
    # `git stash drop stash@{0}` on a dirty tree and left behind a stash nobody
    # could clear. The hazard here is one session capturing or overwriting
    # ANOTHER session's WORKING TREE. Dropping a stash the caller NAMES touches
    # no tree at all, and meets the same explicit-naming standard `git add
    # <paths>` and `git stash push -- <paths>` already meet. What stays refused
    # is the whole rest of the family, each for its own reason: bare `drop`
    # silently takes the most recent stash, which on a shared checkout is
    # probably someone else's; `pop` and `apply` write a stash back INTO the
    # tree, which is the hazard itself; and `clear` destroys every stash on the
    # machine, including ones this session never made.
    ("stash_all",       re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?stash\b(?!\s+(?:list|show|drop\s+\S|push\s+[^\n]*--\s+\S))", re.I)),
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


def dirty_files(tree=None, tracked_only=False):
    """Uncommitted paths in a tree — modified, staged, and (by default) untracked.

    tracked_only drops untracked files from the answer, and is used ONLY when
    judging a worktree. A branch switch does not destroy untracked files — git
    keeps them, or refuses outright when the target branch would overwrite one —
    whereas the 2026-08-09 incident this gate exists for was MODIFIED TRACKED
    files being swept into someone else's commit and then lost. A freshly created
    worktree also always carries build output (mcp-server/node_modules), so
    counting untracked files there would make the gate's own recommended remedy
    permanently unusable from the moment it is created. The shared tree keeps the
    stricter reading, because that is where other sessions actually live.
    """
    args = ["git", "-C", tree or REPO, "status", "--porcelain"]
    args.append("--untracked-files=no" if tracked_only else "--untracked-files=all")
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    files = []
    for line in out.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip().strip('"'))
    return files


# WHICH TREE IS THIS COMMAND ACTUALLY AIMED AT. This gate used to read the SHARED
# checkout's dirtiness no matter where the command ran, which produced a refusal
# that contradicted its own advice: the deny text says "to change branch, use a
# WORKTREE so no other session's tree moves", and then a `git checkout -b` issued
# from inside a worktree was refused anyway, because the MAIN tree was dirty.
# Moving a worktree's branch cannot touch the main tree's files — separate
# checkouts, separate indexes — so the shared tree's dirtiness is not evidence
# about that command. Measured 2026-08-14: the remedy the gate recommends was
# unusable while the condition it complains about was true, which is exactly when
# a session needs it.
#
# THAT FIX WAS HALF A FIX, and the missing half cost another eight days. It read
# the worktree out of the COMMAND TEXT only — a literal `cd <path>` or
# `git -C <path>` — so the exemption reached a command that spelled its tree out
# and no other. The habit every session here actually has is prefixing with
# `cd "$(pwd)" && …`, which names no literal path at all: the regex matched
# nothing, the gate fell back to the shared checkout, and on 2026-08-22 a
# `reset --hard` in a PERFECTLY CLEAN worktree was refused while listing files
# belonging to some other session. The refusal was correct about the risk and
# wrong about the tree, which is the worst shape a gate can have — it teaches the
# reader that the gate does not know what it is talking about.
#
# THE ANSWER MOVED OUT, 2026-08-23, to hooks/worktree_scope.py, because two more
# gates need exactly this answer and staging-attribution-gate.py's own docstring
# already says it reuses the tracker's `git status` diff. Rule a8c55a47: one job,
# one definition — the same arrangement gate_paths.py and cmd_text.py already
# have. That module carries the full reasoning, the resolution order, and the
# reason cwd (from the harness) cannot be spelled into something convenient.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from worktree_scope import target_tree, tree_label  # noqa: E402


# A PATH-SCOPED CHECKOUT IS NOT A BRANCH MOVE. `git checkout <ref> -- <paths>`
# leaves HEAD exactly where it was and rewrites only the paths it names, so it
# cannot make another session's files vanish the way the 2026-08-09 incident did
# — that was `commit` sweeping unnamed files, then `checkout main` moving HEAD
# underneath everyone. `git checkout -- <path>` was already exempt here; the form
# WITH a ref was not, which is the narrow form the deny text itself points people
# toward when they need one file back.
#
# The exemption is conditional, and the condition is the whole protection: it
# applies only when NONE of the named paths is currently dirty. Restoring a path
# that has no uncommitted changes destroys nothing. Restoring one that does is
# still refused, because that is the case where somebody's in-flight work is
# sitting in the file being overwritten.
_CHECKOUT_PATHS_RE = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?checkout\s+(?P<ref>\S+)\s+--\s+(?P<paths>.+)$", re.I)


# INERT-TEXT HANDLING LIVES IN cmd_text.py — shared verbatim with
# hooks/staging-attribution-gate.py, which refused the same prose one moment
# later for a different reason. Two gates, one definition of what is not a
# command. Two copies would drift silently, because each would still pass its
# own tests. (sys.path is already extended above, for worktree_scope.)
from cmd_text import strip_inert_text  # noqa: E402


def path_scoped_checkout_paths(cmd):
    """The paths a `git checkout <ref> -- <paths>` names, or None if not that."""
    m = _CHECKOUT_PATHS_RE.search(cmd.strip())
    if not m or m.group("ref").startswith("-"):
        return None
    raw = m.group("paths").split("&&")[0].split(";")[0].split("|")[0]
    paths = [p.strip().strip("'\"") for p in raw.split() if p.strip()]
    return paths or None


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

        # SCAN THE COMMANDS, NOT THE PROSE THEY CARRY. A heredoc body and a
        # quoted -m message are DATA — they are never executed — yet the whole
        # command string was being pattern-matched, so writing ABOUT a dangerous
        # git command was refused as though it were one. Hit three times on
        # 2026-08-14 alone: a .gitignore comment mentioning a staging command, and
        # twice on commit messages for this very file, whose entire subject is
        # which git commands are safe. The effect is perverse — it makes the fix
        # for a gate the hardest thing to describe in its own commit — and the
        # workaround it forces is writing the message to a file, which nobody
        # will remember and which teaches people the gate is noise.
        scannable = strip_inert_text(cmd)

        hit = None
        for name, pat in DANGEROUS:
            if pat.search(scannable):
                hit = name
                break
        if not hit:
            sys.exit(0)

        # Judge the tree the command is actually aimed at, not always the shared
        # one. target_tree() only ever returns a path under this repo's own
        # worktrees directory, so this cannot be pointed somewhere convenient.
        cwd = (payload.get("cwd") or payload.get("working_directory")
               or payload.get("workingDirectory"))
        tree = target_tree(cmd, cwd)
        dirty = dirty_files(tree, tracked_only=bool(tree))
        if not dirty:
            where = f" in worktree {os.path.basename(tree)}" if tree else ""
            dlog(f"ALLOW({hit}) tree clean{where} :: {cmd[:120]}")
            sys.exit(0)

        # `git checkout <ref> -- <paths>` where every named path is clean: HEAD
        # does not move and nothing uncommitted is overwritten, so there is
        # nothing for this gate to protect.
        scoped = path_scoped_checkout_paths(cmd)
        if scoped:
            collisions = [p for p in scoped
                          if any(d == p or d.startswith(p.rstrip("/") + "/") for d in dirty)]
            if not collisions:
                dlog(f"ALLOW({hit}) path-scoped, named paths clean :: {cmd[:120]}")
                sys.exit(0)
            dirty = collisions  # report only what actually blocks it

        shown = "\n".join(f"    {f}" for f in dirty[:12])
        more = f"\n    ... and {len(dirty) - 12} more" if len(dirty) > 12 else ""

        # NAME THE TREE THAT WAS ACTUALLY JUDGED. The old text always said
        # "~/carr-system", so on the days the gate read the wrong tree it also
        # reported the wrong one, and a session standing in a clean worktree was
        # handed a list of files it had never seen with no way to tell where they
        # lived. Whatever this refuses, the reader can now check it directly.
        judged = (f"Your worktree `{os.path.basename(tree)}`" if tree
                  else "~/carr-system (the SHARED canonical checkout)")
        peers = ("" if tree else
                 " and this machine runs several Claude sessions against that one "
                 "tree, so some of these are very likely another session's "
                 "in-flight work")

        reason = (
            f"GIT WRITER GATE — refused `{hit}`.\n\n"
            f"{judged} has {len(dirty)} uncommitted path(s) right now{peers}:\n\n"
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
               "dirty_count": len(dirty), "excerpt": cmd[:300],
               # WHICH TREE, in the audit trail too. Without it the log cannot
               # answer the question this whole change is about — how often a
               # refusal was about the caller's own tree versus somebody else's.
               "tree": tree_label(tree)})
        dlog(f"DENY({hit}) tree={tree_label(tree)} "
             f"dirty={len(dirty)} :: {cmd[:160]}")
        # Exit 2, not JSON: on any build that does not parse the structured
        # contract, exit 0 reads as ALLOW and the gate fails open silently.
        print(reason, file=sys.stderr)
        sys.exit(2)

    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
