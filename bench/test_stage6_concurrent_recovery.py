"""Stage 6：旧请求晚提交、并发恢复强基线及独立判分的行为测试。"""

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from bench.stage6_concurrent_recovery import (CONDITIONS, METHODS, STRATEGIES,
                                              can_recover, run_one, validate)
from bench.window_uncertainty import ROOT


def by_name(name):
    return next(c for c in CONDITIONS if c.name == name)


class ContractPolicyTests(unittest.TestCase):
    def test_inflight_known_is_still_not_safe_to_retry(self):
        for case in CONDITIONS:
            with self.subTest(case=case.name):
                self.assertFalse(can_recover(case, "verified"))
                self.assertTrue(can_recover(case, "unaware"))

    def test_clock_skew_changes_only_naive_admission(self):
        self.assertTrue(can_recover(by_name("native_clock_under"), "client_age"))
        self.assertFalse(can_recover(by_name("native_clock_over"), "client_age"))
        with self.assertRaises(ValueError):
            can_recover(by_name("native_late_commit"), "unknown_strategy")

    def test_unique_matrix(self):
        self.assertEqual(len(CONDITIONS), 9)
        self.assertEqual(len({x.name for x in CONDITIONS}), len(CONDITIONS))
        self.assertEqual(METHODS, ("b5", "raguard"))
        self.assertEqual(STRATEGIES, ("unaware", "client_age", "verified"))


class LiveGoConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.folder.name) / ("uncertainty.exe" if os.name == "nt" else "uncertainty")
        subprocess.run(["go", "build", "-o", str(cls.binary), "./windowlab/cmd/uncertainty"],
                       cwd=ROOT / "mock", timeout=120, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_late_request_can_duplicate_after_success(self):
        for method in METHODS:
            with self.subTest(method=method):
                c = by_name("native_late_commit")
                unsafe = run_one(self.binary, c, method, "unaware", 1)
                conservative = run_one(self.binary, c, method, "verified", 1)
                self.assertEqual(unsafe.duplicate_effects, 1)
                self.assertEqual(unsafe.unsafe_confirmation, 1)
                self.assertEqual(unsafe.original_pending_before_recovery, 1)
                self.assertEqual(conservative.physical_effects, 1)
                self.assertEqual(conservative.duplicate_effects, 0)
                self.assertEqual(conservative.recovery_http_writes, 0)
                self.assertEqual(conservative.unresolved, 1)

    def test_explicit_no_dedupe_isolated_go(self):
        for method in METHODS:
            with self.subTest(method=method):
                case = by_name("no_dedupe_four")
                unsafe = run_one(self.binary, case, method, "unaware", 1)
                self.assertGreaterEqual(unsafe.duplicate_effects, 1)
                self.assertGreaterEqual(unsafe.worker_successes, 1)
                self.assertEqual(unsafe.worker_successes + unsafe.worker_waits + unsafe.worker_unknowns, case.workers)
                self.assertEqual(unsafe.late_original_effects, 1)

    def test_independent_oracle_rejects_tamper(self):
        # 用受控 Go 真实记录构造负面测试；不得伪造物理效果观测。
        case = by_name("native_early_release")
        original = run_one(self.binary, case, "b5", "verified", 1)
        corrupt = replace(original, unsafe_confirmation=1)
        from bench.stage6_concurrent_recovery import CONDITION_BY_NAME
        self.assertIn(case.name, CONDITION_BY_NAME)
        with self.assertRaisesRegex(ValueError, "矩阵不完整"):
            validate([original], 1)
        # 校验器必须在完整矩阵之外还拒绝单行算术矛盾。
        from bench.stage6_concurrent_recovery import validate_row
        with self.assertRaisesRegex(ValueError, "自检失败"):
            validate_row(corrupt)


if __name__ == "__main__":
    unittest.main()
