"""验证测试域不可进入训练与校准。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench import evidence_budget_experiment as experiment


class TrainingIsolationTests(unittest.TestCase):
    def test_shift_distribution_does_not_affect_calibration(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            experiment.run(60, 30, 42, Path(first))
            import json
            first_protocol = json.loads((Path(first) / "protocol.json").read_text(encoding="utf-8"))
            changed = {**experiment.PROFILES, "reversed": {
                "audit": .01, "cheap": .01, "quick": .99,
            }}
            with patch.object(experiment, "PROFILES", changed):
                experiment.run(60, 30, 42, Path(second))
            second_protocol = json.loads((Path(second) / "protocol.json").read_text(encoding="utf-8"))
            self.assertEqual(first_protocol["observed_calibration"],
                             second_protocol["observed_calibration"])
            self.assertEqual(first_protocol["conservative_split_lower_bound"],
                             second_protocol["conservative_split_lower_bound"])

    def test_all_strategies_use_same_observation_budget(self):
        self.assertEqual((experiment.BUDGET.cost, experiment.BUDGET.queries),
                         (2, 2))


if __name__ == "__main__":
    unittest.main()
