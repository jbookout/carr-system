#!/usr/bin/env python3
"""Synchronized SCAC job census follows manifest contracts and rejects drift."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

import scac_mutation_db_inventory as inventory

ROOT = Path(__file__).resolve().parents[1]


def fixture_workflow(key, version, enabled, execution):
    return {
        'key': key, 'version': version, 'enabled': enabled, 'risk': 'green',
        'inventory': {'owner': 'fixture-owner'}, 'execution': execution,
        'recurrence': {'kind': 'cron', 'cron': '0 8 * * 1', 'timezone': 'UTC'},
        'state': {'kind': 'stateless'}, 'routing': {}, 'filtering': {},
        'validation': {}, 'retry': {'max_attempts': 1},
        'deduplication': {'scope': 'slot'}, 'completion': {'kind': 'receipt'},
        'legacy_schedule': {'provider': 'none'},
    }


FIXTURE_MANIFEST = {'workflows': [
    fixture_workflow('fixture-alpha', 2, False,
                     {'kind': 'script', 'entrypoint': 'tools/fixture.py', 'arguments': ['--dry-run']}),
    fixture_workflow('fixture-beta', 1, True,
                     {'kind': 'cognition', 'cognition_job': 'fixture-cognition'}),
]}


def gate(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'ops' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JobDefinitionCensus(unittest.TestCase):
    def setUp(self):
        self.manifest = copy.deepcopy(FIXTURE_MANIFEST)

    def test_fixed_fixture_vector_has_known_canonical_digest(self):
        expected = {
            'count': 2,
            'digest': 'sha256:72e6b78f67163af5f62bbc4cfbde85fc8f7015053c7c24687fb4fb98c92e3449',
        }
        self.assertEqual(inventory.manifest_job_definition_catalog(self.manifest), expected)

    def test_both_gates_bind_exact_synchronized_manifest_census(self):
        manifest = json.loads((ROOT / 'ops/config/control-plane-workflows.v1.json').read_text())
        expected = inventory.manifest_job_definition_catalog(manifest)
        for name in ['siep11-mutation-registry-local-pg-gate', 'siep12-policy-epoch-local-pg-gate']:
            with self.subTest(gate=name):
                self.assertEqual(getattr(gate(name), 'JOB_DEFINITION_CATALOG', None), expected)

    def test_contract_enabled_and_version_drift_are_discriminated(self):
        baseline = inventory.manifest_job_definition_catalog(self.manifest)
        for mutation in ['enabled', 'version', 'execution', 'state', 'owner']:
            candidate = copy.deepcopy(self.manifest)
            workflow = next(w for w in candidate['workflows'] if w['key'] == 'fixture-alpha')
            if mutation == 'enabled':
                workflow['enabled'] = True
            elif mutation == 'version':
                workflow['version'] = 1
            elif mutation == 'execution':
                workflow['execution']['entrypoint'] = 'unreviewed.py'
            elif mutation == 'state':
                workflow['state']['fixture_drift'] = True
            else:
                workflow['inventory']['owner'] = 'unreviewed'
            with self.subTest(mutation=mutation):
                self.assertNotEqual(inventory.manifest_job_definition_catalog(candidate), baseline)

    def test_manifest_order_is_irrelevant_and_input_is_unchanged(self):
        before = copy.deepcopy(self.manifest)
        expected = inventory.manifest_job_definition_catalog(self.manifest)
        self.assertEqual(self.manifest, before)
        self.manifest['workflows'].reverse()
        self.assertEqual(inventory.manifest_job_definition_catalog(self.manifest), expected)

    def test_duplicate_identities_are_refused(self):
        self.manifest['workflows'].append(copy.deepcopy(self.manifest['workflows'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            inventory.manifest_job_definition_catalog(self.manifest)


if __name__ == '__main__':
    unittest.main()
