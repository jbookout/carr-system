"""The flash-local desk: a Model Room desk that answers on the local Flash model itself.

Until 2026-09-24 the queue could reach Claude and Codex desks and Joe, but nothing ran work on Flash: the desk
registered as "flash" was a Claude session with that name. Joe's routing rulings (decisions 81f6bcf7, 79110363) send
direct questions to Flash first, so Flash needs a desk the queue can hand a task to.

This desk runs Flash's direct protocol from ops/config/model-routes.v1.json: one reply, thinking off, a bounded
answer length. It is synchronous like a codex-session desk and returns the same outcome shape, so bridge.deliver and
the queue executor treat it the same way. Code and script work do not come here: they have their own protocols
(flash-run; the script harness).

A reply with no answer comes back as status "failed" with detail "no_answer", so the task is blocked visibly
rather than completed empty; the route's `then` desk is where it goes next.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

FLASH_URL = os.environ.get("CARR_FLASH_URL", "http://127.0.0.1:8000")
FLASH_MODEL = os.environ.get("CARR_FLASH_MODEL", "qwen3.8-flash-next")
MAX_TOKENS = 4096
TIMEOUT_S = 600.0
HEALTH_TIMEOUT_S = 2.0


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
