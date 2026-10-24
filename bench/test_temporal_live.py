"""比较同预算轮询时，结果、安全和时间开销。"""

import shutil
from pathlib import Path
import tempfile
import unittest

from bench.evidence_live import build_current_mock
from bench.temporal_live import run_case, VISIBILITY_DELAYS_MS


@unittest.skipUnless(shutil.which('go'), 'Go sandbox source build required')
class TemporalLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = build_current_mock(Path(cls.temp.name))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_both_methods_confirm_when_proof_immediately_visible(self):
        for engine in ('b5', 'raguard'):
            for schedule in ('burst', 'spaced'):
                with self.subTest(engine=engine, schedule=schedule):
                    r = run_case(engine, schedule, 'tickets.create',
                                 'after_disconnect', 0, self.binary)
                    self.assertEqual((r.status, r.unsafe, r.false_confirmation), (200, 0, 0))

    def test_spaced_query_rescues_delayed_confirmation(self):
        # 120ms 真值延迟；用较大间隔避免不同 CI 主机的调度差异。
        for engine in ('b5', 'raguard'):
            with self.subTest(engine=engine):
                late = run_case(engine, 'spaced', 'tickets.create',
                                'after_disconnect', 120, self.binary)
                self.assertEqual((late.status, late.unsafe, late.false_confirmation),
                                 (200, 0, 0))
                self.assertLessEqual(late.deferred_checks, 3)

    def test_evidence_after_deadline_does_not_confirm(self):
        for engine in ('b5', 'raguard'):
            with self.subTest(engine=engine):
                r = run_case(engine, 'spaced', 'tickets.create',
                             'after_disconnect', 450, self.binary)
                self.assertNotEqual(r.status, 200)
                self.assertEqual((r.unsafe, r.false_confirmation), (0, 0))

    def test_before_commit_does_not_falsely_claim_success(self):
        for engine in ('b5', 'raguard'):
            r = run_case(engine, 'spaced', 'tickets.create',
                         'before_disconnect', 120, self.binary)
            self.assertEqual((r.status == 200, r.unsafe, r.false_confirmation),
                             (False, 0, 0))

    def test_invalid_delay_rejected(self):
        self.assertNotIn(-1, VISIBILITY_DELAYS_MS)
        with self.assertRaises(ValueError):
            run_case('b5', 'spaced', 'tickets.create',
                     'after_disconnect', -1, self.binary)


if __name__ == '__main__':
    unittest.main()
