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
  - Stop-hook inputs: type == "system", subtype == "stop_hook_summary", and
    attachment.type in ("hook_success", "hook_additional_context") with
    hookName/hookEvent == "Stop".
  - Agent tool_use prompts are NOT extracted from real transcripts at all
    (coordinator review, 2026-09-24, repo is public and prompts carry
    client/deal prose no pattern scan can be trusted to fully catch).
    ops/fixtures/real-replay/agent-prompts.jsonl is a fixed, hand-written
    SYNTHETIC_AGENT_PROMPTS set derived from real *structure* only
    (description + subagent_type + a generic instruction shape).

DROP-WHOLE-RECORD, checked against ops/business_data_patterns.py's shared
pattern set, against the RAW source line first (before any extraction) and
again against every constructed row (belt-and-suspenders): a match drops the
entire record, never a partial redaction of just the matched span. Patterns
cover dollar amounts, sq ft / $/SF, street addresses, practice/clinic naming
("Dr.", DDS, DMD, clinic, dental, practice), lease terms, phone numbers,
emails, hostnames, secret-shaped strings, UUID-shaped tokens, and CARR client
reference ids (L-/C-/V-/D- prefixed).

REDACTION (applied on TOP of the drop-whole-record check, to the rows that
survive it) additionally scrubs, in place, email addresses -> [EMAIL],
hostnames -> [HOST], and secret-marker tokens -> [SECRET] -- a second layer,
not a substitute for the drop above. Command SHAPE is deliberately preserved:
`git add -A`, path arguments, flags are all kept, since the false-denial
defect (PR #1225) depended on exact argument shapes surviving redaction.

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ops import business_data_patterns  # noqa: E402

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


# Agent tool_use prompts are NOT extracted from real transcripts at all
# (coordinator review, 2026-09-24): they carry client/deal context in prose
# that no pattern scan can be trusted to catch completely, and the gates only
# need SHAPE, not content. These are a fixed, hand-written set derived from
# real *structure* (description + subagent_type + a generic instruction
# shape), never from real prompt text.
SYNTHETIC_AGENT_PROMPTS = [
    {
        "kind": "agent_prompt", "synthetic": True,
        "description": "Investigate a defect across the codebase",
        "subagent_type": "general-purpose",
        "prompt_trimmed": "Investigate defect X across N files in the repo and report the root cause and a fix plan. Under 200 words.",
    },
    {
        "kind": "agent_prompt", "synthetic": True,
        "description": "Search for a symbol or pattern",
        "subagent_type": "Explore",
        "prompt_trimmed": "Find where symbol X is defined and every file that references it; report file paths and line numbers only.",
    },
    {
        "kind": "agent_prompt", "synthetic": True,
        "description": "Run and validate a test suite",
        "subagent_type": "general-purpose",
        "prompt_trimmed": "Run the relevant selftest suite for change Y, fix any failures, and report pass/fail counts and remaining blockers.",
    },
    {
        "kind": "agent_prompt", "synthetic": True,
        "description": "Draft a PR for a scoped fix",
        "subagent_type": "general-purpose",
        "prompt_trimmed": "Implement fix Z on branch B, run the local CI gates class, commit, push, and open a PR with a summary and test plan.",
    },
    {
        "kind": "agent_prompt", "synthetic": True,
        "description": "Review a diff for correctness",
        "subagent_type": "general-purpose",
        "prompt_trimmed": "Review the diff since commit C for correctness bugs and report findings ranked by severity, most severe first.",
    },
]


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


def _drop_whole_row(row_candidates: list) -> bool:
    """True if ANY candidate row built from one source record trips a
    business-data pattern -- the whole record is then dropped, none of its
    rows are kept. Checked against the JSON-serialized row (post-redaction),
    never partially redacted through: a match here means drop, not patch."""
    for row in row_candidates:
        if business_data_patterns.has_business_data(json.dumps(row, sort_keys=True)):
            return True
    return False


def run(transcript_glob: str, out_dir: Path, max_bash: int, max_hooks: int, max_agent: int, max_stop: int):
    bash_rows, hook_rows, stop_rows = [], [], []
    dropped = 0
    paths = sorted(glob.glob(transcript_glob))
    for path in paths:
        if (len(bash_rows) >= max_bash and len(hook_rows) >= max_hooks
                and len(stop_rows) >= max_stop):
            break
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    # NOTE: the raw transcript line is NOT scanned whole --
                    # every line carries envelope UUIDs (parentUuid,
                    # sessionId, message ids) that are routine bookkeeping,
                    # not business data, and scanning the raw line against
                    # UUID_RE with no exemption drops essentially every
                    # record (measured: 46,360 of 46,365 lines on this data
                    # set). Instead, the DROP check runs against the
                    # CONSTRUCTED candidate row below -- the actual fields
                    # this script would write -- which is where a business
                    # value (a dollar figure inside a Bash command, a client
                    # ref inside hook advisory text) would actually surface.
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    candidates_bash, candidates_hooks, candidates_stop = [], [], []
                    if len(bash_rows) < max_bash:
                        extract_bash(record, candidates_bash)
                    if len(hook_rows) < max_hooks:
                        extract_hook_context(record, candidates_hooks)
                    if len(stop_rows) < max_stop:
                        extract_stop_hook(record, candidates_stop)
                    # Belt-and-suspenders: also check the CONSTRUCTED rows
                    # (post-redaction) -- a match here still means drop the
                    # row whole, never patch it further.
                    for bucket_rows, candidates in (
                        (bash_rows, candidates_bash),
                        (hook_rows, candidates_hooks),
                        (stop_rows, candidates_stop),
                    ):
                        for row in candidates:
                            if business_data_patterns.has_business_data(json.dumps(row, sort_keys=True)):
                                dropped += 1
                                continue
                            bucket_rows.append(row)
        except OSError as err:
            print(f"extract-real-replay: skipping {path}: {err}", file=sys.stderr)
            continue

    agent_rows = list(SYNTHETIC_AGENT_PROMPTS)[:max_agent]

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
    print(f"extract-real-replay: dropped {dropped} whole records for business-data patterns", file=sys.stderr)
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
