#!/usr/bin/env python3
"""Fixture-only tests for routine plist rendering and explicit activation."""
import importlib.util
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('routine_install', ROOT / 'tools/routines/install.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)

JOBS = {'timezone': 'America/Chicago', 'jobs': [
    {'id': 'lead-signals-weekly', 'label': 'com.carr.routine-lead-signals-weekly', 'weekday': 1, 'hour': 7, 'minute': 30},
    {'id': 'contact-enrichment-weekly', 'label': 'com.carr.routine-contact-enrichment-weekly', 'weekday': 4, 'hour': 9, 'minute': 0},
    {'id': 'social-weekly', 'label': 'com.carr.routine-social-weekly', 'weekday': 5, 'hour': 8, 'minute': 0}
]}

class InstallerTests(unittest.TestCase):
    def test_preview_has_no_files_or_launchctl_effects(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(install.subprocess, 'run', side_effect=AssertionError('preview cannot run commands')):
            home = Path(tmp)
            report = install.install(ROOT, home, JOBS, apply=False)
            self.assertEqual(report['mode'], 'preview')
            self.assertEqual(len(report['jobs']), 3)
            self.assertFalse((home / 'Library').exists())
    def test_exact_schedule_and_credential_free_environment(self):
        for job in JOBS['jobs']:
            plist = plistlib.loads(install.render(ROOT, Path('/Users/fixture'), job))
            self.assertEqual(plist['StartCalendarInterval'], {'Weekday': job['weekday'], 'Hour': job['hour'], 'Minute': job['minute']})
            self.assertEqual(plist['ProgramArguments'], ['/bin/bash', str(ROOT / 'bin/routine-run.sh'), job['id']])
            self.assertFalse(plist.get('RunAtLoad', False))
            self.assertEqual(set(plist['EnvironmentVariables']), {'PATH', 'TZ'})
            self.assertIn('/opt/homebrew/bin', plist['EnvironmentVariables']['PATH'])
            self.assertNotIn('{{', str(plist))
    def test_manifest_drift_refused(self):
        job = {**JOBS['jobs'][2], 'hour': 9}
        with self.assertRaises(ValueError):
            install.render(ROOT, Path('/Users/fixture'), job)
    def test_apply_refuses_feature_tree_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                install.install(ROOT, Path(tmp), JOBS, apply=True)
            self.assertFalse((Path(tmp) / 'Library').exists())
    def test_apply_moves_previous_plist_and_loads_only_routine(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            repo = home / 'carr-system'
            (repo / 'ops/launchd').mkdir(parents=True)
            for job in JOBS['jobs']:
                shutil.copyfile(ROOT / 'ops/launchd' / f"{job['label']}.plist",
                                repo / 'ops/launchd' / f"{job['label']}.plist")
            dest = home / 'Library/LaunchAgents'
            dest.mkdir(parents=True)
            old = dest / 'com.carr.routine-lead-signals-weekly.plist'
            old.write_bytes(b'old fixture')
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, stdout='', stderr='')
            with patch.object(install, 'validate_main'), patch.object(install, 'validate_timezone'), patch.object(install.subprocess, 'run', side_effect=run):
                report = install.install(repo, home, JOBS, apply=True)
            self.assertEqual(report['mode'], 'installed')
            backups = list((dest / '_to_delete').iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b'old fixture')
            self.assertEqual(plistlib.loads(old.read_bytes())['Label'], JOBS['jobs'][0]['label'])
            self.assertEqual(len([c for c in calls if c[1] == 'bootstrap']), 3)
            self.assertTrue(all('com.carr.routine-' in str(c) or c[1] == 'bootstrap' for c in calls))
    def test_timezone_mismatch_refused(self):
        with self.assertRaises(ValueError):
            install.validate_timezone(Path('/zoneinfo/Europe/London'))

if __name__ == '__main__':
    unittest.main()
