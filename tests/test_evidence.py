"""证据可采性门禁的独立回归测试，不模拟远程效果已经安全执行。"""

import unittest
from dataclasses import replace

from raguard.evidence import (
    EvidenceContract, EvidenceDecision, EvidenceObservation, assess_evidence,
)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.contract = EvidenceContract('tickets.create', 'business-1', 'sha256:abc',
                                         'provider-A', 2, 10, min_version=4)
        self.proof = EvidenceObservation('tickets.create', 'business-1', 'sha256:abc',
                                         'provider-A', 2, 100, 4, 'committed',
                                         effect_id='effect-1', verified=True, final=True)

    def assess(self, *items, now=105, deadline=None):
        return assess_evidence(self.contract, tuple(items), now, deadline=deadline)

    def test_positive_result(self):
        self.assertEqual(self.assess(self.proof).decision, EvidenceDecision.CONFIRMED)

    def test_foreign_business_rejected(self):
        proof = replace(self.proof, business_ref='different')
        self.assertEqual(self.assess(proof).decision, EvidenceDecision.WAIT)

    def test_payload_drift_rejected(self):
        proof = replace(self.proof, payload_digest='wrong')
        self.assertEqual(self.assess(proof).decision, EvidenceDecision.WAIT)

    def test_wrong_tool_rejected(self):
        self.assertEqual(self.assess(replace(self.proof, tool='orders.create')).decision,
                         EvidenceDecision.WAIT)

    def test_non_authoritative_source_rejected(self):
        self.assertEqual(self.assess(replace(self.proof, authority='agent')).decision,
                         EvidenceDecision.WAIT)

    def test_unverified_source_rejected(self):
        self.assertEqual(self.assess(replace(self.proof, verified=False)).decision,
                         EvidenceDecision.WAIT)

    def test_nonfinal_result_rejected(self):
        self.assertEqual(self.assess(replace(self.proof, final=False)).decision,
                         EvidenceDecision.WAIT)

    def test_stale_result_rejected(self):
        self.assertEqual(self.assess(self.proof, now=111).decision, EvidenceDecision.WAIT)

    def test_future_result_rejected(self):
        self.assertEqual(self.assess(self.proof, now=99).decision, EvidenceDecision.WAIT)

    def test_authority_epoch_mismatch_rejected(self):
        self.assertEqual(self.assess(replace(self.proof, authority_epoch=1)).decision,
                         EvidenceDecision.WAIT)

    def test_old_version_rejected(self):
        self.assertEqual(self.assess(replace(self.proof, version=3)).decision,
                         EvidenceDecision.WAIT)

    def test_missing_effect_id_rejected(self):
        self.assertEqual(self.assess(replace(self.proof, effect_id='')).decision,
                         EvidenceDecision.WAIT)

    def test_negative_read_is_not_proof_of_no_commit(self):
        absence = replace(self.proof, outcome='absent', effect_id='')
        self.assertEqual(self.assess(absence).reason, 'absence_not_final')

    def test_conflicting_effect_ids_escalate(self):
        other = replace(self.proof, effect_id='effect-2')
        self.assertEqual(self.assess(self.proof, other).decision, EvidenceDecision.ESCALATE)

    def test_conflicting_positive_and_absent_escalate(self):
        absence = replace(self.proof, outcome='absent', effect_id='')
        self.assertEqual(self.assess(self.proof, absence).decision, EvidenceDecision.ESCALATE)

    def test_earlier_absence_then_positive_is_normal(self):
        absence = replace(self.proof, outcome='absent', effect_id='')
        newer = replace(self.proof, version=5)
        self.assertEqual(self.assess(absence, newer).decision, EvidenceDecision.CONFIRMED)

    def test_evidence_deadline_escalates(self):
        self.assertEqual(self.assess(deadline=105).decision, EvidenceDecision.ESCALATE)

    def test_invalid_contract(self):
        with self.assertRaises(ValueError):
            EvidenceContract('', 'business-1', 'digest', 'issuer', 2, 10)
        with self.assertRaises(ValueError):
            EvidenceContract('tool', 'business-1', 'digest', 'issuer', 2, -1)


if __name__ == '__main__':
    unittest.main()
