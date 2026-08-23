#!/usr/bin/env python3
"""worktree-scope-selftest.py — fixtures for hooks/worktree_scope.py.

WHAT IS UNDER TEST: the single question three gates ask — given a Bash command
and the directory the session is standing in, WHICH TREE is this about? Getting
that wrong is not a cosmetic defect. It is what refused pushes from four
unrelated branches on 2026-08-21, and what refused a `reset --hard` in a
perfectly clean worktree on 2026-08-22 while listing another session's files.

WHY THIS RUNS AGAINST A THROWAWAY DIRECTORY AND NEVER TOUCHES A REAL TREE. The
property being proved is that a session standing in a worktree is judged against
THAT worktree, which needs a worktree that is reliably present. Manufacturing one
means writing into `.claude/worktrees/` of a live checkout — precisely what
council recommendation 1 forbids a test from doing, and the same class as the
2026-08-14 selftest that committed fixtures onto live main.

It does not have to. target_tree() is pure path arithmetic: no git process, no
repository state, no index. A tmpdir shaped like a checkout proves exactly the
same thing at none of the risk, and this file runs `git` zero times — which is
the strongest available form of "this test cannot commit to live main".

    .venv/bin/python ops/worktree-scope-selftest.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
from worktree_scope import target_tree, tree_label, worktree_root  # noqa: E402

failures = []


def main():
    with tempfile.TemporaryDirectory(prefix="worktree-scope-") as tmp:
        repo = os.path.realpath(tmp)
        wt_root = os.path.join(repo, ".claude", "worktrees")
        alpha = os.path.join(wt_root, "alpha")
        beta = os.path.join(wt_root, "beta")
        for d in (alpha, beta, os.path.join(repo, "migrations"),
                  os.path.join(alpha, "ops")):
            os.makedirs(d, exist_ok=True)

        def case(name, cmd, cwd, want):
            got = target_tree(cmd, cwd, repo=repo)
            ok = got == want
            print(f"  {'ok  ' if ok else 'FAIL'} {name:26}"
                  + ("" if ok else f"  got={got}  want={want}"))
            if not ok:
                failures.append(name)

        # ── the invoking worktree is the subject ──────────────────────────────
        # THE 2026-08-22 BUG, stated as a test. `cd "$(pwd)"` names no literal
        # path, so the command text yields nothing and the session's own cwd is
        # the only honest evidence about which tree this touches.
        case("pwd-indirection", 'cd "$(pwd)" && git reset --hard origin/main',
             alpha, alpha)
        # The plainest form of the same thing: no `cd` at all, which is what a
        # session already standing in its worktree actually types.
        case("bare-cmd-in-worktree", "git add -A", alpha, alpha)
        # A SUBDIRECTORY of a worktree resolves to the worktree ROOT — the path
        # that gets handed to `git -C`. Porcelain output taken from a subdir is
        # relative to that subdir, which would produce unmatchable path keys in
        # the two staging gates.
        case("worktree-subdir", "git clean -fd", os.path.join(alpha, "ops"), alpha)
        # A relative `cd` resolves against the session's cwd first, the way the
        # shell itself would.
        case("relative-cd-from-worktree", "cd ops && git add .", alpha, alpha)

        # ── but cwd never excuses a command that names another tree ───────────
        # `cd ~/carr-system && git add -A` from inside a worktree is exactly the
        # 2026-08-09 sweep this gate family exists for. Text beats cwd.
        case("cd-canonical-wins", f"cd {repo} && git add -A", alpha, None)
        # ...and a DIFFERENT worktree is judged as that worktree: it is the tree
        # actually at risk.
        case("cd-other-worktree", f"cd {beta} && git clean -fd", alpha, beta)
        case("dash-C-other-worktree", f"git -C {beta} reset --hard", alpha, beta)
        # The relative form that predates cwd resolution and must keep working.
        case("relative-cd-from-repo", "cd .claude/worktrees/beta && git add -A",
             repo, beta)

        # ── nothing can be spelled into a cleaner tree ────────────────────────
        case("cwd-canonical", "git add -A", repo, None)
        case("no-cwd", "git add -A", None, None)
        case("cwd-outside-repo", "git add -A", "/tmp", None)
        case("cwd-missing-worktree", "git add -A",
             os.path.join(wt_root, "gone"), None)
        case("cwd-is-worktrees-root", "git add -A", wt_root, None)
        # Path traversal out of the worktrees root does not survive realpath.
        case("traversal", "git add -A", os.path.join(alpha, "..", ".."), None)
        # A command naming a directory that does not exist proves nothing, so it
        # must not silently become an exemption.
        case("cd-nonexistent", f"cd {os.path.join(wt_root, 'nope')} && git add -A",
             None, None)

        # ── the label callers put in deny text and audit rows ─────────────────
        for name, tree, want in (("label-worktree", alpha, "alpha"),
                                 ("label-canonical", None, "canonical")):
            got = tree_label(tree)
            ok = got == want
            print(f"  {'ok  ' if ok else 'FAIL'} {name:26}"
                  + ("" if ok else f"  got={got!r}  want={want!r}"))
            if not ok:
                failures.append(name)

        # A worktree root is its own root, not None — the two staging gates call
        # worktree_root() directly on cwd and would otherwise fall to canonical.
        got = worktree_root(os.path.join(alpha, "ops"), repo=repo)
        ok = got == alpha
        print(f"  {'ok  ' if ok else 'FAIL'} {'worktree-root-of-subdir':26}"
              + ("" if ok else f"  got={got}  want={alpha}"))
        if not ok:
            failures.append("worktree-root-of-subdir")

    print()
    if failures:
        print("worktree-scope-selftest FAILURES: " + ", ".join(failures))
        return 1
    print("worktree-scope-selftest: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
