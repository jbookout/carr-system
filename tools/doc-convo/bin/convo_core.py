#!/usr/bin/env python3
"""Shared conversation pipeline for the terminal loop and panel engine."""

import atexit
import json
import os
import pathlib
import socket as socketlib
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from collections import deque
from collections.abc import Callable

import speak

TOOL = pathlib.Path(__file__).resolve().parent.parent
RIG = TOOL.parent / "dictation-rig"
WHISPER = "/opt/homebrew/bin/whisper-cli"
WHISPER_SERVER = "/opt/homebrew/bin/whisper-server"
MODEL = pathlib.Path.home() / ".cache/whisper-cpp/models/ggml-large-v3-turbo.bin"
VOCAB = RIG / "vocab-prompt.txt"
EARCON = TOOL / "assets" / "earcon-ack.wav"
CONTEXT = TOOL / "assets" / "hot-context.md"
PREAMBLE = TOOL / "prompt" / "preamble.md"
SESSION_FILE = TOOL / "assets" / ".brain-session-id"
SPEAK = TOOL / "bin" / "speak.py"
VENV_PY = TOOL / ".venv-tts" / "bin" / "python"
RENDER_DAEMON = TOOL / "bin" / "render-daemon.py"
RENDER_SOCKET = TOOL / "assets" / ".render.sock"
RENDER_LOG = TOOL / "assets" / "render-daemon.log"
WHISPER_LOG = TOOL / "assets" / "whisper-server.log"
WHISPER_HOST = "127.0.0.1"
WHISPER_PORT = int(os.environ.get("DOC_WHISPER_PORT", "4681"))
WHISPER_URL = f"http://{WHISPER_HOST}:{WHISPER_PORT}"
BRAIN_MODEL = os.environ.get("DOC_BRAIN_MODEL", "sonnet")
MIN_BYTES = 20_000  # ~0.6s at 16kHz mono s16 — shorter is a misfire, not speech
MIC_RATE = 16_000
MIC_BYTES_PER_SECOND = MIC_RATE * 2
MIC_RING_BYTES = MIC_BYTES_PER_SECOND * 10
MIC_PRE_ROLL_BYTES = int(MIC_BYTES_PER_SECOND * 0.3)


class BrainProcess:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.system_prompt: str | None = None
        self.stderr: deque[str] = deque(maxlen=100)
        self.lock = threading.Lock()

    def _stop(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait()

    def _drain_stderr(self, process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            self.stderr.append(line.rstrip())

    def _start(self, system_prompt: str) -> subprocess.Popen[str]:
        self._stop()
        cmd = [
            "claude", "-p", "--verbose", "--model", BRAIN_MODEL,
            "--tools", "", "--permission-mode", "dontAsk",
            "--append-system-prompt", system_prompt,
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--include-partial-messages",
        ]
        if SESSION_FILE.exists():
            cmd += ["--resume", SESSION_FILE.read_text().strip()]
        process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self.process = process
        self.system_prompt = system_prompt
        self.stderr.clear()
        threading.Thread(
            target=self._drain_stderr, args=(process,), daemon=True,
        ).start()
        return process

    def _ensure(self, system_prompt: str) -> subprocess.Popen[str]:
        if (self.process is None or self.process.poll() is not None or
                self.system_prompt != system_prompt):
            return self._start(system_prompt)
        return self.process

    def ask(self, text: str, system_prompt: str,
            on_text: Callable[[str], None]) -> subprocess.CompletedProcess:
        with self.lock:
            try:
                process = self._ensure(system_prompt)
            except OSError as exc:
                return subprocess.CompletedProcess([], 1, "", str(exc))
            # _start always opens both with PIPE; bound as locals so the reads
            # and writes below are on a pipe that is known to exist.
            stdin, stdout = process.stdin, process.stdout
            if stdin is None or stdout is None:
                self._stop()
                return subprocess.CompletedProcess(
                    [], 1, "", "brain process has no stdin/stdout pipe",
                )
            payload = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                },
            }
            try:
                stdin.write(json.dumps(payload) + "\n")
                stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._stop()
                return subprocess.CompletedProcess([], 1, "", str(exc))

            streamed = False
            result = ""
            is_error = False
            while True:
                line = stdout.readline()
                if not line:
                    try:
                        code = process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        code = process.wait()
                    self.process = None
                    error = "\n".join(self.stderr) or "brain process ended mid-turn"
                    return subprocess.CompletedProcess([], code or 1, result, error)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = event.get("session_id") or ""
                if sid:
                    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                    SESSION_FILE.write_text(sid)
                if event.get("type") == "stream_event":
                    inner = event.get("event") or {}
                    delta = inner.get("delta") or {}
                    if (inner.get("type") == "content_block_delta" and
                            delta.get("type") == "text_delta"):
                        chunk = delta.get("text") or ""
                        if chunk:
                            streamed = True
                            result += chunk
                            on_text(chunk)
                elif event.get("type") == "result":
                    final = event.get("result") or result
                    is_error = bool(event.get("is_error"))
                    if not streamed and final and not is_error:
                        result = final
                        on_text(final)
                    elif not streamed:
                        result = final
                    error = "\n".join(self.stderr)
                    return subprocess.CompletedProcess(
                        [], 1 if is_error else 0, result, error,
                    )

    def close(self) -> None:
        with self.lock:
            self._stop()


class SentenceStream:
    def __init__(self, callback: Callable[[str], None]) -> None:
        self.callback = callback
        self.pending = ""
        self.card = False

    def feed(self, chunk: str) -> None:
        if self.card:
            return
        self.pending += chunk
        card_at = self.pending.find("\nCARD: ")
        if card_at >= 0:
            self.pending = self.pending[:card_at]
            self._emit_complete()
            self.finish()
            self.card = True
            return
        self._emit_complete()

    def _emit_complete(self) -> None:
        while True:
            if ("CARD: ".startswith(self.pending) or
                    self.pending.startswith("CARD: ")):
                return
            boundary = None
            for index in range(1, len(self.pending)):
                if (self.pending[index - 1] in ".!?" and
                        self.pending[index].isspace()):
                    boundary = index
                    break
            if boundary is None:
                return
            complete = self.pending[:boundary].strip()
            self.pending = self.pending[boundary:].lstrip()
            if complete.startswith("CARD: "):
                continue
            for sentence in speak.split_sentences(complete):
                self.callback(sentence)

    def finish(self) -> None:
        complete = self.pending.strip()
        self.pending = ""
        if not complete or complete.startswith("CARD: "):
            return
        for sentence in speak.split_sentences(complete):
            self.callback(sentence)


_BRAIN = BrainProcess()
atexit.register(_BRAIN.close)
_WHISPER_PROCESS = None
_WHISPER_LOCK = threading.Lock()


def pick_mic() -> str:
    """Find the real microphone by name. Device :0 is NOT safe as a default —
    on Joe's Mac it's "Microsoft Teams Audio", a virtual device that records
    pure silence outside a call (found live, 2026-08-08, -91dB)."""
    if "DOC_MIC_DEVICE" in os.environ:
        return os.environ["DOC_MIC_DEVICE"]
    out = subprocess.run(
        ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    ).stderr
    devices = []
    in_audio = False
    for line in out.splitlines():
        if "audio devices" in line:
            in_audio = True
            continue
        if in_audio and "] [" in line:
            idx = line.split("] [")[1].split("]")[0]
            name = line.rsplit("]", 1)[1].strip()
            devices.append((idx, name))
    for idx, name in devices:  # the built-in mic is the desk default
        if "MacBook" in name and "Microphone" in name:
            return f":{idx}"
    for idx, name in devices:  # else: any real microphone, never virtual audio
        if "Microphone" in name and "Teams" not in name:
            return f":{idx}"
    return ":0"


class ResidentMic:
    def __init__(self, mic: str) -> None:
        self.mic = mic
        self.chunks: deque[tuple[int, bytes]] = deque()
        self.buffered = 0
        self.position = 0
        self.process: subprocess.Popen[bytes] | None = None
        self.stderr: deque[str] = deque(maxlen=100)
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.restart = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _append(self, data: bytes) -> None:
        if not data:
            return
        with self.lock:
            start = self.position
            self.position += len(data)
            self.chunks.append((start, data))
            self.buffered += len(data)
            while self.buffered > MIC_RING_BYTES and self.chunks:
                _offset, old = self.chunks.popleft()
                self.buffered -= len(old)

    def _drain_stderr(self, process: subprocess.Popen[bytes]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            self.stderr.append(line.decode(errors="replace").rstrip())

    def _run(self) -> None:
        retry_delay = 0.25
        while not self.stopping.is_set():
            with self.lock:
                mic = self.mic
            try:
                process = subprocess.Popen(
                    ["ffmpeg", "-loglevel", "error", "-f", "avfoundation",
                     "-i", mic, "-ac", "1", "-ar", str(MIC_RATE),
                     "-f", "s16le", "pipe:1"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                )
            except OSError:
                self.restart.wait(retry_delay)
                self.restart.clear()
                retry_delay = min(retry_delay * 2, 5)
                continue
            with self.lock:
                self.process = process
            threading.Thread(
                target=self._drain_stderr, args=(process,), daemon=True,
            ).start()
            captured = False
            stdout = process.stdout  # PIPE above; None here means nothing to read
            while not self.stopping.is_set():
                data = stdout.read(4096) if stdout is not None else b""
                if not data:
                    break
                captured = True
                self._append(data)
            if process.poll() is None:
                process.kill()
            process.wait()
            with self.lock:
                if self.process is process:
                    self.process = None
            retry_delay = 0.25 if captured else min(retry_delay * 2, 5)
            self.restart.wait(retry_delay)
            self.restart.clear()

    def begin(self) -> int | None:
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                return None
            # ALIGN TO A SAMPLE BOUNDARY. Audio is 16-bit mono, so a start on
            # an ODD byte splits every sample in half and everything after it
            # decodes as noise — whisper then invents plausible words from it
            # ("I was just 100% on the salt", live 2026-08-08). The tail is
            # already truncated to an even length in write_since(); this is
            # the missing other half of that guard.
            start = max(0, self.position - MIC_PRE_ROLL_BYTES)
            return start - (start % 2)

    def write_since(self, start: int, wav: pathlib.Path) -> int:
        with self.lock:
            end = self.position
            pieces = []
            for offset, chunk in self.chunks:
                chunk_end = offset + len(chunk)
                if chunk_end <= start or offset >= end:
                    continue
                left = max(0, start - offset)
                right = min(len(chunk), end - offset)
                pieces.append(chunk[left:right])
        audio = b"".join(pieces)
        audio = audio[:len(audio) - len(audio) % 2]
        with wave.open(str(wav), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(MIC_RATE)
            output.writeframes(audio)
        return len(audio)

    def set_mic(self, mic: str) -> None:
        with self.lock:
            self.mic = mic
            process = self.process
        self.restart.set()
        if process is not None and process.poll() is None:
            process.kill()

    def close(self) -> None:
        self.stopping.set()
        self.restart.set()
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            process.kill()
        self.thread.join(timeout=2)


def mean_volume(wav: pathlib.Path) -> float | None:
    out = subprocess.run(
        ["ffmpeg", "-i", str(wav), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    for line in out.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0])
            except ValueError:
                return None
    return None


def warm_voice() -> None:
    if not VENV_PY.exists():
        return
    if RENDER_SOCKET.exists():
        # A socket FILE is not a live daemon (crash leaves it behind, and then
        # nothing would ever respawn). Probe; unlink the corpse on refusal.
        try:
            with socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(str(RENDER_SOCKET))
            return  # alive — nothing to do
        except OSError:
            RENDER_SOCKET.unlink(missing_ok=True)
    RENDER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RENDER_LOG.open("a") as log:
        subprocess.Popen(
            [str(VENV_PY), str(RENDER_DAEMON)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print("· warming Doc's voice (background)")


def _whisper_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{WHISPER_URL}/health", timeout=0.3) as reply:
            return reply.status == 200
    except (OSError, urllib.error.URLError):
        return False


def warm_whisper() -> None:
    global _WHISPER_PROCESS
    if not pathlib.Path(WHISPER_SERVER).exists() or not MODEL.exists():
        return
    if _whisper_ready():
        return
    with _WHISPER_LOCK:
        if _whisper_ready():
            return
        if _WHISPER_PROCESS is not None and _WHISPER_PROCESS.poll() is None:
            return
        WHISPER_LOG.parent.mkdir(parents=True, exist_ok=True)
        try:
            with WHISPER_LOG.open("a") as log:
                _WHISPER_PROCESS = subprocess.Popen(
                    [WHISPER_SERVER, "-m", str(MODEL), "--host", WHISPER_HOST,
                     "--port", str(WHISPER_PORT), "-nt"],
                    stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True,
                )
        except OSError:
            _WHISPER_PROCESS = None
            return
        print("· warming Doc's ears (background)")


def _multipart(wav: pathlib.Path, prompt: str) -> tuple[bytes, str]:
    boundary = f"doc-{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in (("prompt", prompt), ("response_format", "json")):
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        )
        body.extend(value.encode())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
        b"Content-Type: audio/wav\r\n\r\n"
    )
    body.extend(wav.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _transcribe_server(wav: pathlib.Path, prompt: str) -> str:
    warm_whisper()
    deadline = time.monotonic() + 20
    while not _whisper_ready():
        if (_WHISPER_PROCESS is None or
                _WHISPER_PROCESS.poll() is not None or
                time.monotonic() >= deadline):
            raise ConnectionError("whisper-server did not become ready")
        time.sleep(0.05)
    body, content_type = _multipart(wav, prompt)
    request = urllib.request.Request(
        f"{WHISPER_URL}/inference", data=body, method="POST",
        headers={"Content-Type": content_type},
    )
    with urllib.request.urlopen(request, timeout=120) as reply:
        payload = json.loads(reply.read())
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise ValueError("whisper-server returned no text")
    return payload["text"].replace("\n", " ").strip()


def refresh_hot_context() -> str | None:
    age = time.time() - CONTEXT.stat().st_mtime if CONTEXT.exists() else 1e9
    if age > 1800:
        print("· refreshing hot context ...")
        if subprocess.run([str(TOOL / "bin" / "refresh-context.sh")]).returncode != 0:
            print("· refresh failed — using last snapshot if any")
    if not CONTEXT.exists():
        print("no hot-context snapshot and refresh failed")
        return None
    return PREAMBLE.read_text() + "\n" + CONTEXT.read_text()


def transcribe(wav: pathlib.Path) -> str:
    prompt = VOCAB.read_text()
    try:
        return _transcribe_server(wav, prompt)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        pass
    return subprocess.run(
        [WHISPER, "-m", str(MODEL), "-f", str(wav),
         "--prompt", prompt, "-nt", "-np"],
        capture_output=True, text=True,
    ).stdout.replace("\n", " ").strip()


def ask_brain_streaming(
        text: str, system_prompt: str,
        on_sentence: Callable[[str], None] | None = None,
        on_complete: Callable[[str], None] | None = None,
) -> tuple[str, subprocess.CompletedProcess]:
    sentences = SentenceStream(on_sentence or (lambda _sentence: None))
    brain = _BRAIN.ask(text, system_prompt, sentences.feed)
    if brain.returncode == 0:
        sentences.finish()
    reply = brain.stdout.strip()
    if on_complete is not None:
        on_complete(reply)
    return reply, brain


def ask_brain(text: str, system_prompt: str) -> tuple[str, subprocess.CompletedProcess]:
    return ask_brain_streaming(text, system_prompt)
