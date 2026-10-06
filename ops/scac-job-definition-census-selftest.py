#!/usr/bin/env python3
"""Synchronized SCAC job census follows manifest contracts and rejects drift."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

import scac_mutation_db_inventory as inventory

ROOT = Path(__file__).resolve().parents[1]


def gate(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'ops' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JobDefinitionCensus(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / 'ops/config/control-plane-workflows.v1.json').read_text())

    def test_both_gates_bind_exact_synchronized_manifest_census(self):
        expected = {'count': 26, 'digest': 'sha256:d12754980e3e37e04d01d36c167e4c0300501526c7359610160c43ed4efc86e5'}
        for name in ['siep11-mutation-registry-local-pg-gate', 'siep12-policy-epoch-local-pg-gate']:
            with self.subTest(gate=name):
                self.assertEqual(getattr(gate(name), 'JOB_DEFINITION_CATALOG', None), expected)

    def test_contract_enabled_and_version_drift_are_discriminated(self):
        baseline = inventory.manifest_job_definition_catalog(self.manifest)
        for mutation in ['enabled', 'version', 'execution', 'state', 'owner']:
            candidate = copy.deepcopy(self.manifest)
            workflow = next(w for w in candidate['workflows'] if w['key'] == 'npi-sweep-weekly')
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
