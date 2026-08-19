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

Receipt 2 addition — the sender is now a real peer, not a drive-by write:

  * It BINDS its own listening socket in the recipient's socket directory.
    The recipient refuses to send a hold receipt otherwise; its own words are
    "hold-receipt skipped: reply address outside our socket namespace".
  * It STAYS CONNECTED across the send, so the recipient's peer-credential
    lookup has a live fd to ask about (receipt 1 got `peer pid unavailable`
    from a connection that was already closing).
  * It LISTENS for the returning control frame
    {"action":"peer_message_status","status":"held|denied|expired|delivered"},
    which is how the sender learns what the recipient's user decided.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import uuid

CONNECT_TIMEOUT_S = 5.0
STATUSES = ("held", "denied", "expired", "delivered")


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


def inject_keepalive(
    sock_path: str, payload: dict, timeout: float = CONNECT_TIMEOUT_S
) -> socket.socket:
    """Write one NDJSON line and KEEP the connection open.

    Returned socket must be closed by the caller. Receipt 1 half-closed and
    closed immediately and the recipient logged `peer pid unavailable`; a live
    fd is the only thing a peer-credential lookup can interrogate.
    """
    line = json.dumps(payload, separators=(",", ":")) + "\n"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock_path)
    s.sendall(line.encode())
    return s


class ReplyListener:
    """The sender's own socket: a real peer address, not a return-to-nobody.

    Binds inside the recipient's `cc-socks` directory (the recipient checks the
    dirname before it will send a receipt), accepts connections for as long as
    the sender lives, and records every NDJSON frame it is handed.
    """

    def __init__(self, path: str):
        self.path = path
        self.frames: list[dict] = []
        self.raw: list[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._srv: socket.socket | None = None

    def start(self) -> "ReplyListener":
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.path)
        os.chmod(self.path, 0o600)  # same posture as a Claude Code session socket
        srv.listen(8)
        srv.settimeout(0.5)
        self._srv = srv
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._read, args=(conn,), daemon=True).start()

    def _read(self, conn: socket.socket) -> None:
        buf = b""
        conn.settimeout(0.5)
        with conn:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._record(line.decode("utf-8", "replace"))
            if buf.strip():
                self._record(buf.decode("utf-8", "replace"))

    def _record(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        with self._lock:
            self.raw.append(line)
            try:
                self.frames.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    def statuses(self, orig_msg_id: str | None = None) -> list[dict]:
        with self._lock:
            out = [f for f in self.frames if f.get("action") == "peer_message_status"]
        if orig_msg_id:
            out = [f for f in out if f.get("orig_msg_id") in (None, orig_msg_id)]
        return out

    def wait_for_status(
        self, orig_msg_id: str | None = None, timeout: float = 30.0, after: int = 0
    ) -> dict | None:
        """Block until a peer_message_status frame past index `after` arrives."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            got = self.statuses(orig_msg_id)
            if len(got) > after:
                return got[after]
            time.sleep(0.25)
        return None

    def close(self) -> None:
        self._stop.set()
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


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
    p.add_argument(
        "--listen",
        metavar="PATH",
        help="bind this socket as the sender's own address and wait for "
        "peer_message_status on it (must sit beside the target socket)",
    )
    p.add_argument(
        "--wait", type=float, default=0.0, help="seconds to wait for a status frame"
    )
    a = p.parse_args(argv)

    if not is_live(a.socket_path):
        print(f"no listener on {a.socket_path}", file=sys.stderr)
        return 2

    listener = None
    if a.listen:
        if os.path.dirname(a.listen) != os.path.dirname(a.socket_path):
            print(
                "refusing: --listen must sit in the target's socket directory, or "
                "the recipient drops the receipt as out-of-namespace",
                file=sys.stderr,
            )
            return 2
        listener = ReplyListener(a.listen).start()

    reply_to = a.listen or (None if a.plain else a.from_socket)
    payload = peer_turn(a.text, None if a.plain else reply_to)
    print(json.dumps(payload))

    conn = inject_keepalive(a.socket_path, payload)
    try:
        if listener and a.wait:
            got = listener.wait_for_status(
                payload.get("origin", {}).get("msg_id"), a.wait
            )
            print(json.dumps(got) if got else f"(no status frame within {a.wait}s)")
    finally:
        conn.close()
        if listener:
            listener.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
