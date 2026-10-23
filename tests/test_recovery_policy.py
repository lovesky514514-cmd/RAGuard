"""只在公开的恢复策略边界验证判据，不伪造网络状态。"""

import unittest

from raguard.ledger import Record
from raguard.policy import Effect, ToolPolicy
from raguard.recovery import RetryBudget, cached_evidence


class RecoveryPolicyTests(unittest.TestCase):
    def test_positive_receipt_inside_ttl_can_replay(self):
        policy = ToolPolicy(Effect.REVERSIBLE, readback=True)
        record = Record('confirmed', 1, 10, {'ticket_id': 't1'}, 'positive_readback')
        self.assertEqual(cached_evidence(policy, record, 15, 5), 'receipt_only')
        self.assertIsNone(cached_evidence(policy, record, 15.1, 5))

    def test_unknown_or_untrusted_receipt_never_replays(self):
        policy = ToolPolicy(Effect.REVERSIBLE, readback=True)
        for evidence, state in [('http_receipt', 'confirmed'), ('positive_readback', 'unknown')]:
            record = Record(state, 1, 10, {'ticket_id': 't1'}, evidence)
            self.assertIsNone(cached_evidence(policy, record, 11, 30))

    def test_safe_retry_budget_delay(self):
        budget = RetryBudget(3, 0.1, 3)
        self.assertAlmostEqual(budget.delay(2), 0.9)
        with self.assertRaises(ValueError):
            budget.delay(-1)


if __name__ == '__main__':
    unittest.main()
