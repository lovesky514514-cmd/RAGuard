"""确保两组方法对相同可信证据应用相同安全规则。"""

import tempfile
import unittest
from pathlib import Path

from bench.evidence_parity import (DEADLINE, ENGINES, TICKS, Scenario, contract,
                                   fixtures, replay_trace, run_matrix, write_results)
from raguard.evidence import EvidenceDecision, assess_evidence


class EvidenceParityTests(unittest.TestCase):
    def setUp(self):
        self.cases = {scenario.name: scenario for scenario in fixtures()}

    def test_no_duplicate_scenario_keys(self):
        self.assertEqual(len(self.cases), len(fixtures()))

    def test_all_methods_share_decisions_and_costs(self):
        for scenario in fixtures():
            with self.subTest(scenario=scenario.name):
                a = replay_trace(scenario, engine=ENGINES[0])
                b = replay_trace(scenario, engine=ENGINES[1])
                self.assertEqual({k: v for k, v in a.items() if k != 'engine'},
                                 {k: v for k, v in b.items() if k != 'engine'})

    def test_no_false_confirmations_or_external_writes(self):
        for row in run_matrix():
            self.assertEqual(row['false_confirmation'], 0)
            self.assertEqual(row['write_dispatches'], 0)

    def test_delayed_positive_evidence_recovered(self):
        result = replay_trace(self.cases['delayed_commit'], engine='raguard')
        self.assertEqual(result['decision'], EvidenceDecision.CONFIRMED.value)
        self.assertEqual(result['evidence_queries'], 3)
        self.assertEqual(result['virtual_wait_seconds'], 2.0)

    def test_missing_weak_or_stale_evidence_escalates(self):
        for name in ('never_committed', 'absence_not_final', 'unverified_claim',
                     'wrong_payload', 'stale_receipt', 'wrong_business',
                     'commit_unobservable', 'wrong_authority_epoch'):
            with self.subTest(name=name):
                decision = replay_trace(self.cases[name], engine='b5_durable')
                self.assertEqual(decision['decision'], EvidenceDecision.ESCALATE.value)
                self.assertEqual(decision['virtual_wait_seconds'], DEADLINE - TICKS[0])

    def test_conflicts_immediately_escalate(self):
        for name in ('conflicting_effect_ids', 'commit_absence_conflict'):
            with self.subTest(name=name):
                result = replay_trace(self.cases[name], engine='raguard')
                self.assertEqual(result['decision'], EvidenceDecision.ESCALATE.value)
                self.assertEqual(result['evidence_queries'], 1)

    def test_read_only_action_does_not_authorize_retry(self):
        evidence = assess_evidence(contract(), (), 100, deadline=105)
        self.assertEqual(evidence.decision, EvidenceDecision.WAIT)
        self.assertFalse(hasattr(evidence, 'retry_authorized'))

    def test_unknown_engine_rejected(self):
        with self.assertRaises(ValueError):
            replay_trace(self.cases['immediate_commit'], engine='unguarded')

    def test_output_contains_no_personal_path_or_details(self):
        with tempfile.TemporaryDirectory() as temp:
            write_results(run_matrix(), Path(temp))
            report = (Path(temp) / 'REPORT.md').read_text(encoding='utf-8')
            csv_data = (Path(temp) / 'episodes.csv').read_text(encoding='utf-8')
            self.assertIn('合成轨迹', report)
            self.assertIn('false_confirmation', csv_data)
            self.assertNotIn('email', report)


if __name__ == '__main__':
    unittest.main()
