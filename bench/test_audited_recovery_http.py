"""隔离恢复阶段只允许审计通过的正向回读确认。"""

import unittest
from unittest.mock import patch

from bench.audited_recovery_http import AuditedRecoveryClient, validate, one_case
from bench.evidence_frontier import Decision
from raguard.transport import UncertainResult


class AuditedRecoveryTests(unittest.TestCase):
    def test_recovery_write_is_blocked(self):
        client = AuditedRecoveryClient("http://127.0.0.1:8187", "after_commit", "valid")
        client.begin_recovery(2)
        with self.assertRaises(UncertainResult):
            client.execute(None, None)
        self.assertEqual(client.blocked_writes, 1)

    def test_no_second_audit_or_readback_before_recovery(self):
        client = AuditedRecoveryClient("http://127.0.0.1:8187", "before_commit", "valid")
        self.assertIsNone(client.readback(None, None))
        client.begin_recovery(2)
        with patch("bench.audited_recovery_http.read_receipt", return_value=None):
            self.assertIsNone(client.readback(type("Call", (), {
                "stable_key": "key", "fingerprint": "digest"})(), None))
        self.assertEqual(client.audit_queries, 1)
        self.assertEqual(client.audit_action, Decision.STOP)

    def test_invalid_variant_and_duplicate_activation(self):
        with self.assertRaises(ValueError):
            AuditedRecoveryClient("http://127.0.0.1:8187", "after_commit", "bad")
        client = AuditedRecoveryClient("http://127.0.0.1:8187", "after_commit", "valid")
        client.begin_recovery(2)
        with self.assertRaises(ValueError):
            client.begin_recovery(3)

    def test_pair_validation_fails_closed(self):
        with self.assertRaises(ValueError):
            validate([])


if __name__ == "__main__":
    unittest.main()
