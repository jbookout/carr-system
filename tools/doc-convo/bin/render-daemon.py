#!/usr/bin/env python
"""render-daemon.py — keep Doc's frozen voice loaded between renders.

Runs INSIDE .venv-tts. One Unix-socket connection carries one JSON request
and receives one JSON reply.
"""

import json
import os
import pathlib
import signal
import socket
import sys
import time

TOOL = pathlib.Path(__file__).resolve().parent.parent
ASSETS = TOOL / "assets"
SOCKET_PATH = ASSETS / ".render.sock"
PID_PATH = ASSETS / ".render-daemon.pid"
REFERENCE = (
    TOOL.parent / "doc-voice" / "reference" / "doc-identity-reference.wav"
)


def receive_request(conn: socket.socket) -> dict:
    chunks = []
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    server.listen()
    PID_PATH.write_text(str(os.getpid()))

    def stop(_signum, _frame) -> None:
        server.close()
        SOCKET_PATH.unlink(missing_ok=True)
        PID_PATH.unlink(missing_ok=True)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        import perth
        if getattr(perth, "PerthImplicitWatermarker", None) is None:
            perth.PerthImplicitWatermarker = perth.DummyWatermarker  # bake-off fix

        import torch
        import torchaudio
        from chatterbox.tts import ChatterboxTTS

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        model = ChatterboxTTS.from_pretrained(device=device)

        while True:
            conn, _ = server.accept()
            with conn:
                started = time.monotonic()
                chars = 0
                try:
                    request = receive_request(conn)
                    text = request["text"]
                    out = request["out"]
                    if not isinstance(text, str) or not isinstance(out, str):
                        raise TypeError("text and out must be strings")
                    if not pathlib.Path(out).is_absolute():
                        raise ValueError("out must be an absolute path")
                    chars = len(text)
                    audio = model.generate(
                        text, audio_prompt_path=str(REFERENCE),
                        exaggeration=0.40, cfg_weight=0.60,
                    )
                    torchaudio.save(out, audio.cpu(), model.sr)
                    reply: dict[str, object] = {"ok": True}
                except Exception as exc:
                    reply = {"ok": False, "error": str(exc)}
                try:
                    conn.sendall(json.dumps(reply).encode("utf-8"))
                except OSError:
                    pass
                elapsed = time.monotonic() - started
                print(f"render: {chars} chars in {elapsed:.1f}s", file=sys.stderr,
                      flush=True)
    finally:
        server.close()
        SOCKET_PATH.unlink(missing_ok=True)
        PID_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
