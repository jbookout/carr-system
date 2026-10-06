#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('routine_drift', ROOT / 'tools/routines/drift.py')
assert spec and spec.loader
drift = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drift)


class DriftTests(unittest.TestCase):
    def test_any_explicit_enabled_source_overrides_disabled_readback(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            task = home / '.claude/scheduled-tasks/social-batch-weekly'
            task.mkdir(parents=True)
            registry = home / '.claude/scheduled_tasks.json'
            registry.write_text('[{"id":"social-batch-weekly","enabled":false}]')
            jobs = {'jobs': [{'id': 'social-weekly', 'label': 'com.carr.routine-social-weekly',
                              'replaces': ['social-batch-weekly']}], 'retired': []}
            (task / 'SKILL.md').write_text('---\nenabled: true\n---\n')
            self.assertEqual(drift.check(jobs, home)[0]['state'], 'enabled')
            (task / 'SKILL.md').write_text('---\nname: social-batch-weekly\n---\n')
            (task / 'task.json').write_text('{"enabled":false}')
            (task / 'metadata.json').write_text('{"enabled":true}')
            self.assertEqual(drift.check(jobs, home)[0]['state'], 'enabled')
            (task / 'metadata.json').write_text('{broken')
            self.assertEqual(drift.check(jobs, home)[0]['state'], 'unreadable')

    def test_registry_and_directory_status(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            tasks = home / '.claude/scheduled-tasks'
            task = tasks / 'social-batch-weekly'
            task.mkdir(parents=True)
            (task / 'SKILL.md').write_text('---\nname: social-batch-weekly\n---\nDraft posts.\n')
            registry = home / '.claude/scheduled_tasks.json'
            jobs = {'jobs': [{'id': 'social-weekly', 'label': 'com.carr.routine-social-weekly',
                              'replaces': ['social-batch-weekly']}], 'retired': []}
            registry.write_text(json.dumps({'tasks': [{'id': 'social-batch-weekly', 'enabled': True}]}))
            rows = drift.check(jobs, home)
            self.assertEqual([(r['task_id'], r['state']) for r in rows], [('social-batch-weekly', 'enabled')])
            self.assertIn('owner=claude', drift.render(rows))
            self.assertIn('auto-clear', drift.render(rows))
            registry.write_text(json.dumps({'tasks': [{'id': 'social-batch-weekly', 'enabled': False}]}))
            self.assertEqual(drift.check(jobs, home), [])
            registry.unlink()
            self.assertEqual(drift.check(jobs, home)[0]['state'], 'unverified')
            (task / 'task.json').write_text('{"enabled":false}')
            self.assertEqual(drift.check(jobs, home), [])

    def test_unknown_tasks_and_broken_registry(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            registry = home / '.claude/scheduled_tasks.json'
            registry.parent.mkdir()
            registry.write_text('[{"id":"unrelated","enabled":true}]')
            jobs = {'jobs': [{'id': 'lead-signals-weekly', 'label': 'com.carr.routine-lead-signals-weekly',
                              'replaces': ['npi-sweep-weekly']}], 'retired': []}
            self.assertEqual(drift.check(jobs, home), [])
            registry.write_text('{broken')
            self.assertEqual(drift.check(jobs, home)[0]['state'], 'unreadable')

    def test_directory_enabled_and_retired(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            task = home / '.claude/scheduled-tasks/health-audit-monthly'
            task.mkdir(parents=True)
            (task / 'SKILL.md').write_text('---\nenabled: true\n---\n')
            self.assertEqual(drift.check({'jobs': [], 'retired': ['health-audit-monthly']}, home)[0]['state'], 'enabled')

    def test_health_command_wires_drift_failure_and_clear(self):
        with tempfile.TemporaryDirectory() as raw:
            registry = Path(raw) / '.claude/scheduled_tasks.json'
            registry.parent.mkdir()
            env = dict(os.environ, HOME=raw, PYTHONPATH=str(ROOT))
            command = [sys.executable, str(ROOT / 'tools/health-check.py'), '--section', 'routines']
            registry.write_text('[{"id":"social-batch-weekly","enabled":true}]')
            failed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(failed.returncode, 1, failed.stderr)
            self.assertIn('FAIL routine drift', failed.stdout)
            registry.write_text('[{"id":"social-batch-weekly","enabled":false}]')
            passed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertIn('OK routine drift', passed.stdout)


if __name__ == '__main__':
    unittest.main()
