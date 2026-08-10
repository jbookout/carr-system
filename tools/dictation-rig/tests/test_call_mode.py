"""Offline regression tests for Deal Room Call Mode.

They exercise only filesystem-based state and context contracts.  Quill and
macOS accessibility are intentionally never invoked here.
"""

from __future__ import annotations

import importlib.util
import io
import json
import stat
import sys
import tempfile
import time
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch


BIN_DIR = Path(__file__).resolve().parents[1] / "bin"


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BIN_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


call_mode = load_module("call_mode_under_test", "call-mode.py")
transcribe_session = load_module("transcribe_session_under_test", "transcribe_session.py")


class CallModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.recordings = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def session(self, name: str) -> Path:
        path = self.recordings / name
        path.mkdir()
        return path

    def state(self) -> dict[str, object]:
        with patch.object(call_mode, "partner_from_device", return_value="Joe"):
            return call_mode.current_state(self.recordings)

    def test_weekly_deal_labels_pair_joe_and_dell(self) -> None:
        self.assertEqual(call_mode.labels_for("weekly_deal_call", "Joe"), ("Joe", "Dell"))
        self.assertEqual(call_mode.labels_for("weekly_deal_call", "Dell"), ("Dell", "Joe"))

    def test_other_call_has_generic_other_participant_label(self) -> None:
        self.assertEqual(
            call_mode.labels_for("other_call", "Joe"), ("Joe", "Other participant")
        )
        self.assertEqual(
            call_mode.labels_for("weekly_deal_call", None), ("Me", "Other participant")
        )

    def test_write_call_context_persists_mode_recorder_and_channel_labels(self) -> None:
        session = self.session("weekly")
        context = call_mode.write_call_context(session, "weekly_deal_call", "Joe")

        persisted = json.loads((session / call_mode.CONTEXT_FILE).read_text())
        self.assertEqual(context, persisted)
        self.assertEqual(persisted["mode"], "weekly_deal_call")
        self.assertEqual(persisted["recorder"], "Joe")
        self.assertEqual(persisted["speaker_labels"], {"mic": "Joe", "system": "Dell"})
        self.assertEqual(
            persisted["speaker_method"], "separate audio channels; no third-party voiceprint"
        )
        self.assertRegex(persisted["started_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(stat.S_IMODE((session / call_mode.CONTEXT_FILE).stat().st_mode), 0o600)

    def test_start_and_stop_drive_only_the_existing_quill_menu_contract(self) -> None:
        session = self.recordings / "started-by-quill"

        def quill_action(title: str) -> None:
            if title == "Start recording":
                session.mkdir()
                (session / "mic.caf").touch()
            elif title == "Stop recording":
                (session / "meta.json").write_text("{}")

        with (
            patch.object(call_mode, "LOCK_PATH", self.recordings / "operation.lock"),
            patch.object(call_mode, "run_quill_menu_item", side_effect=quill_action) as menu,
            patch.object(call_mode, "partner_from_device", return_value="Joe"),
        ):
            started = call_mode.start_recording("weekly_deal_call", self.recordings)
            stopped = call_mode.stop_recording(self.recordings)

        self.assertEqual(started["state"], "recording")
        self.assertEqual(stopped["state"], "transcribing")
        self.assertEqual(
            [call.args[0] for call in menu.call_args_list],
            ["Start recording", "Stop recording"],
        )

    def test_current_state_detects_recording(self) -> None:
        session = self.session("recording")
        (session / "mic.caf").touch()
        call_mode.write_call_context(session, "weekly_deal_call", "Joe")

        result = self.state()

        self.assertEqual(result["state"], "recording")
        self.assertEqual(result["session"], "recording")
        self.assertEqual(result["mode"], "weekly_deal_call")
        self.assertEqual(result["speaker_labels"], {"mic": "Joe", "system": "Dell"})

    def test_current_state_detects_transcribing_ready_to_extract_and_filed(self) -> None:
        session = self.session("terminal")
        (session / "meta.json").write_text("{}")
        call_mode.write_call_context(session, "other_call", "Joe")

        self.assertEqual(self.state()["state"], "transcribing")
        (session / "transcript.json").write_text("{}")
        self.assertEqual(self.state()["state"], "ready_to_extract")
        (session / "ingested.json").write_text("{}")
        self.assertEqual(self.state()["state"], "filed")

    def test_stale_partial_session_is_not_reported_as_recording(self) -> None:
        session = self.session("stale-partial")
        track = session / "mic.caf"
        track.touch()
        stale = time.time() - call_mode.ACTIVE_FILE_WINDOW_SECONDS - 1
        import os

        os.utime(track, (stale, stale))
        self.assertEqual(self.state()["state"], "state_unknown")

    def test_current_state_is_idle_without_sessions_or_when_newest_session_is_stale(self) -> None:
        self.assertEqual(self.state(), {"state": "idle", "local_partner": "Joe"})

        session = self.session("old-terminal")
        (session / "meta.json").write_text("{}")
        stale = time.time() - call_mode.TERMINAL_AGE_SECONDS - 1
        session.touch()
        import os

        os.utime(session, (stale, stale))
        self.assertEqual(self.state(), {"state": "idle", "local_partner": "Joe"})


class SpeakerLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_speaker_labels_fall_back_for_missing_malformed_or_invalid_context(self) -> None:
        expected = {"mic": "Me", "system": "Other participant"}
        self.assertEqual(transcribe_session.read_speaker_labels(self.session), expected)

        (self.session / "call-context.json").write_text("not json")
        self.assertEqual(transcribe_session.read_speaker_labels(self.session), expected)

        (self.session / "call-context.json").write_text(
            json.dumps({"speaker_labels": {"mic": "  ", "system": "x" * 81}})
        )
        self.assertEqual(transcribe_session.read_speaker_labels(self.session), expected)

    def test_speaker_labels_override_defaults_per_valid_channel_and_trim_whitespace(self) -> None:
        (self.session / "call-context.json").write_text(
            json.dumps({"speaker_labels": {"mic": " Joe ", "system": "Dell"}})
        )
        self.assertEqual(
            transcribe_session.read_speaker_labels(self.session),
            {"mic": "Joe", "system": "Dell"},
        )

        (self.session / "call-context.json").write_text(
            json.dumps({"speaker_labels": {"mic": "Dell", "system": 4}})
        )
        self.assertEqual(
            transcribe_session.read_speaker_labels(self.session),
            {"mic": "Dell", "system": "Other participant"},
        )

    def test_transcript_outputs_are_private_to_the_local_user(self) -> None:
        transcript = {
            "session": "test",
            "engine": "test",
            "model": "test",
            "vocab_prompt": False,
            "generated": "2026-08-10T00:00:00Z",
            "consent": None,
            "speaker_labels": {"mic": "Joe", "system": "Dell"},
            "speaker_method": "separate audio channels; no third-party voiceprint",
            "segments": [],
        }
        transcribe_session.write_outputs(self.session, transcript, "# Test\n")
        for name in ("transcript.json", "transcript.md"):
            self.assertEqual(stat.S_IMODE((self.session / name).stat().st_mode), 0o600)


class CallModeHttpTests(unittest.TestCase):
    def handler(self, path: str, body: bytes, origin: str | None):
        handler = object.__new__(call_mode.CallModeHandler)
        headers = Message()
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
        headers[call_mode.POST_HEADER] = call_mode.POST_HEADER_VALUE
        if origin:
            headers["Origin"] = origin
        handler.headers = headers
        handler.path = path
        handler.rfile = io.BytesIO(body)
        handler.send_json = Mock()
        return handler

    def test_post_requires_an_allowlisted_browser_origin(self) -> None:
        handler = self.handler("/api/stop", b"{}", None)
        handler.do_POST()
        handler.send_json.assert_called_once_with({"error": "origin_not_allowed"}, 403)

    def test_allowed_origin_can_stop_without_invoking_real_quill(self) -> None:
        handler = self.handler("/api/stop", b"{}", "http://127.0.0.1:4682")
        with patch.object(call_mode, "stop_recording", return_value={"state": "idle"}) as stop:
            handler.do_POST()
        handler.send_json.assert_called_once_with({"state": "idle"})
        stop.assert_called_once_with()

    def test_start_requires_explicit_consent_even_from_allowed_origin(self) -> None:
        handler = self.handler(
            "/api/start",
            b'{"mode":"weekly_deal_call"}',
            "https://dealroom.doctorcre.com",
        )
        with patch.object(call_mode, "start_recording") as start:
            handler.do_POST()
        handler.send_json.assert_called_once_with(
            {"error": "confirm that everyone has been told before recording"}, 409
        )
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
