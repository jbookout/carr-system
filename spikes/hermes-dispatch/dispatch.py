#!/usr/bin/env python3
"""One task, one desk, no model in the middle.

Hermes runs this. It reasons about WHICH seat a task belongs in; this file
does not reason at all, which is the point — a router that needs its own model
to route costs the tokens the routing was supposed to save.

    dispatch.py send claude-desk "reconcile the loop board"
    dispatch.py send codex-desk "rename this variable across the package"
    dispatch.py desks
    dispatch.py register claude-desk --socket /tmp/cc-socks/claude-desk.sock
    dispatch.py register codex-desk --model gpt-5.1-codex-mini --cwd ~/carr-system
    dispatch.py where            # how to find THIS session's socket

TWO KINDS, AND WHY THE DIFFERENCE MATTERS TO A ROUTER.
A claude-session desk is a live conversation: it keeps its context, it can be
mid-way through something, and a task lands there as one more turn. A
codex-exec desk is a shot: it starts empty, runs headless at a named model,
and hands back its last message. Context-carrying work goes to the first;
self-contained mechanical work goes to the second, which is the cheaper seat.

EVERY DISPATCH LEAVES A LINE in the results file, NDJSON, one object per
dispatch. That file is how Hermes learns what happened without holding a
connection open — a claude-session answers in its own window on its own time,
so the line records that the turn was DELIVERED, not what the session decided.
A codex-exec run is synchronous, so its line carries the actual result.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "partner-line-78"))

import desks  # noqa: E402
from desks import DeskError, Registry  # noqa: E402
import inject as inject_mod  # noqa: E402  — the proven wire, not a second copy

DEFAULT_RESULTS = Path(
    os.environ.get(
        "CARR_HERMES_RESULTS",
        Path.home() / ".config" / "carr" / "hermes-dispatch-results.jsonl",
    )
)

CODEX_TIMEOUT_S = float(os.environ.get("CARR_HERMES_CODEX_TIMEOUT", "900"))

# Codex prints this on STDOUT and still exits 0, so the exit code lies.
QUOTA_HINT = re.compile(r"You've hit your usage limit[^\n]*", re.I)
RETRY_AT = re.compile(r"try again at ([0-9:]+ ?[AP]M)", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record(results_path: Path, row: dict) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def _to_claude(entry: dict, task: str, msg_id: str) -> dict:
    """Deliver one peer turn to a live labeled session."""
    payload = {
        "type": "user",
        "message": {"role": "user", "content": task},
        "origin": {"kind": "peer", "from": f"hermes:{entry['name']}", "msg_id": msg_id},
    }
    conn = inject_mod.inject_keepalive(entry["socket"], payload)
    try:
        return {"status": "delivered", "detail": "turn accepted by the desk socket"}
    finally:
        conn.close()


def _to_codex(entry: dict, task: str, env: dict | None) -> dict:
    """Run one headless codex shot and hand back its last message."""
    with tempfile.TemporaryDirectory(prefix="hermes-codex-") as tmp:
        last = Path(tmp) / "last-message.txt"
        argv = [
            "codex", "exec",
            "-m", entry["model"],
            "-C", entry.get("cwd") or str(Path.cwd()),
            "-o", str(last),
            task,
        ]
        try:
            # stdin=DEVNULL is load-bearing: `codex exec` reads stdin when it
            # is not a terminal and appends it to the prompt, so an inherited
            # pipe makes the run hang or swallow whatever the caller was fed.
            # It is the same reason every command in CLAUDE.md carries
            # `</dev/null`.
            proc = subprocess.run(
                argv, env=env or os.environ.copy(), capture_output=True,
                text=True, timeout=CODEX_TIMEOUT_S, stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return {"status": "failed", "detail": "codex is not on PATH"}
        except subprocess.TimeoutExpired:
            return {"status": "timed_out", "detail": f"no answer in {CODEX_TIMEOUT_S:.0f}s"}
        result = last.read_text(encoding="utf-8").strip() if last.exists() else ""
        blob = f"{proc.stderr or ''}\n{proc.stdout or ''}"

        # A seat that is out of credit is not a broken seat, and a router needs
        # to tell those apart: the first means send this task somewhere else
        # now, the second means stop sending anything here. Codex reports the
        # first on stdout with exit 0, so returncode alone would call it a win.
        quota = QUOTA_HINT.search(blob)
        if quota:
            return {
                "status": "quota_exhausted",
                "detail": quota.group(0).strip(),
                "retry_after": (RETRY_AT.search(blob) or [None, None])[1]
                if RETRY_AT.search(blob) else None,
                "result": result,
            }
        if proc.returncode != 0 or not result:
            return {
                "status": "failed",
                "detail": (proc.stderr or proc.stdout or "").strip()[-500:],
                "result": result,
            }
        return {"status": "completed", "result": result}


def dispatch(
    name: str,
    task: str,
    registry: Registry | None = None,
    results_path: Path | None = None,
    env: dict | None = None,
) -> dict:
    """Send one task to one desk. Raises DeskError when the desk is not usable."""
    registry = registry or Registry()
    results_path = Path(results_path or DEFAULT_RESULTS)
    entry = registry.resolve(name)          # every refusal happens here
    msg_id = str(uuid.uuid4())

    if entry["kind"] == "claude-session":
        outcome = _to_claude(entry, task, msg_id)
    else:
        outcome = _to_codex(entry, task, env)

    row = {
        "msg_id": msg_id,
        "desk": name,
        "kind": entry["kind"],
        "task": task,
        "dispatched_at": _now(),
        **outcome,
    }
    _record(results_path, row)
    return row


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_where() -> int:
    sock = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
    print("THIS session's socket:", sock or "<unset — not a messaging-enabled session>")
    if sock and desks.PID_SOCKET.match(os.path.basename(sock)):
        print()
        print("That is a pid socket, so it cannot be registered as a desk.")
        print("A desk is a session started on purpose with a name:")
        print()
        print("    claude --messaging-socket-path /tmp/cc-socks/claude-desk.sock")
        print()
        print("Then, once per live window:")
        print()
        print("    dispatch.py register claude-desk --socket /tmp/cc-socks/claude-desk.sock")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--registry", default=None, help="desk registry file")
    p.add_argument("--results", default=None, help="NDJSON results file")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register", help="name a desk")
    r.add_argument("name")
    r.add_argument("--kind", default=None, choices=list(desks.KINDS))
    r.add_argument("--socket", default=None)
    r.add_argument("--model", default=None)
    r.add_argument("--cwd", default=None)

    f = sub.add_parser("forget", help="drop a desk")
    f.add_argument("name")

    sub.add_parser("desks", help="list registered desks and whether they are live")
    sub.add_parser("where", help="print how to find and name THIS session's socket")

    s = sub.add_parser("send", help="dispatch one task to one desk")
    s.add_argument("name")
    s.add_argument("task")

    a = p.parse_args(argv)
    reg = Registry(a.registry) if a.registry else Registry()
    results = Path(a.results) if a.results else DEFAULT_RESULTS

    try:
        if a.cmd == "where":
            return _cmd_where()

        if a.cmd == "register":
            kind = a.kind or ("claude-session" if a.socket else "codex-exec")
            entry = reg.register(a.name, kind, socket=a.socket, model=a.model, cwd=a.cwd)
            print(json.dumps({a.name: entry}, indent=2))
            return 0

        if a.cmd == "forget":
            reg.forget(a.name)
            print(f"forgot {a.name}")
            return 0

        if a.cmd == "desks":
            rows = reg.entries()
            if not rows:
                print("no desks registered — see `dispatch.py where`")
                return 0
            for name, e in sorted(rows.items()):
                if e.get("kind") == "claude-session":
                    live = "live" if desks.is_live(e.get("socket", "")) else "not live"
                    print(f"{name:20} claude-session  {e.get('socket')}  [{live}]")
                else:
                    print(f"{name:20} codex-exec      {e.get('model')}  in {e.get('cwd')}")
            return 0

        row = dispatch(a.name, a.task, registry=reg, results_path=results)
        print(json.dumps(row, indent=2))
        return 0 if row["status"] in ("delivered", "completed") else 1

    except DeskError as e:
        print(f"refused ({e.code}): {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
