"""四维耦合实验的可复现性、配对完整性及安全授权测试。"""

from pathlib import Path
import tempfile
import unittest

from bench.evidence_meta_experiment import MODEL, POLICIES, evaluate, paired, study


class MetaExperimentTests(unittest.TestCase):
    def test_all_outcomes_stay_within_budget_and_do_not_reissue(self):
        for world in MODEL:
            for policy in POLICIES:
                for now in (0, 2):
                    item = evaluate(world, policy, 3, now)
                    self.assertLessEqual(item["cost"], 3)
                    self.assertEqual(item["unsafe"], 0)
                    self.assertNotEqual(item["action"], "reissue")

    def test_reproducible_and_hashed(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertEqual(study((41, 42), (2, 3), 20, Path(a)),
                             study((41, 42), (2, 3), 20, Path(b)))
            self.assertEqual((Path(a) / "SHA256SUMS.json").read_bytes(),
                             (Path(b) / "SHA256SUMS.json").read_bytes())

    def test_incomplete_pairs_are_rejected(self):
        with self.assertRaises(ValueError):
            paired([], (41,), (3,), 1, ("calibrated", "shifted"))

    def test_bad_inputs_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                study((41, 41), (2,), 10, Path(folder))
