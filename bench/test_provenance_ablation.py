"""证据来源消融测试：保留正例、缺失证据和真实物理真值边界。"""

import unittest

from bench.provenance_ablation import (
    CASES, Episode, claim_for_case, counterfactual, validate,
)
from bench.evidence_audit_http import audit_response
from bench.evidence_frontier import Context, Decision
from bench.runner import raw_call
from raguard.model import Call


class ProvenanceAblationTests(unittest.TestCase):
    def setUp(self):
        self.target = Call.parse(raw_call("orders.create"))
        self.context = Context(self.target.stable_key, "region-a",
                               self.target.fingerprint, "trusted-loopback",
                               1, 2, False, False)

    def test_single_field_changes(self):
        original = claim_for_case(self.target, "valid")
        self.assertEqual(original, {
            "scope": "region-a", "key": self.target.stable_key,
            "fingerprint": self.target.fingerprint})
        for case in CASES[2:]:
            with self.subTest(case=case):
                altered = claim_for_case(self.target, case)
                self.assertEqual(sum(original[key] != altered[key] for key in original), 1)

    def test_decoy_is_rejected_and_single_check_bypass_detected(self):
        for case in CASES:
            with self.subTest(case=case):
                receipt = claim_for_case(self.target, case)
                if receipt is not None:
                    receipt = {**receipt, "status": "committed",
                               "effect_id": "effect-1"}
                strict = audit_response(self.context, receipt, "valid", 2)[0]
                weak = counterfactual(self.context, receipt, case, 2)
                self.assertEqual(strict, Decision.CONFIRM if case == "valid" else Decision.STOP)
                self.assertEqual(weak, Decision.STOP if case == "absent" else Decision.CONFIRM)

    def test_invalid_case_and_repeat(self):
        with self.assertRaises(ValueError):
            claim_for_case(self.target, "not_registered")
        with self.assertRaises(ValueError):
            counterfactual(self.context, None, "not_registered", 2)

    def test_validate_rejects_false_baseline_claim(self):
        rows = [
            Episode(1, case, "confirm" if case == "valid" else "stop",
                    {"wrong_identity": "identity", "wrong_scope": "scope",
                     "wrong_fingerprint": "fingerprint"}.get(case, "none"),
                    "stop" if case == "absent" else "confirm",
                    int(case == "valid"), int(case not in ("valid", "absent")), 0,
                    int(case not in ("valid", "absent")),
                    int(case != "absent"))
            for case in CASES
        ]
        validate(rows, 1)
        with self.assertRaises(ValueError):
            validate(rows[:-1], 1)
        rows[0] = Episode(**{**vars(rows[0]), "false_confirmation_baseline": 1})
        with self.assertRaises(ValueError):
            validate(rows, 1)


if __name__ == "__main__":
    unittest.main()
