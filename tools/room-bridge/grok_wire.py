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


def parse_result(stdout: str, returncode: int) -> dict:
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    ends = [event for event in events if event.get("type") == "end"]
    if returncode != 0 or len(ends) != 1 or events[-1].get("type") != "end":
        return {"status": "failed", "detail": "grok_terminal_event_missing_or_failed"}
    end = ends[0]
    models = end.get("modelUsage")
    if not isinstance(models, dict) or set(models) != {PROVIDER_MODEL}:
        return {"status": "failed", "detail": "grok_provider_model_mismatch"}
    usage = models[PROVIDER_MODEL]
    if not isinstance(usage, dict) or not isinstance(usage.get("modelCalls"), int) or usage["modelCalls"] < 1:
        return {"status": "failed", "detail": "grok_provider_usage_invalid"}
    if end.get("stopReason") != "end_turn":
        return {"status": "failed", "detail": "grok_incomplete_turn"}
    if any(event.get("type") == "error" for event in events):
        return {"status": "failed", "detail": "grok_error_event"}
    if not all(isinstance(end.get(key), str) and end[key].strip() for key in ("requestId", "sessionId")):
        return {"status": "failed", "detail": "grok_provider_identity_missing"}
    result = "".join(event["data"] for event in events
                     if event.get("type") == "text" and isinstance(event.get("data"), str)).strip()
    if not result:
        return {"status": "failed", "detail": "grok_empty_result"}
    # Select metadata explicitly: tool arguments, diagnostics, credentials and
    # arbitrary future envelope fields never enter the dispatch receipt.
    metadata = {"requested_model": MODEL, "actual_model": PROVIDER_MODEL,
                "effort": EFFORT, "request_id": end["requestId"],
                "session_id": end["sessionId"], "model_calls": usage["modelCalls"],
                "cost_usd": end.get("total_cost_usd"), "stop_reason": end["stopReason"]}
    return {"status": "completed", "result": result, "provider_metadata": metadata}


def run_task(entry: dict, task: str, *, run=subprocess.run) -> dict:
    validate_entry(entry)
    prompt = ("Read-only retrieval or explanation only. Do not call CARR or MCP tools, "
              "read credential/config files, write files, or delegate. Return a bounded "
              "answer with public source URLs when retrieving.\n\n" + task)
    argv = ["grok", "--model", MODEL, "--reasoning-effort", EFFORT,
            "--max-turns", str(MAX_TURNS), "--always-approve", "--sandbox", "read-only",
            "--output-format", "streaming-json", "--print", prompt]
    try:
        proc = run(argv, cwd=entry.get("cwd"), capture_output=True, text=True,
                   stdin=subprocess.DEVNULL, timeout=TIMEOUT_S)
    except FileNotFoundError:
        return {"status": "failed", "detail": "grok_cli_unavailable"}
    except subprocess.TimeoutExpired:
        return {"status": "timed_out", "detail": "grok_cli_timeout"}
    return parse_result(proc.stdout or "", proc.returncode)
