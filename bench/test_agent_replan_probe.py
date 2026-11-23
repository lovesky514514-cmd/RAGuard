"""Agent 决策入口测试：不把模型建议当作恢复授权。"""

import unittest

from bench.agent_replan_probe import (
    ACTIONS, Episode, parse_choice, replan, scripted, validate,
    validate_endpoint,
)
from bench.runner import raw_call


class AgentReplanProbeTests(unittest.TestCase):
    def test_choices_strict_json(self):
        for action in ACTIONS:
            self.assertEqual(parse_choice('{"action":"' + action + '"}'), action)
        for value in ("retry_same", "{}", '{"action":"force"}', "[]", "null"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_choice(value)

    def test_remote_endpoint_opt_in(self):
        validate_endpoint("http://127.0.0.1:11434/v1/chat/completions", False)
        validate_endpoint("https://example.test/v1/chat/completions", True)
        for endpoint, allow in (
            ("https://example.test/v1/chat/completions", False),
            ("http://example.test/v1/chat/completions", True),
            ("http://localhost:11434/v1/chat/completions?token=secret", False),
            ("https://user:pass@example.test/v1/chat/completions", True),
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    validate_endpoint(endpoint, allow)

    def test_replanning_changes_identity_not_business_arguments(self):
        original = raw_call("orders.create")
        updated = replan(original)
        self.assertEqual(original["args"], updated["args"])
        self.assertEqual(original["tool"], updated["tool"])
        self.assertNotEqual(original["intent"], updated["intent"])
        self.assertEqual(original["intent"], "target")

    def test_scripted_is_never_labeled_real_model(self):
        for name in ACTIONS:
            choice = scripted(name)
            self.assertEqual((choice.origin, choice.response_sha256), ("scripted", ""))
        with self.assertRaises(ValueError):
            scripted("override")

    def test_reject_missing_model_pair(self):
        rows = [
            Episode("scripted", "scripted", 1, fault, engine, action, "", 409, 409,
                    int(fault == "after_commit"), 0, 0,
                    int(fault == "after_commit") * 2, 0, 0, 1)
            for fault in ("before_commit", "after_commit")
            for action in ACTIONS for engine in ("b5", "raguard")
        ]
        validate(rows, 1, scripted_mode=True)
        with self.assertRaisesRegex(ValueError, "配对"):
            validate(rows[:-1], 1, scripted_mode=True)
        changed = [*rows]
        changed[0] = Episode(**{**vars(changed[0]), "false_confirmation": 1})
        with self.assertRaisesRegex(ValueError, "物理效果"):
            validate(changed, 1, scripted_mode=True)


if __name__ == "__main__":
    unittest.main()
