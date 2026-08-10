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
sys.path.insert(0, str(BIN_DIR))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BIN_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


call_mode = load_module("call_mode_under_test", "call-mode.py")
transcribe_session = load_module("transcribe_session_under_test", "transcribe_session.py")
post_call = load_module("post_call_under_test", "post_call.py")
capture_bridge = load_module("capture_bridge_under_test", "capture-bridge.py")


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
        transcribe_session.write_outputs(self.session, transcript)
        self.assertEqual(
            stat.S_IMODE((self.session / "transcript.json").stat().st_mode), 0o600
        )
        self.assertFalse((self.session / "transcript.md").exists())


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

    def test_post_call_report_requires_origin_and_non_simple_header(self) -> None:
        handler = self.handler("/api/post-call?session=s1", b"", None)
        handler.do_GET()
        handler.send_json.assert_called_once_with({"error": "origin_not_allowed"}, 403)

    def test_call_context_is_stored_and_draft_route_syncs_before_local_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp)
            session = recordings / "s1"
            session.mkdir()
            context = {
                "session": "s1", "workspace_kind": "team", "generated_at": "now", "deals": [],
            }
            body = json.dumps(context).encode()
            handler = self.handler("/api/call-context", body, "https://dealroom.doctorcre.com")
            with patch.object(call_mode, "RECORDINGS", recordings):
                handler.do_POST()
            handler.send_json.assert_called_once_with({"ok": True, "session": "s1"})
            self.assertEqual(stat.S_IMODE((session / post_call.CONTEXT_FILE).stat().st_mode), 0o600)

            draft_body = json.dumps({"session": "s1", "approved_content_hash": "a" * 64}).encode()
            draft = self.handler(
                "/api/post-call/drafts/draft-1/create", draft_body,
                "https://dealroom.doctorcre.com",
            )
            with (
                patch.object(call_mode, "RECORDINGS", recordings),
                patch.object(call_mode, "sync_post_call", return_value={"status": {"state": "ready_review"}}) as sync,
                patch.object(call_mode.post_call, "create_outlook_draft", return_value={"draft_id": "draft-1", "idempotent": False}) as create,
            ):
                draft.do_POST()
            sync.assert_called_once_with(session.resolve())
            create.assert_called_once_with(session.resolve(), "draft-1", "a" * 64)
            draft.send_json.assert_called_once_with({"draft_id": "draft-1", "idempotent": False})

    def test_context_body_over_limit_is_rejected_not_truncated(self) -> None:
        handler = self.handler(
            "/api/call-context", b"{}", "https://dealroom.doctorcre.com",
        )
        handler.headers.replace_header("Content-Length", str(call_mode.CONTEXT_BODY_LIMIT + 1))
        handler.do_POST()
        handler.send_json.assert_called_once_with({"error": "request body too large"}, 409)


class PostCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Path(self.tmp.name) / "2026.08.10-1234"
        self.session.mkdir()
        self.context = {
            "session": self.session.name,
            "workspace_kind": "deal_room",
            "generated_at": "2026-08-10T12:34:00Z",
            "deals": [
                {"id": "deal-a", "name": "A", "owner": "Joe", "operating_state": "active", "participants": [
                    {"party_id": "p-1", "ref": "party/p-1", "name": "Vendor A", "email": "a@example.test", "role": "vendor"}
                ]},
                {"id": "deal-b", "name": "B", "owner": "Dell", "operating_state": "active", "participants": [
                    {"party_id": "p-1", "ref": "party/p-1", "name": "Vendor A", "email": "a@example.test", "role": "vendor"}
                ]},
            ],
        }
        (self.session / "transcript.json").write_text(json.dumps({"segments": [{"speaker": "Joe", "text": "Call Vendor A"}]}))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def output(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": 1,
            "report": {"summary": "Summary", "decisions": [], "open_questions": []},
            "joe_tasks": [{"title": "Call vendor", "deal_id": "deal-a", "participant_ids": ["p-1"], "evidence": "Joe agreed to call."}],
            "dell_tasks": [],
            "deal_updates": [{"deal_id": "deal-a", "summary": "Vendor follow-up", "participant_ids": ["p-1"], "evidence": "Vendor requested a follow-up."}],
            "draft_proposals": [{"recipient_party_id": "p-1", "deal_id": "deal-a", "subject": "Next steps", "body": "Thank you.", "participant_ids": ["p-1"], "evidence": "Vendor requested a follow-up."}],
            "review_questions": [],
        }
        result.update(overrides)
        return result

    def test_context_allows_same_party_on_different_deals_but_exact_output_attaches_to_its_deal(self) -> None:
        post_call.validate_context(self.context, self.session.name)
        bad = self.output(joe_tasks=[{"title": "Wrong", "deal_id": "deal-a", "participant_ids": ["missing"], "evidence": "One two."}])
        normalized = post_call.normalize_distillation(bad, self.context, self.session.name)
        self.assertEqual(normalized["joe_tasks"], [])
        self.assertIn("unknown or ambiguous participant_ids", normalized["review_questions"][0]["question"])

    def test_evidence_is_bounded_and_context_is_consumed_after_local_processing(self) -> None:
        bad = self.output(joe_tasks=[{"title": "Too much", "deal_id": "deal-a", "participant_ids": [], "evidence": "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen"}])
        bounded = post_call.normalize_distillation(bad, self.context, self.session.name)
        self.assertEqual(len(bounded["joe_tasks"][0]["evidence"].split()), 15)

        post_call.store_context(self.session, self.context)
        status = post_call.process_session(self.session, distiller=lambda _request: self.output())
        self.assertEqual(status["state"], "ready_review")
        self.assertIsNone(post_call.context_from_session(self.session))
        for name in (post_call.REPORT_FILE, post_call.STATUS_FILE, post_call.CONTEXT_FILE):
            self.assertEqual(stat.S_IMODE((self.session / name).stat().st_mode), 0o600)

    def test_oversize_outlook_draft_becomes_a_review_question_not_a_dead_candidate(self) -> None:
        oversized = self.output(draft_proposals=[{
            "recipient_party_id": "p-1", "deal_id": "deal-a", "subject": "Next steps",
            "body": "📌" * 1000, "participant_ids": ["p-1"],
            "evidence": "Vendor requested a follow-up.",
        }])
        normalized = post_call.normalize_distillation(oversized, self.context, self.session.name)
        self.assertEqual(normalized["draft_proposals"], [])
        self.assertIn("Outlook draft size limit", normalized["review_questions"][0]["question"])

    def test_sanitized_candidates_never_include_email_body_or_recipient_email(self) -> None:
        post_call.store_context(self.session, self.context)
        post_call.process_session(self.session, distiller=lambda _request: self.output())
        shaped = post_call.sanitized_candidates(self.session)
        raw = json.dumps(shaped)
        self.assertNotIn("Thank you.", raw)
        self.assertNotIn("a@example.test", raw)
        self.assertEqual(
            [item["kind"] for item in shaped["post_call_items"]],
            ["assigned_action", "email_draft"],
        )
        self.assertEqual([item["kind"] for item in shaped["legacy_items"]], ["activity"])
        self.assertEqual(shaped["legacy_items"][0]["payload"]["ref"], "deal-a")
        remote_items = shaped["post_call_items"] + shaped["legacy_items"]
        self.assertLessEqual(max(len(item["evidence_quote"].split()) for item in remote_items), 15)

    def test_candidate_receipts_are_bound_to_private_report_items(self) -> None:
        post_call.store_context(self.session, self.context)
        post_call.process_session(self.session, distiller=lambda _request: self.output())
        shaped = post_call.sanitized_candidates(self.session)
        post_bindings = [binding for binding in shaped["bindings"] if binding["remote"] == "post_call"]
        receipt = ["candidate-action", "candidate-draft"]
        post_call.apply_candidate_ids(self.session, post_bindings, receipt)
        report = post_call.read_json(self.session / post_call.REPORT_FILE)
        assert report is not None
        self.assertEqual(report["joe_tasks"][0]["candidate_id"], "candidate-action")
        self.assertEqual(report["draft_proposals"][0]["candidate_id"], "candidate-draft")
        self.assertEqual(report["draft_proposals"][0]["candidate_status"], "pending")

    def test_retention_requires_final_candidate_dispositions(self) -> None:
        post_call.store_context(self.session, self.context)
        post_call.process_session(self.session, distiller=lambda _request: self.output())
        self.assertIsNone(post_call.retention_ready(self.session, "backend-report-hash"))
        report = post_call.read_json(self.session / post_call.REPORT_FILE)
        assert report is not None
        for list_name in ("joe_tasks", "dell_tasks", "deal_updates", "draft_proposals"):
            for index, item in enumerate(report[list_name]):
                item["candidate_id"] = f"{list_name}-{index}"
                item["candidate_status"] = "confirmed"
        post_call.write_private(self.session / post_call.REPORT_FILE, report)
        receipt = post_call.retention_ready(self.session, "backend-report-hash")
        self.assertEqual(receipt["pending_items"], 0)
        self.assertTrue(receipt["aggregate_report_hash"])

    def test_command_distiller_is_injectable_and_never_runs_a_real_command_in_tests(self) -> None:
        class Result:
            returncode = 0
            stdout = '{"schema_version": 1}'

        runner = Mock(return_value=Result())
        result = post_call.command_distiller({"session": self.session.name}, command="fixture-distiller", runner=runner)
        self.assertEqual(result, {"schema_version": 1})
        self.assertEqual(runner.call_args.args[0], ["fixture-distiller"])
        self.assertFalse(runner.call_args.kwargs["shell"])

    def test_local_model_contract_is_strict_and_tolerates_a_json_code_fence(self) -> None:
        schema = post_call.distiller_json_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(post_call.parse_model_json('```json\n{"ok": true}\n```'), {"ok": True})

    def test_post_call_model_port_cannot_collide_with_quill_services(self) -> None:
        self.assertNotIn(post_call.LLAMA_PORT, {8596, 8597})

    def test_long_transcript_is_split_on_segments_and_merged_without_a_real_model(self) -> None:
        transcript = {"segments": [{"speaker": "Joe", "text": "x" * 14000}, {"speaker": "Dell", "text": "y" * 14000}, {"speaker": "Joe", "text": "z" * 14000}]}
        chunks = post_call.transcript_chunks(transcript, limit=25000)
        self.assertEqual(len(chunks), 3)
        first = self.output()
        second = self.output(joe_tasks=[])
        merged = post_call.merge_chunk_outputs([first, second])
        self.assertEqual(len(merged["joe_tasks"]), 1)
        self.assertEqual(merged["report"]["summary"], "Summary\n\nSummary")

    def test_draft_creator_is_injectable_and_requires_a_confirmed_candidate(self) -> None:
        post_call.store_context(self.session, self.context)
        post_call.process_session(self.session, distiller=lambda _request: self.output())
        report = post_call.read_json(self.session / post_call.REPORT_FILE)
        assert report is not None
        draft = report["draft_proposals"][0]
        draft["candidate_id"] = "email-candidate-1"
        draft["candidate_status"] = "confirmed"
        post_call.write_private(self.session / post_call.REPORT_FILE, report)

        class Result:
            returncode = 0
            stdout = "draft only"
            stderr = ""

        runner = Mock(return_value=Result())
        created = post_call.create_outlook_draft(self.session, draft["draft_id"], draft["content_hash"], runner=runner)
        self.assertFalse(created["idempotent"])
        self.assertEqual(Path(runner.call_args.args[0][-1]).name, "outlook-draft.py")
        again = post_call.create_outlook_draft(self.session, draft["draft_id"], draft["content_hash"], runner=runner)
        self.assertTrue(again["idempotent"])
        self.assertEqual(runner.call_count, 1)


class CaptureBridgePostCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Path(self.tmp.name) / "2026.08.10-bridge"
        self.session.mkdir()
        post_call.write_private(
            self.session / ".capture.json", {"session_token": "opaque-session-token"},
        )
        report = {
            "schema_version": 1, "session": self.session.name, "generated_at": "now",
            "report": {"summary": "Weekly summary", "decisions": [], "open_questions": []},
            "joe_tasks": [{"title": "Call vendor", "deal_id": "11111111-1111-4111-8111-111111111111", "participant_ids": [], "evidence": "Joe will call vendor.", "candidate_id": None, "candidate_status": "unfiled"}],
            "dell_tasks": [],
            "deal_updates": [{"deal_id": "11111111-1111-4111-8111-111111111111", "summary": "Waiting on vendor", "participant_ids": [], "evidence": "Vendor owes an update.", "candidate_id": None, "candidate_status": "unfiled"}],
            "draft_proposals": [{
                "recipient_party_id": "22222222-2222-4222-8222-222222222222",
                "recipient_ref": "P-001", "deal_id": "11111111-1111-4111-8111-111111111111",
                "subject": "Next steps", "body": "Private body", "participant_ids": [],
                "evidence": "Vendor requested a follow-up.", "recipient_email": "vendor@example.test",
                "recipient_name": "Vendor", "deal_name": "Deal", "draft_id": "draft-1",
                "content_hash": "a" * 64, "candidate_id": None, "candidate_status": "unfiled",
            }],
            "review_questions": [],
        }
        post_call.write_private(self.session / post_call.REPORT_FILE, report)
        post_call.write_private(
            self.session / post_call.STATUS_FILE,
            {"schema_version": 1, "session": self.session.name, "state": "ready_review"},
        )
        self.cfg = {"base_url": "https://worker.test", "device_id": "joe", "token": "device"}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_claim_wait_helper_observes_late_call_mode_context(self) -> None:
        self.assertIsNone(capture_bridge.wait_for_call_mode(self.session, timeout=0))
        ticks = iter([0.0, 0.01])

        def late_context(_seconds: float) -> None:
            post_call.write_private(
                self.session / "call-context.json", {"mode": "weekly_deal_call"},
            )

        with (
            patch.object(capture_bridge.time, "monotonic", side_effect=lambda: next(ticks)),
            patch.object(capture_bridge.time, "sleep", side_effect=late_context),
        ):
            self.assertEqual(
                capture_bridge.wait_for_call_mode(self.session, timeout=1),
                "weekly_deal_call",
            )

    def test_claim_marks_weekly_workflow_only_after_bounded_mode_resolution(self) -> None:
        (self.session / ".capture.json").unlink()
        post_call.write_private(
            self.session / "announcement.json",
            {"announcement_fired_at": "2026-08-10T12:00:00Z"},
        )
        post_call.write_private(
            self.session / "meta.json", {"started": "2026-08-10T12:00:00Z"},
        )
        claims: list[dict[str, object]] = []

        def request(_method, url, body, _headers):
            if url.endswith("/capture/claim"):
                claims.append(body)
                return 200, {"session_token": "opaque", "ttl_seconds": 60}
            if url.endswith("/capture/status"):
                return 200, {"ok": True}
            raise AssertionError(url)

        with (
            patch.object(capture_bridge, "wait_for_call_mode", return_value="weekly_deal_call"),
            patch.object(capture_bridge, "http_request", side_effect=request),
        ):
            self.assertEqual(capture_bridge.claim_cmd(self.cfg, self.session), 0)
        self.assertEqual(claims[0]["workflow"], "post_call")

    def test_publish_exports_only_sanitized_metadata_and_binds_all_receipts(self) -> None:
        calls: list[tuple[str, str, dict[str, object] | None]] = []

        def request(method, url, body, _headers):
            calls.append((method, url, body))
            if url.endswith("/capture/status"):
                return 200, {"ok": True}
            if url.endswith("/capture/post-call/candidates"):
                return 200, {"candidate_ids": ["action-id", "draft-id"]}
            if url.endswith("/capture/candidates"):
                return 200, {"candidate_ids": ["update-id"]}
            raise AssertionError(url)

        with patch.object(capture_bridge, "http_request", side_effect=request):
            self.assertEqual(capture_bridge.publish_cmd(self.cfg, self.session), 0)
        exported = json.dumps(calls)
        self.assertNotIn("Private body", exported)
        self.assertNotIn("vendor@example.test", exported)
        report = post_call.read_json(self.session / post_call.REPORT_FILE)
        assert report is not None
        self.assertEqual(report["joe_tasks"][0]["candidate_id"], "action-id")
        self.assertEqual(report["draft_proposals"][0]["candidate_id"], "draft-id")
        self.assertEqual(report["deal_updates"][0]["candidate_id"], "update-id")
        self.assertEqual(stat.S_IMODE((self.session / post_call.PUSH_FILE).stat().st_mode), 0o600)

    def test_poll_syncs_all_lanes_files_report_then_marks_purge_safe(self) -> None:
        report = post_call.read_json(self.session / post_call.REPORT_FILE)
        assert report is not None
        ids = ["action-id", "update-id", "draft-id"]
        for item, candidate_id in zip(
            [report["joe_tasks"][0], report["deal_updates"][0], report["draft_proposals"][0]], ids,
        ):
            item["candidate_id"] = candidate_id
            item["candidate_status"] = "pending"
        post_call.write_private(self.session / post_call.REPORT_FILE, report)
        report_posts: list[dict[str, object]] = []

        def request(method, url, body, _headers):
            if url.endswith("/capture/status"):
                return 200, {"ok": True}
            if url.endswith("/capture/session"):
                return 200, {
                    "state": "distilling", "post_call": True,
                    "candidates": {"pending": 0, "confirmed": 3, "skipped": 0},
                    "candidate_statuses": [
                        {"id": "action-id", "kind": "assigned_action", "status": "confirmed", "source": "post_call", "resulting_ref": "action-ref"},
                        {"id": "update-id", "kind": "activity", "status": "confirmed", "source": "legacy", "resulting_ref": "activity-ref"},
                        {"id": "draft-id", "kind": "email_draft", "status": "confirmed", "source": "post_call", "resulting_ref": None},
                    ],
                    "post_call_report": {"filed": False}, "meeting_record": None,
                }
            if url.endswith("/capture/post-call/report"):
                report_posts.append(body)
                return 200, {"filed": True, "candidate_count": 3}
            raise AssertionError(url)

        with patch.object(capture_bridge, "http_request", side_effect=request):
            self.assertEqual(capture_bridge.poll_cmd(self.cfg, self.session), 0)
        self.assertEqual(report_posts[0]["candidate_count"], 3)
        self.assertTrue((self.session / "ingested.json").exists())
        self.assertFalse((self.session / ".capture.json").exists())
        final = post_call.read_json(self.session / post_call.REPORT_FILE)
        assert final is not None
        self.assertTrue(final["backend_report_sha256"])
        for key in ("joe_tasks", "deal_updates", "draft_proposals"):
            self.assertEqual(final[key][0]["candidate_status"], "confirmed")

    def test_poll_never_files_zero_count_report_when_candidate_publish_fails(self) -> None:
        calls: list[str] = []

        def request(_method, url, _body, _headers):
            calls.append(url)
            if url.endswith("/capture/status"):
                return 200, {"ok": True}
            if url.endswith("/capture/post-call/candidates"):
                return 503, {"error": "unavailable"}
            if url.endswith("/capture/candidates"):
                return 200, {"candidate_ids": ["update-id"]}
            raise AssertionError(url)

        with patch.object(capture_bridge, "http_request", side_effect=request):
            self.assertEqual(capture_bridge.poll_cmd(self.cfg, self.session), 0)
        self.assertFalse(any(url.endswith("/capture/session") for url in calls))
        self.assertFalse(any(url.endswith("/capture/post-call/report") for url in calls))
        self.assertTrue((self.session / ".capture.json").exists())


if __name__ == "__main__":
    unittest.main()
