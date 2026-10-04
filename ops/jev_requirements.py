"""Requirements from explicit acceptance contracts; semantic clauses need review."""
import importlib.util
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BUDGET_SECONDS = 6.0
MAX_REQUIREMENTS = 12
MAX_REQUIREMENT_CHARS = 300
MAX_TEST_CHARS = 2000
MAX_FILES = 20


MUTATION_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
TEST_COMMAND = re.compile(r"pytest|selftest|\btest\b|ci\.sh|npm\s+(?:run\s+)?test|node\s+--test", re.I)

GREETING = re.compile(
    r"^(?:hi|hey|hello|yo|good\s+(?:morning|afternoon|evening)|thanks|thank\s+you|thx|cheers)\b", re.I)
FILLER = re.compile(
    r"^(?:ok(?:ay)?|great|cool|nice|perfect|sounds\s+good|lgtm|got\s+it|sure|yes|yep|no|nope|"
    r"awesome|alright|all\s+right|go(?:\s+ahead)?|continue|proceed)[\s.!,]*$", re.I)
REQUEST_QUESTION = re.compile(r"^(?:can|could|would|will)\s+you\b|^please\b", re.I)
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9`\"'(])")
SYSTEM_TAG = re.compile(r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+)>.*?</\1>", re.S)
FENCE = re.compile(r"```.*?```", re.S)
# The same markers hooks/conduct-stop-gate.py is_harness_injected() refuses to
# read as the partner's words.
HARNESS_MARKERS = ("<system-reminder>", "<task-notification>", "[SYSTEM NOTIFICATION",
                   "<local-command", "<command-name>", "Caveat:", "<user-prompt-submit-hook>")



def _keep(sentence):
    text = sentence.strip().strip("-*• ").strip()
    if len(text.split()) < 3:
        return None
    if GREETING.match(text) and len(text.split()) <= 8:
        return None
    if re.match(r"^(?:thanks|thank\s+you)\b", text, re.I) or text.endswith(":"):
        return None
    if FILLER.match(text):
        return None
    if text.endswith("?") and not REQUEST_QUESTION.match(text):
        return None
    return text[:MAX_REQUIREMENT_CHARS]


def split_requirements(prompt):
    """Deterministic clause split of a human request. Crude by design; see top."""
    if not prompt:
        return []
    text = SYSTEM_TAG.sub(" ", prompt)
    text = FENCE.sub(" ", text)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if BULLET.match(line):
            pieces = [BULLET.sub("", line)]
        else:
            pieces = SENTENCE_END.split(line)
        for piece in pieces:
            kept = _keep(piece)
            if kept and kept not in out:
                out.append(kept)
    return out[:MAX_REQUIREMENTS]


def _content(rec):
    message = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    return message.get("content", rec.get("content"))


def _human_text(rec):
    """The text of a human prompt record, or None for tool results and meta."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
        return None
    # Harness-injected turns are not the partner's keystrokes (the conduct gate's
    # is_harness_injected() rule). Once this check could reopen a turn, a
    # background-task notice's boilerplate "If this event is something the user
    # would act on now, send a PushNotification" was read as Joe's request and
    # held a turn open (2026-09-24, the first day it acted).
    origin = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
    if origin.get("kind") not in (None, "", "user", "keyboard", "human") or rec.get("isCompactSummary"):
        return None
    content = _content(rec)
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        text = "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    else:
        return None
    if text.lstrip().startswith(HARNESS_MARKERS):
        return None
    stripped = SYSTEM_TAG.sub(" ", text).strip()
    return stripped or None


def last_turn(recs):
    """(prompt, records after it, turn start timestamp) for the last human prompt."""
    for idx in range(len(recs) - 1, -1, -1):
        text = _human_text(recs[idx])
        if text:
            return text, recs[idx + 1:], recs[idx].get("timestamp")
    return None, [], None


def _tool_uses(recs):
    for rec in recs:
        if rec.get("type") != "assistant":
            continue
        content = _content(rec)
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    yield block


def changed_paths(turn):
    paths = []
    for block in _tool_uses(turn):
        if block.get("name") in MUTATION_TOOLS:
            data = block.get("input") or {}
            path = data.get("file_path") or data.get("notebook_path")
            if path and path not in paths:
                paths.append(path)
    return paths[:MAX_FILES]


def test_output(turn):
    """Tail of the last Bash result whose command looks like a test run, or None."""
    wanted = {b.get("id") for b in _tool_uses(turn)
              if b.get("name") == "Bash" and TEST_COMMAND.search(str((b.get("input") or {}).get("command", "")))}
    found = None
    for rec in turn:
        content = _content(rec)
        if rec.get("type") != "user" or not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in wanted:
                body = block.get("content")
                if isinstance(body, list):
                    body = "\n".join(b.get("text", "") for b in body if isinstance(b, dict))
                found = str(body or "")
    return found[-MAX_TEST_CHARS:] if found else None



def _acceptance():
    spec = importlib.util.spec_from_file_location("acceptance_checks",os.path.join(REPO,"lib","acceptance_checks.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate_requirements(criteria, evidence, *, root=REPO):
    return _acceptance().evaluate(criteria,evidence,root=root)


def check(payload, recs, *, judge_module=None, llm=None, budget=BUDGET_SECONDS):
    prompt, turn, since = last_turn(recs or [])
    if not prompt:
        return None
    acceptance = _acceptance()
    criteria = acceptance.contract(prompt).get("criteria")
    if not criteria:
        requirements = split_requirements(prompt)
        if not requirements:
            return None
        return {"status":"needs_review","advisory":"Semantic requirement acceptance needs review.",
                "unmet":[],"needs_review":requirements}
    # Evidence is supplied by the caller, never inferred from the assistant's
    # close or a matching word in its diff.
    evidence = (payload or {}).get("acceptance_evidence") or {}
    result = acceptance.evaluate(criteria,evidence,root=(payload or {}).get("cwd",REPO))
    unmet = [{"index":i+1,"text":str(criteria[i].get("id",i)),"reason":row["reason"]}
             for i,row in enumerate(result["criteria"]) if row["status"] == "failed"]
    return {**result,"unmet":unmet,"advisory":"Acceptance needs review." if result["status"] == "needs_review" else None}
