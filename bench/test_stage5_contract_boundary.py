"""Stage 5 合同边界、独立物理真值与公平配对测试。"""

from dataclasses import replace
import tempfile
from pathlib import Path
import unittest

from bench.stage5_contract_boundary import (CONDITIONS, METHODS, STRATEGIES,
                                            permitted, run, validate)


def condition(name):
    return next(c for c in CONDITIONS if c.name == name)


class Stage5ContractTests(unittest.TestCase):
    def test_contract_window_and_scope(self):
        accepted = {"native_inside", "before_commit_inside"}
        for c in CONDITIONS:
            with self.subTest(condition=c.name):
                self.assertEqual(permitted(c, "contract"), c.name in accepted)
                self.assertTrue(permitted(c, "unaware"))
        with self.assertRaises(ValueError):
            permitted(condition("native_inside"), "invalid")

    def test_fixed_conditions_unique(self):
        self.assertEqual(len(CONDITIONS), 8)
        self.assertEqual(len({c.name for c in CONDITIONS}), len(CONDITIONS))
        self.assertEqual(METHODS, ("b5", "raguard"))
        self.assertEqual(STRATEGIES, ("unaware", "contract"))

    def test_rejects_incomplete_and_falsified_oracle(self):
        # 此测试实际建立 Go HTTP 沙箱；不能用合成行冒充物理结果。
        with tempfile.TemporaryDirectory() as folder:
            rows = run(Path(folder), repeats=1, seed=15)
        self.assertEqual(len(rows), 32)
        validate(rows, 1)
        with self.assertRaisesRegex(ValueError, "缺少配对"):
            validate(rows[:-1], 1)
        bad = list(rows)
        bad[0] = replace(bad[0], unsafe_confirmation=1 - bad[0].unsafe_confirmation)
        with self.assertRaisesRegex(ValueError, "判分不一致"):
            validate(bad, 1)
        blocked = next(i for i, r in enumerate(rows)
                       if r.strategy == "contract" and not r.policy_admitted)
        bad = list(rows)
        bad[blocked] = replace(
            bad[blocked], http_writes_recovery=1,
            http_writes_total=bad[blocked].http_writes_total + 1,
        )
        with self.assertRaisesRegex(ValueError, "阻断之后"):
            validate(bad, 1)

    def test_invalid_repeat_fails_before_go_build(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                run(Path(folder), repeats=0)


if __name__ == "__main__":
    unittest.main()
