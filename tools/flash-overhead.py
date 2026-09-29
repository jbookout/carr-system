#!/usr/bin/env python3
"""Measure Flash fixed overhead without changing the installed launcher or server.

Run: python3 tools/flash-overhead.py --output out/flash-overhead.json
Requires the running ds4 server, Claude Code and its existing .claude-local config.
A temporary loopback relay records request arrival, first content, usage and
exact log offsets. Prompt bodies stay in memory; only sizes/hashes are saved.
The harness settings mirror ~/.local/bin/flash; scoped uses flash-run's tools.
Each path is run consecutively to expose cache reuse, then on a longer answer.
Prompt ablations measure token cost by replaying the captured Anthropic request
with max_tokens=1: full, tools removed, then system removed. These are marginal
rendered token costs (chat framing included), not estimates from character size.
Concurrent ds4 log intervals are marked unattributable. Use a quiet server for
comparisons. This does not flush the shared disk cache or restart the service.
MTP counters are absent on the normal server; --mtp-log reads a separately
captured --mtp-timing CLI run and records its provenance, never guesses a rate.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

MODEL = "qwen3.8-flash-next"
SHORT = "Reply with only the word PING. Do not use tools."
LONG = "List the integers from 1 through 200, separated by commas. No commentary. Do not use tools."
TOOLS = ["Bash", "Read", "Edit", "Write", "Glob", "Grep"]


def answer_valid(prompt, answer):
    if prompt == SHORT:
        return answer.strip() == "PING"
    return re.findall(r"\d+", answer) == [str(n) for n in range(1, 201)]


def usage_metrics(raw):
    """Normalize OpenAI and Anthropic JSON/SSE usage; Anthropic input excludes cache."""
    events = []
    try:
        events.append(json.loads(raw))
    except (ValueError, UnicodeDecodeError):
        for line in raw.splitlines():
            if line.startswith(b"data: ") and line != b"data: [DONE]":
                try:
                    events.append(json.loads(line[6:]))
                except ValueError:
                    pass
    usage = {}
    for event in events:
        usage.update(event.get("message", {}).get("usage", {}))
        usage.update(event.get("usage") or {})
    if "prompt_tokens" in usage:
        return {"prompt_tokens": usage["prompt_tokens"],
                "cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0)}
    if "input_tokens" in usage:
        cached = usage.get("cache_read_input_tokens", 0)
        return {"prompt_tokens": usage["input_tokens"] + cached + usage.get("cache_creation_input_tokens", 0),
                "cached_tokens": cached, "output_tokens": usage.get("output_tokens", 0)}
    return {}


def log_metrics(text):
    result = {"attributable": text.count("prompt start") <= 1 and text.count("finish=") == 1,
              "prefill_tokens": None, "prefill_s": None, "prefill_tok_s": None,
              "decode_s": None, "decode_tok_s": None, "server_total_s": None,
              "mtp_acceptance_pct": None,
              "cache_events": [line for line in text.splitlines() if "cache" in line and
                               any(x in line for x in ("hit", "miss", "evicted", "stored"))]}
    mtp = re.findall(r"(\d+) verify cycles, (\d+) drafts accepted \(([\d.]+)%\)", text)
    if mtp:
        cycles = sum(int(x[0]) for x in mtp)
        result["mtp_acceptance_pct"] = 100 * sum(int(x[1]) for x in mtp) / cycles if cycles else None
    if not result["attributable"]:
        return result
    prefill = re.findall(r"prefill chunk (\d+)/(\d+).*?avg=([\d.]+) t/s ([\d.]+)s", text)
    if prefill:
        result.update(prefill_tokens=int(prefill[-1][1]), prefill_tok_s=float(prefill[-1][2]))
    done = re.findall(r"prompt done ([\d.]+)s", text)
    decode = re.findall(r"decoding chunk=[\d.]+ t/s avg=([\d.]+) t/s ([\d.]+)s", text)
    finish = re.findall(r"finish=\w+ ([\d.]+)s", text)
    if done:
        result["prefill_s"] = float(done[-1])
    if decode:
        result.update(decode_tok_s=float(decode[-1][0]), decode_s=float(decode[-1][1]))
    if finish:
        result["server_total_s"] = float(finish[-1])
    return result


def log_position(path):
    return path.stat().st_size if path.exists() else 0


def log_slice(path, offset):
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        stream.seek(offset)
        return stream.read().decode(errors="replace")


def digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def request(url, path, body):
    target = urlsplit(url)
    conn = http.client.HTTPConnection(target.hostname, target.port, timeout=180)
    conn.request("POST", path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        response = conn.getresponse()
        raw = response.read()
        if response.status != 200:
            raise RuntimeError(f"ds4 HTTP {response.status}: {raw[:200]!r}")
        return raw
    finally:
        conn.close()


def relay(url, log):
    target = urlsplit(url)
    rows = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.forward()

        def do_GET(self):
            self.forward()

        def forward(self):
            arrived = time.monotonic()
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            offset = log_position(log)
            conn = http.client.HTTPConnection(target.hostname, target.port, timeout=180)
            raw, first_content = bytearray(), None
            try:
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() not in ("host", "connection", "accept-encoding")}
                conn.request(self.command, self.path, body=body, headers=headers)
                response = conn.getresponse()
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in ("connection", "transfer-encoding"):
                        self.send_header(key, value)
                self.end_headers()
                while chunk := response.read1(65536):
                    raw.extend(chunk)
                    if first_content is None and b'"text_delta"' in raw:
                        first_content = time.monotonic()
                    self.wfile.write(chunk)
                    self.wfile.flush()
                completed = time.monotonic()
                if self.command == "POST" and urlsplit(self.path).path in ("/v1/messages", "/v1/chat/completions"):
                    parsed = json.loads(body)
                    rows.append({"path": self.path, "body": parsed, "arrival": arrived,
                                 "first_content_s": first_content - arrived if first_content else None,
                                 "http_s": completed - arrived, "http_status": response.status,
                                 "usage": usage_metrics(raw), "ds4": log_metrics(log_slice(log, offset)),
                                 "log_start_byte": offset, "log_end_byte": log_position(log)})
            finally:
                conn.close()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, rows


def measure_harness(mode, prompt, url, log, cwd):
    server, requests = relay(url, log)
    env = dict(os.environ, CLAUDE_CONFIG_DIR=os.path.expanduser("~/.claude-local"),
               ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}", ANTHROPIC_API_KEY="local",
               ANTHROPIC_MODEL=MODEL, ANTHROPIC_SMALL_FAST_MODEL=MODEL,
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", MAX_THINKING_TOKENS="0")
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    argv = [shutil.which("claude") or "claude", "--strict-mcp-config", "--model", MODEL,
            "-p", prompt, "--output-format", "json", "--max-turns", "1", "--no-session-persistence"]
    if mode == "scoped":
        argv += ["--tools", *TOOLS, "--effort", "low"]
    started = time.monotonic()
    try:
        process = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, timeout=240)
        total = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
    if process.returncode != 0 or not requests:
        raise RuntimeError(f"harness failed ({process.returncode}): {process.stderr[-400:]!r}")
    result = json.loads(process.stdout)
    valid = not result.get("is_error") and answer_valid(prompt, result.get("result", ""))
    first = requests[0]
    bodies = [(row["path"], row.pop("body")) for row in requests]
    for row, (_, body) in zip(requests, bodies):
        row.update(system=digest(body.get("system", "")), tools=digest(body.get("tools", [])),
                   tool_count=len(body.get("tools", [])), requested_model=body.get("model"))
    startup = first["arrival"] - started
    for row in requests:
        row.pop("arrival")
    return {"mode": mode, "answer_valid": valid, "answer": result.get("result", ""),
            "harness_error": result.get("is_error", False), "total_s": total, "startup_to_request_s": startup,
            "harness_tail_s": total - startup - sum(row["http_s"] for row in requests),
            "requests": requests}, bodies[0]


def measure_direct(prompt, url, log):
    body = {"model": MODEL, "max_tokens": 2048, "messages": [{"role": "user", "content": prompt}],
            "chat_template_kwargs": {"enable_thinking": False}}
    offset, started = log_position(log), time.monotonic()
    raw = request(url, "/v1/chat/completions", body)
    total = time.monotonic() - started
    reply = json.loads(raw)
    choice = reply["choices"][0]
    if choice.get("finish_reason") != "stop" or not choice["message"].get("content"):
        raise RuntimeError("direct answer is empty or incomplete")
    return {"mode": "direct", "answer_valid": answer_valid(prompt, choice["message"]["content"]),
            "answer": choice["message"]["content"], "total_s": total, "answered_model": reply.get("model"),
            "usage": usage_metrics(raw), "ds4": log_metrics(log_slice(log, offset)),
            "log_start_byte": offset, "log_end_byte": log_position(log)}


def ablate(path, original, url):
    body = copy.deepcopy(original)
    body.update(max_tokens=1, stream=False, thinking={"type": "disabled"})
    body.pop("stream_options", None)
    results = []
    for label in ("full", "without_tools", "without_system"):
        if label == "without_tools":
            body.pop("tools", None)
            body.pop("tool_choice", None)
        elif label == "without_system":
            body.pop("system", None)
        results.append({"variant": label, **usage_metrics(request(url, path, body))})
    if any("prompt_tokens" not in row for row in results):
        raise RuntimeError("token ablation returned no usage")
    return {"observations": results, "tool_tokens_marginal": results[0]["prompt_tokens"] - results[1]["prompt_tokens"],
            "system_tokens_marginal": results[1]["prompt_tokens"] - results[2]["prompt_tokens"]}


def measure_mtp(cli, weights):
    """Bounded native diagnostic after HTTP timings, with its own scratch lock.

    The supported DS4_LOCK_FILE setting leaves the live server's lock intact.
    Model mmap pages can be shared; the small context bounds extra KV/buffers.
    Results apply only to this greedy repetitive prompt at ctx=2048.
    """
    argv = [str(cli), "--model", str(weights), "--ctx", "2048", "--mtp-timing", "--nothink",
            "--temp", "0", "--tokens", "1024", "-p", LONG]
    with tempfile.TemporaryDirectory(prefix="flash-mtp-") as scratch:
        env = dict(os.environ, DS4_LOCK_FILE=str(Path(scratch) / "ds4.lock"))
        started = time.monotonic()
        process = subprocess.run(argv, cwd=cli.parent, env=env, capture_output=True, timeout=120)
        elapsed = time.monotonic() - started
    log = process.stderr.decode(errors="replace")
    counts = re.findall(r"(\d+) verify cycles, (\d+) drafts accepted", log)
    if process.returncode or not counts:
        raise RuntimeError(f"MTP diagnostic failed ({process.returncode}): {log[-400:]}")
    return {"acceptance_pct": log_metrics(log)["mtp_acceptance_pct"],
            "verify_cycles": sum(int(x[0]) for x in counts),
            "drafts_accepted": sum(int(x[1]) for x in counts), "elapsed_s": elapsed,
            "argv": argv, "log_sha256": hashlib.sha256(process.stderr).hexdigest(),
            "scope": "separate native CLI; greedy repetitive counting; ctx=2048; not live-server per-request telemetry"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--log", type=Path, default=Path.home() / "Library/Logs/ds4-flash-next.log")
    parser.add_argument("--cwd", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mtp-log", type=Path)
    parser.add_argument("--mtp-cli", type=Path, help="optional native ds4 binary for bounded MTP diagnostic")
    parser.add_argument("--mtp-weights", type=Path, help="the same GGUF served by the running ds4 server")
    parser.add_argument("--mtp-only", action="store_true", help="only run the separate MTP diagnostic")
    args = parser.parse_args()
    if bool(args.mtp_cli) != bool(args.mtp_weights) or (args.mtp_only and not args.mtp_cli):
        parser.error("--mtp-cli and --mtp-weights are required together; --mtp-only needs both")
    if args.mtp_only:
        result = measure_mtp(args.mtp_cli.resolve(), args.mtp_weights.resolve())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(args.output)
        return
    target = urlsplit(args.url)
    if (target.scheme != "http" or target.hostname not in ("127.0.0.1", "::1", "localhost")
            or target.port != 8000 or target.path or target.query or target.fragment
            or target.username is not None or args.repeats < 2):
        parser.error("use a loopback ds4 origin on port 8000 and at least two repeats")
    result = {"schema": "flash-overhead/v1", "at": datetime.now(timezone.utc).isoformat(),
              "url": args.url, "model": MODEL, "thinking": "off", "cwd": args.cwd,
              "ds4_log": str(args.log), "runs": [], "ablations": {},
              "cache_policy": "existing shared disk/live cache; no flush or restart",
              "mtp": {"acceptance_pct": None, "reason": "running server lacks --mtp-timing"}}
    # Save each completed observation; an interrupted run is never silently replayed.
    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    for mode in ("full", "scoped", "direct"):
        for index, prompt in enumerate([SHORT] * args.repeats + [LONG]):
            print(f"measuring {mode} {index + 1}", flush=True)
            if mode == "direct":
                row = measure_direct(prompt, args.url, args.log)
            else:
                row, captured = measure_harness(mode, prompt, args.url, args.log, args.cwd)
            row["case"] = "short" if index < args.repeats else "long"
            result["runs"].append(row)
            save()
        if mode != "direct":
            print(f"token ablations {mode}", flush=True)
            result["ablations"][mode] = ablate(*captured, args.url)
            save()
    if args.mtp_log:
        text = args.mtp_log.read_text()
        result["mtp"] = {"acceptance_pct": log_metrics(text)["mtp_acceptance_pct"],
                         "source": str(args.mtp_log), "sha256": hashlib.sha256(text.encode()).hexdigest(),
                         "scope": "separate --mtp-timing run; not a live-server per-request counter"}
    if args.mtp_cli:
        result["mtp"] = measure_mtp(args.mtp_cli.resolve(), args.mtp_weights.resolve())
    result["all_answers_valid"] = all(row["answer_valid"] for row in result["runs"])
    save()
    print(args.output)


if __name__ == "__main__":
    main()
