"""合成数据配对、重跑和分布反转回归。"""

from pathlib import Path
import tempfile
import unittest

from bench.evidence_budget_experiment import draw, run, check_pairs


class BudgetStudyTests(unittest.TestCase):
    def test_random_seed_reproduces_cases(self):
        self.assertEqual(draw(123, "matched", 20), draw(123, "matched", 20))
        self.assertNotEqual(draw(123, "matched", 20), draw(124, "matched", 20))

    def test_incomplete_pair_is_rejected(self):
        with self.assertRaises(ValueError):
            check_pairs([{"case_id": "matched-00000", "policy": "fixed"}], 1)

    def test_reproducible_output_and_no_unsafe_authorization(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            one = run(25, 18, 20261026, Path(a))
            two = run(25, 18, 20261026, Path(b))
            self.assertEqual(one, two)
            self.assertEqual((Path(a) / "SHA256SUMS.json").read_bytes(),
                             (Path(b) / "SHA256SUMS.json").read_bytes())
            self.assertTrue(all(row["unsafe_authorization"] == 0 for row in one))

    def test_environment_reversal_must_remain_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run(200, 200, 12345, Path(folder))
            def successes(env, policy):
                return next(r["safe_confirm"] for r in result
                            if r["domain"] == env and r["policy"] == policy)
            self.assertGreater(successes("matched", "calibrated"),
                               successes("matched", "cheap"))
            self.assertLess(successes("reversed", "calibrated"),
                            successes("reversed", "cheap"))


if __name__ == "__main__":
    unittest.main()
