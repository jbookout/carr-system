import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import job_watchdog as w

FIXTURES = ROOT / "tools/fixtures/job-watchdog"
INDEX = "https://developers.openai.com/api/reference/llms.txt"
RECAP = "https://openai.com/index/devday-2026-recap/"
DOC = "https://developers.openai.com/api/reference/decisions"


class VendorWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = copy.deepcopy(w.load_config(ROOT / "ops/config/job-watchdog.json"))
        self.config["actions"]["file_defects"] = False
        self.calls = []

    def fetch(self, url, timeout, max_bytes):
        self.calls.append(url)
        if url == INDEX and getattr(self, "error", False):
            raise OSError(json.loads((FIXTURES / "vendor-fetch-error.json").read_text())["error"])
        name = "vendor-recap.html" if url == RECAP else getattr(self, "index", "vendor-index-before.txt")
        return (FIXTURES / name).read_text()

    def check(self, now):
        return w.vendor_release_findings(self.root, self.config, now, fetch=self.fetch)

    def report(self, found, now):
        return w.reconcile(self.root, self.config, found, w.Effects(self.root, self.config), now, complete=False)

    def test_no_reference_and_hourly_boundary(self):
        self.assertEqual(self.check(1000), [])
        self.assertEqual(self.calls, [INDEX, RECAP])
        self.assertEqual(self.check(4599), [])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.check(4600), [])
        self.assertEqual(len(self.calls), 4)

    def test_first_reference_board_and_durable_repeat(self):
        self.index = "vendor-index-live.txt"
        found = self.check(1000)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "decisions_docs_live")
        self.assertEqual(found[0]["urls"], [DOC])
        self.assertEqual(found[0]["url"], DOC)
        self.assertEqual(found[0]["owner"], "orchestrator")
        self.report(found, 1000)
        board = json.loads((self.root / "out/boards/carr-v5.json").read_text())
        self.assertEqual(board["tasks"][found[0]["card"]]["status"], "question-for-orchestrator")
        self.assertEqual(self.check(1001), [])
        self.assertEqual(self.check(4600), [])
        w.reconcile(self.root, self.config, [], w.Effects(self.root, self.config), 4700)
        self.assertEqual(self.check(8200), [])

    def test_fetch_failure_is_finding_and_recovery_clears(self):
        self.error = True
        found = self.check(1000)
        self.assertEqual([f["kind"] for f in found], ["vendor_release_fetch_error"])
        self.assertIn("503", found[0]["reason"])
        self.assertEqual(found[0]["url"], INDEX)
        self.assertEqual(self.calls, [INDEX, RECAP])
        self.report(found, 1000)
        self.assertEqual(self.check(1001)[0]["kind"], "vendor_release_fetch_error")
        self.error = False
        self.assertEqual(self.check(4600), [])
        self.assertEqual(w.reconcile(self.root, self.config, [], w.Effects(self.root, self.config), 4600), [])
        rows = w.read_latest(self.root / self.config["paths"]["findings"])
        self.assertIsNotNone(rows[found[0]["key"]]["cleared_at"])

    def test_failed_reporting_retries_without_losing_detection(self):
        self.index = "vendor-index-live.txt"
        found = self.check(1000)
        with patch.object(w.Effects, "report", side_effect=RuntimeError("board unavailable")):
            self.report(found, 1000)
        pending = self.check(1001)
        self.assertEqual(pending[0]["kind"], "decisions_docs_live")
        self.report(pending, 1001)
        self.assertEqual(self.check(1002), [])

    def test_changed_url_set_and_return_to_old_set(self):
        self.index = "vendor-index-live.txt"
        self.report(self.check(1000), 1000)
        def changed(url, timeout, max_bytes):
            text = self.fetch(url, timeout, max_bytes)
            return text + "\nhttps://developers.openai.com/api/reference/decisions/create\n" if url == INDEX else text
        found = w.vendor_release_findings(self.root, self.config, 4600, fetch=changed)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["urls"], [DOC, DOC + "/create"])
        self.report(found, 4600)
        self.assertEqual(self.check(8200), [])

    def test_scan_wires_watch_without_model_calls(self):
        self.index = "vendor-index-live.txt"
        with patch.object(w, "collect", return_value={"errors": []}), patch.object(w, "fetch_document", self.fetch), patch.object(w.Effects, "act", side_effect=AssertionError("watch must only report")):
            self.assertEqual(w.scan(self.root, self.config), 0)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(w.read_latest(self.root / self.config["paths"]["findings"])), 1)

    def test_reference_entry_label_and_announcement_reference_link(self):
        watch = self.config["vendor_release_watches"][0]
        text = '[Decisions](https://developers.openai.com/api/reference/resources/new-resource.md)'
        self.assertEqual(w.release_reference_urls(text, INDEX, watch),
                         ['https://developers.openai.com/api/reference/resources/new-resource.md'])
        self.assertEqual(w.release_reference_urls(f'<a href="{DOC}.md">API reference</a>', RECAP, watch), [DOC + '.md'])
        self.assertEqual(w.release_reference_urls('<a href="/api/reference/decisions.md">Docs</a>', INDEX, watch),
                         ['https://developers.openai.com/api/reference/decisions.md'])
        self.assertEqual(w.release_reference_urls('https://developers.openai.com.evil.invalid/api/reference/decisions', INDEX, watch), [])

    def test_vendor_only_cli_fetch_failure_has_no_agent_actions(self):
        self.error = True
        with patch.object(w, 'collect', side_effect=AssertionError('vendor-only must not collect PRs')), patch.object(w, 'fetch_document', self.fetch), patch.object(w.Effects, 'act', side_effect=AssertionError('no model actions')):
            self.assertEqual(w.scan(self.root, self.config, vendor_only=True), 1)
        rows = w.read_latest(self.root / self.config['paths']['findings'])
        self.assertEqual([f['kind'] for f in rows.values()], ['vendor_release_fetch_error'])
        w.append(self.root / self.config['paths']['findings'],
                 {'key': 'unrelated', 'kind': 'job_dead', 'reported': True, 'reason': 'unrelated job', 'next_action': 'inspect'})
        self.error = False
        with patch.object(w, 'fetch_document', self.fetch), patch.object(w, 'time') as clock:
            clock.time.return_value = w.epoch(rows[next(iter(rows))]['first_seen']) + 3600
            self.assertEqual(w.scan(self.root, self.config, vendor_only=True), 0)
        rows = w.read_latest(self.root / self.config['paths']['findings'])
        error = next(f for f in rows.values() if f['kind'] == 'vendor_release_fetch_error')
        self.assertIsNotNone(error.get('cleared_at'))
        self.assertIsNone(rows['unrelated'].get('cleared_at'))

    def test_transport_enforces_size_and_timeout(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'x' * 11
        with patch.object(w, 'urlopen', return_value=response) as opened:
            with self.assertRaisesRegex(ValueError, 'size limit'):
                w.fetch_document(INDEX, 7, 10)
            self.assertEqual(opened.call_args.kwargs, {'timeout': 7})
            response.read.assert_called_once_with(11)

    def test_other_vendor_uses_only_configuration(self):
        watch = self.config['vendor_release_watches'][0]
        watch.update(id='vendor-feature', match='new-feature', finding_kind='vendor_feature_live',
                     card='vendor-feature', sources=['https://docs.example.org/api/index.txt'],
                     reference_prefixes=['https://docs.example.org/api/'])
        found = w.vendor_release_findings(self.root, self.config, 1000,
                                         fetch=lambda *args: 'https://docs.example.org/api/new-feature')
        self.assertEqual([f['kind'] for f in found], ['vendor_feature_live'])


if __name__ == "__main__":
    unittest.main()
