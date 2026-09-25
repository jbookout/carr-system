"""What a Flash-written script may import while tools/flash-script.py runs it: llm(prompt) asks the local Flash
model one question. Standard library only, since the scripts run with nothing else installed."""

from __future__ import annotations

import json
import os
import urllib.request

URL = os.environ.get("CARR_FLASH_URL", "http://127.0.0.1:8000") + "/v1/chat/completions"
MODEL = os.environ.get("CARR_FLASH_MODEL", "qwen3.8-flash-next")


def llm(prompt: str, max_tokens: int = 8192) -> str:
    """Ask the local Flash model one question and return its visible answer."""
    body = {"model": MODEL, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        reply = json.load(r)
    return (reply["choices"][0]["message"].get("content") or "").split("</think>")[-1].strip()
