"""证据能力候选策略的保守边界测试。"""

from dataclasses import replace
import unittest

from bench.evidence_frontier import Context, Decision, Evidence, Kind, decide


class EvidenceFrontierTests(unittest.TestCase):
    def setUp(self):
        self.context = Context(
            "intent-1", "region-a", "digest", "provider-a", 3, 20,
            service_fence=False, retry_contract=False,
        )
        self.commit = Evidence(
            Kind.COMMITTED, "intent-1", "region-a", "digest", "provider-a",
            3, 30, authenticated=True, final=True,
        )

    def test_matching_final_commit_confirms(self):
        self.assertEqual(decide(self.context, (self.commit,)).action, Decision.CONFIRM)

    def test_absent_read_does_not_authorize(self):
        value = replace(self.commit, kind=Kind.ABSENT_READ, final=False)
        self.assertEqual(decide(self.context, (value,)).action, Decision.STOP)

    def test_wrong_scope_and_generation_fail(self):
        for item in (
            replace(self.commit, scope="region-b"),
            replace(self.commit, generation=2),
            replace(self.commit, identity="other"),
            replace(self.commit, fingerprint="different"),
        ):
            with self.subTest(item=item):
                self.assertEqual(decide(self.context, (item,)).action, Decision.STOP)

    def test_expiry_and_untrusted_authority_fail(self):
        for item in (
            replace(self.commit, valid_until=19),
            replace(self.commit, authenticated=False),
            replace(self.commit, authority="attacker"),
        ):
            with self.subTest(item=item):
                self.assertEqual(decide(self.context, (item,)).action, Decision.STOP)

    def test_nonexecution_without_fence_stops(self):
        proof = replace(self.commit, kind=Kind.FINAL_NON_EXECUTION)
        self.assertEqual(decide(self.context, (proof,)).action, Decision.STOP)
        self.assertEqual(decide(replace(self.context, retry_contract=True),
                                (proof,)).action, Decision.STOP)

    def test_nonexecution_with_service_fence_and_contract(self):
        proof = replace(self.commit, kind=Kind.FINAL_NON_EXECUTION)
        ctx = replace(self.context, service_fence=True, retry_contract=True)
        self.assertEqual(decide(ctx, (proof,)).action, Decision.REISSUE)

    def test_contradictory_authoritative_evidence_fails(self):
        other = replace(self.commit, kind=Kind.FINAL_NON_EXECUTION)
        self.assertEqual(decide(self.context, (self.commit, other)).action, Decision.STOP)

    def test_unfinalized_commit_does_not_confirm(self):
        self.assertEqual(decide(self.context,
                                (replace(self.commit, final=False),)).action, Decision.STOP)


if __name__ == "__main__":
    unittest.main()
