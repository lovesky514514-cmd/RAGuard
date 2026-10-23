"""真实业务步骤的停止、依赖和补偿契约。"""

import tempfile
import unittest
from pathlib import Path

from scripts.sandbox_binary import sandbox_available

from bench.runner import build_sandbox, local_sandbox
from bench.workflows import run_workflow


@unittest.skipUnless(sandbox_available(), '需要 Go 或内置 Windows 沙箱')
class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = build_sandbox(Path(cls.temp.name))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_confirmed_ticket_cannot_compensate_with_unknown_downstream(self):
        with local_sandbox(self.binary, 'notifications.send:after_disconnect', False) as origin:
            case = run_workflow('ticket_notice', origin, check_compensation=True)
        self.assertEqual(case.status, 409)
        self.assertEqual((case.compensation_allowed, case.effects, case.unsafe), (0, 2, 0))
        self.assertEqual((case.physical_goal, case.acknowledged_success), (1, 0))

    def test_before_commit_failure_has_missing_downstream_effect(self):
        with local_sandbox(self.binary, 'notifications.send:before_disconnect', False) as origin:
            case = run_workflow('ticket_notice', origin, check_compensation=True)
        self.assertEqual(case.status, 409)
        self.assertEqual((case.physical_goal, case.unsafe, case.effects), (0, 0, 1))
        self.assertEqual(case.compensation_allowed, 0)

    def test_no_fault_completes_entire_graph(self):
        with local_sandbox(self.binary, '', False) as origin:
            case = run_workflow('order_notice', origin)
        self.assertEqual((case.status, case.acknowledged_success, case.effects), (200, 1, 2))


if __name__ == '__main__':
    unittest.main()
