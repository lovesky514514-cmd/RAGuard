"""跨进程故障点验证：即使恢复者并发执行也不能创造新副作用。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from bench.restart_live import build_binary, run_case


@unittest.skipUnless(shutil.which('go'), '需要 Go 沙箱源码构建')
class RestartLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory()
        cls.binary = build_binary(Path(cls.work.name))

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_crash_after_effect_reconciles_across_processes(self):
        for method in ('b5_durable', 'raguard'):
            with self.subTest(method=method):
                row = run_case(method, 'tickets.create', 'after', False, 4, self.binary)
                self.assertEqual((row.physical_writes, row.duplicate_effects, row.unsafe),
                                 (1, 0, 0))
                self.assertEqual(row.resumed_successes, 4)

    def test_crash_before_effect_never_fabricates_confirmation(self):
        for method in ('b5_durable', 'raguard'):
            with self.subTest(method=method):
                row = run_case(method, 'tickets.create', 'before', False, 4, self.binary)
                self.assertEqual(row.physical_writes, 0)
                self.assertEqual(row.erroneous_confirmation, 0)
                self.assertEqual(row.unsafe, 0)

    def test_opaque_effect_is_not_reexecuted_when_evidence_missing(self):
        for method in ('b5_durable', 'raguard'):
            with self.subTest(method=method):
                row = run_case(method, 'notifications.send', 'after', False, 4, self.binary)
                self.assertEqual((row.physical_writes, row.resumed_successes, row.unsafe),
                                 (1, 0, 0))

    def test_native_idempotency_preserves_single_physical_effect(self):
        for method in ('b5_durable', 'raguard'):
            with self.subTest(method=method):
                row = run_case(method, 'orders.create', 'after', True, 4, self.binary)
                self.assertEqual((row.physical_writes, row.unsafe), (1, 0))

    def test_observed_dead_owner_enables_safe_idempotent_takeover(self):
        for point in ('before', 'after'):
            with self.subTest(crash_point=point):
                normal = run_case('raguard', 'orders.create', point, True, 4, self.binary)
                takeover = run_case('raguard', 'orders.create', point, True, 4,
                                    self.binary, recovery_policy='dead_owner')
                self.assertEqual(normal.resumed_successes, 0)
                self.assertNotEqual(normal.final_status, 200)
                self.assertGreaterEqual(takeover.resumed_successes, 1)
                self.assertEqual(takeover.final_status, 200)
                self.assertEqual((takeover.physical_writes, takeover.unsafe,
                                  takeover.erroneous_confirmation), (1, 0, 0))

    def test_observed_dead_owner_never_reexecutes_opaque_effect(self):
        row = run_case('raguard', 'notifications.send', 'after', False, 4,
                       self.binary, recovery_policy='dead_owner')
        self.assertEqual((row.physical_writes, row.resumed_successes, row.unsafe),
                         (1, 0, 0))

    def test_invalid_recovery_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            run_case('raguard', 'tickets.create', 'after', False, 4,
                     self.binary, recovery_policy='unknown')


if __name__ == '__main__':
    unittest.main()
