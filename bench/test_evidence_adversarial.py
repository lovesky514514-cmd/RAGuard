"""审计时间回退、旧冲突与跨作用域证明的回归。"""

from dataclasses import replace
import unittest
from bench.evidence_audit import Label, Reading, Source, inspect
from bench.evidence_frontier import Context, Decision, Evidence, Kind

CTX = Context("intent", "scope", "digest", "issuer", 1, 4, False, False)
PROOF = Evidence(Kind.COMMITTED, "intent", "scope", "digest", "issuer", 1, 20,
                 authenticated=True, final=True)
OLD = replace(PROOF, kind=Kind.FINAL_NON_EXECUTION)
SRC = Source("reader", 2, 1, True, True, proof_valid_through=5)


class AdversarialAuditTests(unittest.TestCase):
    def test_conflict_precedes_expiry(self):
        result = inspect(CTX, SRC, Reading(Label.HARD_CONFLICT, (PROOF, OLD)), 6)
        self.assertTrue(result.terminal)
        self.assertEqual(result.label, Label.HARD_CONFLICT)

    def test_wrong_scope_precedes_expiry(self):
        result = inspect(CTX, SRC, Reading(Label.COMMIT,
                         (replace(PROOF, scope="wrong"),)), 6)
        self.assertTrue(result.terminal)
        self.assertEqual(result.action, Decision.STOP)

    def test_clock_rollback_rejected(self):
        result = inspect(CTX, SRC, Reading(Label.COMMIT, (PROOF,)), 1)
        self.assertTrue(result.terminal)
        self.assertEqual(result.action, Decision.STOP)

    def test_only_valid_matching_expired_proof_can_refresh(self):
        result = inspect(CTX, SRC, Reading(Label.COMMIT, (PROOF,)), 6)
        self.assertEqual(result.label, Label.STALE)
        self.assertFalse(result.terminal)

    def test_previous_terminal_conflict_rejected(self):
        result = inspect(CTX, SRC, Reading(Label.COMMIT, (PROOF,)), 4,
                         previous=(OLD,))
        self.assertEqual(result.label, Label.HARD_CONFLICT)
        self.assertTrue(result.terminal)


if __name__ == "__main__":
    unittest.main()
