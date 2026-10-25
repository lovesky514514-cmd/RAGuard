"""Regression and negative cases; never touches the runtime gateway."""
import tempfile
import unittest
from pathlib import Path

from bench.semantic_mutation import CLASSES, POLICY, fixture, run, main
from raguard.semantic_guard import SemanticPolicy, audit_stream
from raguard.evidence import EvidenceObservation


class MutationContractTests(unittest.TestCase):
    def test_full_fixed_matrix(self):
        rows = run()
        self.assertEqual(len(rows), 48)
        self.assertEqual({r.mutation for r in rows}, set(CLASSES))
        self.assertEqual(sum(r.false_positive for r in rows), 0)

    def test_existing_whole_history_reference_still_detects_conflict(self):
        _, stream = fixture('business_rebound', 0)
        result = audit_stream(stream, {'operation-0-a':
            fixture('business_rebound', 0)[0]['operation-0-a']}, now=100, policy=POLICY)
        self.assertGreater(result.legacy_conflicts, 0)

    def test_sees_same_effect_id_reused_across_businesses(self):
        c, stream = fixture('cross_business_reuse', 5)
        result = audit_stream(stream, c, now=100, policy=POLICY)
        self.assertEqual([a.reason for a in result.alerts],
                         ['effect_id_reused_across_businesses'])
        self.assertEqual(result.legacy_conflicts, 0)

    def test_monotonic_provider_rule_finds_rollback(self):
        c, stream = fixture('version_rollback', 3)
        result = audit_stream(stream, c, now=100, policy=POLICY)
        self.assertEqual([a.reason for a in result.alerts], ['version_rollback'])
        self.assertEqual(result.legacy_conflicts, 0)

    def test_scope_is_per_authority_tool_epoch(self):
        c, stream = fixture('cross_tool_reuse', 2)
        self.assertEqual(audit_stream(stream, c, now=100, policy=POLICY).alerts, ())

    def test_opt_in_invariants_only(self):
        c, stream = fixture('cross_business_reuse', 0)
        self.assertEqual(audit_stream(stream, c, now=100,
                                     policy=SemanticPolicy()).alerts, ())

    def test_untrusted_or_malformed_evidence_cannot_poison_state(self):
        from dataclasses import replace
        c, stream = fixture('cross_business_reuse', 0)
        poisoned = (replace(stream[0], verified=False), stream[1])
        audit = audit_stream(poisoned, c, now=100, policy=POLICY)
        self.assertEqual(audit.alerts, ())
        self.assertEqual((audit.admitted, audit.unadmitted), (1, 1))

    def test_commit_then_absence_is_detected_by_existing_baseline(self):
        c, stream = fixture('commit_then_absent', 0)
        result = audit_stream(stream, c, now=100, policy=POLICY)
        self.assertGreater(result.legacy_conflicts, 0)

    def test_no_hardcoded_network_or_gateway_dependency(self):
        from raguard import semantic_guard
        self.assertNotIn('urllib', semantic_guard.__dict__)
        self.assertNotIn('Gateway', semantic_guard.__dict__)

    def test_generated_report(self):
        import subprocess, sys
        with tempfile.TemporaryDirectory() as folder:
            res = subprocess.run([sys.executable, '-m', 'bench.semantic_mutation',
                                  '--output', folder], capture_output=True,
                                 text=True, timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual((Path(folder)/'episodes.csv').read_text(
                encoding='utf-8').count('\n'), 49)


if __name__ == '__main__':
    unittest.main()
