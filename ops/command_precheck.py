"""command_precheck.py — warn about a shell command BEFORE it runs. Never deny.

WHY THIS EXISTS, measured 2026-09-18 against 464 real shell calls taken from one
session's own transcript. Twenty-six of them came back an error, a refusal or a
guard block: roughly four thousand characters of error text into a session's
context before any diagnosis, and then a re-read, a fix and a retry for each.
Every one of those was knowable in advance from a fact on disk.

THE MEASUREMENT THAT DECIDES THE DESIGN, and it is the whole reason this file
gathers before it asks. Putting the command text alone in front of a judgment
has NO discriminating power: twenty-six failures and twenty-six successes both
scored a median of 0.44, and at a 0.8 threshold the question raised more false
alarms than it caught real failures. Attach the one environment fact that
decides the command and the same set separates at 0.94 against 0.17. So
ops/jev_precheck.py collects the facts first, deterministically, and a judgment
is asked only when there is something to judge.

IT IS A LIBRARY, AND THAT IS NOT A STYLE CHOICE. A file under hooks/ with a
shebang is a NEW sealed ingress, and admitting one means a mutation-registry
successor: a generated registry file, a production migration, a fixture rebuilt
from origin/main, and a disposable PostgreSQL round trip to learn the catalog
digest the migration reads back on itself. That is the heavy path. Living here
with no shebang and no main guard, invoked by a hook that already runs on every
Bash call, costs a re-digest of that one hook instead — an overlay upsert. The
frontier does not move. Do not add a shebang to this file to "make it runnable".

(The detector is a regex over the whole file and does not know what a docstring
is, so the guard construct is described here and never spelled. And note that
fullInventory enumerates TRACKED files only: an untracked entrypoint passes the
assertion and fails the moment it is staged, which is how this file's first
draft looked safe.)

IT NEVER DENIES, AND THAT IS NOT TIMIDITY. A probabilistic refusal in front of
every shell call converts a model's uncertainty into a blocked session, and the
one thing worse than a wrong command is a session that cannot run the right one.
This prints a warning and returns zero. Rule: exit 2 never appears in this file
and a test asserts it.

IT FAILS OPEN ON EVERYTHING. No credential, service down, slow response, bad
payload, unreadable repository — every one of those returns zero silently. A
pre-check that turns somebody else's outage into this repository's outage has
cost more than it will ever save.

THREE FILTERS BEFORE ANY MONEY IS SPENT, in order, and each one is free:

  1. No literal shell command in Bash or Codex's exec wrapper: return.
  2. Every statement in the command is a known pure read: return. About two
     thirds of real traffic stops here, and no model is needed to know that a
     grep is a read.
  3. The fact-gatherer found NOTHING about the command: return. With nothing on
     disk contradicting it, there is nothing to ask about, and asking anyway is
     what produced the 0.44 median.

Only what survives all three reaches a judgment, measured at roughly half a
second. On the traffic this was built from that is about a third of commands,
some eighty seconds and a fifth of a cent across an entire session.

KILL SWITCH: set CARR_PRECHECK=0. Anything in front of every shell call needs
one, and that is engineering rather than caution.
"""

import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "out", "command-precheck.jsonl")
LOG_MAX_BYTES = 4 * 1024 * 1024  # a count cap is not a size cap; this is a size cap

# Above this, the warning is printed. Deliberately high: a warning in front of
# every command trains a session to ignore warnings, which is worse than none.
# Re-derive it from LOG once real traffic has accumulated.
WARN_AT = 0.80

# Short on purpose. This sits in somebody's way, and a judgment that has not
# arrived in five seconds has already cost more than the command it is about.
TIMEOUT_SECONDS = 5.0

# Every statement is one of these and none of the writers below: pure read.
READS = re.compile(
    r"^(?:grep|rg|cat|head|tail|ls|wc|awk|sed -n|find|echo|printf|jq|pwd|which|"
    r"git (?:show|log|diff|status|branch|rev-parse|ls-files|ls-tree|describe)|"
    r"gh (?:pr view|run view|issue view))\b")
WRITES = re.compile(
    r"(?:>{1,2}\s|\brm\b|\bmv\b|\bcp\b|\bmkdir\b|\btee\b|\bchmod\b|sed -i|"
    r"git (?:add|commit|push|merge|checkout|reset|rebase|stash)|"
    r"gh (?:pr (?:create|merge|edit|close)|release)|npm|psql|curl|ssh)")


def is_pure_read(command):
    """True when every statement is a known read and none is a known write."""
    if WRITES.search(command):
        return False
    statements = [s.strip() for s in re.split(r"&&|\|\||;|\n|\|", command) if s.strip()]
    return bool(statements) and all(READS.match(s) for s in statements)


# The precheck sends a command to TypeSafe. A command carrying a credential
# stays local even when its file facts would otherwise make it eligible.
SENSITIVE_COMMAND = re.compile(
    r"(?i)(?:\b(?:api[_-]?key|access[_-]?token|password|passwd|secret|"
    r"authorization|bearer|pgpassword|database_url)\b\s*(?:=|:|\s+)"
    r"|postgres(?:ql)?://|\b[a-z]+://[^/\s]+@)"
)


def _sibling(name):
    """Load an ops/ library by path. ops/ is not a package by design."""
    import importlib.util
    path = os.path.join(REPO, "ops", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _log(record):
    """Append one observation. Never raises, and never fills the disk."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX_BYTES:
            # TRIM BY BYTES, NOT BY LINES. Keeping the last N lines does not
            # bound a file: one enormous line is its own last N lines, so the
            # file never shrinks and the cap silently does nothing. That is the
            # exact shape of the hook-state files that once filled this disk.
            # Read the tail, then drop the leading partial record.
            with open(LOG, "rb") as handle:
                handle.seek(-LOG_MAX_BYTES // 2, os.SEEK_END)
                tail = handle.read()
            _, _, tail = tail.partition(b"\n")
            with open(LOG, "wb") as handle:
                handle.write(tail)
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    except Exception:
        pass


def repo_root(cwd):
    """The checkout a command's relative paths are written against.

    THE HOOK DOES NOT RUN WHERE THE SESSION RUNS. Hooks execute from the
    canonical checkout, so resolving `ops/foo.py` against this file's own
    location asks whether the file exists in CANONICAL — and most work here
    happens in a worktree. The first live run of this pre-check flagged a file
    the session had just written, at 0.94, because canonical had never seen it.
    A gate that cries wolf on every new file in every worktree is a gate a
    session learns to scroll past, which is worse than not having one.

    Walks up from the session's own directory to the enclosing checkout rather
    than assuming cwd IS the root, because a command can be run from a
    subdirectory while naming paths from the root. Falls back to this file's
    checkout when there is no usable cwd, which is the single-checkout case.
    """
    path = os.path.abspath(os.path.expanduser(cwd or ""))
    for _ in range(12):
        if os.path.exists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return REPO


# One narrow question per KIND of fact, asked together in one request.
#
# The first version asked a single broad question — "is this command likely to
# fail?" — which is the thing the vendor's build guide names as the mistake to
# avoid: a broad question hides several judgments behind one number, and a
# blended 0.84 cannot tell a session WHICH of its facts is the problem. Their
# worked example decomposes one spam question into six independent checks.
#
# Decomposing costs nothing here. Independent questions about one state ride in
# ONE request, run in parallel, and each is scored on its own against the state
# — measured by the vendor at 12.2 times cheaper and 10 times faster than
# asking them separately. So this is the same one request it always was, and it
# now comes back with a probability per reason instead of one blended number.
#
# Each entry is (question id, the facts key it needs, how to phrase it). A
# question is only asked when its fact is actually present, so a command with
# one kind of problem costs one question rather than five.
QUESTIONS = (
    ("undeclared_option", "undeclared_options",
     ("This command passes an option the script does not accept, so the script "
      "will exit on an unrecognised argument.",
      "The option named in `state.environment.undeclared_options` is genuinely "
      "not accepted by the script this command runs, and the script rejects "
      "unknown arguments rather than ignoring them.",
      "The option is accepted after all, or it belongs to a different command "
      "in the line, or the script passes its arguments through to something "
      "that does accept it.")),
    ("bad_import", "import_notes",
     ("This command runs an import that the directory layout does not support.",
      "The import named in `state.environment.import_notes` will raise, because "
      "the directory it imports from is not a package.",
      "The import is written in a form that works anyway, such as an absolute "
      "import or a path-based load, or the command never reaches it.")),
    ("missing_path", "paths_that_do_not_exist",
     ("This command names a repository path that is not there, and needs it to "
      "exist.",
      "A path in `state.environment.paths_that_do_not_exist` is one the command "
      "READS or executes, so its absence stops the command.",
      "The command CREATES or writes that path, or names it only as an "
      "argument to something that tolerates it being absent — a path being "
      "absent is not a problem when the command's job is to make it.")),
    ("guard_refusal", "guard_refusals",
     ("A guard refuses this command before it runs.",
      "The refusal in `state.environment.guard_refusals` applies to this "
      "command as written.",
      "The pattern matched something that is not actually the refused action, "
      "such as the phrase appearing inside a quoted string or a comment.")),
    ("wrong_interface", "module_interfaces",
     ("This command uses a module in a shape that module does not expose.",
      "The command calls a name, or reads a result, that is not in the "
      "interface listed under `state.environment.module_interfaces`.",
      "Everything the command touches is present in the listed interface, or "
      "the command does not call into that module at all. A module merely "
      "being NAMED is not evidence of misuse.")),
)


def check(command, repo=REPO):
    """(probability, facts, reasons) — or (None, {}, {}) when nothing was asked.

    `reasons` maps each asked question to its probability, so a caller can say
    which fact is the problem rather than quoting one blended number.
    """
    precheck = _sibling("jev_precheck")
    facts = precheck.environment_facts(command, repo)
    if not facts:
        return None, {}, {}
    judge = _sibling("jev_judge")
    client = _sibling("typesafe_client")
    questions = {}
    for key, needs, (instruction, yes, no) in QUESTIONS:
        if facts.get(needs):
            questions[key] = client.noul(instruction, true=yes, false=no)
    if not questions:
        return None, facts, {}
    answer = judge.judge({"command": command[:1500], "environment": facts},
                         questions, timeout=TIMEOUT_SECONDS)
    reasons = {}
    for key in questions:
        try:
            reasons[key] = float(answer["answers"][key]["noul"])
        except (KeyError, TypeError, ValueError):
            continue
    if not reasons:
        return None, facts, {}
    # Combined in code, not by the model. Any ONE of these being true is enough
    # to sink the command, so the highest is the command's probability — a
    # weighted average would let four confident "no"s bury one confident "yes".
    return max(reasons.values()), facts, reasons


def _commands(payload):
    """Read literal commands only; dynamic JS expressions have no safe precheck."""
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if tool == "Bash" and isinstance(tool_input, dict):
        command = tool_input.get("command")
        return [command] if isinstance(command, str) else []
    if tool == "exec_command" and isinstance(tool_input, dict):
        command = tool_input.get("cmd")
        return [command] if isinstance(command, str) else []
    if tool != "functions.exec":
        return []
    source = tool_input if isinstance(tool_input, str) else tool_input.get("code", "")
    if not isinstance(source, str):
        return []
    commands = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\b['\"]?cmd['\"]?\s*:\s*", source):
        try:
            command, _ = decoder.raw_decode(source[match.end():])
        except (TypeError, ValueError):
            continue
        if isinstance(command, str) and command not in commands:
            commands.append(command)
        if len(commands) == 3:
            break
    return commands


def _command_cwd(payload):
    cwd = payload.get("cwd") or payload.get("workingDirectory") or ""
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if tool == "exec_command" and isinstance(tool_input, dict):
        workdir = tool_input.get("workdir")
        return workdir if isinstance(workdir, str) else cwd
    if tool != "functions.exec":
        return cwd
    source = tool_input if isinstance(tool_input, str) else tool_input.get("code", "")
    if not isinstance(source, str):
        return None
    matches = list(re.finditer(r"\b['\"]?workdir['\"]?\s*:\s*", source))
    workdirs = []
    decoder = json.JSONDecoder()
    for match in matches:
        try:
            value, _ = decoder.raw_decode(source[match.end():])
        except (TypeError, ValueError):
            return None
        if not isinstance(value, str):
            return None
        workdirs.append(value)
    # Different or dynamic nested working directories make relative facts
    # ambiguous. Silence is safer than a confident warning about another tree.
    return workdirs[0] if len(set(workdirs)) == 1 else (cwd if not matches else None)


def advisory(payload):
    """The warning text for this tool call, or None. NEVER raises, never denies.

    Returns a string the caller prints as additionalContext, so the hosting hook
    stays a thin dispatcher and every decision lives here where it is tested.
    """
    if os.environ.get("CARR_PRECHECK") == "0":
        return None
    try:
        command_cwd = _command_cwd(payload)
        if command_cwd is None:
            return None
        repo = repo_root(command_cwd)
        warnings = []
        for command in _commands(payload):
            if not command.strip() or is_pure_read(command) or SENSITIVE_COMMAND.search(command):
                continue
            probability, facts, reasons = check(command, repo)
            if probability is None:
                continue
            _log({"command": command[:400], "p": probability, "facts": facts,
                  "reasons": reasons, "repo": repo, "warned": probability >= WARN_AT})
            if probability < WARN_AT:
                break  # one model call per hook; the installed door has a 3s budget
            lines = [f"PRE-CHECK {probability:.2f} — this command looks likely to fail. "
                     "It has NOT been blocked; run it anyway if you disagree."]
            needs = {key: fact for key, fact, _ in QUESTIONS}
            for key, reason in sorted(reasons.items(), key=lambda item: -item[1]):
                if reason < WARN_AT:
                    continue
                value = facts.get(needs.get(key), [])
                shown = value if isinstance(value, list) else [str(value)]
                for item in shown[:3]:
                    lines.append(f"  · {reason:.2f}  {item}")
            warnings.append("\n".join(lines))
            break
        return "\n".join(warnings)[:1800] if warnings else None
    except Exception:
        return None  # fails open, always
