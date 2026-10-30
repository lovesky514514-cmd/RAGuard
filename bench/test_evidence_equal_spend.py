"""验证累计成本恒等约束、不可行输入及被迫改选。"""

import unittest

from bench.evidence_equal_spend import ExactSpend


class ExactSpendTests(unittest.TestCase):
    def test_all_preferences_pay_exact_budget(self):
        for preference in ("cheap", "quick"):
            tracker = ExactSpend({"cheap": 1, "quick": 2}, 10, 15)
            got = [tracker.select(preference) for _ in range(10)]
            tracker.verify()
            self.assertEqual(sum(1 if item[0] == "cheap" else 2 for item in got), 15)
            self.assertEqual(tracker.forced, 5)

    def test_mixed_preferences_keep_original_choice(self):
        tracker = ExactSpend({"cheap": 1, "quick": 2}, 6, 9)
        for preference in ["cheap", "quick"] * 3:
            self.assertFalse(tracker.select(preference)[1])
        tracker.verify()

    def test_invalid_budget_and_excess_query(self):
        for total in (3, 13):
            with self.assertRaises(ValueError):
                ExactSpend({"cheap": 1, "quick": 2}, 6, total)
        tracker = ExactSpend({"cheap": 1}, 1, 1)
        tracker.select("cheap")
        with self.assertRaises(ValueError):
            tracker.select("cheap")
        tracker.verify()

    def test_early_completion_rejected(self):
        tracker = ExactSpend({"cheap": 1, "quick": 2}, 2, 3)
        tracker.select("cheap")
        with self.assertRaises(ValueError):
            tracker.verify()
