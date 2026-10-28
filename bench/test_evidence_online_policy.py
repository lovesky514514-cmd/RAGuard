"""在线选择策略的反馈隔离、预算和漂移行为测试。"""

import unittest

from bench.evidence_online_policy import Choice, SlidingWindowUCB


class OnlinePolicyTests(unittest.TestCase):
    def test_explores_unobserved_source(self):
        agent = SlidingWindowUCB((Choice("cheap", 1), Choice("quick", 2)))
        self.assertEqual(agent.select(), "cheap")
        agent.observe("cheap", False)
        self.assertEqual(agent.select(), "quick")

    def test_shift_changes_preference_with_recent_feedback(self):
        agent = SlidingWindowUCB((Choice("cheap", 1), Choice("quick", 2)),
                                 window=6, exploration=0.1)
        agent.observe("cheap", False)
        agent.observe("quick", True)
        for _ in range(3):
            agent.observe("cheap", False)
            agent.observe("quick", True)
        self.assertEqual(agent.select(), "quick")
        for _ in range(6):
            agent.observe("cheap", True)
        self.assertEqual(agent.select(), "quick")  # 还要重新探索消失的来源
        agent.observe("quick", False)
        self.assertEqual(agent.select(), "cheap")

    def test_invalid_evidence_feedback_rejected(self):
        agent = SlidingWindowUCB((Choice("cheap", 1), Choice("quick", 2)))
        with self.assertRaises(ValueError):
            agent.observe("missing", True)
        with self.assertRaises(ValueError):
            agent.observe("quick", 1)
        with self.assertRaises(ValueError):
            SlidingWindowUCB((Choice("cheap", 1), Choice("cheap", 2)))
