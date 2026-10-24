"""真实 HTTP 副作用的有界等待：严格对照、负面证据和资源上限。"""

from pathlib import Path
import shutil
import tempfile
import unittest

from bench.deferred_live import run_case, WAIT_BUDGET
from bench.evidence_live import build_current_mock


@unittest.skipUnless(shutil.which('go'), '需 Go 编译独立模拟沙箱')
class DeferredLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory()
        cls.binary = build_current_mock(Path(cls.work.name))

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_delayed_positive_rescues_both_methods(self):
        for method in ('b5_deferred', 'raguard_deferred'):
            with self.subTest(method=method):
                row = run_case(method, 'tickets.create', 'after_disconnect', 'normal',
                               5, self.binary)
                self.assertEqual(row.resume_status, 200)
                self.assertEqual(row.unsafe, 0)
                self.assertEqual(row.false_confirmation, 0)
                self.assertGreater(row.deferred_checks, 0)

    def test_no_evidence_stops_without_repeating_side_effect(self):
        for method in ('b5_deferred', 'raguard_deferred'):
            row = run_case(method, 'tickets.create', 'after_disconnect',
                           'wrong_business', 0, self.binary)
            self.assertNotEqual(row.resume_status, 200)
            self.assertEqual(row.unsafe, 0)
            self.assertEqual(row.false_confirmation, 0)
            self.assertLessEqual(row.deferred_checks, WAIT_BUDGET.checks)

    def test_before_commit_disconnect_never_false_confirms(self):
        for method in ('b5_deferred', 'raguard_deferred'):
            row = run_case(method, 'tickets.create', 'before_disconnect',
                           'normal', 2, self.binary)
            self.assertEqual(row.false_confirmation, 0)
            self.assertEqual(row.unsafe, 0)
            self.assertNotEqual(row.resume_status, 200)

    def test_bad_binding_stops_polling_early(self):
        row = run_case('raguard_deferred', 'tickets.create', 'after_disconnect',
                       'wrong_business', 0, self.binary)
        self.assertEqual(row.deferred_checks, 1)
        self.assertEqual((row.unsafe, row.false_confirmation), (0, 0))

    def test_no_wait_is_control_group(self):
        for method in ('b5_nowait', 'raguard_nowait'):
            row = run_case(method, 'tickets.create', 'after_disconnect',
                           'normal', 5, self.binary)
            self.assertEqual(row.deferred_checks, 0)
            self.assertEqual(row.false_confirmation, 0)

    def test_all_variants_preserve_effect_truth(self):
        for method in ('b5_nowait', 'raguard_nowait', 'b5_deferred', 'raguard_deferred'):
            row = run_case(method, 'tickets.create', 'after_disconnect',
                           'normal', 2, self.binary)
            self.assertEqual((row.physical_goal, row.unsafe, row.duplicate_effects), (1, 0, 0))


if __name__ == '__main__':
    unittest.main()
