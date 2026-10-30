"""真实 HTTP 证据对照的配对边界与同预算测试。"""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from bench.evidence_http_live import BUDGET, SOURCES, paired, run_one


class EvidenceHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.work.name) / ("uncertainty.exe" if os.name == "nt" else "uncertainty")
        root = Path(__file__).resolve().parents[1]
        subprocess.run(["go", "build", "-o", str(cls.binary), "./windowlab/cmd/uncertainty"],
                       cwd=root / "mock", check=True, timeout=120, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_positive_evidence_same_cost_cap_for_both_engines(self):
        rows = [
            run_one(self.binary, "matched", "after_commit", 0, engine, strategy)
            for engine in ("b5", "raguard") for strategy in ("fixed", "fixed_quick", "calibrated")
        ]
        for row in rows:
            self.assertLessEqual(row.evidence_cost, BUDGET.cost)
            self.assertLessEqual(row.evidence_queries, BUDGET.queries)
            self.assertEqual(row.duplicate_effects, 0)
            self.assertGreaterEqual(row.blocked_recovery_writes, 0)
        self.assertEqual(len(SOURCES), 2)
        matched = {(r.engine, r.strategy): r for r in rows}
        for engine in ("b5", "raguard"):
            self.assertEqual(matched[(engine, "calibrated")].confirmed, 1)
            self.assertEqual(matched[(engine, "fixed_quick")].confirmed, 1)
            self.assertEqual(matched[(engine, "fixed_quick")].evidence_cost,
                             matched[(engine, "calibrated")].evidence_cost)
            self.assertEqual(matched[(engine, "fixed_quick")].evidence_queries,
                             matched[(engine, "calibrated")].evidence_queries)
            self.assertEqual(matched[(engine, "fixed")].confirmed, 0)
            self.assertGreater(matched[(engine, "fixed")].blocked_recovery_writes, 0)

    def test_incomplete_pairs_rejected(self):
        with self.assertRaises(ValueError):
            paired([])


if __name__ == "__main__":
    unittest.main()
