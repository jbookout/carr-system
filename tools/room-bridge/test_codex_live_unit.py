#!/usr/bin/env python3
"""Unit proof for the codex-live desk. No Codex, no model spend.

A stand-in speaks the app-server's side of the wire — the WebSocket upgrade,
then the four messages a turn actually needs — so the client is tested against
the protocol rather than against a live seat. What it pins down is the part
that is not guessable from the schema: the ORDER (initialize, then the
initialized notification, then thread/start or thread/resume, then turn/start),
and that a resumed desk sends thread/resume with the id it was given instead of
starting a fresh thread.

Codex derived this protocol; see codex_wire.py. The live half is proven in
test_codex_live_live.py, which needs a real app-server.

Run:  python3 tools/room-bridge/test_codex_live_unit.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import desks  # noqa: E402
import dispatch  # noqa: E402

FAILURES: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except AssertionError as e:
        FAILURES.append(label)
        print(f"  FAIL  {label}\n          {e}")
    except Exception as e:  # noqa: BLE001
        FAILURES.append(label)
        print(f"  FAIL  {label}\n          unexpected {e!r}")
    else:
        print(f"  ok    {label}")


class FakeAppServer:
    """Speaks the server half: upgrade, then answer the four messages."""

    def __init__(self, path: str, answer: str = "the live seat answered"):
        self.path = path
        self.answer = answer
        self.seen: list[dict] = []
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(4)
        self.srv.settimeout(10)
        threading.Thread(target=self._serve, daemon=True).start()

    # -- websocket plumbing, mirrored from the client side -----------------
    @staticmethod
    def _accept_key(key: str) -> str:
        return base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()

    def _send(self, conn, obj: dict) -> None:
        payload = json.dumps(obj).encode()
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", n))
        conn.sendall(bytes(header) + payload)   # server frames are unmasked

    def _recv(self, conn) -> dict | None:
        def exact(n):
            buf = b""
            while len(buf) < n:
                c = conn.recv(n - len(buf))
                if not c:
                    return None
                buf += c
            return buf
        head = exact(2)
        if not head:
            return None
        size = head[1] & 0x7F
        masked = bool(head[1] & 0x80)
        if size == 126:
            size = struct.unpack("!H", exact(2))[0]
        elif size == 127:
            size = struct.unpack("!Q", exact(8))[0]
        mask = exact(4) if masked else b""
        payload = exact(size) or b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    def _serve(self) -> None:
        # Accept in a loop. resolve() probes liveness by connecting, and that
        # probe would otherwise consume the only accept the real turn needs —
        # the same trap the Claude stand-in hit.
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn) -> None:
        with conn:
            buf = b""
            while b"\r\n\r\n" not in buf:
                c = conn.recv(4096)
                if not c:
                    return
                buf += c
            key = ""
            for line in buf.decode("latin-1").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {self._accept_key(key)}\r\n\r\n"
            ).encode())

            while True:
                msg = self._recv(conn)
                if msg is None:
                    return
                self.seen.append(msg)
                method = msg.get("method")
                if method == "initialize":
                    self._send(conn, {"id": msg["id"], "result": {"userAgent": "fake"}})
                elif method in ("thread/start", "thread/resume"):
                    tid = (msg.get("params") or {}).get("threadId") or "thread-live-0001"
                    self._send(conn, {"id": msg["id"], "result": {"thread": {"id": tid}}})
                elif method == "turn/start":
                    self._send(conn, {"id": msg["id"], "result": {"turn": {"id": "turn-1"}}})
                    self._send(conn, {"method": "item/completed", "params": {
                        "item": {"type": "agentMessage", "text": self.answer}}})

    def methods(self) -> list[str]:
        return [m.get("method") for m in self.seen if m.get("method")]

    def close(self) -> None:
        try:
            self.srv.close()
        except OSError:
            pass


def main() -> int:
    tmp = tempfile.TemporaryDirectory(prefix="codex-live-test-")
    root = Path(tmp.name)
    reg = desks.Registry(root / "desks.json")
    results = root / "results.jsonl"
    sock = str(root / "live.sock")

    def a_turn_reaches_a_live_codex_seat():
        srv = FakeAppServer(sock)
        try:
            reg.register("cx", "codex-live", socket=sock, cwd=str(root))
            row = dispatch.dispatch("cx", "do the thing", registry=reg, results_path=results)
            assert row["status"] == "completed", row
            assert row["result"] == "the live seat answered", row
            assert row["resumed"] is False, row
            assert row["thread_id"] == "thread-live-0001", row
        finally:
            srv.close()

    check("a task reaches a live Codex seat and its answer comes straight back",
          a_turn_reaches_a_live_codex_seat)

    def the_call_order_is_the_one_codex_found():
        srv = FakeAppServer(sock)
        try:
            reg.forget("cx2")
            reg.register("cx2", "codex-live", socket=sock, cwd=str(root))
            dispatch.dispatch("cx2", "again", registry=reg, results_path=results)
            got = srv.methods()
            assert got[:4] == ["initialize", "initialized", "thread/start", "turn/start"], got
        finally:
            srv.close()

    check("the wire follows initialize, initialized, thread/start, turn/start",
          the_call_order_is_the_one_codex_found)

    def a_second_task_resumes_the_same_thread():
        srv = FakeAppServer(sock, answer="still here")
        try:
            row = dispatch.dispatch("cx", "and again", registry=reg, results_path=results)
            assert row["resumed"] is True, row
            assert row["thread_id"] == "thread-live-0001", row
            assert "thread/resume" in srv.methods(), srv.methods()
            assert "thread/start" not in srv.methods(), srv.methods()
        finally:
            srv.close()

    check("a second task resumes the same live thread instead of starting one",
          a_second_task_resumes_the_same_thread)

    def a_pid_socket_is_still_refused():
        try:
            reg.register("sneaky", "codex-live", socket="/tmp/cc-socks/4242.sock")
        except desks.DeskError as e:
            assert e.code == "unlabeled_target", e.code
        else:
            raise AssertionError("a pid socket was accepted as a codex-live desk")

    check("the pid-socket guard covers the new kind too", a_pid_socket_is_still_refused)

    def a_dead_app_server_is_refused():
        reg.register("gone", "codex-live", socket=str(root / "nothing.sock"), cwd=str(root))
        try:
            reg.resolve("gone")
        except desks.DeskError as e:
            assert e.code == "desk_not_live", e.code
        else:
            raise AssertionError("a codex-live desk with no server resolved")

    check("a codex-live desk whose app-server is gone is refused",
          a_dead_app_server_is_refused)

    tmp.cleanup()
    print()
    if FAILURES:
        print(f"codex-live unit: {len(FAILURES)} FAILED")
        return 1
    print("codex-live unit: DONE — every assertion held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
