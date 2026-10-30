"""针对证据越权、过期、冲突与事实推断混淆的安全回归。"""

from dataclasses import replace
import unittest

from bench.evidence_audit import Label, Reading, Source, inspect
from bench.evidence_frontier import Context, Decision, Evidence, Kind

CONTEXT = Context("order", "east", "digest", "official", 2, 0, False, False)
COMMIT = Evidence(Kind.COMMITTED, "order", "east", "digest", "official", 2, 8,
                  authenticated=True, final=True)
NONEXEC = replace(COMMIT, kind=Kind.FINAL_NON_EXECUTION)
PROVER = Source("ledger", 2, 1, can_confirm=True, can_prove_nonexecution=True)


class AuditTests(unittest.TestCase):
    def test_signed_commit_is_confirmable_but_not_reissuable(self):
        got = inspect(CONTEXT, PROVER, Reading(Label.COMMIT, (COMMIT,)), 4)
        self.assertEqual(got.action, Decision.CONFIRM)
        self.assertTrue(got.terminal)

    def test_expired_is_not_invalidated_history_or_authorization(self):
        expired = inspect(CONTEXT, PROVER, Reading(Label.COMMIT, (COMMIT,)), 9)
        self.assertEqual(expired.label, Label.STALE)
        self.assertFalse(expired.terminal)
        self.assertEqual(expired.action, Decision.STOP)

    def test_weak_source_does_not_obtain_execution_rights(self):
        weak = Source("view", 1, 1)
        self.assertTrue(inspect(CONTEXT, weak, Reading(Label.COMMIT, (COMMIT,)), 1).terminal)
        self.assertEqual(inspect(CONTEXT, weak, Reading(Label.COMMIT, (COMMIT,)), 1).action,
                         Decision.STOP)

    def test_binding_authentication_and_proof_role(self):
        for proof in (replace(COMMIT, scope="west"), replace(COMMIT, generation=1),
                      replace(COMMIT, authenticated=False), replace(COMMIT, identity="other")):
            with self.subTest(proof=proof):
                self.assertEqual(inspect(CONTEXT, PROVER, Reading(Label.COMMIT, (proof,)),
                                         1).action, Decision.STOP)

    def test_factual_conflict_stops_without_voting(self):
        got = inspect(CONTEXT, PROVER, Reading(Label.HARD_CONFLICT, (COMMIT, NONEXEC)), 1)
        self.assertTrue(got.terminal)
        self.assertEqual(got.action, Decision.STOP)
        self.assertEqual(got.label, Label.HARD_CONFLICT)
        got = inspect(CONTEXT, PROVER, Reading(Label.COMMIT, (COMMIT,)), 1,
                      previous=(NONEXEC,))
        self.assertEqual(got.action, Decision.STOP)

    def test_inference_disagreement_and_absence_do_not_authorize(self):
        for label in (Label.SOFT_CONFLICT, Label.ABSENT, Label.PENDING, Label.ROUTE_A):
            got = inspect(CONTEXT, PROVER, Reading(label), 1)
            self.assertFalse(got.terminal)
            self.assertEqual(got.action, Decision.STOP)

    def test_unexpected_proofs_fail_closed(self):
        got = inspect(CONTEXT, PROVER, Reading(Label.ABSENT, (COMMIT,)), 1)
        self.assertTrue(got.terminal)
        self.assertEqual(got.action, Decision.STOP)

    def test_nonexecution_claim_must_not_masquerade_as_commit(self):
        got = inspect(CONTEXT, PROVER, Reading(Label.COMMIT, (NONEXEC,)), 1)
        self.assertEqual(got.action, Decision.STOP)
        self.assertTrue(got.terminal)

    def test_same_kind_proofs_cannot_claim_hard_conflict(self):
        got = inspect(CONTEXT, PROVER, Reading(Label.HARD_CONFLICT, (COMMIT, COMMIT)), 1)
        self.assertEqual(got.action, Decision.STOP)
        self.assertTrue(got.terminal)
