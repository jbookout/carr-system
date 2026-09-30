#!/usr/bin/env python3
"""Behavioral acceptance tests; entirely local, with synthetic Slack messages."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import dot_relay as relay


class AllowlistTests(unittest.TestCase):
    def setUp(self):
        self.repo = "/safe/carr-system"

    def test_read_commands_and_bounded_test_entrypoints(self):
        commands = [
            "git fetch origin", "git log --oneline -n 5", "git show HEAD",
            "git diff --stat HEAD", "git status --short", "git ls-files",
            "git grep -n pattern -- lib/example.py", "cat lib/example.py",
            "head -n 30 lib/example.py", "sed -n '1,30p' lib/example.py",
            "grep -n pattern lib/example.py", "rg -n pattern lib", "ls lib",
            "wc -l lib/example.py", "python3 -m pytest -q tools",
            "python3 tools/test-example.py", "./ops/ci.sh --only unit",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(relay.allowed(command, self.repo, self.repo))

    def test_refusal_matrix(self):
        commands = [
            "git status; ls", "git status | cat", "git status && ls",
            "cat `whoami`", "cat $(whoami)", "cat lib/example.py > output",
            "cat lib/example.py >> output", "cat < lib/example.py",
            "cat ../outside", "cat lib/../../outside", "cat /etc/passwd",
            "cat ~/.hermes/.env", "cat .env", "cat .git/config",
            "cat lib/../example.py", "cat /safe/carr-system-other/file",
            "rm file", "git push", "git commit", "git checkout main",
            "curl https://example.invalid", "env cat lib/example.py",
            "PATH=/evil git status", "git -c core.pager=evil log",
            "git --git-dir=/outside status", "git fetch evil",
            "git fetch origin main:main", "git diff --output=output",
            "git grep --open-files-in-pager=evil pattern",
            "sed -n '1e evil' lib/example.py", "sed -i s/a/b/ lib/example.py",
            "rg --pre evil pattern lib", "rg --files /outside",
            "python3 -c 'print(1)'", "python3 -m pytest -p evil",
            "python3 -m pytest --override-ini pythonpath=/outside",
            "python3 /outside/test-example.py", "./ops/ci.sh --apply",
            "cat $HOME/file", "cat ${HOME}/file", "cat lib/*.py",
            "git status\nls", "cat lib/\\..\\outside", "cat lib\x00/file",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(relay.allowed(command, self.repo, self.repo))

    def test_cwd_and_scratch_boundaries(self):
        self.assertFalse(relay.allowed("git status", "/outside", self.repo))
        self.assertFalse(relay.allowed("ls", "/tmp/arbitrary", self.repo))
        self.assertTrue(relay.allowed("cat file", "/tmp/owned", self.repo, ("/tmp/owned",)))
        self.assertFalse(relay.allowed("cat /tmp/owned-other/file", "/tmp/owned", self.repo, ("/tmp/owned",)))
        self.assertFalse(relay.allowed("python3 tools/test-example.py", "/tmp/owned", self.repo, ("/tmp/owned",)))


class FakeSlack:
    def __init__(self):
        self.posts = []
        self.incoming = []

    def post(self, text, thread=None):
        self.posts.append((text, thread))
        return "1.000001" if thread is None else f"9.{len(self.posts):06d}"

    def replies(self, thread):
        return list(self.incoming)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "example.txt").write_text("example output\n")
        self.state = self.root / "state"
        self.slack = FakeSlack()
        self.engine = relay.Relay(self.slack, self.state, self.repo, "agent", cwd=self.repo)

    def test_job_command_output_report_roundtrip_and_restart(self):
        thread = self.engine.send_job("Synthetic brief")
        self.assertEqual(thread, "1.000001")
        self.assertIn("Synthetic brief", self.slack.posts[0][0])
        self.slack.incoming = [
            {"ts": "2.000001", "user": "stranger", "text": "```mac-run\ncat example.txt\n```"},
            {"ts": "2.000002", "user": "agent", "text": "```mac-run\ncat example.txt\nrm example.txt\n```"},
        ]
        self.assertFalse(self.engine.poll(thread, execute=True))
        self.assertIn("example output", self.slack.posts[1][0])
        self.assertEqual(self.slack.posts[2], ("held for orchestrator", thread))
        rows = [json.loads(s) for s in (self.state / thread / "ledger.jsonl").read_text().splitlines()]
        self.assertEqual([r["allowed"] for r in rows], [True, False])
        self.assertEqual(set(rows[0]), {"ts", "command", "cwd", "exit", "allowed", "bytes"})
        self.assertIn("rm example.txt", (self.state / thread / "pending.jsonl").read_text())
        restarted = relay.Relay(self.slack, self.state, self.repo, "agent", cwd=self.repo)
        self.assertFalse(restarted.poll(thread, execute=True))
        self.assertEqual(len(self.slack.posts), 3)
        self.slack.incoming.append({"ts": "3.000001", "user": "agent", "text": "Completed example.\nDOT-REPORT-END"})
        self.assertTrue(restarted.poll(thread, execute=True))
        self.assertEqual((self.state / thread / "report.txt").read_text(), "Completed example.\n")
        self.assertTrue((self.repo / "example.txt").exists())

    def test_watch_never_executes_and_can_later_relay(self):
        thread = self.engine.send_job("brief")
        self.slack.incoming = [{"ts": "2.000001", "user": "agent", "text": "```mac-run\ncat example.txt\n```"}]
        self.assertFalse(self.engine.poll(thread, execute=False))
        self.assertEqual(len(self.slack.posts), 1)
        self.assertFalse((self.state / thread / "ledger.jsonl").exists())
        self.engine.poll(thread, execute=True)
        self.assertIn("example output", self.slack.posts[1][0])

    def test_symlink_escape_is_held(self):
        (self.root / "private").write_text("must stay local")
        (self.repo / "link").symlink_to(self.root / "private")
        result = relay.run_command("cat link", self.repo, self.repo)
        self.assertFalse(result["allowed"])
        self.assertNotIn("must stay local", result["output"])

    def test_output_is_capped_and_environment_secrets_not_inherited(self):
        (self.repo / "large").write_text("line\n" * 10000)
        result = relay.run_command("cat large", self.repo, self.repo)
        self.assertTrue(result["allowed"])
        self.assertLessEqual(len(result["output"].splitlines()), 150)
        self.assertLessEqual(len(result["output"].encode()), 12000)
        (self.repo / "tools").mkdir()
        (self.repo / "tools/test-env.py").write_text("import os; print(os.getenv('PRIVATE_SECRET', 'absent'))")
        from unittest.mock import patch
        with patch.dict(os.environ, {"PRIVATE_SECRET": "must-stay-local"}):
            result = relay.run_command("python3 tools/test-env.py", self.repo, self.repo)
        self.assertEqual(result["output"].strip(), "absent")

    def test_timeout_kills_command(self):
        (self.repo / "tools").mkdir()
        (self.repo / "tools/test-slow.py").write_text("import time; time.sleep(60)")
        result = relay.run_command("python3 tools/test-slow.py", self.repo, self.repo, timeout=0.05)
        self.assertEqual(result["exit"], 124)

    def test_interrupted_command_is_never_reexecuted(self):
        thread = self.engine.send_job("brief")
        self.slack.incoming = [{"ts": "2.000001", "user": "agent", "text": "```mac-run\ncat example.txt\n```"}]
        from unittest.mock import patch
        with patch.object(relay, "run_command", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.engine.poll(thread, execute=True)
        restarted = relay.Relay(self.slack, self.state, self.repo, "agent", cwd=self.repo)
        with patch.object(relay, "run_command", side_effect=AssertionError("reexecuted")):
            restarted.poll(thread, execute=True)
        self.assertEqual(self.slack.posts[-1], ("held for orchestrator", thread))

    def test_report_marker_inside_fence_does_not_finish(self):
        thread = self.engine.send_job("brief")
        self.slack.incoming = [{"ts": "2.000001", "user": "agent", "text": "```text\nDOT-REPORT-END\n```"}]
        self.assertFalse(self.engine.poll(thread, execute=True))

    def test_edited_reply_is_ignored(self):
        thread = self.engine.send_job("brief")
        self.slack.incoming = [{"ts": "2.000001", "user": "agent", "edited": {"ts": "3.000001"}, "text": "```mac-run\ncat example.txt\n```"}]
        self.engine.poll(thread, execute=True)
        self.assertEqual(len(self.slack.posts), 1)

    def test_report_preserves_code_fences(self):
        thread = self.engine.send_job("brief")
        report = "Report.\n```text\nexample\n```\n"
        self.slack.incoming = [{"ts": "2.000001", "user": "agent", "text": report + "DOT-REPORT-END"}]
        self.assertTrue(self.engine.poll(thread, execute=True))
        self.assertEqual((self.state / thread / "report.txt").read_text(), report)

    def test_changing_sender_on_restart_refuses_existing_thread_state(self):
        thread = self.engine.send_job("brief")
        changed = relay.Relay(self.slack, self.state, self.repo, "other", cwd=self.repo)
        with self.assertRaises(ValueError):
            changed.poll(thread, execute=True)

    def test_report_with_same_message_commands_still_relays_before_finishing(self):
        thread = self.engine.send_job("brief")
        self.slack.incoming = [{"ts": "2.000001", "user": "agent", "text": "```mac-run\ncat example.txt\n```\nReport.\nDOT-REPORT-END"}]
        self.assertTrue(self.engine.poll(thread, execute=True))
        self.assertIn("example output", self.slack.posts[-1][0])


class TransportTests(unittest.TestCase):
    def test_pagination_and_plain_text_posts(self):
        calls = []
        def api(method, payload):
            calls.append((method, payload))
            if method == "chat.postMessage":
                return {"ok": True, "ts": "1.000001"}
            if not payload.get("cursor"):
                return {"ok": True, "messages": [{"ts": "2.000001"}], "response_metadata": {"next_cursor": "next"}}
            return {"ok": True, "messages": [{"ts": "3.000001"}], "response_metadata": {"next_cursor": ""}}
        slack = relay.SlackTransport("not-a-credential", "destination", api=api)
        self.assertEqual(slack.post("plain text", "1.000001"), "1.000001")
        self.assertFalse(calls[0][1]["mrkdwn"])
        self.assertFalse(calls[0][1]["unfurl_links"])
        self.assertEqual(len(slack.replies("1.000001")), 2)
        self.assertEqual(calls[-1][1]["cursor"], "next")

    def test_backoff_honors_retry_after_and_resets(self):
        slack = FakeSlack()
        waits = []
        class Engine:
            calls = 0
            def poll(self, thread, execute=False):
                self.calls += 1
                if self.calls == 1:
                    raise relay.SlackError("rate limited", retry_after=90, transient=True)
                if self.calls == 2:
                    raise relay.SlackError("network", transient=True)
                return self.calls == 4
        self.assertEqual(relay.watch(Engine(), "1.000001", sleep=waits.append), 0)
        self.assertEqual(waits, [90, 60, 30])

    def test_permanent_error_stops_and_polling_is_bounded(self):
        class Engine:
            def poll(self, thread, execute=False):
                raise relay.SlackError("not authorized")
        with self.assertRaises(relay.SlackError):
            relay.watch(Engine(), "1.000001", sleep=lambda _: self.fail("unexpected retry"))
        class Idle:
            def poll(self, thread, execute=False):
                return False
        self.assertEqual(relay.watch(Idle(), "1.000001", max_polls=2, sleep=lambda _: None), 2)

    def test_credentials_require_external_mode_600_and_do_not_use_environment(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            cred = root / "runtime.env"
            cred.write_text("SLACK_BOT_TOKEN='synthetic'\nSLACK_HOME_CHANNEL='destination'\nDOT_SLACK_SENDER='agent'\n")
            with self.assertRaises(ValueError):
                relay.read_config(cred, repo)
            cred.chmod(0o600)
            with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "wrong"}):
                cfg = relay.read_config(cred, repo)
            self.assertEqual(cfg["token"], "synthetic")
            inside = repo / "runtime.env"
            inside.write_text(cred.read_text())
            inside.chmod(0o600)
            with self.assertRaises(ValueError):
                relay.read_config(inside, repo)

    def test_http_errors_never_expose_response_body_or_credentials(self):
        from unittest.mock import patch
        import urllib.error
        import io
        error = urllib.error.HTTPError("https://slack.com/api/conversations.replies", 429, "secret", {"Retry-After": "75"}, io.BytesIO(b"secret"))
        slack = relay.SlackTransport("synthetic", "destination", use_sdk=False)
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(relay.SlackError) as raised:
                slack.replies("1.000001")
        self.assertEqual(raised.exception.retry_after, 75)
        self.assertNotIn("secret", str(raised.exception))


class CLITests(unittest.TestCase):
    def test_cli_send_then_relay_saves_report_without_printing_brief(self):
        from unittest.mock import patch
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            cred = root / "runtime.env"
            cred.write_text("SLACK_USER_TOKEN=synthetic\nSLACK_HOME_CHANNEL=destination\nDOT_SLACK_SENDER=agent\n")
            cred.chmod(0o600)
            brief = root / "brief.txt"
            brief.write_text("Synthetic brief")
            fake = FakeSlack()
            options = ["--credentials", str(cred), "--state-dir", str(root / "state")]
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(relay.main(options + ["send-job", str(brief)], repo=repo, transport_factory=lambda *_: fake), 0)
            self.assertEqual(stdout.getvalue(), "1.000001\n")
            fake.incoming = [{"ts": "2.000001", "user": "agent", "text": "Completed.\nDOT-REPORT-END"}]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(relay.main(options + ["relay", "1.000001"], repo=repo, transport_factory=lambda *_: fake), 0)
            self.assertEqual((root / "state/1.000001/report.txt").read_text(), "Completed.\n")

    def test_cli_error_is_sanitized(self):
        import io
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as temp:
            err = io.StringIO()
            with redirect_stderr(err):
                result = relay.main(["--credentials", str(Path(temp)/"missing"), "watch", "1.000001"], repo=Path(temp))
            self.assertEqual(result, 1)
            self.assertNotIn(temp, err.getvalue())


if __name__ == "__main__":
    unittest.main()
