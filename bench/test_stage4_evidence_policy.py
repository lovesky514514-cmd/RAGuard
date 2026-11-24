"""Stage 4 预注册安全规则、模型来源及公平配对的回归测试。"""

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from bench.stage4_evidence_policy import (
    CASES, EvidenceEpisode, AuditChoice, FIRST, FINAL, PROMPTS,
    evidence_source, policy, run, validate,
)


def sample(repeats=1):
    choices = [
        AuditChoice(n, p, "scripted", "scripted", FIRST[p], int(FIRST[p] == "audit"), "")
        for n in range(1, repeats + 1) for p in range(len(PROMPTS))
    ]
    rows = []
    for n in range(1, repeats + 1):
        for case in CASES:
            for engine in ("b5", "raguard"):
                valid = case == "valid_commit"
                rows.append(EvidenceEpisode(
                    n, case, evidence_source(case), case, policy(case),
                    "confirm" if valid else "stop",
                    policy(case), 1, "", engine, 409, 200 if valid else 409,
                    int(case != "not_observed"), 0, 0, int(case != "not_observed"),
                    int(valid), 0, int(valid), int(not valid),
                ))
    return choices, rows


class Stage4EvidenceTests(unittest.TestCase):
    def test_case_contracts(self):
        self.assertEqual(len(CASES), 6)
        self.assertEqual(sum(policy(case) == "confirm" for case in CASES), 1)
        self.assertEqual(evidence_source("valid_commit"), "real_go_http")
        self.assertEqual(evidence_source("wrong_identity"), "research_layer_injection")
        with self.assertRaises(ValueError):
            policy("unknown_case")

    def test_complete_matrix(self):
        a, b = sample(2)
        validate(a, b, 2, "scripted")
        self.assertEqual(len(a), 2 * len(PROMPTS))
        self.assertEqual(len(b), 2 * len(CASES) * 2)

    def test_missing_rows_fail(self):
        a, b = sample()
        with self.assertRaisesRegex(ValueError, "自主查证矩阵"):
            validate(a[:-1], b, 1, "scripted")
        with self.assertRaisesRegex(ValueError, "证据矩阵"):
            validate(a, b[:-1], 1, "scripted")

    def test_falsified_effects_fail(self):
        a, b = sample()
        changed = list(b)
        changed[0] = replace(changed[0], false_confirmation=1)
        with self.assertRaisesRegex(ValueError, "判分"):
            validate(a, changed, 1, "scripted")

    def test_mismatched_engine_advice_fail(self):
        a, b = sample()
        changed = list(b)
        changed[1] = replace(changed[1], suggested_action="retry_replanned", model_action_correct=0)
        with self.assertRaisesRegex(ValueError, "不匹配"):
            validate(a, changed, 1, "scripted")

    def test_script_cannot_be_labeled_as_model(self):
        a, b = sample()
        with self.assertRaisesRegex(ValueError, "来源标签"):
            validate(a, b, 1, "model")

    def test_invalid_repeat_fails_before_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run(Path(tmp), repeats=0)


if __name__ == "__main__":
    unittest.main()
