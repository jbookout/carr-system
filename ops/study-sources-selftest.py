#!/usr/bin/env python3
"""Offline public-command tests; models and HTTP are fake executables."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]


class StudySourcesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="study-sources-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for name in ("bin", "ops/prompts", "tools", "fake-bin"):
            (self.root / name).mkdir(parents=True)
        for name in ("bin/study-sources.sh", "bin/study_sources.py",
                     "ops/prompts/source-study-brief.md", "tools/progress_board.py"):
            source = ROOT / name
            if source.exists():
                shutil.copyfile(source, self.root / name)
        self.env = dict(os.environ, PATH=str(self.root / "fake-bin") + os.pathsep + os.environ["PATH"],
                        FAKE_EVENTS=str(self.root / "events.jsonl"), PROGRESS_BOARD_ROOT=str(self.root / "out"))
        self.executable("bin/grok-run.sh", """
            import json, os, sys
            from pathlib import Path
            if '--help' in sys.argv:
                print('--prompt --effort')
                sys.exit(0)
            cards = json.loads((Path(os.environ['PROGRESS_BOARD_ROOT']) / 'boards/source-studies.json').read_text())['tasks']
            assert any(c['status'] == 'running' for c in cards.values())
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'grok', 'args': sys.argv[1:]}) + '\\n')
            print('RAW X POST: methods and linked source https://example.com/method')
        """)
        self.executable("fake-bin/codex", """
            import json, os, sys
            from pathlib import Path
            assert os.fstat(0).st_rdev == os.stat('/dev/null').st_rdev
            prompt = sys.argv[-1]
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'codex', 'args': sys.argv[1:], 'cwd': os.getcwd()}) + '\\n')
            Path('report.md').write_text('# Why Joe picked it\\nA concrete application plan.\\n')
        """)
        self.executable("fake-bin/curl", """
            import json, os, sys
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'curl', 'args': sys.argv[1:]}) + '\\n')
            print('<html><body>RAW ARTICLE</body></html>')
        """)

    def executable(self, name, body):
        path = self.root / name
        path.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(body))
        path.chmod(0o755)

    def run_tool(self, *urls):
        return subprocess.run(['bash', str(self.root / 'bin/study-sources.sh'), *urls],
                              cwd=self.root, env=self.env, input='must not reach codex',
                              capture_output=True, text=True, timeout=30)

    def events(self):
        return [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]

    def cards(self):
        return json.loads((self.root / 'out/boards/source-studies.json').read_text())['tasks']

    def test_each_url_gets_one_retrieval_one_high_study_and_done_card(self):
        urls = ('https://x.com/author/status/123', 'https://example.com/article')
        result = self.run_tool(*urls)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events()
        self.assertEqual([e['kind'] for e in events].count('grok'), 1)
        self.assertEqual([e['kind'] for e in events].count('curl'), 1)
        studies = [e for e in events if e['kind'] == 'codex']
        self.assertEqual(len(studies), 2)
        self.assertEqual(len(self.cards()), 2)
        for study in studies:
            args = study['args']
            self.assertEqual(args[args.index('-m') + 1], 'gpt-6.1-sol')
            self.assertIn('model_reasoning_effort="high"', args)
            self.assertIn('WHY JOE PICKED IT', args[-1])
            folder = Path(study['cwd'])
            self.assertEqual(folder.parent.parent, self.root / 'out/source-studies')
            self.assertEqual(folder.parent.name, datetime.now().date().isoformat())
            self.assertTrue((folder / 'retrieval.txt').is_file())
            self.assertTrue((folder / 'report.md').is_file())
        for card in self.cards().values():
            self.assertEqual(card['status'], 'done')
            self.assertIn('report.md', card['note'])

    def test_missing_report_blocks_card_with_reason(self):
        self.executable('fake-bin/codex', 'pass')
        result = self.run_tool('https://x.com/author/status/123')
        self.assertEqual(result.returncode, 1, result.stderr)
        card, = self.cards().values()
        self.assertEqual(card['status'], 'blocked')
        self.assertIn('missing report', card['note'])
        self.assertIn('report.md', card['note'])

    def test_retrieval_failure_blocks_without_study(self):
        self.executable('bin/grok-run.sh', 'import sys; sys.exit(4)')
        result = self.run_tool('https://x.com/author/status/123')
        self.assertEqual(result.returncode, 1, result.stderr)
        card, = self.cards().values()
        self.assertEqual(card['status'], 'blocked')
        self.assertIn('retrieval failed (exit 4)', card['note'])
        self.assertFalse(list(self.root.glob('out/source-studies/*/*/codex.log')))

    def test_supported_grok_timeout_option_is_forwarded(self):
        self.executable('bin/grok-run.sh', """
            import json, os, sys
            if '--help' in sys.argv:
                print('--timeout-seconds')
                sys.exit(0)
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'grok', 'args': sys.argv[1:]}) + '\\n')
            print('RAW POST')
        """)
        result = self.run_tool('https://twitter.com/author/status/123')
        self.assertEqual(result.returncode, 0, result.stderr)
        grok, = [e for e in self.events() if e['kind'] == 'grok']
        self.assertEqual(grok['args'][grok['args'].index('--timeout-seconds') + 1], '600')

    def test_empty_report_is_blocked(self):
        self.executable('fake-bin/codex', "from pathlib import Path; Path('report.md').write_text('   ')")
        result = self.run_tool('https://example.com/article')
        self.assertEqual(result.returncode, 1, result.stderr)
        card, = self.cards().values()
        self.assertEqual(card['status'], 'blocked')
        self.assertIn('missing report', card['note'])

    def test_failed_study_cannot_promote_a_report(self):
        self.executable('fake-bin/codex', "from pathlib import Path; import sys; Path('report.md').write_text('partial'); sys.exit(9)")
        result = self.run_tool('https://example.com/article')
        self.assertEqual(result.returncode, 1, result.stderr)
        card, = self.cards().values()
        self.assertEqual(card['status'], 'blocked')
        self.assertIn('study failed (exit 9)', card['note'])

    def test_more_than_four_sources_run_in_parallel_waves(self):
        self.executable('fake-bin/codex', """
            import json, os, time
            from pathlib import Path
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'start', 'pid': os.getpid()}) + '\\n')
            time.sleep(0.5)
            Path('report.md').write_text('Application plan')
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'end', 'pid': os.getpid()}) + '\\n')
        """)
        result = self.run_tool(*(f'https://example.com/article/{i}' for i in range(9)))
        self.assertEqual(result.returncode, 0, result.stderr)
        active, peak, starts = set(), 0, 0
        for event in self.events():
            if event['kind'] == 'start':
                active.add(event['pid'])
                starts += 1
                peak = max(peak, len(active))
            elif event['kind'] == 'end':
                active.remove(event['pid'])
        self.assertEqual(starts, 9)
        self.assertLessEqual(peak, 4)
        self.assertGreater(peak, 1)
        self.assertFalse(active)
        self.assertEqual(len(self.cards()), 9)
        self.assertTrue(all(card['status'] == 'done' for card in self.cards().values()))

    def test_repeated_source_preserves_prior_report_and_card(self):
        url = 'https://example.com/article'
        for _ in range(2):
            result = self.run_tool(url)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(self.root.glob('out/source-studies/*/*/report.md'))), 2)
        self.assertEqual(len(self.cards()), 2)

    def test_retrieval_timeout_blocks_without_study(self):
        self.env['STUDY_RETRIEVAL_TIMEOUT_SECONDS'] = '0.2'
        self.executable('bin/grok-run.sh', """
            import sys, time
            if '--help' in sys.argv:
                print('--prompt --effort')
                sys.exit(0)
            time.sleep(10)
        """)
        result = self.run_tool('https://x.com/author/status/123')
        self.assertEqual(result.returncode, 1, result.stderr)
        card, = self.cards().values()
        self.assertEqual(card['status'], 'blocked')
        self.assertIn('retrieval timed out', card['note'])
        self.assertFalse(list(self.root.glob('out/source-studies/*/*/codex.log')))


if __name__ == '__main__':
    unittest.main()
