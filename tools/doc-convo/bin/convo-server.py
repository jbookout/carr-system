#!/usr/bin/env python3
"""Local HTTP engine for the Doc conversation panel."""

import json
import os
import pathlib
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

import convo_core
import reflexes
import speak
from timings import Turn

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "4680"))
PANEL = convo_core.TOOL / "panel" / "panel.html"
TIMINGS = convo_core.TOOL / "assets" / "turn-timings.jsonl"


def split_card(reply: str) -> tuple[str, object | None]:
    lines = reply.splitlines()
    card = None
    kept = []
    for line in lines:
        if line.startswith("CARD: "):
            try:
                card = json.loads(line[6:])
            except json.JSONDecodeError:
                card = None
        else:
            kept.append(line)
    return "\n".join(kept).strip(), card


class Engine:
    def __init__(self, system_prompt: str | None) -> None:
        self.state = "idle"
        self.turns: list[dict] = []
        self.system_prompt = system_prompt
        self.recording: subprocess.Popen[bytes] | None = None
        self.utterance: pathlib.Path | None = None
        self.listeners: list[queue.Queue[tuple[str, dict]]] = []
        self.lock = threading.Lock()
        self.workdir = pathlib.Path(tempfile.mkdtemp(prefix="doc-convo-server."))
        self.turn_number = 0
        self.timing: Turn | None = None
        self.mic = convo_core.pick_mic()

    def snapshot(self) -> dict:
        with self.lock:
            return {"state": self.state, "turns": list(self.turns[-20:])}

    def subscribe(self) -> queue.Queue[tuple[str, dict]]:
        listener: queue.Queue[tuple[str, dict]] = queue.Queue()
        with self.lock:
            self.listeners.append(listener)
            state = self.state
        listener.put(("state", {"state": state}))
        return listener

    def unsubscribe(self, listener: queue.Queue[tuple[str, dict]]) -> None:
        with self.lock:
            if listener in self.listeners:
                self.listeners.remove(listener)

    def emit(self, event: str, data: dict) -> None:
        with self.lock:
            listeners = list(self.listeners)
        for listener in listeners:
            listener.put((event, data))

    def set_state(self, state: str) -> None:
        with self.lock:
            self.state = state
        self.emit("state", {"state": state})

    def progress(self, label: str) -> None:
        # The thinking ticker (Joe, 2026-08-08): every label is a TRUE step the
        # engine is on right now — the truthful-bridge law made visible. Never
        # emit a step that isn't actually happening.
        self.emit("progress", {"label": label})

    def add_turn(self, you: str, doc: str,
                 card: object | None = None) -> dict:
        turn = {"you": you, "doc": doc, "card": card, "ts": time.time()}
        with self.lock:
            self.turns.append(turn)
            self.turns = self.turns[-20:]
        self.emit("turn", {"you": you, "doc": doc, "card": card})
        return turn

    def update_turn(self, turn: dict, doc: str,
                    card: object | None = None) -> None:
        with self.lock:
            turn["doc"] = doc
            turn["card"] = card
        self.emit("turn", {
            "you": turn["you"], "doc": doc, "card": card,
            "replace": True, "ts": turn["ts"],
        })

    def start(self) -> bool:
        timing = Turn()
        timing.mark("mic_open_start")
        with self.lock:
            if self.state != "idle":
                return False
            self.turn_number += 1
            utterance = self.workdir / f"utt-{self.turn_number}.wav"
            # PER-TURN CAPTURE, restored 2026-08-08 on Joe's call. The resident
            # ring buffer was faster on paper and wrong in practice: garbled
            # transcripts every turn ("I was just 100% on the salt"). An
            # alignment fix did not clear it, so the whole mechanism is out —
            # accurate and slower beats fast and deaf. Costs ~350ms of arm time
            # and one process spawn per turn; that is the price of it working.
            try:
                recording = subprocess.Popen(
                    ["ffmpeg", "-y", "-loglevel", "error", "-f", "avfoundation",
                     "-i", self.mic, "-ac", "1", "-ar", "16000", str(utterance)],
                    stderr=subprocess.PIPE,
                )
            except OSError:
                return False
            self.recording = recording
            self.utterance = utterance
            self.timing = timing
            self.state = "listening"
        time.sleep(0.35)   # let avfoundation open before we say "listening"
        timing.mark("mic_open_done")
        self.emit("state", {"state": "listening"})
        return True

    def refresh_mic(self) -> bool:
        with self.lock:
            if self.state != "idle":
                return False
            self.mic = convo_core.pick_mic()
        return True

    def stop(self) -> bool:
        with self.lock:
            if (self.state != "listening" or self.recording is None or
                    self.utterance is None):
                return False
            recording = self.recording
            timing = self.timing
            if timing is not None:
                timing.mark("record_stop_start")
            utterance = self.utterance
            self.state = "thinking"
        try:
            recording.send_signal(signal.SIGINT)
            recording.wait(timeout=5)
        except subprocess.TimeoutExpired:
            recording.kill()
            recording.wait()
        except OSError:
            utterance.unlink(missing_ok=True)
        if timing is not None:
            timing.mark("record_stop_done")
        self.emit("state", {"state": "thinking"})
        self.progress("heard you")
        threading.Thread(
            target=self._finish_turn, args=(timing,), daemon=True,
        ).start()
        return True

    def _heard_nothing(self, message: str) -> None:
        self.add_turn("", message)

    def _finish_turn(self, timing: Turn | None) -> None:
        try:
            subprocess.Popen(["afplay", str(convo_core.EARCON)])
            utterance = self.utterance
            if utterance is None or not utterance.exists() or utterance.stat().st_size < convo_core.MIN_BYTES:
                self._heard_nothing("· heard nothing")
                return
            if timing is not None:
                timing.mark("volume_check_start")
            volume = convo_core.mean_volume(utterance)
            if timing is not None:
                timing.mark("volume_check_done")
            if volume is not None and volume < -45:
                self._heard_nothing(f"· heard only silence (mic level {volume}dB)")
                return
            self.progress("making out your words")
            if timing is not None:
                timing.mark("stt_start")
            text = convo_core.transcribe(utterance)
            if timing is not None:
                timing.mark("stt_done")
            if not text:
                self._heard_nothing("· heard nothing")
                return
            # REFLEX: whole-utterance turns that carry no business content are
            # answered instantly from cache — no brain call, no render wait.
            # Falls through on anything that isn't an exact conversational
            # phrase, so a question about the book can never be intercepted.
            if timing is not None:
                timing.mark("reflex_check_start")
            reflex = reflexes.match(text)
            if timing is not None:
                timing.mark("reflex_check_done")
            if reflex:
                self.add_turn(text, reflex)
                self.set_state("speaking")
                if timing is not None:
                    timing.mark("tts_start")
                speak.stream(
                    reflex,
                    on_first_play=(lambda: timing.mark("tts_first_audio"))
                    if timing is not None else None,
                )
                if timing is not None:
                    timing.mark("tts_done")
                return

            if self.system_prompt is None:
                self._heard_nothing("· no hot-context snapshot")
                return
            # Truthful bridge while the brain works. NEUTRAL by design (demo
            # lesson 2026-08-08: "Checking the record." on "can you hear me?"
            # read as canned) — record language only when a record is truly
            # in play, which v0 can't know pre-brain.
            self.progress("thinking it over")
            subprocess.Popen(
                [sys.executable, str(convo_core.SPEAK), "--cache-only",
                 "One moment."],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            speaking = False
            streamed_turn = None
            streamed_doc = ""
            # None is the end-of-stream sentinel; only sentences go in otherwise.
            speech_queue: queue.Queue[str | None] = queue.Queue()

            def emit_envelope(wav: pathlib.Path) -> None:
                env = wav.with_suffix(".env.json")
                payload = {"ms": 100, "levels": []}
                if env.exists():
                    try:
                        payload = json.loads(env.read_text())
                    except (OSError, json.JSONDecodeError):
                        pass
                self.emit("envelope", payload)

            def start_speaking() -> None:
                nonlocal speaking
                if not speaking:
                    speaking = True
                    if timing is not None:
                        timing.mark("tts_first_audio")
                    self.set_state("speaking")

            def speak_sentences() -> None:
                while True:
                    sentence = speech_queue.get()
                    if sentence is None:
                        return
                    speak.stream(sentence, before_play=emit_envelope,
                                 on_first_play=start_speaking)

            def on_sentence(sentence: str) -> None:
                nonlocal streamed_doc, streamed_turn
                if timing is not None:
                    timing.mark("brain_first_sentence")
                    timing.mark("tts_start")
                streamed_doc = f"{streamed_doc} {sentence}".strip()
                if streamed_turn is None:
                    streamed_turn = self.add_turn(text, streamed_doc)
                    self.set_state("rendering")
                    self.progress("voice coming")
                else:
                    self.update_turn(streamed_turn, streamed_doc)
                speech_queue.put(sentence)

            speech_thread = threading.Thread(target=speak_sentences, daemon=True)
            speech_thread.start()

            def finish_speech() -> None:
                speech_queue.put(None)
                speech_thread.join()
                if timing is not None:
                    timing.mark("tts_done")

            if timing is not None:
                timing.mark("brain_start")
            reply, brain = convo_core.ask_brain_streaming(
                text, self.system_prompt, on_sentence=on_sentence,
            )
            if timing is not None:
                timing.mark("brain_done")
            doc, card = split_card(reply)
            if brain.returncode != 0:
                finish_speech()
                if streamed_turn is None:
                    self._heard_nothing("· brain error")
                else:
                    self.update_turn(
                        streamed_turn,
                        f"{streamed_doc}\n· brain stopped mid-reply",
                    )
                return
            if streamed_turn is None:
                if not doc:
                    finish_speech()
                    self._heard_nothing("· brain returned no answer")
                    return
                streamed_turn = self.add_turn(text, doc, card)
            elif doc != streamed_doc or card is not None:
                self.update_turn(streamed_turn, doc, card)
            finish_speech()
        except (OSError, ValueError) as exc:
            self._heard_nothing(f"· conversation error: {exc}")
        finally:
            if timing is not None:
                report = timing.report()
                try:
                    TIMINGS.parent.mkdir(parents=True, exist_ok=True)
                    with TIMINGS.open("a") as timing_log:
                        timing_log.write(
                            json.dumps(report, separators=(",", ":")) + "\n"
                        )
                except OSError:
                    pass
                self.emit("timing", report)
            with self.lock:
                self.recording = None
                self.utterance = None
                self.timing = None
            self.progress("")
            self.set_state("idle")

    def close(self) -> None:
        for path in self.workdir.iterdir():
            path.unlink(missing_ok=True)
        self.workdir.rmdir()


class ConvoServer(ThreadingHTTPServer):
    """Carries the engine as a DECLARED attribute. Hanging it on a plain
    ThreadingHTTPServer works at runtime but is invisible to the type checker,
    so nothing catches a handler reaching for a field the server never got."""

    engine: Engine


class Handler(BaseHTTPRequestHandler):
    server_version = "DocConvo/1"

    @property
    def engine(self) -> Engine:
        return cast(ConvoServer, self.server).engine

    def json_response(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/":
            if not PANEL.exists():
                body = f"503 panel missing: {PANEL}\n".encode("utf-8")
                self.send_response(503)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = PANEL.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/state":
            self.json_response(200, self.engine.snapshot())
        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            listener = self.engine.subscribe()
            try:
                while True:
                    try:
                        event, data = listener.get(timeout=15)
                        chunk = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
                    except queue.Empty:
                        chunk = b": keepalive\n\n"
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                self.engine.unsubscribe(listener)
        else:
            self.json_response(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/talk":
            self.json_response(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.json_response(400, {"error": "invalid JSON"})
            return
        action = body.get("action") if isinstance(body, dict) else None
        if action == "start":
            ok = self.engine.start()
        elif action == "stop":
            ok = self.engine.stop()
        elif action == "refresh_mic":
            ok = self.engine.refresh_mic()
        else:
            self.json_response(
                400, {"error": "action must be start, stop, or refresh_mic"},
            )
            return
        if not ok:
            self.json_response(409, {"ok": False})
            return
        self.json_response(200, {"ok": True})

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}", file=sys.stderr)


def main() -> int:
    convo_core.warm_voice()
    convo_core.warm_whisper()
    system_prompt = convo_core.refresh_hot_context()
    engine = Engine(system_prompt)
    server = ConvoServer((HOST, PORT), Handler)
    server.engine = engine

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
