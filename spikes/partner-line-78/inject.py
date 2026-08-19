#!/usr/bin/env python3
"""Partner-line injector: drop one turn onto a LIVE Claude Code session.

Not the Claude Code UI. A plain process writing NDJSON to the session's unix
socket. The wire format is the one Claude Code prints in its own debug log:

    [uds-messaging] Inject messages:
      echo '{"type":"user","message":{"role":"user","content":"hello"}}' \
        | socat - UNIX-CONNECT:<sock>

One JSON object per line, newline-delimited, over a SOCK_STREAM unix socket.
A `peer` origin marks the turn as coming from another session, which is what
renders as <cross-session-message>; without it the turn lands as a plain user
message.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import uuid

CONNECT_TIMEOUT_S = 5.0


def peer_turn(text: str, reply_to: str | None, msg_id: str | None = None) -> dict:
    """Build one peer turn. `reply_to` is this sender's own socket path."""
    msg = {"type": "user", "message": {"role": "user", "content": text}}
    if reply_to:
        msg["origin"] = {
            "kind": "peer",
            "from": f"uds:{reply_to}",
            "msg_id": msg_id or str(uuid.uuid4()),
        }
    return msg


def inject(sock_path: str, payload: dict, timeout: float = CONNECT_TIMEOUT_S) -> None:
    """Write one NDJSON line to a live session socket. Raises on failure."""
    line = json.dumps(payload, separators=(",", ":")) + "\n"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        s.sendall(line.encode())
        # Half-close: the reader's `end` handler flushes any trailing buffer.
        s.shutdown(socket.SHUT_WR)
    finally:
        s.close()


def is_live(sock_path: str, timeout: float = 0.25) -> bool:
    """Same probe Claude Code uses: connect succeeds => a process is listening."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("socket_path", help="target session socket, e.g. /tmp/cc-socks/123.sock")
    p.add_argument("text", help="the turn text to deliver")
    p.add_argument(
        "--from-socket",
        default=os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET"),
        help="sender's own socket path; marks the turn origin.kind=peer",
    )
    p.add_argument("--plain", action="store_true", help="send with no peer origin")
    a = p.parse_args(argv)

    if not is_live(a.socket_path):
        print(f"no listener on {a.socket_path}", file=sys.stderr)
        return 2

    payload = peer_turn(a.text, None if a.plain else a.from_socket)
    inject(a.socket_path, payload)
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
