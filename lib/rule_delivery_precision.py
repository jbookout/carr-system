"""Pure action predicates for a shadow rule-delivery candidate.

The caller owns current delivery and boot evidence. This module proposes IDs;
it neither fetches rule text nor changes hook output, rule authority, or state.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
from typing import Callable

from lib import rule_routes

VERSION = "source-lookup-v5"
WRITERS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"})
DELEGATORS = frozenset({"Agent", "Task", "spawn_agent"})
SHELLS = frozenset({"bash", "zsh", "sh"})
READ_VERBS = frozenset({"find", "search-doctrine", "read-doctrine", "doctrine-sections",
                        "doctrine-index", "standing-context", "list-verbs"})
WRITE_RECORD_VERBS = frozenset({"teach", "amend-rule", "log-decision", "update-decision"})


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.I) is not None


def _commands(command: str, depth: int = 0) -> list[tuple[str, ...]]:
    """Conservative executable heads; quoted data never becomes an action."""
    if depth > 2:
        return []
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return []
    rows: list[tuple[str, ...]] = []
    part: list[str] = []
    for token in words + [";"]:
        if token and all(c in ";&|\n" for c in token):
            while part and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", part[0]):
                part.pop(0)
            if part:
                if part[0] in {"env", "command", "exec"}:
                    part = part[1:]
                if part:
                    rows.append(tuple(part))
                    if Path(part[0]).name in SHELLS and "-c" in part:
                        at = part.index("-c")
                        if at + 1 < len(part):
                            rows.extend(_commands(part[at + 1], depth + 1))
            part = []
        else:
            part.append(token)
    return rows


@dataclass(frozen=True)
class Facts:
    event: str
    tool: str
    text: str
    commands: tuple[tuple[str, ...], ...]
    verbs: frozenset[str]
    paths: tuple[str, ...]
    write: bool
    delegate: bool

    def command(self, head: str, action: str | None = None) -> bool:
        for words in self.commands:
            if Path(words[0]).name != head:
                continue
            tail = list(words[1:])
            while tail and tail[0].startswith("-"):
                option = tail.pop(0)
                if option in {"-C", "-c", "--git-dir", "--work-tree", "--repo", "-R"} and tail:
                    tail.pop(0)
            if action is None or (tail and tail[0] == action):
                return True
        return False


def facts(payload: dict) -> Facts:
    event = str(payload.get("hook_event_name") or "")
    tool = str(payload.get("tool_name") or "")
    args = payload.get("tool_input")
    args = args if isinstance(args, dict) else {}
    text = (payload.get("prompt") or "") if event == "UserPromptSubmit" else " ".join(
        str(args.get(k) or "") for k in ("prompt", "description", "message", "body", "content"))
    command = args.get("command") or args.get("cmd") or ""
    commands = tuple(_commands(command)) if isinstance(command, str) else ()
    verbs = set()
    if tool.startswith(("mcp__carr__", "mcp__carr_records__")):
        verb = tool.rsplit("__", 1)[-1].replace("_", "-")
        if verb == "call-verb" and isinstance(args.get("verb"), str):
            verb = args["verb"]
        verbs.add(verb)
    for words in commands:
        if Path(words[0]).name == "run.sh" and len(words) > 2 and words[1] == "call":
            verbs.add(words[2])
        if Path(words[0]).name == "call-verb.py" and len(words) > 1:
            verbs.add(words[1])
    paths = tuple(rule_routes.call_paths(args))
    return Facts(event, tool, str(text), commands, frozenset(verbs), paths,
                 tool in WRITERS, tool in DELEGATORS)


def _intent(f: Facts, actions: str, subjects: str, negatives: str = "") -> bool:
    return (_has(f.text, actions) and _has(f.text, subjects)
            and (not negatives or not _has(f.text, negatives)))


def _draft(f: Facts) -> bool:
    if _has(f.text, r"\binternal[- ]only\b|\bnot (?:client|prospect|public)[- ](?:visible|facing)\b"):
        return False
    return _intent(f, r"\b(?:draft|write|edit|compose|publish|send|review|lint)\b",
                   r"\b(?:prospect|client[- ](?:facing|visible)|outreach|social|proposal|newsletter)\b")


def _surface(f: Facts) -> bool:
    return _intent(f, r"\b(?:build|design|restyle|render|create|ship|review|implement|revise)\b",
                   r"\b(?:surface|board|dashboard|ui|app|packet|report|proposal|artifact|workstation)\b")


def _git_delivery(f: Facts) -> bool:
    return f.command("git", "commit") or f.command("git", "push") or any(
        len(words) > 2 and Path(words[0]).name == "gh" and words[1:3] in
        (("pr", "create"), ("pr", "merge")) for words in f.commands)


def _source(f: Facts) -> bool:
    return _has(f.text, r"https?://\S+") and _has(
        f.text, r"\b(?:read|study|source|article|thread|video|link|learn|apply|extract)\b")


def _mail(f: Facts) -> bool:
    return _intent(f, r"\b(?:read|capture|search|check|fetch|backfill|retrieve|inspect|find)\b",
                   r"\b(?:joe'?s|outlook|apple mail|apple calendar|carr mailbox|meetings|attendees)\b") and _has(
                       f.text, r"\b(?:mail|emails?|calendar|meetings|attendees|inbox)\b")


def _sweep(f: Facts) -> bool:
    return f.delegate and _has(f.text, r"\b(?:hunt|sweep|look everywhere|search (?:the )?(?:whole|entire|all)|find every|trace every)\b") and _has(
        f.text, r"\b(?:disk|files?|folders?|backup|source|repo|computer|places?|sinks?)\b")


def _review(f: Facts) -> bool:
    return not _has(f.text, r"<task-notification") and _has(f.text, r"\b(?:independent|adversarial|reviewer|verifier|fresh[- ](?:eyes|context))\b") and _has(
        f.text, r"\b(?:review|reviewer|verifier|verify|audit|APPROVE|REQUEST_CHANGES|evidence)\b")


def _finding(f: Facts) -> bool:
    if _has(f.text, r"<task-notification"):
        result = re.search(r'<result>(.*?)</result>', f.text, re.S | re.I)
        return (_has(f.text, r"<summary>\s*(?:Subagent|Agent)\b") and result is not None
                and len(result[1].strip()) >= 50
                and not _has(result[1], r'\b(?:waiting|interim|may be|pausing)\b'))
    if _has(f.text, r"<cross-session-message"):
        return _has(f.text, r"\b(?:finding|cause|failed|breaks|defect)\b")
    return _review(f) or _has(f.text, r"^Executor\s*:") or (
        _has(f.text, r"\b(?:session|agent|subagent|worker|peer|reviewer)\b") and
        _has(f.text, r"\b(?:finding|report|claim|result|says?|said|completed|finished)\b"))


def _code(f: Facts) -> bool:
    return f.write or _intent(
        f, r"\b(?:build|implement|code|repair|fix|rework|carry|engineer|add|put in|pin)\b",
        r"\b(?:PR|hook|harness|pipeline|code|schema|migration|gate|checker|validator|worktree|branch|repository|repo|script)\b",
        r"\b(?:read[- ]only|survey only|change nothing|do not (?:edit|modify))\b")


def _correction(f: Facts) -> bool:
    return f.event == 'UserPromptSubmit' and not _has(f.text, r"<task-notification|<cross-session-message") and _has(
        f.text, r"^\s*(?:no\b|quit\b|fine[, ]|fair[, ]|hang on\b|more feedback\b|to be clear\b)|\b(?:you didn't|you did not|isn't what|not what|don't buy|we got crossed|I only meant|doesn't need|should never|from now on)\b")


RECORD_MUTATIONS = frozenset({
    'log-decision', 'update-decision', 'add-loop', 'close-loop', 'record-finding',
    'log-activity', 'stamp-touch', 'log-outreach', 'record-loi-submission',
    'record-negotiation-round', 'set-critical-date', 'open-incident',
    'link-salesforce-reference', 'record-source-observation',
    'prepare-tour-route-version', 'accept-tour-route-version', 'register-tour-property',
    'append-tour-coordinate-candidate', 'append-tour-entrance-verification-receipt',
})


def _lookup(f: Facts) -> bool:
    if 'standing-context' in f.verbs or f.tool.rsplit('__', 1)[-1] in {'standing-context', 'standing_context'}:
        return False
    if f.tool in {'Read', 'WebFetch', 'WebSearch'}:
        return True
    if f.verbs & (READ_VERBS - {'standing-context'} | {'read-loop', 'loop-board', 'loop-headers', 'current-work-requests', 'log-decision-history'}):
        return True
    if f.tool.rsplit('__', 1)[-1].replace('_', '-') in {'read-loop', 'loop-board', 'loop-headers', 'current-work-requests'}:
        return True
    return False


Predicate = Callable[[Facts], bool]
REFINEMENTS: dict[str, Predicate] = {
    "113b3833": _lookup,
    "4a9188f3": lambda f: _correction(f) or f.command('git', 'commit') or 'teach' in f.verbs,
    "185013c6": lambda f: f.delegate or _intent(f, r"\b(?:spawn|delegate|dispatch|fan[- ]?out|staff)\b", r"\b(?:agent|worker|model|workflow|task)\b"),
    "2b66211d": lambda f: _review(f) or _has(f.text, r"\b(?:fan[- ]?out|fleet|verifier|fresh context|review panel|several agents|multiple agents)\b"),
    "5cd8d0f6": lambda f: _has(f.text, r"\b(?:blocked|refused|denied|permission error|cannot run|can't run)\b"),
    "5e896ed6": _source,
    "81709f57": lambda f: _has(f.text, r"\b(?:red[- ]?team|panel|council|distinct lenses|adversarial review)\b"),
    "8aefcdce": lambda f: _has(f.text, r"\b(?:copilot|outlook|calendar|teams)\b"),
    "86647daf": lambda f: _git_delivery(f) or bool(f.verbs & WRITE_RECORD_VERBS) or _intent(f, r"\b(?:finished|completed|shipped|decided|built)\b", r"\b(?:shared system|build|system|decision|rule)\b"),
    "b587f6c2": lambda f: _has(f.text, r"\b(?:architecture|mechanism|design)\b") and _has(f.text, r"\b(?:fork|irreversible|expensive reversal|no deterministic verifier|council)\b"),
    "c20dc3d5": _finding,
    "d7f74c93": lambda f: _has(f.text, r"\b(?:confidential|exposure|access boundary|data access|agent access|credential custody)\b"),
    "df55c398": _sweep,
    "ede4c735": _draft,
    "725dff46": lambda f: _draft(f) and _has(f.text, r"\b(?:vendor|network|team|dell)\b"),
    "a8f159ad": _surface,
    "67580c28": _surface,
    "9293d609": _surface,
}

ADDITIONS: dict[str, Predicate] = {
    "4a53ff82": _code,
    "a7784a18": lambda f: _code(f) or _git_delivery(f),
    "bc9188b4": _git_delivery,
    "185013c6": lambda f: f.delegate,
    "6cfb67f5": lambda f: f.delegate,
    "df55c398": _sweep,
    "c6f69dee": lambda f: f.delegate,
    "fb110a39": lambda f: f.delegate,
    "49533583": _mail,
    "c66dc739": lambda f: _mail(f) and _has(f.text, r"\b(?:mail|emails?|inbox)\b"),
    "113b3833": _lookup,
    "647f843d": lambda f: "add-loop" in f.verbs,
    "1b8e7f43": lambda f: "add-loop" in f.verbs and _has(f.text, r"\b(?:blocked|refused|unavailable)\b"),
    "4a9188f3": lambda f: "teach" in f.verbs or _correction(f),
    "bbffc139": _correction,
    "ca841807": lambda f: bool(f.verbs & RECORD_MUTATIONS),
    "c20dc3d5": _finding,
    "2b66211d": _review,
    "24e10ee8": lambda f: _git_delivery(f) or _code(f),
    "e65efc68": _code,
    "ab814a26": lambda f: bool(f.verbs & {"teach", "approve-rule", "activate-rule", "amend-rule"}),
    "5d44d3f3": lambda f: bool(f.verbs & {"new-client", "new-deal", "import-parties"}),
    "57d83b75": lambda f: "new-client" in f.verbs,
    "67580c28": _surface,
    "9293d609": _surface,
    "a8f159ad": _surface,
    "80def9d2": _surface,
    "ede4c735": _draft,
    "51d9f05f": _draft,
    "5e896ed6": _source,
    "94806da2": _source,
    "6437ae15": _source,
}


def select(repo: Path, payload: dict, baseline_ids: list[str], boot_ids: list[str],
           config: dict | None = None) -> list[str]:
    """Propose IDs without changing baseline, payload, or repository state.

    Unknown baseline IDs survive. Optional ``active_ids`` binds the live
    corpus; optional allow/refine/add lists make train candidates explicit.
    Boot exclusion relies on the caller's confirmed full-text boot receipt.
    """
    del repo
    config = {} if config is None else config
    f = facts(payload)
    selected = set(baseline_ids) - set(boot_ids)
    if config.get("refine", True):
        enabled = set(config.get("refine_ids", REFINEMENTS))
        selected = {rid for rid in selected if rid not in enabled or
                    rid not in REFINEMENTS or REFINEMENTS[rid](f)}
    if config.get("add_actions", True):
        enabled = set(config.get("add_ids", ADDITIONS))
        selected.update(rid for rid in enabled if rid in ADDITIONS and ADDITIONS[rid](f))
    selected.difference_update(boot_ids)
    if "active_ids" in config:
        selected.intersection_update(config["active_ids"])
    if "allow_ids" in config:
        selected.intersection_update(config["allow_ids"])
    return sorted(selected)
