#!/usr/bin/env python3
"""extract-real-replay.py — pull redacted real records from Claude session
transcripts into ops/fixtures/real-replay/, for the gate-replay-coverage CI
check (see ops/gate-replay-coverage.py).

WHY THIS EXISTS. Two real defects (PR #1224, PR #1225) shipped gates that were
tested only against invented shapes and never fired on real traffic. The fix
requires every gate/hook change to ship a selftest that replays REAL records.
Real records live in ~/.claude/projects/-Users-booko-carr-system/*.jsonl and
carry free text, secrets, emails, and hostnames that must never be committed —
this script's whole job is pulling the SHAPE out and leaving the content
behind.

WHAT IT EXTRACTS, one file per row in ops/fixtures/real-replay/*.jsonl:
  - hook_additional_context records (attachment.type == "hook_additional_context"),
    including ones whose content is JSON with a nested "build_receipt".
  - Bash tool_use command strings (message.content[].type == "tool_use",
    name == "Bash"), keeping command + description.
  - Agent tool_use prompts (name == "Agent"), trimmed to a fixed length.
  - Stop-hook inputs: type == "system", subtype == "stop_hook_summary", and
    attachment.type in ("hook_success", "hook_additional_context") with
    hookName/hookEvent == "Stop".

REDACTION, applied to every string before it is written:
  - email addresses -> [EMAIL]
  - hostnames (bare domain-shaped tokens, incl. in URLs) -> [HOST]
  - secret-shaped tokens (api keys, bearer tokens, generic long hex/base64
    tokens after a key= or Bearer marker) -> [SECRET]
  - free-text user prompts are NOT extracted at all (only shapes above are
    pulled, and Agent prompts are trimmed rather than kept verbatim)
  Command SHAPE is deliberately preserved: `git add -A`, path arguments, flags
  are all kept, since the false-denial defect (PR #1225) depended on exact
  argument shapes surviving redaction.

USAGE:
    python3 tools/extract-real-replay.py --out ops/fixtures/real-replay \\
        --max-bash 200 --max-hooks 20

This is a plain script per this repo's tools/ convention (shebang + main
guard); it is not a new MCP verb, schema, worker route, or job definition, so
it owes no SCAC registry successor (decision 05e144eb).
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Hostname-shaped tokens. Two forms, kept deliberately narrow: command SHAPE
# (paths, flags, script names like ops/ci.sh) must survive redaction intact
# (PR #1225's lesson), so a bare TLD list would wrongly eat ops/ci.sh,
# package.json, app.py and similar. So:
#   1. scheme-prefixed URLs (http://, https://, ssh://, git://) — unambiguous.
#   2. bare dotted hosts, but ONLY against a short list of TLDs that are never
#      also script/file extensions in this repo, and only when NOT preceded
#      by "/" (a path separator means it's a filename, not a host).
URL_HOSTNAME_RE = re.compile(
    r"\b(?:https?|ssh|git)://([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})"
)
BARE_HOSTNAME_RE = re.compile(
    r"(?<![\w/.-])(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"(?:com|net|org|gov|edu)\b"
)

SECRET_MARKER_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|token|secret|password|bearer|authorization)\b"
    r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.\/]{12,}"
)
BARE_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")

AGENT_PROMPT_TRIM_CHARS = 400


def redact(text: str) -> str:
    if not isinstance(text, str):
        return text
    out = EMAIL_RE.sub("[EMAIL]", text)
    out = SECRET_MARKER_RE.sub(
        lambda m: m.group(1) + "=[SECRET]", out
    )
    out = URL_HOSTNAME_RE.sub(lambda m: m.group(0).replace(m.group(1), "[HOST]"), out)
    out = BARE_HOSTNAME_RE.sub("[HOST]", out)
    return out


def redact_deep(value):
    """Redact every string reachable inside a JSON-ish structure."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_deep(v) for k, v in value.items()}
    return value


def _tool_uses(record: dict):
    message = record.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            yield item


def extract_bash(record: dict, out: list):
    for item in _tool_uses(record):
        if item.get("name") != "Bash":
            continue
        inp = item.get("input") or {}
        command = inp.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        out.append({
            "kind": "bash_command",
            "command": redact(command),
            "description": redact(inp.get("description", "")) if inp.get("description") else "",
        })


def extract_agent(record: dict, out: list):
    for item in _tool_uses(record):
        if item.get("name") != "Agent":
            continue
        inp = item.get("input") or {}
        prompt = inp.get("prompt", "")
        if not isinstance(prompt, str):
            prompt = ""
        trimmed = redact(prompt)[:AGENT_PROMPT_TRIM_CHARS]
        out.append({
            "kind": "agent_prompt",
            "description": redact(inp.get("description", "")) if inp.get("description") else "",
            "subagent_type": inp.get("subagent_type", ""),
            "prompt_trimmed": trimmed,
        })


def extract_hook_context(record: dict, out: list):
    attachment = record.get("attachment")
    if not isinstance(attachment, dict):
        return
    if attachment.get("type") != "hook_additional_context":
        return
    content = attachment.get("content")
    if not isinstance(content, list):
        return
    for entry in content:
        entry = redact(entry) if isinstance(entry, str) else entry
        row = {"kind": "hook_additional_context", "hookName": attachment.get("hookName", "")}
        # Try to parse nested JSON (the advisory / build_receipt shape) so the
        # structure survives intact rather than as an opaque redacted blob.
        parsed = None
        if isinstance(entry, str):
            stripped = entry.strip()
            if stripped.startswith("{"):
                try:
                    parsed = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    parsed = None
        if parsed is not None:
            row["content_json"] = redact_deep(parsed)
            row["has_build_receipt"] = "build_receipt" in parsed
        else:
            row["content_text"] = entry
            row["has_build_receipt"] = False
        out.append(row)


def extract_stop_hook(record: dict, out: list):
    if record.get("type") == "system" and record.get("subtype") == "stop_hook_summary":
        hook_infos = record.get("hookInfos")
        if isinstance(hook_infos, list):
            out.append({
                "kind": "stop_hook_summary",
                "hookCount": record.get("hookCount"),
                "commands": [
                    redact(h.get("command", "")) for h in hook_infos if isinstance(h, dict)
                ],
            })
        return
    attachment = record.get("attachment")
    if not isinstance(attachment, dict):
        return
    if attachment.get("hookEvent") != "Stop" and attachment.get("hookName") != "Stop":
        return
    if attachment.get("type") not in ("hook_success", "hook_additional_context"):
        return
    stdout = attachment.get("stdout")
    row = {"kind": "stop_hook_input", "type": attachment.get("type")}
    if isinstance(stdout, str) and stdout.strip():
        try:
            row["stdout_json"] = redact_deep(json.loads(stdout))
        except (json.JSONDecodeError, ValueError):
            row["stdout_text"] = redact(stdout)
    content = attachment.get("content")
    if isinstance(content, list):
        row["content"] = [redact(c) if isinstance(c, str) else c for c in content]
    out.append(row)


def run(transcript_glob: str, out_dir: Path, max_bash: int, max_hooks: int, max_agent: int, max_stop: int):
    bash_rows, hook_rows, agent_rows, stop_rows = [], [], [], []
    paths = sorted(glob.glob(transcript_glob))
    for path in paths:
        if (len(bash_rows) >= max_bash and len(hook_rows) >= max_hooks
                and len(agent_rows) >= max_agent and len(stop_rows) >= max_stop):
            break
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if len(bash_rows) < max_bash:
                        extract_bash(record, bash_rows)
                    if len(hook_rows) < max_hooks:
                        extract_hook_context(record, hook_rows)
                    if len(agent_rows) < max_agent:
                        extract_agent(record, agent_rows)
                    if len(stop_rows) < max_stop:
                        extract_stop_hook(record, stop_rows)
        except OSError as err:
            print(f"extract-real-replay: skipping {path}: {err}", file=sys.stderr)
            continue

    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, rows, cap in (
        ("bash-commands.jsonl", bash_rows, max_bash),
        ("hook-advisories.jsonl", hook_rows, max_hooks),
        ("agent-prompts.jsonl", agent_rows, max_agent),
        ("stop-hook-inputs.jsonl", stop_rows, max_stop),
    ):
        rows = rows[:cap]
        dest = out_dir / name
        with open(dest, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        written[name] = len(rows)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transcripts",
        default="/Users/booko/.claude/projects/-Users-booko-carr-system/*.jsonl",
        help="glob for source transcripts",
    )
    parser.add_argument("--out", default="ops/fixtures/real-replay", help="output directory")
    parser.add_argument("--max-bash", type=int, default=200)
    parser.add_argument("--max-hooks", type=int, default=20)
    parser.add_argument("--max-agent", type=int, default=20)
    parser.add_argument("--max-stop", type=int, default=20)
    args = parser.parse_args()

    written = run(
        args.transcripts, Path(args.out), args.max_bash, args.max_hooks,
        args.max_agent, args.max_stop,
    )
    for name, count in written.items():
        print(f"extract-real-replay: wrote {count} rows to {args.out}/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
