"""Bounded Grok CLI retrieval through the existing authenticated subscription.

The answer's prose cannot certify a model. Only the CLI's terminal modelUsage
event does; absent, mixed or mismatched provider metadata refuses completion.
No token is loaded here, and no other provider is a fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

MODEL = "grok-4.7"
PROVIDER_MODEL = "grok-4.7-build"
EFFORT = "high"
TIMEOUT_S = 180.0
MAX_TURNS = 60


def requested_urls(task: str) -> list[str]:
    return list(dict.fromkeys(url.rstrip(".,;)]}") for url in re.findall(r"https?://[^\s<>\"']+", task)))


def public_url(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        return url.scheme in {"https", "http"} and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        return False


def retrieval_request_error(urls: list[str]) -> dict | None:
    if any(not public_url(url) for url in urls):
        return {"status": "failed", "detail": "invalid_retrieval_url",
                "next_route": "Use a public source URL without embedded credentials."}
    return None


def retrieval_prompt(urls: list[str]) -> str:
    if not urls:
        return ""
    shape = {"retrieval": {"requested_urls": urls, "sources": [{"url": urls[0],
        "text": "verbatim retrieved source text"}],
        "source_urls": urls, "unresolved_portions": [], "status": "complete or partial"}}
    return ("Return a JSON object in this shape: " + json.dumps(shape) +
        ". Each source needs its public URL and retrieved text or an existing artifact path in the artifact field. "
        "Omit artifact when no file exists. "
        "List missing thread, quoted post, linked source or media portions explicitly. "
        "Never substitute an acknowledgment or summary for the source. "
        "If the caller requires a trailing CARR_QUEUE_RESULT line, put it after the JSON object.\n\n")


def retrieval_result(text: str, urls: list[str], *, artifact_root=None) -> dict:
    if error := retrieval_request_error(urls):
        return error
    missing = {"requested_urls": urls, "sources": [], "source_urls": [],
               "unresolved_portions": urls, "status": "unusable_retrieval"}
    refusal = {"status": "failed", "detail": "unusable_retrieval", "retrieval": missing,
        "next_route": "Read the requested source through its canonical repository or browser; verify the raw source before use."}
    source_text, marker, terminal = text.rpartition("\nCARR_QUEUE_RESULT ")
    try:
        payload = json.loads(source_text if marker else text)
    except ValueError:
        return refusal
    record = payload.get("retrieval") if isinstance(payload, dict) else None
    if not isinstance(record, dict) or record.get("requested_urls") != urls:
        return refusal
    sources, source_urls, unresolved = (record.get(key) for key in
                                       ("sources", "source_urls", "unresolved_portions"))
    if (record.get("status") not in ("complete", "partial") or not isinstance(sources, list)
            or not isinstance(source_urls, list) or not all(public_url(url) for url in source_urls)
            or not isinstance(unresolved, list) or not all(isinstance(p, str) and p.strip() for p in unresolved)):
        return refusal
    evidence = []
    for source in sources:
        if not isinstance(source, dict) or source.get("url") not in source_urls:
            return refusal
        item = {key: source[key] for key in ("url", "text", "artifact") if key in source}
        if any(not isinstance(item[key], str) or not item[key].strip()
               for key in ("text", "artifact") if key in item):
            return refusal
        if "artifact" in item:
            if artifact_root is None:
                return refusal
            try:
                root = Path(artifact_root).resolve()
                path = (root / item["artifact"]).resolve()
                exists = path.is_relative_to(root) and path.is_file() and path.stat().st_size
            except (OSError, ValueError):
                exists = False
            if not exists:
                return refusal
        if not any(isinstance(item.get(key), str) and item[key].strip() for key in ("text", "artifact")):
            return refusal
        evidence.append(item)
    if not any(source["url"] in urls for source in evidence):
        return refusal
    unresolved = list(dict.fromkeys([*unresolved, *(url for url in urls
        if not any(source["url"] == url for source in evidence))]))
    retrieval = {"requested_urls": urls, "sources": evidence, "source_urls": source_urls,
        "unresolved_portions": unresolved,
        "status": "partial" if unresolved or record["status"] == "partial" else "complete"}
    result = json.dumps({"retrieval": retrieval})
    if marker:
        # The queue's existing task/capability validator owns this protocol.
        result += marker + terminal
    return {"status": "completed", "result": result, "retrieval": retrieval}


def preserve_retrieval_receipt(outcome: dict, *, task: str, effort=EFFORT,
                              max_turns=MAX_TURNS, writable=False, timeout_seconds=TIMEOUT_S) -> str:
    """Keep only adapter-derived diagnostics; never provider stderr, prompts or source text."""
    configured = os.environ.get("GROK_RUN_RECEIPT")
    if configured:
        path = Path(configured).expanduser().absolute()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    else:
        root = Path.home() / ".local/state/carr/grok-runs"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix="retrieval-", suffix=".json", dir=root)
        path = Path(name)
    receipt = {"requested_model": MODEL, "effort": effort, "status": outcome["status"],
        "task_sha256": hashlib.sha256(task.encode()).hexdigest(), "max_turns": max_turns,
        "sandbox": "workspace" if writable else "read-only", "timeout_seconds": timeout_seconds,
        "detail": outcome.get("detail"), "retrieval_status": outcome.get("retrieval", {}).get("status"),
        "diagnostic_path": str(path)}
    metadata = outcome.get("provider_metadata", {})
    if metadata.get("actual_model") == PROVIDER_MODEL:
        receipt["actual_model"] = PROVIDER_MODEL
    for key in ("model_calls", "cost_usd"):
        if type(metadata.get(key)) in (int, float):
            receipt[key] = metadata[key]
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(receipt, stream, sort_keys=True)
        stream.write("\n")
    return str(path)

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
    """Keep the latest assistant response with exactly one end.

    These events do not identify which task produced each response. Prose
    cannot establish lifecycle provenance or authorize reusing an older answer.
    The verified read-only invocation suppresses lifecycle hooks at the source.
    """
    chunks = []
    final_chunks = []
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
        elif event.get("type") == "usage":
            # A response boundary saves its chunks, including an explicitly
            # identified empty response. Older streams omit messageId, so text
            # also establishes a boundary. Accounting alone cannot erase it.
            if chunks or event.get("messageId"):
                final_chunks = chunks
                chunks = []
        elif event.get("type") == "end":
            end = event
    if chunks:
        final_chunks = chunks
    text = "".join(final_chunks)
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
    return {"text": text, "end": end, "detail": detail, "code": code}


def parse_result(stdout: str, returncode: int, *, urls: list[str] | None = None,
                 artifact_root=None, effort=EFFORT) -> dict:
    parsed = parse_stream(stdout.splitlines(), returncode)
    if parsed["code"]:
        return {"status": "failed", "detail": parsed["detail"]}
    end = parsed["end"]
    usage = end["modelUsage"][PROVIDER_MODEL]
    if not all(isinstance(end.get(key), str) and end[key].strip() for key in ("requestId", "sessionId")):
        return {"status": "failed", "detail": "grok_provider_identity_missing"}
    result = parsed["text"].strip()
    if not result and not urls:
        return {"status": "failed", "detail": "grok_empty_result"}
    # Select metadata explicitly: tool arguments, diagnostics, credentials and
    # arbitrary future envelope fields never enter the dispatch receipt.
    metadata = {"requested_model": MODEL, "actual_model": PROVIDER_MODEL,
                "effort": effort, "request_id": end["requestId"],
                "session_id": end["sessionId"], "model_calls": usage["modelCalls"],
                "cost_usd": end.get("total_cost_usd"), "stop_reason": end["stopReason"]}
    outcome = retrieval_result(result, urls, artifact_root=artifact_root) if urls else {"status": "completed", "result": result}
    return {**outcome, "provider_metadata": metadata}


def invoke_cli(prompt: str, *, cwd=None, effort=EFFORT, max_turns=MAX_TURNS,
               writable=False, timeout_seconds=TIMEOUT_S, run=subprocess.run):
    """The sole model-work invocation; the desk retains its 180-second default."""
    argv = ["grok", "--model", MODEL, "--reasoning-effort", effort,
            "--max-turns", str(max_turns), "--always-approve",
            "--sandbox", "workspace" if writable else "read-only",
            "--output-format", "streaming-json", "--print", prompt]
    env = dict(os.environ)
    env.pop("CARR_GROK_RUN_READ_ONLY", None)
    if not writable:
        env["CARR_GROK_RUN_READ_ONLY"] = "1"
    return run(argv, cwd=cwd, capture_output=True, text=True,
               stdin=subprocess.DEVNULL, timeout=timeout_seconds, env=env)


def run_task(entry: dict, task: str, *, run=subprocess.run) -> dict:
    validate_entry(entry)
    urls = requested_urls(task)
    prompt = ("Read-only retrieval or explanation only. Do not call CARR or MCP tools, "
              "read credential/config files, write files, or delegate. Return a bounded "
              "answer with public source URLs when retrieving.\n\n" + retrieval_prompt(urls) + task)
    outcome = retrieval_request_error(urls)
    if outcome is None:
        try:
            proc = invoke_cli(prompt, cwd=entry.get("cwd"), run=run)
        except FileNotFoundError:
            outcome = {"status": "failed", "detail": "grok_cli_unavailable"}
        except subprocess.TimeoutExpired:
            outcome = {"status": "timed_out", "detail": "grok_cli_timeout"}
        else:
            outcome = parse_result(proc.stdout or "", proc.returncode, urls=urls, artifact_root=entry.get("cwd"))
    if urls:
        try:
            outcome["diagnostic_path"] = preserve_retrieval_receipt(outcome, task=task)
        except OSError:
            return {"status": "failed", "detail": "grok_receipt_unavailable"}
    return outcome
