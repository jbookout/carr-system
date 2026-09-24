"""lib/jev_server_receipts.py — read a session's Jev call receipts from the
SERVER rather than from files the gated model writes as a matter of course.

THIS IS A LIBRARY, ON PURPOSE: no shebang and no main guard (see the header of
ops/typesafe_client.py for why that matters to the sealed source inventory).

WHY (2026-09-24). A reviewer bypassed every Jev gate by appending one forged
JSON record to a file the gated model can write: the session transcript, or
out/jev-calls.jsonl. The model's shell runs as the same OS user as the hooks,
so no local file, key or daemon can be trusted for credit. Every production
Jev call now goes through the Worker's `ask-jev` verb, which makes the vendor
call itself and appends a server-timestamped row (migration 0587) before it
returns the answers. This module reads those rows back through the Worker's
`read-jev-call-receipts` verb, over the same zero-install HTTPS door every
local caller uses (mcp-server/local-verb.mjs, which holds the LOCAL_TOKENS
bearer; this module never sees it). The read door returns only rows written by
the calling principal and matched by their ask-jev tool_call row.

DETECTABLE, NOT PREVENTED. The model's OS user can read the database owner
credential, so it can forge, backdate or delete rows directly; a forged row
without its tool_call twin is never returned, and `./run.sh health` flags it.

NEVER RAISES, NEVER GUESSES. Returns {"status": "ok", "receipts": [...],
"server_now": ...} or {"status": "unreachable", "reason": <category>}. An
unreachable server is NOT "no receipts": callers must treat every facet as
unverified and say so loudly (lib/jev_required_actions.py).

TEST SEAM. CARR_JEV_SERVER_RECEIPTS_FIXTURE names a JSON file holding the
exact object this function would return; the offline selftests use it. Same
shape and posture as completion-evidence-gate.py's CARR_JEV_CALLS_LOG_OVERRIDE:
hooks inherit the harness's environment, not the model's shell's.
"""

import json
import os
import shutil
import subprocess

READ_VERB = "read-jev-call-receipts"
FIXTURE_ENV = "CARR_JEV_SERVER_RECEIPTS_FIXTURE"
DEFAULT_TIMEOUT_SECONDS = 12.0
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _canonical_repo_root(fallback):
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=fallback, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        if out:
            return os.path.dirname(out)
    except Exception:
        pass
    return fallback


def _local_verb_script():
    own = os.path.join(REPO, "mcp-server", "local-verb.mjs")
    if os.path.isfile(own):
        return own
    candidate = os.path.join(_canonical_repo_root(REPO), "mcp-server", "local-verb.mjs")
    return candidate if os.path.isfile(candidate) else None


def _node_binary():
    for candidate in (shutil.which("node"), "/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _category(stderr):
    text = stderr or ""
    for marker, category in (
            ('"unknown_tool"', "verb_not_deployed"),
            ("could not reach the deployed Worker", "worker_unreachable"),
            ("no MCP token", "local_token_missing"),
            ("refusing MCP token file", "local_token_insecure")):
        if marker in text:
            return category
    return "server_read_failed"


SESSION_ENV_KEYS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID", "CODEX_THREAD_ID")


def session_ids_for(payload_session):
    """The payload's session id first, then the process environment's if it
    differs. ask() keys a receipt on the environment's id (all a Bash tool call
    can see); a hook is handed the payload's. They agree in every local
    transcript measured, but the harness does not promise they stay equal
    after a resume, so the gate reads both rather than guess."""
    out = []
    for value in (payload_session, *(os.environ.get(k) for k in SESSION_ENV_KEYS)):
        if isinstance(value, str) and value.strip() and value.strip() not in out:
            out.append(value.strip())
    return out[:2]


def fetch_receipts_for(session_ids, since_iso, *, budget_seconds, runner=None):
    """Read every id in `session_ids` within ONE time budget (F4: a hook's
    Worker read stays well under the hook's own timeout) and merge the rows.
    Any id that cannot be read makes the whole result unreachable: a partial
    read must not look like a complete one."""
    import time
    deadline = time.monotonic() + float(budget_seconds)
    rows, seen, now, truncated = [], set(), None, False
    for sid in session_ids or ():
        left = deadline - time.monotonic()
        if left < 0.5:
            return {"status": "unreachable", "reason": "server_timeout"}
        got = fetch_session_receipts(sid, since_iso, timeout=left, runner=runner)
        if got.get("status") != "ok":
            return got
        for row in got.get("receipts") or []:
            key = row.get("receipt_id") or id(row)
            if key not in seen:
                seen.add(key)
                rows.append(row)
        now = got.get("server_now") or now
        truncated = truncated or bool(got.get("truncated"))
    if not session_ids:
        return {"status": "unreachable", "reason": "no_session_id"}
    if truncated:
        # The server hit its row cap before this turn's window ended: rows the
        # verdict needs may be missing, so this is not a complete read.
        return {"status": "unreachable", "reason": "server_truncated"}
    return {"status": "ok", "receipts": rows, "server_now": now, "truncated": False}


def fetch_session_receipts(session_id, since_iso=None, *, timeout=DEFAULT_TIMEOUT_SECONDS,
                           runner=None, limit=500):
    """The server's receipts for `session_id` recorded at/after `since_iso`
    (ISO-8601, optional). See the module docstring for the return shape."""
    fixture = os.environ.get(FIXTURE_ENV)
    if fixture:
        try:
            with open(fixture, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {
                "status": "unreachable", "reason": "fixture_malformed"}
        except (OSError, ValueError):
            return {"status": "unreachable", "reason": "fixture_unreadable"}
    if not isinstance(session_id, str) or not session_id.strip():
        return {"status": "unreachable", "reason": "no_session_id"}
    script, node = _local_verb_script(), _node_binary()
    if runner is None and (script is None or node is None):
        return {"status": "unreachable", "reason": "node_or_local_verb_missing"}
    args = {"session_id": session_id, "limit": int(limit)}
    if since_iso:
        args["since"] = since_iso
    try:
        proc = (runner or subprocess.run)(
            [node or "node", script or "local-verb.mjs", READ_VERB, json.dumps(args)],
            capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"status": "unreachable", "reason": "server_timeout"}
    except Exception:
        return {"status": "unreachable", "reason": "server_read_failed"}
    if proc.returncode != 0:
        return {"status": "unreachable", "reason": _category(proc.stderr)}
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return {"status": "unreachable", "reason": "server_response_unparseable"}
    receipts = out.get("receipts") if isinstance(out, dict) else None
    if not isinstance(out, dict) or out.get("ok") is not True or not isinstance(receipts, list):
        return {"status": "unreachable", "reason": "server_response_malformed"}
    return {"status": "ok", "receipts": [r for r in receipts if isinstance(r, dict)],
            "server_now": out.get("server_now"), "truncated": out.get("truncated") is True}
