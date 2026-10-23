"""Saga 强基线的真实沙箱、持久性和安全补偿测试。"""

import tempfile
import unittest
from pathlib import Path

from scripts.sandbox_binary import sandbox_available
import json
import urllib.request

from bench.runner import build_sandbox, local_sandbox
from bench.saga import SagaExecutor, SagaJournal
from bench.world_client import WorkflowClient
from bench.workflows import Step, prepare_call, run_workflow
from raguard.model import Call


@unittest.skipUnless(sandbox_available(), '需要 Go 或内置 Windows 沙箱')
class SagaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = build_sandbox(Path(cls.temp.name))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def call(self, name='ticket'):
        return Call.parse(prepare_call(Step(name, 'tickets.create', {'message': '维修请求'})))

    def world(self, origin):
        with urllib.request.urlopen(origin+'/v1/lab/effects', timeout=2) as response:
            return json.load(response)

    def test_saga_compensates_once_and_persists_after_restart(self):
        with local_sandbox(self.binary, '', False) as origin, tempfile.TemporaryDirectory() as folder:
            journal_path = str(Path(folder) / 'saga.db')
            call = self.call()
            first = SagaExecutor(SagaJournal(journal_path), WorkflowClient(origin))
            self.assertEqual(first.run([call]).state, 'confirmed')
            second = SagaExecutor(SagaJournal(journal_path), WorkflowClient(origin))
            self.assertEqual(second.run([call]).state, 'confirmed')
            self.assertEqual(self.world(origin)['attempts'], 1)
            self.assertEqual(second.abort([call]).state, 'compensated')
            self.assertEqual(second.abort([call]).state, 'compensated')
            self.assertEqual([e['kind'] for e in self.world(origin)['events']],
                             ['commit', 'compensate'])

    def test_uncertain_commit_not_reissued_after_restart(self):
        with local_sandbox(self.binary, 'tickets.create:after_disconnect', True) as origin, tempfile.TemporaryDirectory() as folder:
            db = str(Path(folder) / 'saga.db')
            call = self.call()
            for _ in range(2):
                result = SagaExecutor(SagaJournal(db), WorkflowClient(origin)).run([call])
                self.assertEqual(result.state, 'stopped')
            self.assertEqual(self.world(origin)['attempts'], 1)
            self.assertEqual(len(self.world(origin)['events']), 1)

    def test_explicit_planner_abort_compensates_in_both_methods(self):
        for method in ('b4', 'raguard'):
            with self.subTest(method=method), local_sandbox(self.binary, '', False) as origin:
                case = run_workflow('ticket_notice', origin, method=method,
                                    abort_after='ticket')
                self.assertEqual((case.status, case.compensation_allowed, case.unsafe),
                                 (409, 1, 0))
                self.assertEqual([e['kind'] for e in self.world(origin)['events']],
                                 ['commit', 'compensate'])

    def test_out_of_order_dependencies_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            saga = SagaExecutor(SagaJournal(str(Path(folder) / 'saga.db')), None)
            bad = Call.parse(prepare_call(Step('notify', 'notifications.send',
                                               {'message': 'x'}, ('ticket',))))
            with self.assertRaisesRegex(ValueError, '依赖'):
                saga.run([bad])

    def test_workflow_reference_baseline_handles_downstream_uncertainty(self):
        with local_sandbox(self.binary, 'notifications.send:after_disconnect', False) as origin:
            case = run_workflow('ticket_notice', origin, method='b4')
        self.assertEqual(case.status, 409)
        self.assertEqual((case.unsafe, case.effects, case.physical_goal), (0, 2, 1))


if __name__ == '__main__':
    unittest.main()
