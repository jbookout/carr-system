"""The flash-local desk: a Model Room desk that answers on the local Flash model itself.

Until 2026-09-24 the queue could reach Claude and Codex desks and Joe, but nothing ran work on Flash: the desk
registered as "flash" was a Claude session with that name. Joe's routing rulings (decisions 81f6bcf7, 79110363) send
direct questions to Flash first, so Flash needs a desk the queue can hand a task to.

This desk runs Flash's direct protocol from ops/config/model-routes.v1.json: one reply, thinking off, a bounded
answer length. It is synchronous like a codex-session desk and returns the same outcome shape, so bridge.deliver and
the queue executor treat it the same way. Code work does not come here: it has its own protocol
(flash-run). Script work does (2026-09-26): a task that names its data with `data: <path>` lines runs
tools/flash-script.py, Flash's sandboxed script protocol, instead of one direct reply. Only data under the policy's
`script_data_roots` is accepted, and nothing inside it may be a symbolic link, because the harness copies what it is
given and the answer is posted to the room: a path that could reach a key or a credential never gets that far.

A reply with no answer comes back as status "failed" with detail "no_answer", so the task is blocked visibly
rather than completed empty. ops/config/model-routes.v1.json names the route's `then` desk (Opus, via
claude-desktop) as where a hand-off would go next, but the queue path (queue_dispatch.py) does not dispatch
there today: a Hermes queue task's CARR_QUEUE_META.target is fixed in the task body at creation and checked
against the claiming desk's own alias, so moving a task to a different desk needs a new, linked task rather
than a reassignment of this one. Until that lands, a no-answer or malformed-protocol reply here only retries
on the SAME flash-local desk, under its own diagnosable code, and then blocks.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

FLASH_URL = os.environ.get("CARR_FLASH_URL", "http://127.0.0.1:8000")
FLASH_MODEL = os.environ.get("CARR_FLASH_MODEL", "qwen3.8-flash-next")
MAX_TOKENS = 4096
TIMEOUT_S = 600.0
HEALTH_TIMEOUT_S = 2.0
SCRIPT_TIMEOUT_S = 1800.0
REPO = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO / "ops" / "config" / "model-routes.v1.json"
FLASH_SCRIPT = REPO / "tools" / "flash-script.py"
DATA_LINE = re.compile(r"^\s*data:\s*(\S.*?)\s*$", re.I | re.M)
TASK_LINE = re.compile(r"^\[Hermes queue (t_[A-Za-z0-9_-]+)\] ?")
# queue_dispatch.QueueDeskExecutor._prompt appends the queue's result protocol after this sentence; the script
# question is what comes before it, and this desk writes the result line itself.
PROTOCOL_MARK = "Your final non-empty line must be exactly one JSON object prefixed with CARR_QUEUE_RESULT"
MAX_SUMMARY = 480


def is_up(url: str = FLASH_URL, timeout: float = HEALTH_TIMEOUT_S) -> bool:
    """True when the Flash server answers its model list. Local only: 127.0.0.1."""
    try:
        with urllib.request.urlopen(f"{url}/v1/models", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def run_turn(task: str, *, url: str = FLASH_URL, model: str = FLASH_MODEL, max_tokens: int = MAX_TOKENS,
             timeout: float = TIMEOUT_S, opener=urllib.request.urlopen) -> dict:
    """One direct answer from Flash, thinking off. Returns {"status", "result"?, "detail"?, "finish"?}."""
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": task}],
            # the ds4 server reads Qwen3.8's chat_template_kwargs per request; thinking off took a direct turn
            # from 230 s to 6 s in the 2026-09-24 tests
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(f"{url}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with opener(req, timeout=timeout) as r:
            reply = json.load(r)
    except TimeoutError:
        return {"status": "timed_out", "detail": f"no answer in {timeout:.0f}s"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"status": "failed", "detail": f"flash unreachable: {type(exc).__name__}"}
    try:
        choice = reply["choices"][0]
        content = (choice["message"].get("content") or "").strip()
        finish = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return {"status": "failed", "detail": "flash reply was malformed"}
    if not content:
        return {"status": "failed", "detail": "no_answer", "finish": finish}
    return {"status": "completed", "result": content, "finish": finish}


def data_roots(policy_path: Path = POLICY_PATH) -> list[str]:
    """The folders a script task may name data in, from the routing policy; none when it cannot be read."""
    try:
        roots = json.loads(policy_path.read_text()).get("script_data_roots") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [os.path.realpath(os.path.expanduser(r)) for r in roots if isinstance(r, str) and r.strip()]


def _refusal(path: str, roots: list[str]) -> str | None:
    if os.path.islink(path):
        return f"{path} is a link"
    real = os.path.realpath(path)
    if not any(real != r and real.startswith(r + os.sep) for r in roots):
        return f"{path} is outside the allowed data folders"
    if not os.path.exists(real):
        return f"{path} does not exist"
    for top, dirs, files in os.walk(real):
        for name in dirs + files:
            if os.path.islink(os.path.join(top, name)):
                return f"{os.path.join(top, name)} is a link"
    return None


def script_inputs(text: str, *, roots: list[str] | None = None):
    """(paths, question, refusal) for a task's `data:` lines. No data lines: (None, text, None), not a script task.
    Any path outside the roots, missing, a link or holding a link refuses the whole task."""
    named = DATA_LINE.findall(text or "")
    question = DATA_LINE.sub("", text or "").strip()
    if not named:
        return None, question, None
    roots = data_roots() if roots is None else [os.path.realpath(os.path.expanduser(r)) for r in roots
                                                 if isinstance(r, str) and r.strip()]
    if not roots:
        return None, question, "no script data folders are configured"
    for path in named:
        refusal = _refusal(os.path.expanduser(path), roots)
        if refusal:
            return None, question, refusal
    return [os.path.expanduser(p) for p in named], question, None


def _run_flash_script(question: str, paths: list[str]):
    """tools/flash-script.py --json: (exit code, its result row, or {"detail": stderr} when it printed none)."""
    try:
        proc = subprocess.run([sys.executable, str(FLASH_SCRIPT), "--json", question, *paths],
                              capture_output=True, text=True, timeout=SCRIPT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, {"detail": f"no answer in {SCRIPT_TIMEOUT_S:.0f}s"}
    try:
        return proc.returncode, json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return proc.returncode, {"detail": (proc.stderr.strip().splitlines() or ["no output"])[-1][:300]}


def _result_line(task_id: str, outcome: str, summary: str) -> str:
    return "CARR_QUEUE_RESULT " + json.dumps({"v": 1, "task_id": task_id, "outcome": outcome,
                                              "summary": summary[:MAX_SUMMARY]}, separators=(",", ":"))


def run_task(prompt: str, *, roots: list[str] | None = None, runner=None, **turn_kwargs) -> dict:
    """The flash-local desk's entry: a queued task naming data runs the script protocol, anything else one direct
    reply (run_turn). The data is checked again here, whatever routed the task."""
    head = prompt.split(PROTOCOL_MARK, 1)[0]
    paths, question, refusal = script_inputs(head, roots=roots)
    if refusal:
        return {"status": "failed", "detail": f"script data refused: {refusal}"}
    if paths is None:
        return run_turn(prompt, **turn_kwargs)
    m = TASK_LINE.match(question)
    task_id = m.group(1) if m else None
    question = "\n".join(line for line in TASK_LINE.sub("", question).splitlines()
                          if not line.startswith("[Model Room source")).strip()
    code, row = (runner or _run_flash_script)(question, paths)
    answer = (row.get("answer") or "").strip()
    if code not in (0, 4) or (code == 0 and not answer):
        return {"status": "failed", "detail": f"flash-script: {row.get('detail') or 'no answer'}"}
    if code == 0:
        text, outcome, summary = answer, "success", f"Flash answered from the named data: {answer.splitlines()[0]}"
    else:
        desk = row.get("handoff_desk") or "the Opus desk"
        text = f"Flash's answer ({row.get('handoff')}, needs {desk}): {answer or '(none)'}"
        outcome, summary = "blocked", f"Flash's script answer was handed off ({row.get('handoff')}); needs {desk}."
    if not task_id:
        return {"status": "completed", "result": text}
    return {"status": "completed", "result": f"{text}\n{_result_line(task_id, outcome, ' '.join(summary.split()))}"}
