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



class ReleaseRecoveryTests(unittest.TestCase):
    def test_lost_ack_after_release_confirms_server_state(self):
        from unittest.mock import patch
        from bench.window_uncertainty import release_held
        replies = [ConnectionResetError("lost release acknowledgement"),
                   {"released": True, "pending": 0}]
        with patch("bench.window_uncertainty.control", side_effect=replies) as call:
            release_held("http://127.0.0.1:1")
        self.assertEqual([x.args[1] for x in call.call_args_list],
                         ["release", "state"])

    def test_retry_only_after_confirmed_still_pending(self):
        from unittest.mock import patch
        from bench.window_uncertainty import release_held
        replies = [ConnectionResetError("connection reset"),
                   {"released": False, "pending": 1},
                   {"released": True, "pending": 1}]
        with patch("bench.window_uncertainty.control", side_effect=replies) as call, \
                patch("bench.window_uncertainty.time.sleep"):
            release_held("http://127.0.0.1:1")
        self.assertEqual([x.args[1] for x in call.call_args_list],
                         ["release", "state", "release"])

    def test_inconsistent_server_state_fails_closed(self):
        from unittest.mock import patch
        from bench.window_uncertainty import release_held
        replies = [ConnectionResetError("connection reset"),
                   {"released": False, "pending": 0}]
        with patch("bench.window_uncertainty.control", side_effect=replies):
            with self.assertRaisesRegex(RuntimeError, "状态与挂起请求数矛盾"):
                release_held("http://127.0.0.1:1")

    def test_unreachable_server_does_not_appear_released(self):
        from unittest.mock import patch
        from bench.window_uncertainty import release_held
        with patch("bench.window_uncertainty.control",
                   side_effect=ConnectionResetError("server unavailable")) as call, \
                patch("bench.window_uncertainty.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "无法由服务器状态确认"):
                release_held("http://127.0.0.1:1")
        self.assertEqual(call.call_count, 6)

if __name__ == "__main__":
    unittest.main()
