"""统计验收：不允许缺行、重复或错配的数据悄悄进入区间估计。"""

import unittest
from bench.analyze import paired_difference, paired_by_stratum, quantile


def case(method, repeat):
    return dict(method=method, tool='orders.create', fault='after_disconnect',
                rate=1.0, repeat=repeat, seed=42, no_readback=0,
                acknowledged_success=int(method == 'raguard'))


class StatisticalIntegrityTests(unittest.TestCase):
    def test_reject_missing_pair(self):
        with self.assertRaisesRegex(ValueError, '配对不完整'):
            paired_difference([case('b3', 0), case('raguard', 1)])

    def test_reject_duplicate_episode(self):
        with self.assertRaisesRegex(ValueError, '重复试验键'):
            paired_difference([case('b3', 0), case('raguard', 0), case('b3', 0)])

    def test_stratum_keeps_same_business_fault(self):
        samples = [case(method, repeat) for repeat in range(4)
                   for method in ('b3', 'raguard')]
        rows = paired_by_stratum(samples)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['delta_mean'], rows[0]['pairs']), (1.0, 4))

    def test_reject_invalid_quantile(self):
        with self.assertRaises(ValueError):
            quantile([1.0, float('nan')], .5)


if __name__ == '__main__':
    unittest.main()
