"""服务端晚提交、时钟保护窗口与作用域的回归测试。"""

import shutil
import os
import tempfile
import unittest
from pathlib import Path

from bench.window_uncertainty import Contract, admitted, run_case


class ContractTests(unittest.TestCase):
    def test_known_ttl_requires_margin(self):
        contract = Contract(6, "region-a", 1, True)
        self.assertTrue(contract.permits(3, "region-a"))
        self.assertFalse(contract.permits(5, "region-a"))
        self.assertFalse(contract.permits(3, "region-b"))

    def test_unknown_contract_or_unsettled_request_stops(self):
        for retention, finished in ((None, True), (6, False)):
            with self.subTest(retention=retention, finished=finished):
                contract = Contract(retention, "region-a", 1, finished)
                self.assertFalse(admitted("verified", 3, "region-a", contract))
                self.assertTrue(admitted("client_age", 3, "region-a", contract))


@unittest.skipUnless(shutil.which("go"), "需要 Go 1.21+")
class LivePairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bench.window_uncertainty import ROOT
        import subprocess
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / ("uncertainty.exe" if os.name == "nt" else "uncertainty")
        subprocess.run(["go", "build", "-o", str(cls.binary), "./windowlab/cmd/uncertainty"],
                       cwd=ROOT / "mock", check=True, capture_output=True, timeout=120)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_late_commit_after_recovery_causes_duplicate(self):
        for method in ("b5", "raguard"):
            condition = ("inflight", 3, 0, 6, "same", True)
            naive = run_case(self.binary, method, "client_age", condition)
            bounded = run_case(self.binary, method, "verified", condition)
            with self.subTest(method=method):
                self.assertEqual((naive.duplicate_effects, naive.unsafe_confirmation), (1, 1))
                self.assertEqual((bounded.blocked, bounded.duplicate_effects), (1, 0))

    def test_settled_original_replays_within_retention(self):
        for method in ("b5", "raguard"):
            row = run_case(self.binary, method, "verified",
                           ("after_commit", 3, 0, 6, "same", True))
            with self.subTest(method=method):
                self.assertEqual((row.safe_confirmation, row.duplicate_effects), (1, 0))

    def test_unknown_ttl_fails_closed(self):
        for method in ("b5", "raguard"):
            row = run_case(self.binary, method, "verified",
                           ("after_commit", 3, 0, 6, "same", False))
            with self.subTest(method=method):
                self.assertEqual((row.blocked, row.duplicate_effects), (1, 0))


if __name__ == "__main__":
    unittest.main()
