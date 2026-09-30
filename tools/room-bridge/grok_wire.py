"""Bounded Grok CLI retrieval through the existing authenticated subscription.

The answer's prose cannot certify a model. Only the CLI's terminal modelUsage
event does; absent, mixed or mismatched provider metadata refuses completion.
No token is loaded here, and no other provider is a fallback.
"""

from __future__ import annotations

import json
import subprocess

MODEL = "grok-4.7"
PROVIDER_MODEL = "grok-4.7-build"
EFFORT = "high"
TIMEOUT_S = 180.0
MAX_TURNS = 60


def validate_entry(entry: dict) -> None:
    if (entry.get("model") != MODEL or entry.get("effort") != EFFORT
            or entry.get("sandbox") != "read-only"):
        raise ValueError("Grok retrieval requires grok-4.7/high/read-only")


def model_usage_error(models) -> str | None:
    """Validate the complete provider model set and evidence of a model call."""
    if not isinstance(models, dict) or set(models) != {PROVIDER_MODEL}:
        return "grok_provider_model_mismatch"
    usage = models[PROVIDER_MODEL]
    if not isinstance(usage, dict) or type(usage.get("modelCalls")) is not int or usage["modelCalls"] < 1:
        return "grok_provider_usage_invalid"
    return None


def parse_stream(lines, returncode: int = 0) -> dict:
    """Read one stream ending in exactly one end; never join post-end text."""
    chunks = []
    end: dict = {}
    detail = None
    for line in lines:
        if not line.strip():
            continue
        if end:
            detail = "grok_terminal_event_missing_or_failed"
            break
        try:
            event = json.loads(line)
        except ValueError:
            detail = "grok_invalid_stream"
            break
        if not isinstance(event, dict):
            detail = "grok_invalid_stream"
            break
        if event.get("type") == "error":
            detail = "grok_error_event"
            break
        if event.get("type") == "text":
            if not isinstance(event.get("data"), str):
                detail = "grok_invalid_stream"
                break
            chunks.append(event["data"])
        elif event.get("type") == "end":
            end = event
    code = 0
    if detail:
        end = {**end, "stopReason": "invalid_stream"}
        code = 4
    elif returncode != 0 or not end:
        detail, code = "grok_terminal_event_missing_or_failed", 4
    elif end.get("stopReason") != "end_turn":
        detail, code = "grok_incomplete_turn", 4
    else:
        detail = model_usage_error(end.get("modelUsage"))
        if detail:
            code = 5
    return {"text": "".join(chunks), "end": end, "detail": detail, "code": code}


def parse_result(stdout: str, returncode: int) -> dict:
    parsed = parse_stream(stdout.splitlines(), returncode)
    if parsed["code"]:
        return {"status": "failed", "detail": parsed["detail"]}
    end = parsed["end"]
    usage = end["modelUsage"][PROVIDER_MODEL]
    if not all(isinstance(end.get(key), str) and end[key].strip() for key in ("requestId", "sessionId")):
        return {"status": "failed", "detail": "grok_provider_identity_missing"}
    result = parsed["text"].strip()
    if not result:
        return {"status": "failed", "detail": "grok_empty_result"}
    # Select metadata explicitly: tool arguments, diagnostics, credentials and
    # arbitrary future envelope fields never enter the dispatch receipt.
    metadata = {"requested_model": MODEL, "actual_model": PROVIDER_MODEL,
                "effort": EFFORT, "request_id": end["requestId"],
                "session_id": end["sessionId"], "model_calls": usage["modelCalls"],
                "cost_usd": end.get("total_cost_usd"), "stop_reason": end["stopReason"]}
    return {"status": "completed", "result": result, "provider_metadata": metadata}


def invoke_cli(prompt: str, *, cwd=None, effort=EFFORT, max_turns=MAX_TURNS,
               writable=False, run=subprocess.run):
    """The sole model-work invocation, bounded identically for both adapters."""
    argv = ["grok", "--model", MODEL, "--reasoning-effort", effort,
            "--max-turns", str(max_turns), "--always-approve",
            "--sandbox", "workspace" if writable else "read-only",
            "--output-format", "streaming-json", "--print", prompt]
    return run(argv, cwd=cwd, capture_output=True, text=True,
               stdin=subprocess.DEVNULL, timeout=TIMEOUT_S)


def run_task(entry: dict, task: str, *, run=subprocess.run) -> dict:
    validate_entry(entry)
    prompt = ("Read-only retrieval or explanation only. Do not call CARR or MCP tools, "
              "read credential/config files, write files, or delegate. Return a bounded "
              "answer with public source URLs when retrieving.\n\n" + task)
    try:
        proc = invoke_cli(prompt, cwd=entry.get("cwd"), run=run)
    except FileNotFoundError:
        return {"status": "failed", "detail": "grok_cli_unavailable"}
    except subprocess.TimeoutExpired:
        return {"status": "timed_out", "detail": "grok_cli_timeout"}
    return parse_result(proc.stdout or "", proc.returncode)
