"""verb_gate_recheck.py — re-run the four client verb gates against a call
that reached the same verb through a door their own hook matcher cannot see.

WHY THIS EXISTS (bypass audit C33, 2026-09-24). escalation-gate.py,
blocker-decider-gate.py, drift-claim-gate.py, and rule-shape-gate.py are each
registered in ~/.claude/settings.json on the matcher
`mcp__.*__(record-defect|add-loop)` or `mcp__.*__(teach|activate-rule)`. That
matcher is a literal tool_name match, so a session that reaches the same verb
through `./run.sh call <verb> '<json>'` (CLAUDE.md's own documented fallback
door for a verb the classifier denies) was never seen by any of the four --
same verb, same JSON, zero checks.

WHAT THIS FILE DOES AND WHERE IT IS CALLED FROM. It is a plain library (no
shebang, no main guard -- see ops/typesafe_client.py's header for why that
matters here: either one would register this as a sealed script entrypoint
over one function that only ever runs inside another hook's process). It is
imported from hooks/guard-unattended.py, which is ALREADY registered on the
Bash matcher and already an enforcing gate (it denies today, unlike the
"never blocks" rail at rule-pack-preuse-reselection.py) -- so closing this
door costs no new hook registration and no new PreToolUse entry.

HOW IT CLOSES THE DOOR: same JSON, same code, different envelope. Rather than
re-implement each gate's judgment, this module builds the exact PreToolUse
payload a direct `mcp__carr__<verb>` call would have produced --
{"tool_name": "mcp__carr__<verb>", "tool_input": <parsed JSON args>,
"session_id": ..., "transcript_path": ...} -- and subprocess-invokes the real
gate file with it on stdin, the same way ops/guard-selftest.py proves this
file's own logic: from the ARTIFACT, not an import of its functions. A gate
that denies (exit 2) denies the Bash call; the verdict is bit-for-bit what a
direct verb call would have received.

SCOPE, STATED RATHER THAN PRETENDED AWAY. This closes exactly one of the two
doors named in the audit -- `./run.sh call <verb> ...`. The other,
mcp__*__call-verb (any MCP prefix, handled server-side in
mcp-server/src/mcp.js's callTool()), is NOT closed here. The only PreToolUse
hook registered broadly enough to see a call-verb passthrough for every MCP
prefix is hooks/rule-pack-preuse-reselection.py, whose own docstring states
twice, as a design guarantee, that it "never blocks" -- adding a deny path
there would break a promise that file makes to every caller, not just extend
its own reselection logic, and Jev's architecture judgment (recorded in this
PR's description) agreed that is a bigger, separate decision than this PR's
scope. Closing mcp__*__call-verb needs either a hook-registration change
(this PR is not authorized to make one) or a change inside mcp.js's
`call-verb` branch — both are reported, not made, here.

Not every gate this module calls can deny. Of the four, only
escalation-gate.py and blocker-decider-gate.py ever exit 2; drift-claim-gate.py
and rule-shape-gate.py are advisory-only (they print additionalContext and
always allow) -- see the bypass audit's C32 finding for rule-shape-gate.py.
All four are still invoked, for the same reason the direct verb call runs all
four: the advisory context is part of "the same checks."
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))

VERB_NAMES = frozenset({"record-defect", "add-loop", "teach", "activate-rule"})

# Mirrors the real registration in ~/.claude/settings.json:
#   mcp__.*__(record-defect|add-loop)  -> drift-claim-gate, escalation-gate,
#                                          blocker-decider-gate
#   mcp__.*__(teach|activate-rule)     -> rule-shape-gate
GATES_FOR_VERB = {
    "record-defect": ("blocker-decider-gate.py", "drift-claim-gate.py"),
    "add-loop": ("escalation-gate.py", "blocker-decider-gate.py", "drift-claim-gate.py"),
    "teach": ("rule-shape-gate.py",),
    "activate-rule": ("rule-shape-gate.py",),
}

# `./run.sh call [--reason "why"] [--branch name] <verb> '<json args>'` —
# see tools/call-verb.py's own module docstring for the full usage grammar.
_RUN_SH_CALL = re.compile(r"(?:^|[;&|]|\s)(?:\./)?run\.sh\s+call\b")
_FLAGS_WITH_VALUE = {"--reason", "--branch"}


def parse_run_sh_call(cmd):
    """Return (verb, args_dict) for a `./run.sh call ... <verb> '<json>'`
    invocation naming one of the four gated verbs, else None.

    Best-effort and fails open on anything it cannot parse cleanly (the same
    convention as every other check in guard-unattended.py: a check this
    module cannot confidently make is not made, rather than guessed at).
    """
    if not isinstance(cmd, str) or not cmd:
        return None
    m = _RUN_SH_CALL.search(cmd)
    if not m:
        return None
    # Truncate at the next unescaped shell separator, same guard every other
    # `[^|;&]*` pattern in guard-unattended.py uses, so a chained command
    # after the call is not swept into the argument parse.
    tail_match = re.search(r"run\.sh\s+call\b([^|;&]*)", cmd[m.start():])
    if not tail_match:
        return None
    try:
        tokens = shlex.split(tail_match.group(1))
    except ValueError:
        return None

    verb, json_arg = None, None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _FLAGS_WITH_VALUE:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        if verb is None:
            verb = tok
        elif json_arg is None:
            json_arg = tok
        i += 1

    if verb not in VERB_NAMES:
        return None

    args = {}
    if json_arg:
        try:
            parsed = json.loads(json_arg)
        except (ValueError, TypeError):
            # A verb call whose JSON this module cannot parse is not a call
            # this module can safely re-check -- fail open rather than deny
            # on a guess, and let the real verb handler report the malformed
            # JSON on its own terms.
            return None
        if isinstance(parsed, dict):
            args = parsed
    return verb, args


def recheck(verb, args, *, session_id=None, transcript_path=None, interpreter=None,
            timeout=15):
    """Re-run every gate registered on `verb`'s direct mcp__*__<verb> matcher,
    fed the SAME tool_input this call would have carried directly.

    Returns (deny_reason, context_notes). deny_reason is None when nothing
    denies; context_notes collects any advisory additionalContext the
    non-denying gates returned (drift-claim-gate.py, rule-shape-gate.py),
    for a caller that wants to surface it.
    """
    gates = GATES_FOR_VERB.get(verb, ())
    payload = {
        "tool_name": f"mcp__carr__{verb}",
        "tool_input": args if isinstance(args, dict) else {},
        "session_id": session_id or "",
        "transcript_path": transcript_path,
    }
    body = json.dumps(payload)
    py = interpreter or sys.executable
    contexts = []
    for gate in gates:
        path = os.path.join(HOOKS_DIR, gate)
        if not os.path.exists(path):
            continue
        try:
            proc = subprocess.run([py, path], input=body, capture_output=True,
                                   text=True, timeout=timeout)
        except Exception:
            # Fail open: the same "a check this module cannot make is not
            # made" convention as the parser above and every rule in
            # guard-unattended.py's own RULES table.
            continue
        if proc.returncode == 2:
            reason = (proc.stderr or "").strip() or f"{gate} refused this call"
            return (
                f"{reason}\n\n(caught by verb_gate_recheck: `./run.sh call {verb} ...` "
                f"is the same write a direct {verb} call would make, and {gate} already "
                f"refuses it there)",
                contexts,
            )
        if proc.returncode == 0 and proc.stdout:
            try:
                out = json.loads(proc.stdout)
                ctx = (out.get("hookSpecificOutput") or {}).get("additionalContext")
            except (ValueError, AttributeError):
                ctx = None
            if ctx:
                contexts.append(ctx)
    return None, contexts
