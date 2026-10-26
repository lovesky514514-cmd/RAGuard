"""严格配对分析的行为回归。"""

from dataclasses import dataclass, replace
import unittest

from bench.window_uncertainty_analysis import paired_rows


@dataclass(frozen=True)
class Case:
    condition: str = "after_commit-3-0-6-same-1"
    repeat: int = 0
    strategy: str = "verified"
    method: str = "b5"
    safe_confirmation: int = 1
    duplicate_effects: int = 0
    wrong_scope_effects: int = 0
    unsafe_confirmation: int = 0
    unresolved: int = 0
    network_attempts: int = 2


class PairedTests(unittest.TestCase):
    def check(self, cases):
        return paired_rows(
            cases, conditions=(("after_commit", 3, 0, 6, "same", True),),
            strategies=("verified",), repeats=1,
        )

    def test_matching_pair(self):
        data = self.check([Case(), Case(method="raguard")])
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["same_outcome"], 1)

    def test_missing_duplicate_and_wrong_identity(self):
        base = [Case(), Case(method="raguard")]
        for items in (base[:1], base + [base[0]],
                      [base[0], replace(base[1], condition="other")]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                self.check(items)

    def test_metric_difference_is_not_hidden(self):
        result = self.check([Case(), Case(method="raguard", safe_confirmation=0,
                                         unresolved=1, network_attempts=3)])[0]
        self.assertEqual(result["same_outcome"], 0)
        self.assertEqual(result["delta_safe_confirmation"], -1)
        self.assertEqual(result["delta_network_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
