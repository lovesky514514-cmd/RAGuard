"""不允许以超时、负面读数或异常作为外部写入许可。"""

import unittest

from raguard.deferred import EvidenceWaitBudget, wait_for_evidence
from raguard.model import Call
from raguard.policy import TOOLS


CALL = Call.parse(dict(tenant='lab', principal='actor', task='episode', step='one',
                       intent='one', tool='tickets.create',
                       args={'message': 'simulated'}, dependencies=[]))


class DeferredTests(unittest.TestCase):
    def test_delayed_positive_evidence(self):
        calls = []

        def reader(*unused):
            calls.append(1)
            return {'ticket_id': 'mock-1'} if len(calls) == 3 else None

        result = wait_for_evidence(CALL, TOOLS[CALL.tool], reader,
                                   EvidenceWaitBudget(checks=4, interval_seconds=0))
        self.assertTrue(result.confirmed)
        self.assertEqual(result.checks, 3)

    def test_missing_evidence_not_confirmed(self):
        result = wait_for_evidence(CALL, TOOLS[CALL.tool], lambda *x: None,
                                   EvidenceWaitBudget(checks=3, interval_seconds=0))
        self.assertFalse(result.confirmed)
        self.assertEqual(result.checks, 3)

    def test_readback_exception_not_success(self):
        def failing(*unused):
            raise ConnectionError('uncertain')

        result = wait_for_evidence(CALL, TOOLS[CALL.tool], failing,
                                   EvidenceWaitBudget(checks=2, interval_seconds=0))
        self.assertFalse(result.confirmed)

    def test_readback_disabled_cannot_confirm(self):
        result = wait_for_evidence(CALL, TOOLS['notifications.send'],
                                   lambda *x: {'fake': True}, EvidenceWaitBudget())
        self.assertFalse(result.confirmed)
        self.assertEqual(result.checks, 0)

    def test_deadline_caps_queries(self):
        ticks = [0.0]
        def clock():
            return ticks[0]
        def sleep(dt):
            ticks[0] += dt
        result = wait_for_evidence(CALL, TOOLS[CALL.tool], lambda *x: None,
                                   EvidenceWaitBudget(checks=20, interval_seconds=1, deadline_seconds=2),
                                   clock=clock, sleep=sleep)
        self.assertFalse(result.confirmed)
        self.assertEqual(result.checks, 2)

    def test_stops_waiting_after_binding_violation(self):
        checks = []

        def reader(*unused):
            checks.append(1)
            return None

        result = wait_for_evidence(CALL, TOOLS[CALL.tool], reader,
                                   EvidenceWaitBudget(checks=5, interval_seconds=0),
                                   should_continue=lambda: False)
        self.assertEqual(result.reason, 'contract_violation')
        self.assertEqual(result.checks, 1)

    def test_invalid_budget(self):
        for budget in ({'checks': 0}, {'checks': True}, {'interval_seconds': float('nan')},
                       {'deadline_seconds': 0}, {'checks': 33}):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                EvidenceWaitBudget(**budget)

    def test_does_not_change_call_identity(self):
        seen = []
        def reader(call, _policy):
            seen.append(call.stable_key)
            return None
        wait_for_evidence(CALL, TOOLS[CALL.tool], reader,
                          EvidenceWaitBudget(checks=3, interval_seconds=0))
        self.assertEqual(seen, [CALL.stable_key] * 3)


if __name__ == '__main__':
    unittest.main()
