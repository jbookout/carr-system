#!/usr/bin/env python3
"""worktree-self-plumb.py — SessionStart self-plumb for a carr-system
worktree Claude Code (or Codex, or a bare `git worktree add`) created without
ever calling bin/worktree.sh.

THE LIVE FAILURE THIS CLOSES. A session landed in a worktree created by
Claude Code's own native isolation (a bare `git worktree add` under
.claude/worktrees/, outside this repo's `run.sh worktree` command). It found
no mcp-server/node_modules there, could not run its own tooling, and fell
back to running from the shared CANONICAL checkout instead — the exact
multi-writer collision `run.sh worktree` exists to prevent (see that file's
header). bin/worktree.sh's create path already solves this for worktrees IT
creates, by symlinking .venv, out, and mcp-server/node_modules back to the
canonical tree. A worktree created by any other door gets none of that,
because nothing calls the linking step for it.

`bin/worktree.sh --plumb [path]` (added alongside this file) is the same
three links, made callable against a worktree bin/worktree.sh did not
create. THIS hook is what makes that automatic: at SessionStart, if the
session's cwd is inside a non-canonical carr-system worktree that is
missing any of the three links, it calls `--plumb` for that worktree and
says so in the brief.

WHY THIS IS A SEPARATE HOOK, NOT AN EXTENSION OF hooks/session-brief.py.
The task that produced this file named session-brief.py as the natural
extension point, on the reasonable assumption that it is the general
SessionStart entry point every local session runs, and that "cwd is not a
carr-system worktree" would just be the common case it skips. That assumption
does not hold: session-brief.py is wired ONLY into the two VAULT project
settings files (claude-tree/settings/my-drive-root.settings.json and
carr-ai-project.settings.json, deployed by bin/sync-settings.sh to the
"My Drive" and "My Drive/CARR AI" trees). It is never wired into any
carr-system checkout's own settings — this repo's tracked .claude/settings.json
carries no SessionStart hook at all today, and neither ~/.claude/settings.json
(user-level; SessionStart there is gate-integrity.py only) reaches it for a
session rooted in a carr-system worktree specifically. Concretely: 100% of
session-brief.py's actual invocations are vault-rooted sessions, 0% are
carr-system-worktree sessions — extending it would be correct code that
never runs for the failure this file exists to fix.

The fix that actually reaches the failure has to live somewhere every
carr-system worktree carries BY CONSTRUCTION, regardless of which door
created it — and that is exactly what a file tracked in the repo gives for
free: `git worktree add` (by us, by Claude Code's native isolation, by Codex,
by hand) always checks out HEAD, so this repo's own .claude/settings.json —
where this hook is registered as SessionStart, added in the same commit —
ships into every worktree automatically. No per-worktree deployment step, no
"most doors don't know to run it" gap. That is the same reasoning
bin/worktree.sh's own header gives for CREATE PATH FRESHNESS and --sweep:
the fix has to live on a path every case actually walks, not a path only the
already-correct case walks.

hooks/*.py are covered by the gate-integrity.py baseline (rule: a session
that adds or edits a hook re-blesses the baseline in the same commit) — this
file was blessed alongside its addition; see ops/config/gate-baseline.json.

FAIL-SOFT, ALWAYS. A boot-time convenience must never fail or block a
session: any error anywhere in here is swallowed and the hook prints
nothing, same discipline as hooks/session-brief.py's own nightly/loose-work
lines.
"""
import json
import os
import subprocess
import sys

# __file__ resolves through the absolute canonical path this hook is always
# invoked by ("${HOME}/carr-system/hooks/worktree-self-plumb.py" in
# .claude/settings.json — the same "always call the canonical copy"
# convention hooks/delegation-gate.py already uses), so REPO is the
# canonical tree regardless of which worktree's cwd triggered this hook.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Must match the three names bin/worktree.sh links at create time and
# re-links under --plumb. Kept here only to decide WHETHER to bother calling
# --plumb (and what to say if we do) — the actual linking decision (what
# counts as already-plumbed, the tracked-real-dir guard) stays solely in
# bin/worktree.sh's link(), never duplicated here (rule a8c55a47).
PLUMB_LINKS = (".venv", "out", os.path.join("mcp-server", "node_modules"))


def resolve_cwd(payload):
    return (payload.get("cwd") or payload.get("working_directory")
            or payload.get("workingDirectory") or os.getcwd())


def run_git(args, cwd):
    try:
        p = subprocess.run(["git", *args], cwd=cwd,
                            capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    try:
        cwd = resolve_cwd(payload)
        if not cwd or not os.path.isdir(cwd):
            return 0

        toplevel = run_git(["rev-parse", "--show-toplevel"], cwd)
        if not toplevel:
            return 0  # not inside any git working tree — most sessions (the vault) end here

        toplevel = os.path.realpath(toplevel)
        canon = os.path.realpath(REPO)
        if toplevel == canon:
            return 0  # the canonical tree itself — nothing to plumb

        # If this hook fired at all, cwd is under a worktree that carries
        # this repo's tracked .claude/settings.json (that is the only way
        # Claude Code would have run it) — so toplevel is necessarily a
        # carr-system checkout. Whether it is a REGISTERED worktree of THIS
        # canonical tree is still --plumb's own guard to enforce; if it
        # refuses, this hook just prints nothing (see except below).
        missing = [name for name in PLUMB_LINKS
                   if not os.path.islink(os.path.join(toplevel, name))
                   and os.path.isdir(os.path.join(canon, name))]
        if not missing:
            return 0  # already fully plumbed — silent, same as most sessions

        plumb = subprocess.run(
            ["zsh", os.path.join(canon, "bin", "worktree.sh"), "--plumb", toplevel],
            cwd=canon, capture_output=True, text=True, timeout=20)
        if plumb.returncode == 0:
            applied = [ln.strip() for ln in plumb.stdout.splitlines() if ln.strip()]
            print(
                "worktree self-plumb: this session's worktree "
                f"({os.path.basename(toplevel)}) was missing {', '.join(missing)} "
                "— a door other than run.sh worktree created it. Applied: "
                + "; ".join(applied)
            )
        # A non-zero exit (not a registered worktree, or some other refusal)
        # is deliberately silent here — the boot hook proposes the fix, it
        # does not surface --plumb's own refusal reasoning at every session
        # start; run `./run.sh worktree --plumb` by hand to see it.
    except Exception:
        pass  # fail-soft: this must never block or fail a session
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
