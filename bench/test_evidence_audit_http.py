"""验证 HTTP 回执对抗变体只能获得有效的正向授权。"""

from dataclasses import replace
import unittest

from bench.evidence_audit_http import VARIANTS, audit_response, validate_pairs
from bench.evidence_frontier import Context, Decision


class AuditHTTPTests(unittest.TestCase):
    def setUp(self):
        self.context = Context("key", "scope", "digest", "trusted-loopback", 1, 2, False, False)
        self.reply = {"status": "committed", "effect_id": "effect-1",
                      "key": "key", "scope": "scope", "fingerprint": "digest"}

    def test_valid_receipt(self):
        self.assertEqual(audit_response(self.context, self.reply, "valid", 2)[0],
                         Decision.CONFIRM)

    def test_injected_and_absent_proofs_never_confirm(self):
        for variant in VARIANTS:
            if variant != "valid":
                with self.subTest(variant=variant):
                    self.assertEqual(audit_response(self.context, self.reply, variant, 2)[0],
                                     Decision.STOP)
            self.assertEqual(audit_response(self.context, None, variant, 2)[0],
                             Decision.STOP)

    def test_malformed_and_unknown(self):
        with self.assertRaises(ValueError):
            audit_response(self.context, self.reply, "unknown", 2)
        for reply in ({}, {"status": "committed"}, {**self.reply, "effect_id": 1}):
            self.assertEqual(audit_response(self.context, reply, "valid", 2)[0],
                             Decision.STOP)

    def test_clock_rollback(self):
        later = replace(self.context, server_tick_upper=5)
        self.assertEqual(audit_response(later, self.reply, "valid", 2)[0], Decision.STOP)

    def test_incomplete_pairs(self):
        with self.assertRaises(ValueError):
            validate_pairs([])


if __name__ == "__main__":
    unittest.main()
