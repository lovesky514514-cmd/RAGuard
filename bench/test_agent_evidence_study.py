"""两轮证据恢复研究：模型权限边界、真实效果判分与严格配对。"""

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from bench.agent_evidence_study import (
    EVIDENCE, FIRST, Episode, parse_action, run, validate,
)


def sample():
    rows = []
    for fault in ("before_commit", "after_commit"):
        for evidence in EVIDENCE:
            for engine in ("b5", "raguard"):
                valid = fault == "after_commit" and evidence == "visible"
                rows.append(Episode(
                    "scripted", "scripted", 1, fault, evidence, engine,
                    "audit", "committed" if valid else "not_observed",
                    "confirm" if valid else "stop", 0, "", 409,
                    200 if valid else 409, int(fault == "after_commit"),
                    0, 0, int(fault == "after_commit"), 1, int(valid),
                    0, 0, int(valid), int(not valid),
                ))
    return rows


class AgentEvidenceTests(unittest.TestCase):
    def test_strict_model_output(self):
        for action in FIRST:
            self.assertEqual(parse_action('{"action":"' + action + '"}', FIRST), action)
        for content in ('{"action":"audit","extra":1}', '{"action":"confirm"}',
                        'not json', 'null', '[]'):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    parse_action(content, FIRST)

    def test_complete_pairs(self):
        validate(sample(), 1)

    def test_missing_pair(self):
        with self.assertRaisesRegex(ValueError, "配对不完整"):
            validate(sample()[:-1], 1)

    def test_reject_false_confirmation(self):
        rows = sample()
        rows[0] = replace(rows[0], false_confirmation=1)
        with self.assertRaisesRegex(ValueError, "不一致"):
            validate(rows, 1)

    def test_reject_b5_raguard_decision_drift(self):
        rows = sample()
        rows[1] = replace(rows[1], final_action="retry_same")
        with self.assertRaisesRegex(ValueError, "建议不一致"):
            validate(rows, 1)

    def test_invalid_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run(Path(tmp), repeats=0)


if __name__ == "__main__":
    unittest.main()
