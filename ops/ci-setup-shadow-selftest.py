#!/usr/bin/env python3
"""Behavioral replays for the default-off setup experiment."""
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('setup_shadow', ROOT / 'ops/ci-setup-shadow.py')
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class SetupReplays(unittest.TestCase):
    def identity(self):
        return dict(os='linux', architecture='arm64', runtime='26.5.1',
                    installer='npm-11', lock='a'*64, package_path='mcp-server',
                    source_tree='b'*40)

    def test_each_incompatible_input_misses_with_compatible_control(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'source'; source.mkdir()
            (source/'module').write_text('locked dependency')
            mod.save_tree(source, root/'cache', self.identity())
            self.assertTrue(mod.restore_tree(root/'cache', root/'clean', self.identity()))
            for key in self.identity():
                identity = self.identity(); identity[key] += '-changed'
                target = root/key
                self.assertFalse(mod.restore_tree(root/'cache', target, identity), key)
                self.assertFalse(target.exists())

    def test_corrupt_partial_and_missing_ack_restore_miss(self):
        for fault in ('corrupt', 'partial', 'ack'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as d:
                root = Path(d); source = root/'source'; source.mkdir()
                (source/'module').write_text('good')
                mod.save_tree(source, root/'cache', self.identity())
                if fault == 'corrupt': (root/'cache/tree/module').write_text('bad')
                if fault == 'partial': (root/'cache/tree/module').unlink()
                if fault == 'ack': (root/'cache/manifest.json').unlink()
                self.assertFalse(mod.restore_tree(root/'cache', root/'target', self.identity()))
                self.assertFalse((root/'target').exists())

    def test_clones_are_independent_and_cache_contains_no_cluster(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'source'; source.mkdir()
            (source/'module').write_text('good')
            mod.save_tree(source, root/'cache', self.identity())
            for name in ('a', 'b'):
                self.assertTrue(mod.restore_tree(root/'cache', root/name, self.identity()))
            (root/'a/module').write_text('mutation')
            self.assertEqual((root/'b/module').read_text(), 'good')
            self.assertEqual((root/'cache/tree/module').read_text(), 'good')
            (source/'PG_VERSION').write_text('17')
            with self.assertRaises(mod.Refusal): mod.save_tree(source, root/'cluster', self.identity())

    def test_external_symlink_cannot_be_saved(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/'source'; source.mkdir()
            (root/'credential').write_text('canary-private')
            (source/'link').symlink_to(root/'credential')
            with self.assertRaises(mod.Refusal): mod.save_tree(source, root/'cache', self.identity())

    def row(self, cold, setup, total, dependency='dep', check='check'):
        return dict(cold=cold, setup_seconds=setup, job_seconds=total,
                    dependency_digest=dependency, check_digest=check, passed=True,
                    identity=self.identity())

    def test_matching_outputs_under_different_inputs_cannot_qualify(self):
        baseline=[self.row(cold, 10, 30) for _ in range(10) for cold in (True, False)]
        for key in self.identity():
            candidate=[self.row(cold, 8, 28) for _ in range(10) for cold in (True, False)]
            candidate[0]['identity'][key] += '-changed'
            self.assertEqual(mod.compare(baseline,candidate)['action'], 'remove-candidate-cache', key)

    def test_malformed_and_unpaired_samples_cannot_qualify(self):
        baseline=[self.row(cold, 10, 30) for _ in range(10) for cold in (True, False)]
        for fault in ('cold', 'identity', 'row'):
            candidate=[self.row(cold, 8, 28) for _ in range(10) for cold in (True, False)]
            if fault == 'row': candidate[0]=None
            else: candidate[0].pop(fault)
            self.assertEqual(mod.compare(baseline,candidate)['action'], 'remove-candidate-cache', fault)

    def test_timeout_disposes_owned_grandchild(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            pidfile=root/'child.pid'
            code=("import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c',"
                  "'import time; time.sleep(30)']); "
                  "open(sys.argv[1],'w').write(str(child.pid)); time.sleep(30)")
            with self.assertRaises(mod.Refusal):
                mod.command([sys.executable,'-c',code,str(pidfile)],root,{},timeout=1)
            pid=int(pidfile.read_text())
            # A zombie has exited and cannot consume another trial's capacity.
            state=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
            try:
                self.assertTrue(not state or state.startswith('Z'),state)
            finally:
                if state and not state.startswith('Z'): os.kill(pid,9)

    def test_acceptance_p95_total_and_output_parity(self):
        baseline=[self.row(cold, 10, 30) for _ in range(10) for cold in (True, False)]
        winner=[self.row(cold, 8, 28) for _ in range(10) for cold in (True, False)]
        self.assertEqual(mod.compare(baseline, winner)['action'], 'eligible-for-full-lane-proof')
        for fault in ('slow-cold', 'slow-warm', 'total', 'dependency', 'check', 'missing', 'nan', 'failure'):
            bad=[dict(x) for x in winner]
            if fault == 'slow-cold': bad[0]['setup_seconds']=20
            if fault == 'slow-warm': bad[1]['setup_seconds']=20
            if fault == 'total': bad[0]['job_seconds']=100
            if fault in ('dependency','check'): bad[0][fault+'_digest']='changed'
            if fault == 'missing': bad.pop()
            if fault == 'nan': bad[0]['setup_seconds']=math.nan
            if fault == 'failure': bad[0]['passed']=False
            self.assertEqual(mod.compare(baseline,bad)['action'], 'remove-candidate-cache', fault)
        self.assertEqual(mod.compare([],[])['action'],'remove-candidate-cache')

    def test_result_failures_never_become_success_or_expose_child_text(self):
        canary='canary-client-private-token'
        for code in ("pass", "print('partial')", "import sys; print('refused'); sys.exit(75)",
                     "import sys; sys.exit(1)", "raise Exception('"+canary+"')"):
            with self.subTest(code=code), self.assertRaises(mod.Refusal) as failure:
                mod.command([sys.executable, '-c', code], Path.cwd(), {}, require_output=True)
            self.assertNotIn(canary,str(failure.exception))
        self.assertEqual(mod.command([sys.executable,'-c',"print('{}')"],Path.cwd(),{},require_output=True),'{}\n')

    def test_empty_dependency_ack_is_not_parity(self):
        for value in ('{}', '{"name":"package"}', '[]'):
            with self.assertRaises(mod.Refusal): mod.npm_dependencies(value)
        self.assertTrue(mod.npm_dependencies('{"name":"package","dependencies":{"a":{"version":"1"}}}'))

    def test_exit_zero_failed_or_incomplete_test_ack_refuses(self):
        healthy='ok 1 - fixture\n# tests 1\n# pass 1\n# fail 0\n# cancelled 0\n'
        with patch.object(mod,'command',return_value=healthy):
            self.assertTrue(mod.check_npm(Path.cwd(),{}))
        for output in ('', healthy.replace('# fail 0', '# fail 1'),
                       healthy.replace('ok 1', 'not ok 1'),
                       healthy.replace('# cancelled 0', '# cancelled 1'),
                       healthy.replace('# fail 0\n','')):
            with self.subTest(output=output), patch.object(mod,'command',return_value=output):
                with self.assertRaises(mod.Refusal): mod.check_npm(Path.cwd(),{})

    def test_workflow_is_only_default_off_private_measurement(self):
        source=(ROOT/'.github/workflows/ci-setup-shadow.yml').read_text()
        self.assertIn('default: false',source)
        self.assertIn('if: ${{ inputs.enabled }}',source)
        self.assertIn('persist-credentials: false',source)
        self.assertIn('fetch-depth: 0',source)
        self.assertIn('contents: read',source)
        self.assertNotIn('actions/cache',source)
        self.assertNotIn('pull_request:',source)
        self.assertNotIn('schedule:',source)

    def test_default_off_has_no_install_or_output_file(self):
        result=subprocess.run([sys.executable,str(ROOT/'ops/ci-setup-shadow.py')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        self.assertIn('disabled',result.stdout)

    def test_inventory_covers_classes_and_history_obligations(self):
        inventory=mod.inventory(ROOT)
        self.assertEqual(set(inventory['classes']),set(mod.class_names(ROOT)))
        self.assertIn('full-history',inventory['classes']['freshness']['retain'])
        self.assertIn('historic-schema-blob',inventory['classes']['migration']['retain'])
        self.assertEqual(inventory['removals'],[])
        self.assertFalse(inventory['database_reuse'])


if __name__ == '__main__': unittest.main()
