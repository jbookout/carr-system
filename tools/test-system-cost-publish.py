"""Cost collector publication through its command/module seam, without providers."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import system_costs as costs
import progress_board as board


class CostPublication(unittest.TestCase):
    def test_first_publish_creates_cost_board_and_reads_back_collected_evidence(self):
        now = datetime.now(timezone.utc)
        report = costs.summarize({'budget_usd': 100, 'providers': {
            'jev': {'label': 'Jev', 'plan': 'Usage', 'monthly_usd': 0, 'budget_usd': 50}}},
            {'jev': {'state': 'ready', 'rows': []}}, now.date() - timedelta(days=1), observed_at=now.isoformat())
        remote = {}
        calls = []
        def verb(name, args):
            calls.append((name, args))
            if name == 'read-progress-board':
                return {'ok': True, 'snapshot': remote.get(args['board_id']), 'questions': []}
            if name == 'publish-board-snapshot':
                remote[args['board_id']] = {'version': args['base_version'] + 1, 'snapshot_json': args['snapshot']}
                return {'ok': True}
            self.fail('unexpected verb: ' + name)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'fixture-config.json'
            config.write_text('{}')
            with patch.object(costs, 'collect', return_value=report), patch.object(costs, 'read_tokens', return_value={}), \
                 patch.object(board, 'REPO_ROOT', root), patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': str(root / 'out')}), \
                 patch.object(board, 'call_verb', side_effect=verb), \
                 patch.object(board, 'refresh_and_publish', side_effect=lambda project: board.publish_board(project)), \
                 redirect_stdout(io.StringIO()):
                result = costs.main(['--config', str(config), '--output', str(root / 'out/system-costs.json'), '--publish'])
            self.assertEqual(result, 0)
            self.assertTrue((root / 'out/boards/system-costs.json').is_file())
            self.assertEqual(remote['system-costs']['snapshot_json']['costs']['state'], 'ready')
            self.assertEqual(remote['system-costs']['snapshot_json']['costs']['through'], report['through'])
            self.assertEqual([name for name, _ in calls], ['read-progress-board', 'publish-board-snapshot', 'read-progress-board'])


    def test_existing_board_is_published_without_reinitializing_it(self):
        report = {'state': 'ready', 'providers': [], 'alerts': []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text('{}')
            output = root / 'system-costs.json'
            with patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': str(root)}), \
                 patch.object(board, 'refresh_and_publish'), \
                 patch.object(costs, 'collect', return_value=report), patch.object(costs, 'read_tokens', return_value={}):
                import argparse
                board.command_init(argparse.Namespace(project='system-costs', title='Existing board title'))
                before = board.state_path('system-costs').read_text()
                with patch.object(board, 'command_init', side_effect=AssertionError('board reset')), \
                     patch.object(board, 'publish_board') as publish, redirect_stdout(io.StringIO()):
                    self.assertEqual(costs.main(['--config', str(config), '--output', str(output), '--publish']), 0)
                publish.assert_called_once_with('system-costs')
                self.assertEqual(board.state_path('system-costs').read_text(), before)

    def test_concurrent_board_create_is_tolerated_but_other_init_failures_propagate(self):
        report = {'state': 'ready', 'providers': [], 'alerts': []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text('{}')
            original_init = board.command_init
            def concurrent_init(args):
                original_init(args)  # Another collector wins its exclusive create.
                original_init(args)  # The current create gets the existing-board refusal.
            with patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': str(root)}), \
                 patch.object(board, 'refresh_and_publish'), \
                 patch.object(costs, 'collect', return_value=report), patch.object(costs, 'read_tokens', return_value={}), \
                 patch.object(board, 'command_init', side_effect=concurrent_init), \
                 patch.object(board, 'publish_board') as publish, redirect_stdout(io.StringIO()):
                self.assertEqual(costs.main(['--config', str(config), '--output', str(root / 'costs.json'), '--publish']), 0)
                publish.assert_called_once_with('system-costs')
            with patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': str(root / 'fresh')}), \
                 patch.object(costs, 'collect', return_value=report), patch.object(costs, 'read_tokens', return_value={}), \
                 patch.object(board, 'command_init', side_effect=SystemExit('publication failed')), \
                 patch.object(board, 'publish_board') as publish, redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, 'publication failed'):
                    costs.main(['--config', str(config), '--output', str(root / 'costs.json'), '--publish'])
                publish.assert_not_called()


if __name__ == '__main__':
    unittest.main()
