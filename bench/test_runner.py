"""验证配对计划、真实故障真值、强基线和统计行为。"""

import tempfile
import unittest
from pathlib import Path

from scripts.sandbox_binary import sandbox_available

from bench.analyze import aggregate, paired_difference, quantile
from bench.faultmatrix import plan
from bench.runner import Trial, build_sandbox, selected_fault, trial_result


class ExperimentPlanTests(unittest.TestCase):
    def test_smoke_plan_pairs_five_single_step_strategies(self):
        trials = plan('smoke', 2, 42)
        self.assertEqual(len(trials), 4 * 2 * 2 * 2 * 5)
        for i in range(0, len(trials), 5):
            block = trials[i:i + 5]
            self.assertEqual(len({(x.tool, x.fault, x.rate, x.repeat, x.seed)
                                  for x in block}), 1)
            self.assertEqual(len({x.method for x in block}), 5)

    def test_random_fault_selection_is_independent_of_method(self):
        base = Trial('b0', 'orders.create', 'after_disconnect', 0.13, 4, 7)
        other = Trial('raguard', base.tool, base.fault, base.rate, base.repeat, base.seed)
        self.assertEqual(selected_fault(base), selected_fault(other))
        self.assertFalse(selected_fault(Trial('b0', base.tool, base.fault, 0, 4, 7)))
        self.assertTrue(selected_fault(Trial('b0', base.tool, base.fault, 1, 4, 7)))

    def test_aggregate_raw_counts_not_overconfident_percentage(self):
        rows = [dict(method='b0', tool='a', fault='f', rate=1.0, no_readback=0,
                     acknowledged_success=0, physical_goal=1, unsafe=1,
                     escalated=1, extra_calls=1, latency_ms=10, fault_fired=1),
                dict(method='b0', tool='a', fault='f', rate=1.0, no_readback=0,
                     acknowledged_success=1, physical_goal=1, unsafe=0,
                     escalated=0, extra_calls=0, latency_ms=20, fault_fired=1)]
        item = aggregate(rows)[0]
        self.assertEqual((item['n'], item['unsafe_n'], item['success_n']), (2, 1, 1))
        self.assertEqual(quantile([10, 20], .95), 20)

    def test_paired_bootstrap_uses_same_repetition(self):
        rows = []
        for repeat in range(8):
            for method in ('b3', 'raguard'):
                rows.append(dict(method=method, tool='t', fault='after', rate=1.0,
                                 repeat=repeat, seed=7, no_readback=0,
                                 acknowledged_success=int(method == 'raguard')))
        result = paired_difference(rows, samples=100)
        self.assertEqual(result['pairs'], 8)
        self.assertEqual(result['delta_mean'], 1.0)


@unittest.skipUnless(sandbox_available(), '需要 Go 或内置 Windows 沙箱')
class RealGoBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = build_sandbox(Path(cls.temp.name))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_trial(self, method, tool, fault, rate=1.0, no_readback=False):
        return trial_result(Trial(method, tool, fault, rate, 0, 8, no_readback), self.binary)

    def test_naive_retry_duplicates_after_commit(self):
        case = self.run_trial('b1', 'tickets.create', 'after_disconnect')
        self.assertEqual((case.fault_fired, case.unsafe, case.duplicate_effects), (1, 1, 1))
        self.assertEqual(case.acknowledged_success, 0)

    def test_strong_b3_reconciles_without_repeat(self):
        case = self.run_trial('b3', 'tickets.create', 'after_disconnect')
        self.assertEqual((case.unsafe, case.acknowledged_success, case.attempts), (0, 1, 1))

    def test_gateway_recovers_after_commit(self):
        case = self.run_trial('raguard', 'tickets.create', 'after_invalid_json')
        self.assertEqual((case.unsafe, case.acknowledged_success), (0, 1))
        self.assertEqual(case.fault_fired, 1)

    def test_gateway_stops_unreadable_opaque_commit(self):
        case = self.run_trial('raguard', 'notifications.send', 'after_disconnect')
        self.assertEqual((case.status, case.unsafe, case.physical_goal), (409, 0, 1))
        self.assertEqual(case.acknowledged_success, 0)

    def test_with_unavailable_readback_stable_idempotent_ack(self):
        case = self.run_trial('raguard', 'orders.create', 'after_disconnect', no_readback=True)
        self.assertEqual((case.status, case.unsafe, case.physical_goal), (200, 0, 1))
        self.assertEqual(case.acknowledged_success, 1)

    def test_b3_strong_retry_can_succeed_with_no_readback(self):
        case = self.run_trial('b3', 'orders.create', 'after_disconnect', no_readback=True)
        self.assertEqual((case.status, case.unsafe, case.physical_goal), (200, 0, 1))

    def test_zero_fault_rate_never_fires(self):
        case = self.run_trial('b0', 'orders.create', 'after_disconnect', rate=0)
        self.assertEqual((case.status, case.fault_selected, case.unsafe), (200, 0, 0))


if __name__ == '__main__':
    unittest.main()
