"""影子实验的预算、安全边界、数据隔离和分布偏移测试。"""

import unittest

from bench.scheduler_pilot import BASELINES, evaluate, run, sample, summarize
from raguard.evidence_scheduler import ObservationTrace, Schedule, fit_scheduler


class ShadowSchedulerTests(unittest.TestCase):
    def test_schedule_rejects_invalid_or_duplicate_ticks(self):
        for ticks in ((0, 0, 1), (1, 2), (0, 4, 13), (2, 1, 3), (False, 1, 2)):
            with self.subTest(ticks=ticks), self.assertRaises(ValueError):
                Schedule(ticks)

    def test_no_query_authorizes_write(self):
        for s in BASELINES.values():
            row = evaluate(ObservationTrace('fast', None), s, method='test',
                           seed=0, shifted=False)
            self.assertEqual((row.confirmed, row.false_confirmation,
                              row.write_dispatches), (0, 0, 0))

    def test_positive_evidence_never_confirms_before_visible(self):
        result = evaluate(ObservationTrace('slow', 8), Schedule((0, 4, 12)),
                          method='test', seed=0, shifted=False)
        self.assertEqual((result.confirmed, result.reads, result.first_confirmation_tick),
                         (1, 3, 12))

    def test_fallback_for_unknown_provider(self):
        fit = fit_scheduler(sample(1, 30))
        self.assertEqual(fit.choose('unseen'), fit.pooled)

    def test_no_per_episode_oracle_leakage(self):
        fit = fit_scheduler(sample(1, 30))
        # The same public provider metadata MUST imply the same schedule.
        self.assertEqual(fit.choose('fast'), fit.choose('fast'))
        self.assertNotIn('first_valid_tick', fit.__dict__)

    def test_seeded_data_reproducible(self):
        self.assertEqual(sample(10, 25), sample(10, 25))
        self.assertNotEqual(sample(10, 25), sample(11, 25))

    def test_full_matrix_has_five_matched_methods_and_two_shifts(self):
        rows = run(test_count=15, train_count=15)
        self.assertEqual(len(rows), 5 * 2 * 2 * 15 * 5)
        self.assertEqual(sum(r.false_confirmation for r in rows), 0)
        self.assertEqual(sum(r.write_dispatches for r in rows), 0)
        self.assertTrue(all(1 <= r.reads <= 3 for r in rows))
        self.assertEqual(len(summarize(rows)), 10)


if __name__ == '__main__':
    unittest.main()
