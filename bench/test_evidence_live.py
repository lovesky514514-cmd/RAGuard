"""两套真实调用路径在 Go 沙箱下共享证据准入契约。"""

import tempfile
import shutil
import unittest
from pathlib import Path

from bench.evidence_live import build_current_mock, run_case


@unittest.skipUnless(shutil.which('go'), '证据端到端实验需要 Go 源码构建')
class EvidenceLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory()
        cls.binary = build_current_mock(Path(cls.work.name))

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def check(self, method, tool='tickets.create', fault='after_disconnect',
              mode='normal', no_readback=False):
        return run_case(method, tool, fault, mode, no_readback, self.binary)

    def test_valid_proof_recovers_ambiguous_commit(self):
        for method in ('b5_durable', 'raguard'):
            with self.subTest(method=method):
                row = self.check(method)
                self.assertEqual(row.resume_status, 200)
                self.assertEqual(row.unsafe, 0)
                self.assertEqual(row.false_confirmation, 0)
                self.assertGreaterEqual(row.admitted_proofs, 1)

    def test_invalid_proof_does_not_confirm_unknown_mutation(self):
        for mode in ('wrong_business', 'wrong_epoch', 'wrong_digest',
                     'stale', 'not_final'):
            for method in ('b5_durable', 'raguard'):
                with self.subTest(mode=mode, method=method):
                    row = self.check(method, mode=mode)
                    self.assertNotEqual(row.resume_status, 200)
                    self.assertEqual(row.false_confirmation, 0)
                    self.assertEqual(row.unsafe, 0)
                    self.assertGreaterEqual(row.rejected_proofs, 1)

    def test_unavailable_evidence_stays_unresolved(self):
        for method in ('b5_durable', 'raguard'):
            with self.subTest(method=method):
                row = self.check(method, no_readback=True)
                self.assertEqual(row.resume_status, 409)
                self.assertEqual(row.unsafe, 0)

    def test_fair_pair_same_live_outcome(self):
        for tool in ('orders.create', 'tickets.create'):
            for mode in ('normal', 'wrong_business'):
                with self.subTest(tool=tool, mode=mode):
                    a = self.check('b5_durable', tool=tool, mode=mode)
                    b = self.check('raguard', tool=tool, mode=mode)
                    self.assertEqual((a.physical_goal, a.unsafe, a.false_confirmation),
                                     (b.physical_goal, b.unsafe, b.false_confirmation))


if __name__ == '__main__':
    unittest.main()
