"""端到端边界测试：原版引擎持久恢复，物理真值只在评价阶段读取。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from bench.window_live import VerifiedWindow, build_binary, run_case, METHODS


class WindowAdmissionTests(unittest.TestCase):
    def test_guarantee_boundaries(self):
        known = VerifiedWindow(0, 4, 'region-a')
        self.assertTrue(known.permits(tick=2, scope='region-a'))
        self.assertFalse(known.permits(tick=4, scope='region-a'))
        self.assertFalse(known.permits(tick=2, scope='region-b'))
        self.assertFalse(VerifiedWindow(0, None, 'region-a').permits(tick=2, scope='region-a'))


@unittest.skipUnless(shutil.which('go'), '需要 Go 1.21+ 重新构建本机研究沙箱')
class WindowLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.binary = build_binary(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_expiry_creates_second_physical_effect_without_contract(self):
        for method in METHODS:
            with self.subTest(method=method):
                r = run_case(method, 'after_commit', 6, 'same', 'unaware', self.binary)
                self.assertEqual(r.duplicate_effects, 1)
                self.assertEqual(r.initial_status, 409)

    def test_proven_window_permits_safe_recovery(self):
        for method in METHODS:
            with self.subTest(method=method):
                r = run_case(method, 'after_commit', 2, 'same', 'contract', self.binary)
                self.assertEqual((r.duplicate_effects, r.acknowledged_completion), (0, 1))

    def test_boundary_and_scope_rejected_equally(self):
        for method in METHODS:
            for tick, scope in ((4, 'same'), (2, 'moved'), (6, 'same')):
                with self.subTest(method=method, tick=tick, scope=scope):
                    r = run_case(method, 'after_commit', tick, scope, 'contract', self.binary)
                    self.assertEqual((r.blocked, r.duplicate_effects, r.unsafe_confirmation), (1, 0, 0))

    def test_unknown_contract_stops_both(self):
        for method in METHODS:
            with self.subTest(method=method):
                r = run_case(method, 'after_commit', 2, 'same', 'contract', self.binary,
                             guaranteed=False)
                self.assertEqual((r.blocked, r.duplicate_effects), (1, 0))

    def test_precommit_drop_can_reduce_availability(self):
        for method in METHODS:
            with self.subTest(method=method):
                baseline = run_case(method, 'before_commit', 6, 'same', 'unaware', self.binary)
                guarded = run_case(method, 'before_commit', 6, 'same', 'contract', self.binary)
                self.assertEqual((baseline.physical_effects, baseline.acknowledged_completion), (1, 1))
                self.assertEqual((guarded.physical_effects, guarded.unresolved), (0, 1))

    def test_methods_have_identical_fair_contract_boundary(self):
        for tick, scope, fault in ((2, 'same', 'after_commit'),
                                   (4, 'same', 'after_commit'),
                                   (6, 'moved', 'before_commit')):
            a = run_case('b5', fault, tick, scope, 'contract', self.binary)
            b = run_case('raguard', fault, tick, scope, 'contract', self.binary)
            self.assertEqual((a.blocked, a.duplicate_effects, a.wrong_scope_effects,
                              a.acknowledged_completion, a.unresolved),
                             (b.blocked, b.duplicate_effects, b.wrong_scope_effects,
                              b.acknowledged_completion, b.unresolved))


if __name__ == '__main__':
    unittest.main()
