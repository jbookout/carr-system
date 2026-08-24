#!/usr/bin/env python3
"""worktree_scope.py — ONE definition of "which tree is this command about".

WHY THIS MODULE EXISTS. Every Claude Code hook on this machine is wired into
settings.json by its ABSOLUTE CANONICAL PATH (`/Users/booko/carr-system/hooks/
...`; gate-integrity.py:433 enforces that spelling). So the script-relative
`REPO = dirname(dirname(__file__))` that every gate here computes always
resolves to ~/carr-system — the shared integration checkout — no matter which
worktree the session issuing the command is actually standing in.

That is correct for a gate judging where a FILE lives. It is wrong for a gate
judging TREE STATE, and the difference cost this fleet eight days:

  - 2026-08-14  git-writer-gate.py refused `git checkout -b` issued from inside
                a worktree because the MAIN tree was dirty — the exact remedy
                its own deny text recommends, unusable precisely when needed.
  - 2026-08-21  an UNTRACKED migration in one session's worktree refused pushes
                from four unrelated branches plus canonical main.
  - 2026-08-22  a `reset --hard` in a perfectly clean worktree was refused, and
                handed the session a list of files belonging to somebody else.

Council recommendation 1, 2026-08-23 process audit, both chairs, safe / very
high confidence: "every git gate must evaluate the INVOKING worktree's state,
never the canonical checkout." Codex chair: "Canonical dirt, untracked files,
migrations, or branch position must not affect another worktree's push."

WHY IT IS A MODULE AND NOT THREE COPIES. Rule a8c55a47 — a manual path and an
automated path that do the same job must be the same code. Three gates need this
answer (git-writer-gate.py, staging-attribution-gate.py, staging-observation-
tracker.py), and staging-attribution-gate.py's docstring already says it reuses
the tracker's `git status` diff. Three copies would drift silently, because each
would still pass its own tests — which is the same reasoning that put the gate
list in gate_paths.py and the inert-text rule in cmd_text.py.

THE RESOLUTION ORDER, and each step earns its place:
  1. a tree the command TEXT names, via `git -C <path>` or `cd <path>` — because
     `cd <somewhere> && git ...` really does aim somewhere else, and a command
     naming the CANONICAL checkout must be judged against canonical however
     comfortable the session's own cwd would be.
  2. the session's own cwd, from the hook payload — supplied by the harness, not
     parsed out of a string the session wrote. This is the step that was missing
     for eight days: the habit here is `cd "$(pwd)" && ...`, which names no
     literal path, so text-only resolution matched nothing and fell through to
     canonical every time.
  3. the shared canonical checkout, which is the right answer when a session is
     genuinely standing in it.

THIS CANNOT BE POINTED SOMEWHERE CONVENIENT. Every candidate — from the text or
from cwd — goes through worktree_root(), which only ever returns a directory
directly under THIS repo's own `.claude/worktrees/`. A session cannot spell its
way into a cleaner tree, and cwd does not come from the command string at all.

WHAT THIS MODULE DOES NOT DECIDE. It answers "which tree", never "allow or
deny". Each gate keeps its own judgement, its own thresholds and its own deny
text; they merely stop disagreeing about the subject of the sentence.

Fixtures: ops/worktree-scope-selftest.py
"""

import os
import re

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# `git -C <path>` and `cd <path>`, in that order of authority: an explicit -C is
# unambiguous, a `cd` is the shell habit. Quoted and bare forms both, because
# both are written here daily.
_DASH_C_RE = re.compile(r"\bgit\s+-C\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))")
_CD_RE = re.compile(r"\bcd\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))")


def worktrees_root(repo=REPO):
    """This repo's own worktrees directory, resolved."""
    return os.path.realpath(os.path.join(repo, ".claude", "worktrees"))


def worktree_root(path, repo=REPO):
    """The registered worktree CONTAINING `path`, or None.

    Returns the worktree's ROOT rather than the deep path handed in, because
    that root is what gets passed to `git -C`, and porcelain output taken from a
    subdirectory is relative to that subdirectory — which would silently produce
    unmatchable path keys in the two staging gates.
    """
    if not path:
        return None
    try:
        p = os.path.realpath(os.path.expanduser(path))
    except Exception:
        return None
    root = worktrees_root(repo)
    if not p.startswith(root + os.sep):
        return None
    name = os.path.relpath(p, root).split(os.sep)[0]
    if name in ("", ".", ".."):
        return None
    wt = os.path.join(root, name)
    return wt if os.path.isdir(wt) else None


def named_dirs(cmd, cwd=None, repo=REPO):
    """Existing directories the command TEXT names, via `git -C` or `cd`, in order.

    A RELATIVE PATH IS THE NATURAL FORM AND WAS BEING MISSED. Commands here
    routinely read `cd ~/carr-system && cd .claude/worktrees/x`, and resolving
    that against the HOOK's own cwd rather than a meaningful root made the
    exemption apply to absolute paths only — so the first real use of it after it
    shipped was refused. A relative path now resolves against the session's cwd
    first, which is what the shell itself would do, and only then against the
    repo root, which is how these were written by hand for a year.
    """
    if not cmd:
        return []
    out = []
    for rx in (_DASH_C_RE, _CD_RE):
        for m in rx.finditer(cmd):
            raw = next((g for g in m.groups() if g), None)
            if not raw:
                continue
            raw = os.path.expanduser(raw)
            if os.path.isabs(raw):
                candidates = [raw]
            else:
                candidates = ([os.path.join(cwd, raw)] if cwd else []) + \
                             [os.path.join(repo, raw), raw]
            for c in candidates:
                try:
                    cand = os.path.realpath(c)
                except Exception:
                    continue
                if os.path.isdir(cand):
                    out.append(cand)
                    break
    return out


def target_tree(cmd, cwd=None, repo=REPO):
    """The worktree this command is about, or None for the shared checkout.

    None means "the canonical checkout" rather than "unknown": callers pair it
    with `tree or REPO`, and the two staging gates additionally use its
    truthiness to decide whether untracked files count (a fresh worktree always
    carries build output, so counting untracked there makes the gate's own
    recommended remedy unusable from the moment it is created).
    """
    repo_real = os.path.realpath(repo)
    for d in named_dirs(cmd, cwd, repo):
        wt = worktree_root(d, repo)
        if wt:
            return wt
        # A command that names the CANONICAL checkout is aimed at the canonical
        # checkout, whatever directory the session happens to be standing in.
        # `cd ~/carr-system && git add -A` from inside a worktree is exactly the
        # 2026-08-09 sweep, and stopping here is what keeps cwd from excusing it.
        if d == repo_real or d.startswith(repo_real + os.sep):
            return None
    return worktree_root(cwd, repo)


def tree_label(tree):
    """How a deny text or an audit row should name the tree that was judged."""
    return os.path.basename(tree) if tree else "canonical"
