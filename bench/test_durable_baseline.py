"""独立耐久强基线 B5 的行为和真实 Go 副作用安全测试。"""

import tempfile
import unittest
from pathlib import Path
from bench.durable_baseline import DurableBaseline
from bench.runner import BUSINESS_KEY, build_sandbox, local_sandbox, raw_call
from raguard.transport import SandboxClient
from scripts.sandbox_binary import sandbox_available


class DurableContractTests(unittest.TestCase):
    def test_negative_receipt_ttl_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                DurableBaseline(Path(temp) / 'db.sqlite3', SandboxClient(), receipt_ttl=-1)


@unittest.skipUnless(sandbox_available(), '需要 Go 或内置 Windows 沙箱')
class DurableSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = build_sandbox(Path(cls.temp.name))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def scenario(self, tool, fault='', no_readback=False):
        import json
        from urllib.request import urlopen
        with local_sandbox(self.binary, f'{tool}:{fault}' if fault else '', no_readback) as origin:
            client = SandboxClient(origin, timeout=0.6, research_business_key=BUSINESS_KEY)
            with tempfile.TemporaryDirectory() as temp:
                db = str(Path(temp) / 'b5.sqlite3')
                first = raw_call(tool)
                initial, _ = DurableBaseline(db, client).call(first, 'trusted-business')
                renamed = {**first, 'task': 'new-task', 'step': 'new-step',
                           'intent': 'new-intent'}
                second, _ = DurableBaseline(db, client).call(renamed, 'trusted-business')
                with urlopen(origin + '/v1/lab/effects') as result:
                    effects = json.load(result)['events']
                return initial, second, len(effects)

    def test_idempotent_retry_with_no_readback(self):
        self.assertEqual(self.scenario('orders.create', 'after_disconnect', True), (200, 200, 1))

    def test_non_idempotent_unknown_is_not_retried(self):
        self.assertEqual(self.scenario('tickets.create', 'after_disconnect', True), (409, 409, 1))

    def test_positive_readback_reconciles(self):
        self.assertEqual(self.scenario('tickets.create', 'after_invalid_json'), (200, 200, 1))

    def test_opaque_unknown_is_not_retried(self):
        self.assertEqual(self.scenario('notifications.send', 'after_disconnect'), (409, 409, 1))

    def test_changed_parameters_with_same_business_identity_are_rejected(self):
        with local_sandbox(self.binary, '', False) as origin:
            client = SandboxClient(origin, research_business_key=BUSINESS_KEY)
            with tempfile.TemporaryDirectory() as temp:
                gateway = DurableBaseline(str(Path(temp) / 'b5.sqlite3'), client)
                first = raw_call('tickets.create')
                self.assertEqual(gateway.call(first, 'stable')[0], 200)
                self.assertEqual(gateway.call({**first, 'args': {'message': '其他业务'}},
                                              'stable')[0], 409)
