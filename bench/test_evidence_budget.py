"""证据预算调度的安全性、成本与漂移测试。"""

from dataclasses import replace
from random import Random
import unittest

from bench.evidence_budget import Budget, Source, calibration, query_budgeted
from bench.evidence_frontier import Context, Decision, Evidence, Kind


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.context = Context("order", "zone", "sha", "server", 1, 5, False, False)
        self.receipt = Evidence(Kind.COMMITTED, "order", "zone", "sha", "server", 1,
                                100, authenticated=True, final=True)
        self.blank = replace(self.receipt, kind=Kind.ABSENT_READ, final=False)
        self.sources = (
            Source("slow", 3, 3, frozenset({Kind.COMMITTED, Kind.ABSENT_READ})),
            Source("cheap", 1, 1, frozenset({Kind.COMMITTED, Kind.ABSENT_READ})),
            Source("quick", 2, 1, frozenset({Kind.COMMITTED, Kind.ABSENT_READ})),
        )

    def test_calibrated_vs_cheap_under_equal_budget(self):
        proofs = {"cheap": self.blank, "quick": self.receipt}
        score = {"quick": .8, "cheap": .2}
        a = query_budgeted(self.context, self.sources, Budget(2, 2, 3),
                           proofs.get, policy="calibrated", estimates=score)
        b = query_budgeted(self.context, self.sources, Budget(2, 2, 3),
                           proofs.get, policy="cheap")
        self.assertEqual((a.verdict.action, a.cost), (Decision.CONFIRM, 2))
        self.assertEqual((b.verdict.action, b.cost), (Decision.STOP, 1))

    def test_zero_budget_and_no_oracle(self):
        calls = []
        def fetch(name):
            calls.append(name)
            return self.receipt
        result = query_budgeted(self.context, self.sources, Budget(0, 2, 3),
                                fetch, policy="fixed")
        self.assertEqual(calls, [])
        self.assertEqual(result.verdict.action, Decision.STOP)

    def test_unverified_or_undeclared_evidence_stops(self):
        for proof in (replace(self.receipt, authenticated=False),
                      replace(self.receipt, kind=Kind.FINAL_NON_EXECUTION),
                      replace(self.receipt, valid_until=5)):
            with self.subTest(proof=proof):
                result = query_budgeted(self.context, (self.sources[2],),
                                        Budget(2, 1, 3), lambda _: proof,
                                        policy="fixed")
                self.assertEqual(result.verdict.action, Decision.STOP)

    def test_nonexecution_requires_fence_and_retry_contract(self):
        proof = replace(self.receipt, kind=Kind.FINAL_NON_EXECUTION)
        source = (Source("finality", 1, 1, frozenset({Kind.FINAL_NON_EXECUTION})),)
        run = lambda ctx: query_budgeted(ctx, source, Budget(1, 1, 1),
                                         lambda _: proof, policy="fixed")
        self.assertEqual(run(self.context).verdict.action, Decision.STOP)
        ctx = replace(self.context, service_fence=True, retry_contract=True)
        self.assertEqual(run(ctx).verdict.action, Decision.REISSUE)

    def test_training_calibration_and_reproducible_random(self):
        scores = calibration([{"quick": self.receipt, "cheap": self.blank},
                              {"quick": self.blank, "cheap": self.receipt}],
                             self.sources, self.context)
        self.assertEqual((scores["quick"], scores["cheap"]), (.5, .5))
        def run():
            return query_budgeted(self.context, self.sources, Budget(3, 2, 3),
                                  lambda _: self.blank, policy="random", rng=Random(42))
        self.assertEqual(run(), run())

    def test_invalid_budget_strategy_and_source(self):
        trials = (
            (Budget(-1, 1, 1), "cheap", self.sources, None),
            (Budget(1, 1, 1), "invalid", self.sources, None),
            (Budget(1, 1, 1), "calibrated", self.sources, None),
            (Budget(1, 1, 1), "fixed", self.sources[:1] * 2, None),
        )
        for budget, policy, sources, estimates in trials:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                query_budgeted(self.context, sources, budget, lambda _: self.receipt,
                               policy=policy, estimates=estimates)


if __name__ == "__main__":
    unittest.main()
