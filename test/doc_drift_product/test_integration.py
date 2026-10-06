import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class ProductIntegrationTest(unittest.TestCase):
    def load(self, name):
        spec = importlib.util.spec_from_file_location(name, ROOT / 'ops' / f'{name}.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_artifact_omits_unchecked_values_everywhere(self):
        module = self.load('doc-drift-product')
        claim = dict(file='README.md', line=1, kind='path', target='/private/person@example.invalid/key',
                     sources=['https://example.invalid/?token=secret'], unchecked='machine-local reference')
        report = dict(claims=[claim], unchecked=[claim], findings=[])
        public = module.public_report(report)
        self.assertEqual(public['claims'], [dict(file='README.md', line=1, kind='path', unchecked='machine-local reference')])
        self.assertEqual(public['unchecked'], public['claims'])
        self.assertNotIn('person@', json.dumps(public))
        self.assertNotIn('secret', json.dumps(public))
        self.assertIn('person@', json.dumps(report))

    def test_all_main_events_share_one_writer_group(self):
        workflow = yaml.load((ROOT / '.github/workflows/doc-drift.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['concurrency']['group'], "doc-drift-${{ github.event.pull_request.number || 'main' }}")
        self.assertEqual(workflow['concurrency']['cancel-in-progress'], 'false')

    def test_dependency_projection_refuses_changed_snapshot_version(self):
        module = self.load('doc-drift-product')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts/doc-drift').mkdir(parents=True)
            (root / 'requirements.txt').write_text('PyYAML==6.0.3\n')
            (root / 'scripts/doc-drift/requirements.txt').write_text('PyYAML==6.0.2\n')
            with self.assertRaisesRegex(ValueError, 'dependency projection'):
                module.verify_dependencies(root)
            (root / 'scripts/doc-drift/requirements.txt').write_text('PyYAML==6.0.3\n')
            module.verify_dependencies(root)


if __name__ == '__main__':
    unittest.main()
