"""Offline tests for tools/partner-line/watch.py — the acceptance bar for
open idea #78 item 1.

NO LIVE SOCKET, NO NETWORK. poll_once() takes fetch/injector/notifier as
injectable dependencies specifically so these tests can stand fakes in for
all three; nothing here opens a real unix socket or calls the deployed
Worker. Same offline-only stance as tools/dictation-rig/tests/test_call_mode.py.

Run:
    python3 -m pytest tools/partner-line/tests/test_watch.py
    # or, no pytest required:
    python3 -m unittest tools/partner-line/tests/test_watch.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

WATCH_PATH = Path(__file__).resolve().parents[1] / "watch.py"


def load_watch():
    spec = importlib.util.spec_from_file_location("partner_line_watch", WATCH_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


watch = load_watch()


def turn(seq, sponsor, seat="claude", kind="turn", body="hi"):
    return {"seq": seq, "sponsor": sponsor, "seat": seat, "kind": kind, "body": body}


class FakeFetch:
    """Stands in for watch.fetch_turns: hands back a canned batch regardless
    of the after_seq it's called with, and records every call it saw."""

    def __init__(self, turns):
        self.turns = turns
        self.calls: list[int] = []

    def __call__(self, after_seq, *, room):
        self.calls.append(after_seq)
        return {"ok": True, "room": room, "turns": self.turns,
                "latest_seq": max((t["seq"] for t in self.turns), default=after_seq),
                "more": False}


class RecordingInjector:
    def __init__(self):
        self.injected: list[tuple[str, dict]] = []

    def __call__(self, sock_path, payload):
        self.injected.append((sock_path, payload))


class RecordingNotifier:
    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, title, subtitle, message):
        self.calls.append((title, subtitle, message))


class PureLogicTests(unittest.TestCase):
    """classify_turn / new_turns / next_watermark / format_body / build_peer_message."""

    def test_classify_skips_self_sponsor(self):
        self.assertEqual(watch.classify_turn(turn(1, "joe"), "joe"), "skip_self")

    def test_classify_selects_other_partner(self):
        self.assertEqual(watch.classify_turn(turn(1, "dell"), "joe"), "inject")

    def test_classify_skips_non_turn_kind(self):
        self.assertEqual(watch.classify_turn(turn(1, "dell", kind="system"), "joe"), "skip_kind")
        self.assertEqual(watch.classify_turn(turn(2, "dell", kind="receipt"), "joe"), "skip_kind")

    def test_new_turns_drops_already_seen_seqs(self):
        turns = [turn(1, "dell"), turn(2, "dell"), turn(3, "dell")]
        self.assertEqual([t["seq"] for t in watch.new_turns(turns, since_seq=1)], [2, 3])
        self.assertEqual(watch.new_turns(turns, since_seq=3), [])

    def test_watermark_advances_over_skipped_turns_too(self):
        # A skip still counts as processed; the watermark must move past it or
        # the same skipped turn would be re-fetched forever.
        turns = [turn(5, "joe", kind="turn"), turn(6, "dell", kind="system")]
        self.assertEqual(watch.next_watermark(4, turns), 6)

    def test_watermark_never_goes_backward(self):
        self.assertEqual(watch.next_watermark(10, [turn(3, "dell")]), 10)

    def test_format_body_names_sponsor_and_seat(self):
        self.assertEqual(
            watch.format_body(turn(1, "dell", seat="claude", body="ping")),
            "[dell · claude] ping",
        )

    def test_peer_message_shape_matches_spec(self):
        msg = watch.build_peer_message("hello")
        self.assertEqual(msg, {
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "origin": {"kind": "peer"},
        })


class PidSocketRefusalTests(unittest.TestCase):
    """The one non-negotiable rule: never inject to an unlabeled/pid socket."""

    def test_bare_digit_label_is_refused(self):
        with self.assertRaises(ValueError):
            watch.validate_target_label("12345")

    def test_explicit_pid_sock_filename_is_refused(self):
        with self.assertRaises(ValueError):
            watch.validate_target_label("12345.sock")

    def test_socket_path_for_label_refuses_pid_shape(self):
        with self.assertRaises(ValueError):
            watch.socket_path_for_label("98765", sock_dir="/tmp/cc-socks")

    def test_registered_label_is_accepted(self):
        path = watch.socket_path_for_label("dell-main", sock_dir="/tmp/cc-socks")
        self.assertEqual(path, "/tmp/cc-socks/dell-main.sock")

    def test_label_with_path_separator_is_refused(self):
        with self.assertRaises(ValueError):
            watch.validate_target_label("../etc/passwd")

    def test_empty_label_is_refused(self):
        with self.assertRaises(ValueError):
            watch.validate_target_label("")
        with self.assertRaises(ValueError):
            watch.validate_target_label("   ")

    def test_label_that_merely_starts_with_digits_is_fine(self):
        # Only a BARE-digit filename is a pid socket; "42-dell" is a real label.
        watch.validate_target_label("42-dell")  # must not raise


class PollOnceTests(unittest.TestCase):
    """poll_once(): watermark advance, --since override, --dry-run, partner
    filter, and the kill switch — all with fakes standing in for the socket,
    the subprocess read, and osascript."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "partner-line-watch.json"
        self.pause_path = Path(self.tmp.name) / "partner-line-paused"

    def tearDown(self):
        self.tmp.cleanup()

    def _poll(self, turns, **kwargs):
        fetch = FakeFetch(turns)
        injector = RecordingInjector()
        notifier = RecordingNotifier()
        result = watch.poll_once(
            self_partner="joe",
            target_label="joe-main",
            state_path=self.state_path,
            pause_path=self.pause_path,
            fetch=fetch,
            injector=injector,
            notifier=notifier,
            **kwargs,
        )
        return result, fetch, injector, notifier

    def test_first_poll_injects_other_partner_and_advances_watermark(self):
        result, _, injector, notifier = self._poll([turn(1, "dell", body="hey joe")])
        self.assertEqual(result["injected"], 1)
        self.assertEqual(result["watermark"], 1)
        self.assertEqual(len(injector.injected), 1)
        self.assertEqual(len(notifier.calls), 1)
        self.assertEqual(watch.load_state(self.state_path), {"last_seq": 1})

    def test_self_authored_turn_is_skipped_not_injected(self):
        result, _, injector, notifier = self._poll([turn(1, "joe", body="my own echo")])
        self.assertEqual(result["injected"], 0)
        self.assertEqual(result["watermark"], 1)  # still advances past it
        self.assertEqual(injector.injected, [])
        self.assertEqual(notifier.calls, [])

    def test_non_turn_kind_is_skipped_not_injected(self):
        result, _, injector, _ = self._poll([turn(1, "dell", kind="receipt")])
        self.assertEqual(result["injected"], 0)
        self.assertEqual(result["watermark"], 1)
        self.assertEqual(injector.injected, [])

    def test_watermark_advances_correctly_across_two_polls(self):
        # Poll 1 sees seq 1-2, poll 2 (against a fetch fake that ignores
        # after_seq and just re-serves a fixed batch) must not re-inject
        # anything at or below the stored watermark.
        turns_batch_1 = [turn(1, "dell"), turn(2, "dell")]
        result1, fetch1, injector1, _ = self._poll(turns_batch_1)
        self.assertEqual(result1["watermark"], 2)
        self.assertEqual(fetch1.calls, [0])

        turns_batch_2 = [turn(1, "dell"), turn(2, "dell"), turn(3, "dell")]
        result2, fetch2, injector2, _ = self._poll(turns_batch_2)
        self.assertEqual(fetch2.calls, [2])          # asked with the stored watermark
        self.assertEqual(result2["processed"], 1)     # only seq 3 is new
        self.assertEqual(result2["watermark"], 3)
        self.assertEqual(len(injector2.injected), 1)

    def test_since_override_reads_from_the_given_seq_this_run(self):
        # State starts empty (watermark 0); --since should steer THIS poll's
        # fetch call, not the empty stored state.
        result, fetch, _, _ = self._poll([turn(10, "dell")], since_override=7)
        self.assertEqual(fetch.calls, [7])
        self.assertEqual(result["watermark"], 10)
        self.assertEqual(watch.load_state(self.state_path), {"last_seq": 10})

    def test_dry_run_injects_nothing_and_never_advances_watermark(self):
        result, _, injector, notifier = self._poll([turn(1, "dell")], dry_run=True)
        self.assertEqual(result["injected"], 0)
        self.assertEqual(injector.injected, [])
        self.assertEqual(notifier.calls, [])
        # Nothing was ever written — no state file at all.
        self.assertFalse(self.state_path.exists())

    def test_dry_run_then_real_run_still_starts_from_zero(self):
        self._poll([turn(1, "dell"), turn(2, "dell")], dry_run=True)
        result, fetch, injector, _ = self._poll([turn(1, "dell"), turn(2, "dell")])
        self.assertEqual(fetch.calls, [0])  # dry run left no trace
        self.assertEqual(result["watermark"], 2)
        self.assertEqual(len(injector.injected), 2)

    def test_kill_switch_blocks_injection_but_still_advances_watermark(self):
        self.pause_path.write_text("paused by hand\n")
        result, _, injector, notifier = self._poll([turn(1, "dell", body="are you there")])
        self.assertEqual(result["paused"], True)
        self.assertEqual(result["injected"], 0)
        self.assertEqual(injector.injected, [])
        self.assertEqual(notifier.calls, [])
        # State still advances — nothing should pile up and dump once resumed.
        self.assertEqual(watch.load_state(self.state_path), {"last_seq": 1})

    def test_removing_pause_file_resumes_injection(self):
        self.pause_path.write_text("paused\n")
        self._poll([turn(1, "dell")])  # paused: skipped, watermark -> 1
        self.pause_path.unlink()
        result, _, injector, notifier = self._poll([turn(1, "dell"), turn(2, "dell")])
        self.assertEqual(result["processed"], 1)   # only seq 2 is new
        self.assertEqual(len(injector.injected), 1)
        self.assertEqual(len(notifier.calls), 1)

    def test_no_new_turns_is_a_no_op(self):
        result, _, injector, notifier = self._poll([])
        self.assertEqual(result, {"processed": 0, "injected": 0, "watermark": 0, "paused": False})
        self.assertEqual(injector.injected, [])
        self.assertEqual(notifier.calls, [])


class SelfResolutionTests(unittest.TestCase):
    """resolve_self()/resolve_target_label() refuse rather than guess."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.whoami = Path(self.tmp.name) / "partner"
        self.target = Path(self.tmp.name) / "partner-line-target"

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolve_self_reads_whoami_file(self):
        self.whoami.write_text("dell\n")
        self.assertEqual(watch.resolve_self(whoami_path=self.whoami), "dell")

    def test_resolve_self_exits_when_nothing_set(self):
        with self.assertRaises(SystemExit):
            watch.resolve_self(whoami_path=self.whoami)  # file doesn't exist

    def test_resolve_self_exits_on_unknown_partner(self):
        self.whoami.write_text("nobody\n")
        with self.assertRaises(SystemExit):
            watch.resolve_self(whoami_path=self.whoami)

    def test_resolve_target_label_reads_target_file(self):
        self.target.write_text("dell-main\n")
        self.assertEqual(watch.resolve_target_label(None, target_path=self.target), "dell-main")

    def test_resolve_target_label_prefers_cli_flag(self):
        self.target.write_text("dell-main\n")
        self.assertEqual(watch.resolve_target_label("other-label", target_path=self.target), "other-label")

    def test_resolve_target_label_exits_when_nothing_set(self):
        with self.assertRaises(SystemExit):
            watch.resolve_target_label(None, target_path=self.target)

    def test_resolve_target_label_exits_on_pid_shaped_value(self):
        self.target.write_text("54321\n")
        with self.assertRaises(SystemExit):
            watch.resolve_target_label(None, target_path=self.target)


if __name__ == "__main__":
    unittest.main()
