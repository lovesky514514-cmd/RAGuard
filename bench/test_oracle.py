"""独立业务真值判定器的回归测试。"""
import unittest
from bench.oracle import EffectEvent as E, evaluate

class OracleTests(unittest.TestCase):
    def test_exactly_once(self):
        self.assertEqual(evaluate([E("commit", "e1", "order:1")], {"order:1": 1}).exactly_once_success, True)

    def test_duplicate_failure(self):
        v = evaluate([E("commit", "e1", "order:1"), E("commit", "e2", "order:1")], {"order:1": 1})
        self.assertFalse(v.task_success)
        self.assertEqual(v.duplicate_executions, 1)

    def test_compensated_duplicate_not_exactly_once(self):
        v = evaluate([E("commit", "e1", "order:1"), E("commit", "e2", "order:1"), E("compensate", "e2", "order:1")], {"order:1": 1})
        self.assertTrue(v.task_success)
        self.assertFalse(v.exactly_once_success)
        self.assertEqual(v.duplicate_executions, 1)

    def test_missing_effect(self):
        v = evaluate([], {"order:1": 1})
        self.assertFalse(v.task_success)
        self.assertEqual(v.missing_effects, 1)

    def test_unintended_effect(self):
        v = evaluate([E("commit", "e1", "order:1"), E("commit", "e2", "order:other")], {"order:1": 1})
        self.assertFalse(v.task_success)
        self.assertEqual(v.unintended_executions, 1)

    def test_duplicate_log_same_physical_effect_not_counted_twice(self):
        v = evaluate([E("commit", "e1", "order:1"), E("commit", "e1", "order:1")], {"order:1": 1})
        self.assertTrue(v.exactly_once_success)

    def test_wrong_compensation_must_fail(self):
        with self.assertRaises(ValueError):
            evaluate([E("compensate", "never", "order:1")], {"order:1": 1})

if __name__ == "__main__":
    unittest.main()
