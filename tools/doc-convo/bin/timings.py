#!/usr/bin/env python3
"""Small monotonic turn timer."""

import threading
import time


STAGES = {
    "mic_open": ("mic_open_start", "mic_open_done"),
    "record_stop": ("record_stop_start", "record_stop_done"),
    "volume_check": ("volume_check_start", "volume_check_done"),
    "stt": ("stt_start", "stt_done"),
    "reflex_check": ("reflex_check_start", "reflex_check_done"),
    "brain_ttfs": ("brain_start", "brain_first_sentence"),
    "brain_total": ("brain_start", "brain_done"),
    "tts_first_audio": ("tts_start", "tts_first_audio"),
    "tts_total": ("tts_start", "tts_done"),
}


class Turn:
    def __init__(self) -> None:
        self.started = time.monotonic_ns()
        self.marks: dict[str, int] = {}
        self.lock = threading.Lock()

    def mark(self, name: str) -> None:
        with self.lock:
            self.marks.setdefault(name, time.monotonic_ns())

    def report(self) -> dict[str, float]:
        finished = time.monotonic_ns()
        with self.lock:
            marks = dict(self.marks)
        report = {}
        for stage, (start, done) in STAGES.items():
            if start in marks and done in marks:
                report[stage] = round((marks[done] - marks[start]) / 1e6, 1)
            else:
                report[stage] = 0.0
        report["total_ms"] = round((finished - self.started) / 1e6, 1)
        return report
