#!/usr/bin/env python3
"""one-repo-gate.py — the PreToolUse DENY gate on code written outside the repo.

WHY THIS EXISTS. CARR's CLAUDE.md rev 10: "the code lives in ONE repo:
jbookout/carr-system (~/carr-system locally) ... If a session cannot reach that
repo it must STOP and say so, not improvise a home." That rule is prose, so it
binds only sessions that read it and obey it — which is precisely how it failed.
A cloud session that could not see carr-system filed an entire system audit plus
two specs into an empty scaffold repo and hand-wrote a DECISIONS.md duplicating
what the add-loop verb already does. Nothing refused it. It was found by a human.

THE MECHANISM THE ORIGINATING LOOP NAMED DOES NOT WORK, recorded here so nobody
re-proposes it. DirectoryAdded is a real event and fires on /add-dir and on the
SDK's register_repo_root, but it is POST-ACTION: the directory is already
registered when the hook runs, and exit 2 — like every other non-zero code and
stderr with it — goes to the debug log only. A hook there is an audit line, not
a control. Verified against the published hooks reference, not assumed.

WHAT ACTUALLY ENFORCES IT is the door the failure came through. The shape is not
"a directory got registered", it is "engineering work got WRITTEN somewhere that
is not the repo" — a PreToolUse Write gate, the same door record-home-gate.py and
gate-edit-gate.py already sit on, which does deny.

WHAT IT DENIES, and every test is PATH-STRUCTURAL rather than content-sniffing,
for the reason lint-gate.py's doctrine gives and the originating loop repeats:
under-parsing leaves the hole, over-parsing blocks legitimate work and breeds the
habit of switching the gate off.

  CREATING a file whose extension is code (.py .js .mjs .ts .sql .sh) inside a
  git working tree that is not carr-system.

WHAT IT DELIBERATELY ALLOWS, each for a reason that cost something to learn:

  A. WORKTREES. `run.sh worktree` is the house default for any session that will
     commit (rule 4a53ff82), and a worktree's .git is a FILE pointing back at the
     canonical repo. A prefix comparison would deny every worktree session's
     first Python file, and a gate that fires on the normal path is one that gets
     turned off the same day. Resolved through the git COMMON dir, so a worktree
     anywhere on disk is recognised as the repo it belongs to.
  B. EDITING SOMETHING THAT ALREADY EXISTS. The failure shape is new work landing
     in a foreign home. Patching a file already present in another checkout is
     ordinary, and denying it is the over-parsing above.
  C. ANYTHING OUTSIDE A GIT TREE. Scratchpads, /tmp and the vault are owned by
     other controls or by nothing, and this gate has no business there. The vault
     specifically belongs to record-home-gate.py.
  D. EVERY NON-CODE EXTENSION. Markdown has its own gate and its own rules.

KNOWN LIMIT, stated rather than discovered later: this covers Write, Edit and
MultiEdit. A `python3 -c` heredoc from Bash writes the same file and is not seen
here — the identical tool-shaped hole loop #287 documents in record-home-gate.py,
whose fix is a real design question about finding write targets inside arbitrary
shell. This gate is not complete coverage and must not be described as such.

FAILS CLOSED ON DENY, OPEN ON ERROR. Exit 2 plus stderr is the deny path, the
same contract guard-unattended.py and record-home-gate.py use: the structured
JSON contract needs exit 0, so on any build that does not parse it an exit 0
reads as ALLOW and the gate would fail open silently. Any INTERNAL error allows
the call, because a gate that wedges a session costs more than the marginal
safety of failing closed on a single-operator machine.
"""

import json
import os
import sys

# Script-relative, never a ~/carr-system literal — the same fix record-home-gate.py
# carries. On a CI runner the checkout is /home/runner/work/carr-system/carr-system,
# outside $HOME, and a literal would silently name a path that does not exist,
# which here would mean the repo never matches itself and the gate denies its own
# tree. CARR_ONE_REPO_ROOT overrides it for the selftest's fixtures.
REPO = os.environ.get("CARR_ONE_REPO_ROOT") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), ".."))

CODE_EXTENSIONS = (".py", ".js", ".mjs", ".ts", ".sql", ".sh")
LOG = os.path.join(REPO, "out", "hook-guard.log")

DENY = (
    "BLOCKED by the CARR one-repo gate: {path}\n"
    "That is a NEW {ext} file inside {tree}, which is a git working tree and is "
    "not carr-system. The code lives in ONE repo — jbookout/carr-system, "
    "~/carr-system locally — and nowhere else.\n"
    "If you cannot reach that repo, STOP and say so rather than improvising a "
    "home: work filed into another repo is not preserved, it is stranded "
    "somewhere nobody reads. That is not hypothetical — an entire system audit "
    "and two specs were lost that way once already.\n"
    "Editing a file that already exists here is allowed; creating new code is not."
)


def log(line):
    """Best effort. A gate that cannot write its log still has to gate."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"one-repo-gate {line}\n")
    except Exception:
        pass


def git_common_dir(start):
    """The canonical .git directory for whatever working tree contains `start`,
    or None when nothing above it is a git tree.

    Walks up rather than shelling out to git, so the gate stays fast enough for
    a PreToolUse hook and cannot be confused by inherited GIT_DIR — which is set
    in every git hook's environment and would otherwise make this answer depend
    on who invoked the session rather than on where the file is going.

    A worktree's .git is a FILE reading `gitdir: /path/to/repo/.git/worktrees/x`.
    Trimming at /worktrees/ yields the shared .git, which is what makes a
    worktree resolve to the repo it belongs to instead of to itself.
    """
    current = os.path.realpath(start)
    while True:
        candidate = os.path.join(current, ".git")
        if os.path.isdir(candidate):
            return os.path.realpath(candidate)
        if os.path.isfile(candidate):
            try:
                with open(candidate) as fh:
                    text = fh.read().strip()
            except OSError:
                return None
            if text.startswith("gitdir:"):
                gitdir = text.split(":", 1)[1].strip()
                if not os.path.isabs(gitdir):
                    gitdir = os.path.join(current, gitdir)
                marker = os.sep + "worktrees" + os.sep
                if marker in gitdir:
                    gitdir = gitdir.split(marker)[0]
                return os.path.realpath(gitdir)
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def check(tool_input, cwd):
    """The refusal reason, or None to allow."""
    raw = tool_input.get("file_path") or tool_input.get("filePath") or ""
    if not raw:
        return None
    path = os.path.expanduser(raw)
    if not os.path.isabs(path):
        path = os.path.join(cwd or os.getcwd(), path)
    path = os.path.abspath(path)

    extension = os.path.splitext(path)[1].lower()
    if extension not in CODE_EXTENSIONS:
        return None
    if os.path.exists(path):
        return None                      # editing what exists is allowed (B)

    tree = git_common_dir(os.path.dirname(path))
    if tree is None:
        return None                      # not a git tree at all (C)
    home = git_common_dir(REPO) or os.path.realpath(os.path.join(REPO, ".git"))
    if tree == home:
        return None                      # the repo, worktrees included (A)

    return DENY.format(path=path, ext=extension,
                       tree=os.path.dirname(tree) or tree)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:                                   # fail OPEN
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)
    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
        if tool not in ("Write", "Edit", "MultiEdit") or not isinstance(tool_input, dict):
            sys.exit(0)
        reason = check(tool_input, payload.get("cwd"))
        if reason:
            log(f"DENY {tool} :: {reason.splitlines()[0][:200]}")
            print(reason, file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    except Exception as exc:                                   # fail OPEN
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
