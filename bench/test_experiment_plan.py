"""验证试验设计能够固定、重复和区分不同条件。"""
from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import unittest

from bench.experiment_plan import DOMAINS, EFFECTS, FAULTS, RATES, trials, write_csv


class ExperimentPlanTests(unittest.TestCase):
    def test_conditions_have_expected_cartesian_product(self):
        plan = list(trials(repeats=2))
        self.assertEqual(len(plan), len(DOMAINS) * len(EFFECTS) * len(FAULTS) * len(RATES) * 2)
        self.assertEqual(len({row.scenario_id for row in plan}), len(plan))

    def test_repeatable_seed_without_global_random_state(self):
        self.assertEqual(list(trials(1, 12345)), list(trials(1, 12345)))
        self.assertNotEqual(list(trials(1, 12345)), list(trials(1, 12346)))

    def test_csv_is_excel_compatible_utf8_and_has_no_fake_metrics(self):
        with TemporaryDirectory() as work:
            path = Path(work) / 'plan.csv'
            count = write_csv(path, 1)
            self.assertEqual(count, 360)
            self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))
            with path.open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), count)
            self.assertNotIn('task_success', rows[0])

    def test_invalid_repeat_count(self):
        with self.assertRaises(ValueError):
            list(trials(0))


if __name__ == '__main__':
    unittest.main()
