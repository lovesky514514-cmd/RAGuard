"""多预算统计拒绝缺行，并以随机种子为统计单元。"""

import unittest

from bench.evidence_budget_sensitivity import aggregate, paired_differences


class SensitivityTests(unittest.TestCase):
    def test_missing_pair_rejected(self):
        with self.assertRaises(ValueError):
            aggregate([], (41,), (12,))

    def test_complete_pair_and_seed_statistics(self):
        rows = []
        for seed, value in ((41, 2), (42, 4)):
            for phase in ("matched", "shifted"):
                for engine in ("b5", "raguard"):
                    for policy in ("fixed_cheap", "fixed_quick", "alternate", "sliding_ucb"):
                        rows.append({"seed": seed, "cap": 12, "phase": phase,
                                     "engine": engine, "policy": policy, "safe": value,
                                     "cost": 10, "skipped_queries": 1, "unsafe": 0})
        result = aggregate(rows, (41, 42), (12,))
        deltas = paired_differences(rows, (41, 42), (12,))
        self.assertEqual(len(deltas), 24)
        self.assertTrue(all(d['delta_safe'] == 0 for d in deltas))
        self.assertEqual(len(result), 16)
        self.assertEqual(result[0]["mean_safe"], 3)
        self.assertEqual(result[0]["min_safe"], 2)
        self.assertEqual(result[0]["max_safe"], 4)

    def test_duplicate_pair_rejected(self):
        rows = [{"seed": 41, "cap": 12, "phase": "matched", "engine": "b5",
                 "policy": "fixed_cheap", "safe": 0, "cost": 1,
                 "skipped_queries": 0, "unsafe": 0}]
        with self.assertRaises(ValueError):
            aggregate(rows + rows, (41,), (12,))


if __name__ == "__main__":
    unittest.main()
