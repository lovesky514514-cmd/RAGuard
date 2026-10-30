"""元信息感知规划、硬预算与负面响应测试。"""

from dataclasses import replace
import unittest

from bench.evidence_audit import Label, Reading, Source
from bench.evidence_frontier import Context, Decision, Evidence, Kind
from bench.evidence_meta_planner import CapabilityPlanner, World

CTX = Context("order", "zone", "digest", "official", 1, 0, False, False)
PROOF = Evidence(Kind.COMMITTED, "order", "zone", "digest", "official", 1, 20,
                 authenticated=True, final=True)
SOURCES = (Source("hint", 1, 1), Source("a", 2, 1, True, proof_valid_through=2),
           Source("b", 2, 1, True, proof_valid_through=8),
           Source("arbiter", 3, 2, True, proof_valid_through=9))
WORLDS = (
    World(1, (("hint", Label.ROUTE_A), ("a", Label.COMMIT))),
    World(1, (("hint", Label.ROUTE_A), ("a", Label.ABSENT))),
    World(1, (("hint", Label.ROUTE_B), ("b", Label.COMMIT))),
    World(1, (("hint", Label.ROUTE_B), ("b", Label.ABSENT))),
)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = CapabilityPlanner(SOURCES, WORLDS)

    def test_budget_does_not_silently_expand(self):
        calls = []
        result = self.planner.run(CTX, 0, lambda name: calls.append(name))
        self.assertEqual(calls, [])
        self.assertEqual(result.action, Decision.STOP)

    def test_conditional_queries_and_independent_receipt(self):
        for world in WORLDS:
            def fetch(name):
                label = world.reply(name)
                return Reading(label, (PROOF,) if label == Label.COMMIT else ())
            result = self.planner.run(CTX, 3, fetch)
            self.assertLessEqual(result.cost, 3)
            self.assertNotEqual(result.action, Decision.REISSUE)

    def test_invalid_receipt_never_confirmed(self):
        world = WORLDS[2]
        def fetch(name):
            label = world.reply(name)
            return Reading(label, (replace(PROOF, scope="wrong"),)
                           if label == Label.COMMIT else ())
        result = self.planner.run(CTX, 3, fetch)
        self.assertEqual(result.action, Decision.STOP)

    def test_wrong_prediction_or_deadline_is_not_oracle(self):
        result = self.planner.run(CTX, 3, lambda _: Reading(Label.PENDING))
        self.assertEqual(result.action, Decision.STOP)

    def test_invalid_source_and_world_config_rejected(self):
        with self.assertRaises(ValueError):
            CapabilityPlanner((SOURCES[0], SOURCES[0]), WORLDS)
        with self.assertRaises(ValueError):
            CapabilityPlanner(SOURCES, (World(1, (("absent-source", Label.COMMIT),)),))
