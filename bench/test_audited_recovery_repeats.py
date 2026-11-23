"""重复研究的固定配对、物理真值校验与离线篡改检测。"""

import unittest

from bench.audited_recovery_repeats import audit, falsification_checks, schedule
from bench.evidence_audit_http import VARIANTS
from bench.window_live import METHODS
from bench.audited_recovery_http import FAULTS


def sample_rows(repeats=2):
    """生成仅供判分器测试的合成行；不冒充真实 Go 运行结果。"""
    rows = []
    for repeat, order, engine, fault, variant in schedule(repeats, 42):
        effects = int(fault == "after_commit")
        confirmed = int(bool(effects) and variant == "valid")
        rows.append(dict(repeat=repeat, order=order, engine=engine, fault=fault,
                         variant=variant, initial_status=409,
                         final_status=200 if confirmed else 409,
                         audit_decision="confirm" if confirmed else "stop",
                         physical_effects=effects, safe_confirmation=confirmed,
                         unsafe_confirmation=0, duplicate_effects=0,
                         blocked_recovery_writes=0 if confirmed else 2,
                         queries=1, real_execute_attempts=effects * 2,
                         added_execute_attempts=0, unresolved=1 - confirmed,
                         audit_reason="受控测试证据"))
    return rows


class RepeatedAuditTests(unittest.TestCase):
    def test_schedule_complete_and_stable(self):
        first = schedule(3, 17)
        self.assertEqual(first, schedule(3, 17))
        self.assertNotEqual(first, schedule(3, 18))
        self.assertEqual(len(first), 3 * len(METHODS) * len(FAULTS) * len(VARIANTS))
        self.assertEqual(len(set((r, e, f, v) for r, _, e, f, v in first)), len(first))

    def test_invalid_parameters_fail(self):
        for repeats, seed in ((0, 1), (101, 1), (True, 1), (1, 1.5)):
            with self.subTest(repeats=repeats, seed=seed):
                with self.assertRaises(ValueError):
                    schedule(repeats, seed)

    def test_valid_pairs_pass(self):
        audit(sample_rows(), 2)

    def test_missing_and_duplicate_pairs_fail(self):
        rows = sample_rows()
        for altered in (rows[:-1], rows + [dict(rows[0])]):
            with self.assertRaisesRegex(ValueError, "配对"):
                audit(altered, 2)

    def test_false_authorization_and_oracle_mismatch_fail(self):
        rows = sample_rows()
        bad = [dict(x) for x in rows]
        bad[0]["unsafe_confirmation"] = 1
        with self.assertRaisesRegex(ValueError, "判分不一致"):
            audit(bad, 2)
        bad = [dict(x) for x in rows]
        bad[0]["physical_effects"] = 1 - bad[0]["physical_effects"]
        with self.assertRaisesRegex(ValueError, "判分不一致"):
            audit(bad, 2)

    def test_offline_falsification_detects_all_mutations(self):
        checks = falsification_checks(sample_rows(), 2)
        self.assertEqual(len(checks), 7)
        self.assertTrue(all(x["detected"] == 1 for x in checks))


if __name__ == "__main__":
    unittest.main()
