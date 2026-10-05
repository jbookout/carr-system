#!/usr/bin/env python3
import copy
import unittest
from pathlib import Path
from registry_chain import registry_chain, snapshot_selection, validate_chain, successor_snapshot_selection


class RegistryChain(unittest.TestCase):
    def test_frontier_and_snapshot_pins_come_from_the_chain(self):
        chain = registry_chain()
        current = chain['versions'][-1]
        snapshot = snapshot_selection(current['number'], chain)
        self.assertEqual(snapshot['SCAC_CURRENT_NUMBER'], str(current['number']))
        self.assertEqual(snapshot['SCAC_CURRENT_ENTRY_COUNT'], str(current['entry_count']))
        self.assertEqual(snapshot['SCAC_CURRENT_SOURCE_COUNT'], str(current['source_count']))
        self.assertIn(current['version'], snapshot['SCAC_VERSION_ARRAY'])
        self.assertNotIn("'"+current['version']+"'", snapshot['SCAC_HISTORICAL_ARRAY'])
        self.assertEqual(snapshot_selection(38, chain)['SCAC_HISTORICAL_ARRAY'].split(',')[-1], "'scac-mutation-registry.v36'")

    def test_new_successors_are_selected_without_a_shell_arm(self):
        chain = copy.deepcopy(registry_chain())
        predecessor = chain['versions'][-1]
        row = dict(predecessor, number=predecessor['number']+1,
            version=f"scac-mutation-registry.v{predecessor['number']+1}",
            predecessor=predecessor['version'], migration='migrations/0901_example_scac_successor.sql',
            dependencies=[Path(predecessor['migration']).name, '0900_example.sql'])
        row.pop('snapshot', None)
        chain['versions'].append(row)
        ledger = [Path(predecessor['migration']).name, '0900_example.sql', '0901_example_scac_successor.sql']
        self.assertEqual(successor_snapshot_selection(ledger, predecessor['number'], chain)['SCAC_CURRENT_NUMBER'], str(row['number']))
        with self.assertRaisesRegex(ValueError, 'dependency'):
            successor_snapshot_selection(ledger[1:], predecessor['number'], chain)
        self.assertEqual(successor_snapshot_selection(ledger[:-1], predecessor['number'], chain), {})

    def test_a_gap_or_rewritten_predecessor_is_refused(self):
        chain = copy.deepcopy(registry_chain())
        chain['versions'][-1]['predecessor'] = 'scac-mutation-registry.v1'
        with self.assertRaisesRegex(ValueError, 'continuity'):
            validate_chain(chain)
        chain = copy.deepcopy(registry_chain())
        chain['versions'].pop(10)
        with self.assertRaisesRegex(ValueError, 'continuity'):
            validate_chain(chain)


if __name__ == '__main__':
    unittest.main()
