"""业务改名测试同时包含强幂等对照和无幂等服务。"""

import tempfile
import unittest
from pathlib import Path

from bench.identity_drift import METHODS, renamed_call, run_case
from bench.runner import build_sandbox, raw_call
from scripts.sandbox_binary import sandbox_available


class IdentityDriftUnitTests(unittest.TestCase):
    def test_replanning_only_changes_planner_identity(self):
        original = raw_call('orders.create')
        changed = renamed_call(original)
        self.assertEqual(original['args'], changed['args'])
        self.assertEqual(original['tool'], changed['tool'])
        self.assertNotEqual(original['step'], changed['step'])
        self.assertNotEqual(original['task'], changed['task'])
        self.assertEqual(len(METHODS), 4)


@unittest.skipUnless(sandbox_available(), '需要 Go 或 Windows 内置沙箱')
class IdentityDriftGoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory()
        cls.binary = build_sandbox(Path(cls.work.name))

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def case(self, method, tool, fault='none', no_readback=False):
        return run_case(method, tool, fault, no_readback, self.binary)

    def test_step_based_identity_duplicates_despite_native_idempotency(self):
        baseline = self.case('b3_step', 'orders.create')
        strong = self.case('b3_business', 'orders.create')
        system = self.case('raguard', 'orders.create')
        self.assertEqual(baseline.duplicate_effects, 1)
        self.assertEqual((strong.duplicate_effects, system.duplicate_effects), (0, 0))

    def test_business_key_alone_does_not_protect_non_native_write(self):
        baseline = self.case('b3_business', 'tickets.create')
        system = self.case('raguard', 'tickets.create')
        self.assertEqual(baseline.duplicate_effects, 1)
        self.assertEqual(system.duplicate_effects, 0)

    def test_readback_unavailable_stops_uncertain_non_native_write(self):
        baseline = self.case('b3_business', 'tickets.create', 'after_disconnect', True)
        system = self.case('raguard', 'tickets.create', 'after_disconnect', True)
        self.assertEqual(baseline.unsafe, 1)
        self.assertEqual((system.unsafe, system.renamed_status), (0, 409))
        self.assertEqual(system.physical_goal, 1)

    def test_equal_capability_b5_stops_on_unknown(self):
        baseline = self.case('b5_durable', 'tickets.create', 'after_disconnect', True)
        system = self.case('raguard', 'tickets.create', 'after_disconnect', True)
        self.assertEqual((baseline.unsafe, system.unsafe), (0, 0))
        self.assertEqual((baseline.renamed_status, system.renamed_status), (409, 409))

    def test_equal_capability_b5_recovers_with_evidence(self):
        baseline = self.case('b5_durable', 'tickets.create', 'after_disconnect')
        system = self.case('raguard', 'tickets.create', 'after_disconnect')
        self.assertEqual((baseline.unsafe, system.unsafe), (0, 0))
        self.assertEqual((baseline.renamed_status, system.renamed_status), (200, 200))

    def test_native_idempotency_handles_ambiguous_commit_without_readback(self):
        strong = self.case('b3_business', 'orders.create', 'after_disconnect', True)
        system = self.case('raguard', 'orders.create', 'after_disconnect', True)
        self.assertEqual((strong.unsafe, system.unsafe), (0, 0))
        self.assertEqual((strong.renamed_status, system.renamed_status), (200, 200))