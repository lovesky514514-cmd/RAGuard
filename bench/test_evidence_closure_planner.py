"""条件证据规划的预算优势及安全边界测试。"""

from dataclasses import replace
import unittest

from bench.evidence_frontier import Context, Decision, Evidence, Kind
from bench.evidence_closure_planner import ClosurePlanner, Reading, Scenario, Source

CTX = Context("intent", "zone", "digest", "provider", 1, 0, False, False)
PROOF = Evidence(Kind.COMMITTED, "intent", "zone", "digest", "provider", 1, 10, authenticated=True, final=True)
SOURCES = (Source("route", 1), Source("a", 2, True), Source("b", 2, True))
WORLDS = (
    Scenario(1, (("route", "route_a"), ("a", "commit"), ("b", "absent"))),
    Scenario(1, (("route", "route_a"), ("a", "absent"), ("b", "absent"))),
    Scenario(1, (("route", "route_b"), ("a", "absent"), ("b", "commit"))),
    Scenario(1, (("route", "route_b"), ("a", "absent"), ("b", "absent"))),
)

class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = ClosurePlanner(SOURCES, WORLDS)

    def test_contingent_choice_under_budget(self):
        self.assertEqual(self.planner.choose((), 2), ("a", .25))
        self.assertEqual(self.planner.choose((), 3), ("route", .5))
        self.assertEqual(self.planner.choose((("route", "route_b"),), 2), ("b", .5))

    def test_confirm_only_with_valid_actual_receipt(self):
        for world in WORLDS:
            def fetch(name):
                kind = world.reply(name)
                return Reading(kind, PROOF if kind == "commit" else None)
            value = self.planner.execute(CTX, 3, fetch)
            self.assertLessEqual(value.total_cost, 3)
            self.assertEqual(value.decision == Decision.CONFIRM,
                             any(answer == "commit" for _, answer in world.replies))
            self.assertNotEqual(value.decision, Decision.REISSUE)

    def test_unauthenticated_expired_wrong_scope_and_bogus_proof_stop(self):
        for proof in (replace(PROOF, authenticated=False),
                      replace(PROOF, valid_until=-1),
                      replace(PROOF, scope="other"), replace(PROOF, identity="other")):
            with self.subTest(proof=proof):
                def fetch(name):
                    return Reading("route_a") if name == "route" else Reading("commit", proof)
                self.assertEqual(self.planner.execute(CTX, 3, fetch).decision, Decision.STOP)

    def test_unexpected_source_response_cannot_authorize(self):
        self.assertEqual(self.planner.execute(CTX, 3, lambda _: Reading("commit", PROOF)).decision,
                         Decision.STOP)

    def test_budget_and_model_validation(self):
        with self.assertRaises(ValueError):
            self.planner.choose((), -1)
        with self.assertRaises(ValueError):
            ClosurePlanner((Source("route", 1), Source("route", 2)), WORLDS)
        with self.assertRaises(ValueError):
            ClosurePlanner(SOURCES, (Scenario(1, (("route", "commit"),)),))
        with self.assertRaises(ValueError):
            self.planner.choose((("route", "route_a"), ("route", "route_b")), 3)
