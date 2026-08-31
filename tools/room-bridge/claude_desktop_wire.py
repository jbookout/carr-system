#!/usr/bin/env python3
"""Backend-only Claude background-session and Desktop handoff wire.

The Model Room bridge does not drive the Claude Desktop UI.  It starts a
durable Claude Code background session with a caller-supplied UUID, observes
that session through ``claude agents --json --all``, reads the completed
assistant result from Claude Code's own persisted transcript, and finally
uses Claude Code's supported ``/desktop`` command from an attached PTY.  The
PTY is load-bearing: slash commands are interactive client commands, not
prompts that should be sent to a model with ``-p``.

No poll creates a session.  ``launch_background`` is called only after Hermes
has atomically claimed an explicitly addressed queue card; later poll cycles
only inspect the UUID already persisted in room-bridge state.
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import subprocess
import time
import uuid
from pathlib import Path


UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
SHORT_ID = re.compile(r"\bbackgrounded\s*[·:]?\s*([0-9a-f]{8})\b", re.IGNORECASE)
ACTIVE_STATES = {"starting", "running", "working", "idle"}
COMPLETED_STATES = {"completed"}
FAILED_STATES = {"failed", "stopped", "killed"}
NEEDS_INPUT_STATES = {"needs_input", "needs-input", "blocked"}


class ClaudeDesktopError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _session_id(value: str | None = None) -> str:
    candidate = value or str(uuid.uuid4())
    if not UUID.fullmatch(candidate):
        raise ClaudeDesktopError("invalid_session_id", "Claude session id must be a UUID")
    return candidate.lower()


def launch_background(
    entry: dict,
    task: str,
    *,
    request_id: str | None = None,
    run=subprocess.run,
    claude_bin: str = "claude",
    timeout_s: float = 30.0,
) -> dict:
    """Start one supervisor-hosted Claude session and return its durable UUID."""
    request = _session_id(request_id)
    model = str(entry.get("model") or "").strip()
    effort = str(entry.get("effort") or "").strip()
    cwd = str(entry.get("cwd") or "").strip()
    permission_mode = str(entry.get("permission_mode") or "dontAsk").strip()
    if not model or not effort or not cwd or not task.strip():
        raise ClaudeDesktopError(
            "invalid_background_contract",
            "Claude background dispatch requires model, effort, cwd, and a non-empty task",
        )
    argv = [
        claude_bin,
        "--bg",
        # Claude's background supervisor owns the session UUID and explicitly
        # ignores --session-id.  The request UUID names this dispatch; the
        # genuine session UUID is resolved from the supervisor immediately
        # after launch and is the only ref persisted by the bridge.
        "--name", f"model-room-{request[:8]}",
        "--model", model,
        "--effort", effort,
        "--permission-mode", permission_mode,
        task,
    ]
    try:
        proc = run(
            argv,
            cwd=cwd,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise ClaudeDesktopError("claude_unavailable", "claude is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeDesktopError("background_launch_timeout", "claude --bg did not return") from exc
    if proc.returncode != 0:
        # Provider output can contain task context.  Keep the bridge receipt a
        # bounded error class and leave diagnostics in Claude's own state.
        raise ClaudeDesktopError(
            "background_launch_failed", f"claude --bg exited {proc.returncode}"
        )
    match = SHORT_ID.search(f"{proc.stdout or ''}\n{proc.stderr or ''}")
    if match is None:
        raise ClaudeDesktopError(
            "background_session_unidentified", "claude --bg returned no background id"
        )
    short_id = match.group(1).lower()
    try:
        listed = run(
            [claude_bin, "agents", "--json", "--all"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
        rows = json.loads(listed.stdout) if listed.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        rows = []
    row = next(
        (item for item in rows if isinstance(item, dict)
         and (item.get("id") == short_id
              or str(item.get("sessionId") or "").lower().startswith(short_id))),
        None,
    ) if isinstance(rows, list) else None
    sid = str((row or {}).get("sessionId") or "").lower()
    if not UUID.fullmatch(sid):
        # This exact short id came from the launch above. Stop it rather than
        # leave untracked model work running when the durable identity proof
        # cannot be established.
        try:
            run([claude_bin, "stop", short_id], capture_output=True, text=True,
                timeout=timeout_s, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
        raise ClaudeDesktopError(
            "background_session_unidentified", "Claude supervisor did not resolve the session UUID"
        )
    return {
        "status": "delivered",
        "session_id": sid,
        "session_short_id": short_id,
        "session_name": f"model-room-{request[:8]}",
        "transport": "claude-desktop",
    }


def inspect_session(
    session_id: str,
    *,
    run=subprocess.run,
    claude_bin: str = "claude",
    timeout_s: float = 15.0,
) -> dict:
    """Return one redacted supervisor fact for a background session UUID."""
    sid = _session_id(session_id)
    try:
        proc = run(
            [claude_bin, "agents", "--json", "--all"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClaudeDesktopError("background_status_unavailable", "Claude agent status failed") from exc
    if proc.returncode != 0:
        raise ClaudeDesktopError("background_status_unavailable", "Claude agent status refused")
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeDesktopError("background_status_invalid", "Claude agent status was not JSON") from exc
    if not isinstance(rows, list):
        raise ClaudeDesktopError("background_status_invalid", "Claude agent status was not a list")
    row = next(
        (item for item in rows if isinstance(item, dict) and item.get("sessionId") == sid),
        None,
    )
    if row is None:
        return {"session_id": sid, "state": "unknown", "found": False}
    state = str(row.get("state") or "running").strip().lower()
    return {
        "session_id": sid,
        "state": state,
        "found": True,
        "kind": str(row.get("kind") or "background"),
    }


def _transcript_candidates(session_id: str, root: Path) -> list[Path]:
    sid = _session_id(session_id)
    try:
        return sorted(root.glob(f"**/{sid}.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return []


def read_final_text(
    session_id: str,
    *,
    transcript_root: Path | None = None,
    tail_bytes: int = 8 * 1024 * 1024,
) -> str | None:
    """Read the last final assistant text from Claude's own session transcript.

    Only a bounded tail is parsed.  Queue prompts require their typed terminal
    line at the end, so a result that is not in the final eight MiB is not a
    valid current completion signal anyway.
    """
    root = Path(transcript_root or (Path.home() / ".claude" / "projects"))
    candidates = _transcript_candidates(session_id, root)
    if not candidates:
        return None
    path = candidates[0]
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - tail_bytes)
            handle.seek(start)
            raw = handle.read()
    except OSError:
        return None
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if start:
        lines = lines[1:]
    finals: list[str] = []
    typed: list[str] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("type") != "assistant":
            continue
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        chunks = message.get("content")
        if not isinstance(chunks, list):
            continue
        text = "\n".join(
            str(chunk.get("text"))
            for chunk in chunks
            if isinstance(chunk, dict) and chunk.get("type") == "text"
            and isinstance(chunk.get("text"), str)
        ).strip()
        if not text:
            continue
        if "CARR_QUEUE_RESULT " in text:
            typed.append(text)
        if message.get("stop_reason") == "end_turn":
            finals.append(text)
    return typed[-1] if typed else (finals[-1] if finals else None)


def handoff_to_desktop(
    session_id: str,
    *,
    claude_bin: str = "claude",
    timeout_s: float = 30.0,
    ready_timeout_s: float = 8.0,
) -> dict:
    """Attach to a completed background session and invoke supported /desktop."""
    sid = _session_id(session_id)
    master, slave = pty.openpty()
    try:
        try:
            proc = subprocess.Popen(
                [claude_bin, "attach", sid],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
            )
        except OSError as exc:
            raise ClaudeDesktopError("desktop_handoff_unavailable", "could not attach to Claude") from exc
        finally:
            os.close(slave)

        # Wait for the attached client to paint at least once.  This is a PTY
        # readiness boundary, not a visual/UI heuristic; a quiet client gets a
        # bounded fallback so an output-theme change cannot wedge the bridge.
        ready_deadline = time.monotonic() + ready_timeout_s
        while proc.poll() is None and time.monotonic() < ready_deadline:
            readable, _, _ = select.select([master], [], [], 0.25)
            if readable:
                try:
                    os.read(master, 65536)
                except OSError:
                    pass
                break
        if proc.poll() is not None:
            raise ClaudeDesktopError("desktop_attach_failed", "Claude attach exited before handoff")
        os.write(master, b"/desktop\r")

        deadline = time.monotonic() + timeout_s
        while proc.poll() is None and time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.25)
            if readable:
                try:
                    os.read(master, 65536)
                except OSError:
                    pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
            raise ClaudeDesktopError("desktop_handoff_timeout", "Claude /desktop did not exit")
        if proc.returncode != 0:
            raise ClaudeDesktopError(
                "desktop_handoff_failed", f"Claude /desktop exited {proc.returncode}"
            )
        return {"status": "opened", "session_id": sid}
    finally:
        try:
            os.close(master)
        except OSError:
            pass
