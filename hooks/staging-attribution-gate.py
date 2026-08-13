#!/usr/bin/env python3
"""staging-attribution-gate.py — PreToolUse gate: name every path you commit.

WHY, IN TWO INCIDENTS THE SAME DAY (2026-08-13), NEITHER CAUGHT BY A HUMAN. A
broad-staging session swept three foreign paths into its own commit. Later the
same day, a broad `git add` absorbed an approved export-target retirement into
a commit whose message describes only an unrelated change — so the retirement
now has no commit that names it, and reverting that commit would silently
un-retire two export targets. The rule forbidding wholesale staging is recited
at every session start (rule 308ef1de, CLAUDE.md, this hook's own sibling
hooks/git-writer-gate.py) and did not hold. Prose does not bind. Decision
9e1f83c2: make it mechanical.

git-writer-gate.py already exists and already refuses `git add -A` / `commit
-a` / `git add .` — but ONLY when the shared tree happens to be dirty at that
moment, because its job is "don't sweep a CONCURRENT writer's in-flight work."
This gate is narrower in scope and stricter in trigger: it refuses the
wholesale FORMS unconditionally (decision 9e1f83c2 calls for the rule to bind
regardless of tree state — a session should never reach for -A/-a/. at all,
dirty tree or not), and it adds a capability git-writer-gate.py does not have:
per-PATH attribution, so a session naming specific paths can still be refused
if one of those paths belongs to someone else. The two hooks are complementary,
not redundant; both are registered.

WHAT IT REFUSES OUTRIGHT, NO OVERRIDE, REGARDLESS OF TREE STATE:
  git add -A / --all / -a          git commit -a / -am / --all
  git add .                        any combined short flag carrying the above
There is no legitimate reason to reach for these — the fix is always to name
the paths you actually wrote. -u/--update is treated the same way: it stages
every tracked modification tree-wide, which is the same hazard by a different
flag.

WHAT IT REFUSES UNLESS OVERRIDDEN: `git add <path...>` naming one or more
specific paths, where a named path is a TRACKED file carrying an uncommitted
modification (not a brand-new untracked file — see the attribution section
below for why that distinction exists) that this session did not itself write.

THE ATTRIBUTION MECHANISM, AND ITS HONEST LIMIT. "This session modified it" is
answered from the session's own transcript (payload.transcript_path), which the
harness hands every PreToolUse call. Every Write/Edit/MultiEdit tool_use record
in that transcript names the file it touched (input.file_path) — this is ground
truth about what the session itself did, not a guess, and it costs no state
file, no locking, and no second hook to keep in sync with this one.

WHAT THIS CATCHES: a session staging a tracked, modified path that neither
appears in its own transcript's Write/Edit/MultiEdit history NOR is a brand-new
untracked file. That is exactly the incident shape — an existing file another
session is mid-editing, swept into a stranger's commit.

WHAT THIS MISSES, STATED PLAINLY: a session that modifies a tracked file via
Bash (a redirect, sed -i, a script that writes output) rather than the
Write/Edit/MultiEdit tools leaves no transcript trace this gate can see, so
staging that file reads as "not written by this session" even though it was.
To keep that from becoming a false refusal — a gate that cries wolf gets
worked around — brand-new UNTRACKED paths ("??" in porcelain) are allowed
through unconditionally: creating a new file is the common case for a Bash
based write, and the specific harm this decision targets (another session's
uncommitted EDIT to an EXISTING file, mixed into a commit that does not
describe it) does not apply to a file with no prior committed content to
misattribute. The tradeoff is deliberate and asymmetric: it will occasionally
let a foreign new file through, and it will very rarely refuse this session's
own Bash-authored edit to an existing tracked file — bias toward the latter
being rare and toward never blocking a session's own legitimate work over
catching every possible foreign path.

THE OVERRIDE, mirroring the break-glass envelope in tools/db-tap.py (env flag
+ required reason). Because a PreToolUse hook only ever sees the COMMAND TEXT
about to run, not the eventual subprocess's real environment, the flag and the
reason are given as inline env-var assignments on the same Bash invocation —
ordinary, harmless shell syntax that git itself ignores:

    CARR_ADOPT_ORPHAN=1 CARR_ADOPT_REASON="Dell's session vanished mid-build; \\
    adopting his half-finished harden-gates.sh so the work is not lost" \\
    git add ops/harden-gates.sh && git commit -m "..."

Both the flag and a non-empty reason are required together. A valid override
allows the staging (with an additionalContext confirmation so it is visible in
the transcript, never silent) and appends one receipt line to the SAME
out/break-glass-receipts.log tools/db-tap.py and tools/call-verb.py already
write — one audit trail for every break-glass act in the repo, not a second
file that could drift in format (rule a8c55a47). local_actor_slug() and
append_receipt() are loaded from tools/db-tap.py by path (it is un-importable
by name, same as tools/call-verb.py already does) so the identity/receipt logic
has exactly one home. The wholesale forms above have NO override: there is no
legitimate reason to need one, so none is offered.

FAILS SAFE, LIKE EVERY GATE HERE: any parse error, missing transcript, or
non-git / non-Bash command is ALLOWED, never blocked. This must not become a
general Bash blocker.

Fixtures: ops/staging-attribution-gate-selftest.py
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "out", "staging-attribution-gate.jsonl")
DEBUG = os.path.join(REPO, "out", "hook-guard.log")

# Ordered: nothing here needs precedence over anything else, both are checked.
COMMIT_WHOLESALE = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?commit\b[^\n|;&]*(?:\s-\w*a\w*\b|--all\b)", re.I
)
ADD_WHOLESALE = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?add\s+"
    r"(?:-{1,2}[aAuU]\w*\b|--all\b|--update\b|\.(?=\s|$|/))",
    re.I,
)
# Captures the argument text of one `git add ...` invocation, stopping at a
# shell separator so a chained `git add x && git commit -m y` does not slurp
# the commit half into the path list.
ADD_INVOCATION = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?add\s+([^\n]*?)(?=&&|;|\||\n|$)", re.I
)

OVERRIDE_FLAG = re.compile(r"\bCARR_ADOPT_ORPHAN=1\b")
OVERRIDE_REASON = re.compile(
    r'\bCARR_ADOPT_REASON=(?:"([^"]+)"|\'([^\']+)\'|(\S+))'
)

MAX_TRANSCRIPT_LINES = 100_000  # defensive cap; fails safe (allow) past this


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()} staging-attribution-gate {msg}\n")
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


def _db_tap():
    """Dynamic load by path (hyphenated filename, un-importable by name) — the
    exact pattern tools/call-verb.py already uses to reuse local_actor_slug()
    and append_receipt() rather than re-implementing the break-glass receipt
    format a third time (rule a8c55a47)."""
    import importlib.util

    path = os.path.join(REPO, "tools", "db-tap.py")
    spec = importlib.util.spec_from_file_location("db_tap_for_staging_gate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def porcelain_status():
    """{repo-relative-posix-path: 2-char XY status} for the whole tree."""
    try:
        out = subprocess.run(
            ["git", "-C", REPO, "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception:
        return {}
    status = {}
    for line in out.splitlines():
        if len(line) <= 3:
            continue
        code = line[:2]
        rest = line[3:].strip()
        # A rename/copy line is "old -> new"; the path that matters for a
        # staging decision is the new one.
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        status[rest.strip('"')] = code
    return status


def repo_relative(path, cwd):
    """Best-effort repo-relative POSIX path for an argument as typed."""
    if not path:
        return None
    p = path
    if not os.path.isabs(p):
        base = cwd if cwd and os.path.isabs(cwd) else REPO
        p = os.path.join(base, p)
    try:
        rel = os.path.relpath(p, REPO)
    except ValueError:
        return None  # different drive/mount root; cannot be a repo path
    if rel.startswith(".."):
        return None  # outside the repo entirely
    return rel.replace(os.sep, "/")


def own_written_paths(transcript_path):
    """Repo-relative paths this session itself wrote, per its OWN transcript.

    Ground truth, not inference: every Write/Edit/MultiEdit tool_use record in
    this session's transcript names the file it touched. Scans the whole file
    because an early-session edit is exactly as valid an attribution as a late
    one; capped defensively so a pathological transcript cannot hang the gate.
    """
    written = set()
    if not transcript_path or not os.path.exists(transcript_path):
        return written
    try:
        with open(transcript_path, "r", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= MAX_TRANSCRIPT_LINES:
                    break
                line = line.strip()
                if not line or '"tool_use"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                msg = rec.get("message") if isinstance(rec, dict) else None
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") not in ("Write", "Edit", "MultiEdit"):
                        continue
                    inp = block.get("input") or {}
                    fp = inp.get("file_path") if isinstance(inp, dict) else None
                    if isinstance(fp, str) and fp:
                        rel = repo_relative(fp, None)
                        if rel:
                            written.add(rel)
                        written.add(fp)  # keep the absolute form too
    except Exception as exc:
        dlog(f"transcript scan error (allowing): {exc}")
    return written


def extract_add_paths(command, cwd):
    """[(as-typed, repo-relative-or-None), ...] for every `git add` invocation
    in the command that is NOT one of the wholesale forms (already filtered by
    the caller)."""
    out = []
    for m in ADD_INVOCATION.finditer(command):
        args_text = m.group(1).strip()
        if not args_text:
            continue
        try:
            tokens = shlex.split(args_text)
        except ValueError:
            continue  # unbalanced quotes etc — let the caller fail safe
        for tok in tokens:
            if tok.startswith("-"):
                continue
            out.append((tok, repo_relative(tok, cwd)))
    return out


def deny(reason):
    dlog(f"DENY :: {reason.splitlines()[0][:160]}")
    print(reason, file=sys.stderr)
    sys.exit(2)


def allow_with_context(context):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": context,
        }
    }))
    sys.exit(0)


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
        if not isinstance(cmd, str) or "git" not in cmd:
            sys.exit(0)

        session_id = payload.get("session_id") or payload.get("sessionId")
        cwd = payload.get("cwd")

        # --- 1. Wholesale forms: refused outright, no override, regardless of
        #        tree state. ---
        wholesale_hit = None
        if COMMIT_WHOLESALE.search(cmd):
            wholesale_hit = "commit_all"
        elif ADD_WHOLESALE.search(cmd):
            wholesale_hit = "add_all"

        if wholesale_hit:
            reason = (
                f"STAGING ATTRIBUTION GATE — refused `{wholesale_hit}`.\n\n"
                "Joe's rule, plain: name every path you commit. `git add -A`, "
                "`git add .`, `git add --all`, `git add -u/--update`, and "
                "`git commit -a`/`-am`/`--all` are refused OUTRIGHT — every "
                "time, whether or not the tree looks dirty right now — because "
                "each one stages whatever anyone else happens to have "
                "in-flight in this shared tree along with your own work. This "
                "already happened twice on 2026-08-13: once three foreign "
                "paths were swept into a commit, and once an approved "
                "export-target retirement got absorbed into a commit whose "
                "message describes only an unrelated change, so the retirement "
                "now has no commit that names it (decision 9e1f83c2).\n\n"
                "THE CORRECT FORM:\n"
                "  git add <path you personally wrote> [<another path>...]\n"
                "  git commit -m \"message describing exactly those paths\"\n\n"
                "There is no override for this refusal. If you are not sure "
                "which paths are yours, run `git status` and name them.\n"
            )
            audit({"ts": now(), "hook": "staging-attribution-gate",
                   "classes": ["wholesale_stage"], "patterns": [wholesale_hit],
                   "session": session_id, "excerpt": cmd[:300]})
            deny(reason)

        # --- 2. Named-path staging: check each named path for foreign,
        #        uncommitted, tracked modifications this session did not
        #        itself make. ---
        add_paths = extract_add_paths(cmd, cwd)
        if not add_paths:
            sys.exit(0)

        status = porcelain_status()
        if not status:
            sys.exit(0)  # clean tree (or git unreachable) — nothing to catch

        written = own_written_paths(payload.get("transcript_path"))

        foreign = []
        for as_typed, rel in add_paths:
            if not rel:
                continue  # outside the repo or unresolvable — not this gate's job
            code = status.get(rel)
            if not code:
                continue  # nothing uncommitted at this path — staging it is a no-op
            if code == "??":
                continue  # brand-new untracked file — see docstring for why
            if rel in written or as_typed in written:
                continue  # this session's own Write/Edit/MultiEdit, ground truth
            foreign.append((as_typed, rel, code))

        if not foreign:
            sys.exit(0)

        override_ok = OVERRIDE_FLAG.search(cmd)
        reason_match = OVERRIDE_REASON.search(cmd)
        override_reason = None
        if reason_match:
            override_reason = next(
                (g for g in reason_match.groups() if g), None
            )

        foreign_list = "\n".join(f"    {p} (git status: {c})" for _, p, c in foreign)

        if override_ok and override_reason and override_reason.strip():
            db_tap = None
            actor = "identity-not-set"
            try:
                db_tap = _db_tap()
                actor = db_tap.local_actor_slug()
                db_tap.append_receipt(
                    actor, "staging-adopt-orphan",
                    ", ".join(p for _, p, _ in foreign),
                    "local-tree", override_reason,
                )
            except Exception as exc:
                dlog(f"receipt append failed (allowing anyway): {exc}")
            audit({"ts": now(), "hook": "staging-attribution-gate",
                   "classes": ["orphan_adopt_override"],
                   "patterns": ["adopt:" + p for _, p, _ in foreign],
                   "session": session_id, "actor": actor,
                   "reason": override_reason, "excerpt": cmd[:300]})
            allow_with_context(
                "STAGING ATTRIBUTION GATE — orphan-adoption override engaged.\n"
                f"Adopting {len(foreign)} path(s) not attributed to this "
                f"session's own edits:\n{foreign_list}\n"
                f"Reason given: {override_reason.strip()}\n"
                "Receipted to out/break-glass-receipts.log — this is visible "
                "afterward, not silent."
            )

        own_list = ", ".join(sorted(written)[:8]) or "(none this session has written)"
        reason = (
            "STAGING ATTRIBUTION GATE — refused: path(s) not written by this "
            "session.\n\n"
            f"{foreign_list}\n\n"
            "Each has an uncommitted, tracked modification and none of them "
            "appear in this session's own Write/Edit/MultiEdit history "
            f"(this session wrote: {own_list}). ~/carr-system runs several "
            "concurrent Claude sessions against one shared working tree — "
            "this is very likely another session's in-flight work.\n\n"
            "DO THIS INSTEAD:\n"
            "  - stage only the path(s) you personally wrote\n"
            "  - if a listed path really is yours (edited via Bash rather "
            "than the Write/Edit tool, so this gate could not see it), say so "
            "and stage it by name\n"
            "  - if you are genuinely ADOPTING an orphaned file because its "
            "writer disappeared, use the named override:\n\n"
            "    CARR_ADOPT_ORPHAN=1 CARR_ADOPT_REASON=\"why you're adopting "
            "it\" git add <path> && git commit -m \"...\"\n\n"
            "Both the flag and a non-empty reason are required together, and "
            "the adoption is receipted to out/break-glass-receipts.log so it "
            "is visible afterward.\n"
        )
        audit({"ts": now(), "hook": "staging-attribution-gate",
               "classes": ["foreign_path_stage"],
               "patterns": [f"foreign:{p}" for _, p, _ in foreign],
               "session": session_id, "excerpt": cmd[:300]})
        deny(reason)

    except SystemExit:
        raise
    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
