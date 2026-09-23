"""jev_requirements.py — a SHADOW requirement checklist for a turn that changed code.

WHY (Joe, 2026-09-23, decision a98c2832: Jev supervision checks). A session can
finish a turn with a green diff that quietly drops one of the things the human
asked for. This module asks, once per such turn: for each requirement in the
human's last request, is it satisfied by what the turn actually changed?

SHADOW, AND ONLY SHADOW. Stop-gate reopening is rationed to three hooks, so this
never blocks and never reopens a turn. hooks/completion-evidence-gate.py calls
check() after its own decision is made and ignores the result for that decision;
the most this module does is return ONE advisory line, which the gate shows only
when it is not already blocking. Every judgment is recorded to
out/jev-judge.jsonl through ops/jev_judge.record() under KIND, so a threshold can
be measured later on real traffic instead of guessed now.

THE LIMITATION, STATED PLAINLY. Jev cannot write text: it answers noul (a yes/no
probability), choice and score. So Jev cannot split a request into requirements.
The split here is deterministic and crude: bullets and lines, then sentences and
semicolons, dropping greetings, filler and questions put to the assistant. It
will sometimes keep a sentence that is context rather than an order, and it
will miss a requirement buried mid-sentence. Optionally, when a local
OpenAI-compatible server answers at LOCAL_LLM_URL inside LOCAL_LLM_SECONDS, its
list is used instead; that is best-effort, never required, and any failure or
slowness silently falls back to the deterministic split.

WHAT "CHANGED CODE" MEANS. Paths written by Write/Edit/MultiEdit/NotebookEdit in
the turn since the last human prompt, and their git diff against HEAD (or, when
the turn already committed them, the commits made since the turn began). A file
changed only through Bash is not seen. No changed path, or an empty diff, means
no call at all.

ONE REQUEST. All requirements ride in one Jev call as one noul each, against
named fields {task, diff, test_output}; the vendor measured batching about 12x
cheaper, and each question is scored on its own.

A LIBRARY. No shebang and no main guard, for the reason ops/jev_judge.py gives.
Fail open: a total budget of BUDGET_SECONDS, every exception swallowed, an
unavailable Jev recorded as an error row and returning None, and session
"selftest" skipped outright.
"""

import importlib.util
import json
import os
import re
import subprocess
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KIND = "requirement_checklist"
LOW_AT = 0.30
BUDGET_SECONDS = 6.0
MAX_REQUIREMENTS = 12
MAX_REQUIREMENT_CHARS = 300
MAX_TASK_CHARS = 4000
MAX_DIFF_CHARS = 12000
MAX_TEST_CHARS = 2000
MAX_FILES = 20

LOCAL_LLM_URL = "http://127.0.0.1:8000/v1/chat/completions"
LOCAL_LLM_SECONDS = 1.0

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


def _sibling(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "ops", f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- the split

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


def local_llm_requirements(prompt, *, url=LOCAL_LLM_URL, timeout=LOCAL_LLM_SECONDS, opener=None):
    """Best-effort list from a local OpenAI-compatible server, or None. Never raises."""
    if os.environ.get("CARR_JEV_REQ_LOCAL_LLM", "1") == "0":
        return None
    try:
        body = json.dumps({
            "model": os.environ.get("CARR_JEV_REQ_LOCAL_MODEL", "local"),
            "temperature": 0,
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content":
                    "List every distinct requirement the user's request asks for, one per "
                    "line, each starting with '- '. Omit greetings, filler and questions. "
                    "Output only the list."},
                {"role": "user", "content": prompt[:MAX_TASK_CHARS]},
            ],
        }).encode()
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        content = data["choices"][0]["message"]["content"]
        items = []
        for line in str(content).splitlines():
            if BULLET.match(line):
                item = BULLET.sub("", line).strip()[:MAX_REQUIREMENT_CHARS]
                if item and item not in items:
                    items.append(item)
        return items[:MAX_REQUIREMENTS] or None
    except Exception:
        return None


# ---------------------------------------------------------- the transcript

def _content(rec):
    message = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    return message.get("content", rec.get("content"))


def _human_text(rec):
    """The text of a human prompt record, or None for tool results and meta."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
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


def _git(args, cwd, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0.2:
        return ""
    try:
        run = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                             timeout=min(remaining, 2.0), env=_GIT_ENV.get("env"))
        return run.stdout if run.returncode == 0 else ""
    except Exception:
        return ""


# A GIT_DIR or GIT_INDEX_FILE inherited from a caller overrides -C, so every
# call runs under ops/git_env.py's scrubbed environment when it loads.
_GIT_ENV: dict = {}


def turn_diff(paths, since, deadline):
    chunks = []
    try:
        _GIT_ENV["env"] = _sibling("git_env").scrubbed_env()
    except Exception:
        _GIT_ENV["env"] = None
    for path in paths:
        folder = os.path.dirname(path) or "."
        if not os.path.isdir(folder):
            continue
        piece = _git(["diff", "--no-color", "HEAD", "--", path], folder, deadline)
        if not piece and os.path.exists(path) and not _git(["ls-files", "--", path], folder, deadline):
            # --no-index exits 1 when the files differ, so read the file itself.
            piece = ""
            if not piece:
                try:
                    with open(path, errors="replace") as handle:
                        piece = f"new file {path}\n" + handle.read(4000)
                except OSError:
                    piece = ""
        if not piece and since:
            piece = _git(["log", "-p", "--no-color", "-n", "3", f"--since={since}", "--", path],
                         folder, deadline)
        if piece:
            chunks.append(piece)
    return "\n".join(chunks)[:MAX_DIFF_CHARS]


# ------------------------------------------------------------------ the ask

def _question_text(n, requirement):
    return (f"Is requirement {n} satisfied by `diff`? Requirement {n}: \"{requirement}\" "
            f"(from the human's request in `task`; `test_output`, when present, is the "
            f"last test run of the turn).")


def advisory_line(requirements, probs):
    low = sorted((p, i) for i, p in enumerate(probs) if p is not None and p < LOW_AT)
    if not low:
        return None
    p, i = low[0]
    text = requirements[i]
    text = text if len(text) <= 90 else text[:87] + "..."
    more = f" (+{len(low) - 1} more under {LOW_AT:.1f})" if len(low) > 1 else ""
    return f"Jev (shadow): requirement {i + 1} may be unmet (p={p:.2f}): \"{text}\"{more}"


def check(payload, recs, *, judge_module=None, llm=local_llm_requirements, budget=BUDGET_SECONDS):
    """Shadow requirement checklist for the last turn. One advisory line or None.

    Never raises and never decides anything for the caller.
    """
    try:
        deadline = time.monotonic() + budget
        session = (payload or {}).get("session_id") or (payload or {}).get("sessionId")
        if session == "selftest":
            return None
        prompt, turn, since = last_turn(recs or [])
        if not prompt:
            return None
        paths = changed_paths(turn)
        if not paths:
            return None
        diff = turn_diff(paths, since, deadline)
        if not diff.strip():
            return None

        source = "split"
        requirements = None
        if llm is not None:
            requirements = llm(prompt)
            if requirements:
                source = "local_llm"
        if not requirements:
            requirements = split_requirements(prompt)
        if not requirements:
            return None

        jj = judge_module or _sibling("jev_judge")
        note = {"source": source, "requirements": requirements, "paths": paths}
        remaining = deadline - time.monotonic()
        try:
            if remaining < 0.5:
                raise TimeoutError("requirement checklist budget spent before the Jev call")
            tsc = jj._client()
            questions = {
                f"req_{n}": tsc.noul(_question_text(n, text),
                                     true=f"The diff carries out requirement {n}.",
                                     false=f"The diff does not carry out requirement {n}, or only part of it.")
                for n, text in enumerate(requirements, 1)}
            state = {"task": prompt[-MAX_TASK_CHARS:], "diff": diff}
            tests = test_output(turn)
            if tests:
                state["test_output"] = tests
            answer = jj.judge(state, questions, timeout=remaining)
        except Exception as exc:
            try:
                jj.record(KIND, session, None, None, note=note, error=exc)
            except Exception:
                pass
            return None
        jj.record(KIND, session, answer, None, note=note)
        probs = []
        for n in range(1, len(requirements) + 1):
            try:
                prob = float(answer["answers"][f"req_{n}"]["noul"])
                probs.append(prob if 0.0 <= prob <= 1.0 else None)
            except Exception:
                probs.append(None)
        return advisory_line(requirements, probs)
    except Exception:
        return None
