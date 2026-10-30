"""跨回合 Go HTTP 证据实验的边界回归。"""

import unittest

from bench.evidence_online_http import Episode, Scenario, choice, scenarios, validate
from bench.evidence_online_policy import Choice, SlidingWindowUCB


class OnlineHTTPTests(unittest.TestCase):
    def test_scenario_seed_and_hidden_switch(self):
        a = scenarios(123, 20)
        self.assertEqual(a, scenarios(123, 20))
        self.assertEqual([s.phase for s in a[:10]], ["matched"] * 10)
        self.assertEqual([s.phase for s in a[10:]], ["shifted"] * 10)
        self.assertNotEqual(a, scenarios(124, 20))

    def test_fixed_policy_and_online_initial_exploration(self):
        learner = SlidingWindowUCB((Choice("cheap", 1), Choice("quick", 2)),
                                   window=12, exploration=.35)
        self.assertEqual(choice("fixed_cheap", 0, learner), "cheap")
        self.assertEqual(choice("fixed_quick", 0, learner), "quick")
        self.assertEqual(choice("sliding_ucb", 0, learner), "cheap")
        learner.observe("cheap", False)
        self.assertEqual(choice("sliding_ucb", 1, learner), "quick")

    def test_invalid_scenario_count(self):
        for count in (0, 7, 9, 1002):
            with self.subTest(count=count), self.assertRaises(ValueError):
                scenarios(12, count)

    def test_exact_spend_rejects_unequal_cost(self):
        with self.assertRaises(ValueError):
            validate([], 8, exact_budget=True)

    def test_mutually_exclusive_budget_modes(self):
        from bench.evidence_online_http import run
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            run(1, 8, Path(folder), exact_budget=True, budget_cap=True)

    def test_incomplete_and_duplicate_pair_fail(self):
        with self.assertRaises(ValueError):
            validate([], 8)
        example = Episode(0, "matched", "b5", "fixed_cheap", "cheap",
                          1, 1, 200, 1, 0, 0, 0, 1, 1, 0)
        with self.assertRaises(ValueError):
            validate([example, example], 8)


if __name__ == "__main__":
    unittest.main()
