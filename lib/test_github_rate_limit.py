"""Reservation contracts shared by GitHub callers."""
import json
import tempfile
import unittest
from pathlib import Path

from lib.github_rate_limit import GitHubReadBudget


class ReservationTests(unittest.TestCase):
    def test_absolute_slot_and_delay_callers_share_one_schedule(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'budget.json'
            first = GitHubReadBudget({}, path=path, clock=lambda: 100.0)
            second = GitHubReadBudget({}, path=path, clock=lambda: 100.0)
            self.assertEqual(first.reserve('core', absolute=True), 100.0)
            self.assertEqual(second.reserve('core'), 2.0)
            self.assertEqual(first.reserve('core', absolute=True), 104.0)
            self.assertEqual(json.loads(path.read_text())[first.shared]['next_start'], 106.0)

    def test_absolute_slot_preserves_completion_cooldown(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'budget.json'
            now = [100.0]
            budget = GitHubReadBudget({}, path=path, clock=lambda: now[0])
            with budget.call_slot(timeout=1) as mark_started:
                self.assertEqual(budget.reserve('core', absolute=True), 100.0)
                mark_started()
                now[0] = 101.0
            now[0] = 101.5
            self.assertEqual(budget.reserve('core', absolute=True), 103.0)


if __name__ == '__main__':
    unittest.main()
