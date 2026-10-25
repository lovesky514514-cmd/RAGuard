"""真实旧进程退出后、同一执行代次的安全交接集成测试。"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from raguard.gateway import Gateway
from raguard.identity import BusinessIntentRegistry
from raguard.ledger import Ledger
from raguard.model import Call
from raguard.policy import TOOLS
from raguard.supervisor import ObservedOwner
from tests.identity_helpers import trusted
from tests.test_gateway import FakeUpstream, request


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.ledger = Ledger(str(root / 'effects.db'))
        self.identities = BusinessIntentRegistry(str(root / 'identity.db'))
        self.upstream = FakeUpstream(cannot_read=True)

    def original(self, raw):
        call = self.identities.resolve(trusted(raw), Call.parse(raw))
        _, new = self.ledger.claim(call)
        self.assertTrue(new)
        return call

    def gateway(self):
        return Gateway(self.ledger, self.upstream, identities=self.identities)

    def dead_worker(self):
        process = subprocess.Popen([sys.executable, '-c', 'import sys;sys.exit(72)'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.wait(timeout=5)
        return process

    def test_confirmed_exit_enables_single_idempotent_takeover(self):
        raw = request('orders.create')
        call = self.original(raw)
        self.assertEqual(self.gateway().call(raw, identity=trusted(raw))[0], 202)
        observed = ObservedOwner(self.dead_worker(), call, 1)
        self.assertTrue(observed.release(self.ledger, TOOLS[call.tool]))
        self.assertEqual(self.gateway().call(raw, identity=trusted(raw))[0], 200)
        self.assertEqual(self.upstream.executions, 1)
        self.assertEqual(self.ledger.record(call).generation, 2)
        self.assertFalse(observed.release(self.ledger, TOOLS[call.tool]))

    def test_wrong_generation_has_no_effect(self):
        raw = request('orders.create')
        call = self.original(raw)
        self.assertFalse(ObservedOwner(self.dead_worker(), call, 99).release(
            self.ledger, TOOLS[call.tool]))
        self.assertEqual(self.gateway().call(raw, identity=trusted(raw))[0], 202)

    def test_opaque_write_must_not_be_reissued(self):
        raw = request('notifications.send')
        call = self.original(raw)
        self.assertFalse(ObservedOwner(self.dead_worker(), call, 1).release(
            self.ledger, TOOLS[call.tool]))
        self.assertEqual(self.gateway().call(raw, identity=trusted(raw))[0], 202)
        self.assertEqual(self.upstream.executions, 0)

    def test_legacy_self_reported_termination_no_longer_accepted(self):
        with self.assertRaises(TypeError):
            Gateway(self.ledger, self.upstream, identities=self.identities,
                    owner_termination='caller_claimed_dead')


if __name__ == '__main__':
    unittest.main()
