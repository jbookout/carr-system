#!/usr/bin/env python3
"""canonical-edit-gate.py — PreToolUse DENY on editing a TRACKED file in the
SHARED canonical checkout, before the dirty state ever lands.

WHY THIS EXISTS. Worktree-per-session has been this repo's active taught rule
since git-writer-gate.py was built: a session takes an isolated worktree
BEFORE its first write to tracked files in ~/carr-system, and the canonical
checkout is the integration lane only, never a scratchpad. That rule is
enforced at THREE later moments — commit (ops/githooks/pre-commit refuses a
commit on `main`), push (the server ruleset is PR-only) and branch-switch
(hooks/git-writer-gate.py) — and at NONE of the moment that actually creates
the problem: nothing stopped an Edit or Write from landing on a tracked
canonical file in the first place. By the time pre-commit fires, the dirty
state already exists, another session may already be reading a half-written
file, and "just use a worktree" is advice about the past instead of a rail on
the present.

Joe, 2026-08-14, naming exactly this gap: "a rule should be impossible to
overlook." This is the edit-time rail — the same rule the other three gates
already enforce, moved to the moment it actually bites.

WHAT IT DENIES: an Edit / Write / MultiEdit / NotebookEdit whose target is a
file GIT ALREADY TRACKS, inside THIS canonical checkout — never a worktree,
never a file outside the repo. `git ls-files` is asked directly rather than
inferred, the same way git-writer-gate.py asks `git status` directly rather
than guessing dirtiness from the command text.

WHAT IT ALLOWS, and why each one is not the hazard this rule exists for:
  - anything inside a REGISTERED WORKTREE (.claude/worktrees/<name>/...) — the
    whole point of the remedy this gate points people toward. Resolved the
    same way git-writer-gate.py's target_tree() resolves it: bounded strictly
    to this repo's own worktrees directory, so nothing else can claim the
    exemption, and unconditional once there — a session editing tracked files
    in its OWN tree is exactly the intended shape of the rule, not a hole in it.
  - a brand-new, UNTRACKED file anywhere in the canonical checkout — out/,
    .claude/ scratch, a log, or a file a session is about to `git add` for the
    first time. THIS IS A DELIBERATE CHOICE, not an oversight, made the same
    way git-writer-gate.py already draws it: that gate's dirty_files() uses
    tracked_only=True specifically when judging a WORKTREE, because a fresh
    worktree's own untracked build output (mcp-server/node_modules, and so on)
    must stay writable or its own remedy becomes unusable the moment it is
    created. The object BOTH gates protect is EXISTING SHARED STATE — content
    another session could be mid-editing, or that a destructive git command
    could lose. A file that does not exist in git's history yet has neither
    property: nothing is "in flight" at a path nothing has ever occupied. So
    this gate applies the identical test to the canonical tree: DENY tracks
    what already exists and is shared; untracked stays open, everywhere.
  - anything outside this repo entirely.

REGISTRATION MATCHES THE REPO'S OWN CONVENTION, not a new one: it joins the
existing "Write|Edit|MultiEdit" PreToolUse group in ops/config/hooks.json
alongside record-home-gate.py and gate-edit-gate.py, rather than opening a
fresh matcher for the same three tools. The tool check below additionally
recognises NotebookEdit, matching gate-edit-gate.py's and lint-gate.py's own
tool lists exactly (their registered matcher does not name NotebookEdit
either) — a defensive superset already established twice in this codebase,
kept here for the same reason: cheap insurance if the matcher is ever widened,
free today.

BASH IS DELIBERATELY OUT OF SCOPE. git-writer-gate.py's Bash door models one
narrow thing — destructive GIT COMMANDS run through a shell — not arbitrary
file writes; `sed -i`, `> file`, `tee`, and friends against a tracked canonical
path are not covered by anything it does today, and building that general
shell-write detector is a materially larger gate than the one asked for here.
Left alone on purpose, matching the brief.

ESCAPE HATCH, same spelling and discipline as pre-commit's
CARR_ALLOW_MAIN_COMMIT — a single-use env var:

    CARR_ALLOW_CANONICAL_EDIT=1

for the rare deliberate integration edit (reconciling this very checkout is
the legitimate case, the same one pre-commit names for itself). ONE HONEST
DIFFERENCE FROM THE GIT HOOK IT COPIES: pre-commit's variable rides in front of
a single shell command (`CARR_ALLOW_MAIN_COMMIT=1 git commit ...`), because a
git hook inherits that exact command's environment. Edit/Write/NotebookEdit
are not shell commands and have no line to prefix, so this variable has to
already be SET in the process environment this hook inherits before the edit
is attempted — export it for the one edit, then unset it; never carry it in a
profile. The discipline is the same even though the syntax cannot be.
Logged LOUDLY when used: to the shared audit trail and back into the calling
session's own context via systemMessage, so a bypass is visible, not merely
permitted.

THIS IS AN ACCIDENT-STOPPER, NOT A SECURITY CONTROL, stated as plainly as
pre-commit says it about itself. It exists to catch the edit nobody meant to
make in the shared tree, which is the only kind that has actually happened.

FAILS OPEN on any internal error: a wedged session is worse than one more
dirty file in a tree that already has a human to fix it.

Fixtures: ops/canonical-edit-gate-selftest.py
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

# Script-relative, matching every gate in this family (git-writer-gate.py,
# record-home-gate.py) — never expanduser("~/carr-system"), which points at a
# directory that does not exist on a CI runner or a second machine.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WT_ROOT = os.path.realpath(os.path.join(REPO, ".claude", "worktrees"))
LOG = os.path.join(REPO, "out", "conduct-gate.jsonl")
DEBUG = os.path.join(REPO, "out", "conduct-gate.log")

ESCAPE_VAR = "CARR_ALLOW_CANONICAL_EDIT"
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()}  canonical-edit-gate  {msg}\n")
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


def in_worktree(path):
    """True when `path` sits inside THIS repo's own registered worktrees dir.

    Bounded exactly like git-writer-gate.py's target_tree(): only ever a path
    under WT_ROOT, never anywhere convenient a caller might point it, and it
    does not require the path to already exist — a Write creating a brand-new
    file in a worktree is still, obviously, a worktree edit.
    """
    return path == WT_ROOT or path.startswith(WT_ROOT + os.sep)


def is_tracked(rel_path):
    """Does git already track this path in the canonical checkout?

    Asked directly, the same way git-writer-gate.py asks `git status` directly
    rather than inferring dirtiness from a command string. `git ls-files`
    prints the path back when it is tracked and nothing when it is not, so
    this needs no --error-unmatch exit-code handling on either answer.
    """
    try:
        out = subprocess.run(
            ["git", "-C", REPO, "ls-files", "--", rel_path],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return False                                   # fail OPEN, like the rest
    return bool(out.strip())


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        dlog(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if tool not in EDIT_TOOLS:
            sys.exit(0)
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        path = (ti.get("file_path") or ti.get("filePath") or "") if isinstance(ti, dict) else ""
        if not path:
            sys.exit(0)

        ap = os.path.realpath(os.path.expanduser(path))

        if not (ap == REPO or ap.startswith(REPO + os.sep)):
            dlog(f"ALLOW(outside-repo) {tool} {path}")
            sys.exit(0)                                # not our tree at all

        if in_worktree(ap):
            dlog(f"ALLOW(worktree) {tool} {path}")
            sys.exit(0)                                # the remedy itself

        rel = os.path.relpath(ap, REPO)

        if not is_tracked(rel):
            dlog(f"ALLOW(untracked) {tool} {rel}")
            sys.exit(0)                                # nothing shared to clobber yet

        # From here the write is genuinely aimed at a tracked file in the
        # shared integration tree.
        if os.environ.get(ESCAPE_VAR) == "1":
            note = (
                f"CANONICAL EDIT GATE — escape hatch used ({ESCAPE_VAR}=1) for "
                f"'{rel}'. This is a deliberate direct edit to the shared "
                f"integration tree, not a worktree edit. Logged."
            )
            audit({"ts": now(), "hook": "canonical-edit-gate",
                   "classes": ["canonical_tree_edit"],
                   "patterns": ["canonical-edit:escape-hatch"],
                   "session": payload.get("session_id"), "path": rel,
                   "decision": "allow-escape-hatch"})
            dlog(f"ALLOW(escape-hatch) {tool} {rel}")
            print(json.dumps({
                "systemMessage": note,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": note,
                },
            }))
            sys.exit(0)

        reason = (
            "CANONICAL EDIT GATE — refused.\n\n"
            f"'{rel}' is tracked by git and sits in ~/carr-system itself — the "
            "SHARED integration tree, not a session's own copy. Worktree-per-"
            "session is this repo's active rule: a session takes its own "
            "worktree BEFORE its first write to a tracked canonical file, and "
            "this checkout is for merged, reviewed integration only.\n\n"
            "DO THIS INSTEAD, one command:\n"
            "    ./run.sh worktree <name>\n"
            "    cd .claude/worktrees/<name>\n"
            "Then edit, commit, push and open a PR from there.\n\n"
            "This rule already binds at commit (ops/githooks/pre-commit refuses "
            "`main`), at push (the server ruleset is PR-only) and at branch-"
            "switch (git-writer-gate.py) — this is the same rule, caught at the "
            "moment it actually starts: the first edit, before any of that dirty "
            "state exists.\n\n"
            "New, untracked files and anything already inside a worktree are "
            "unaffected. If this is genuinely a deliberate integration edit "
            f"(reconciling this checkout is the real case), set {ESCAPE_VAR}=1 "
            "in the environment for this one edit and retry — never carry it "
            "in a profile.\n"
        )

        audit({"ts": now(), "hook": "canonical-edit-gate",
               "classes": ["canonical_tree_edit"],
               "patterns": ["canonical-edit:tracked-file"],
               "session": payload.get("session_id"), "path": rel})
        dlog(f"DENY {tool} {rel}")
        # Exit 2, not JSON: on a build that does not parse the structured
        # contract, exit 0 reads as ALLOW and the gate fails open silently —
        # same convention as every deny path in this family.
        print(reason, file=sys.stderr)
        sys.exit(2)

    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
