"""终止凭证仅对已终止的同一执行代次生效。"""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from raguard.gateway import Gateway
from raguard.handoff import OwnerTermination
from raguard.identity import BusinessIntentRegistry
from raguard.ledger import Ledger
from raguard.model import Call
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

    def claimed(self, raw):
        call = self.identities.resolve(trusted(raw), Call.parse(raw))
        record, new = self.ledger.claim(call)
        self.assertTrue(new)
        return call, record

    def gateway(self, termination=None):
        return Gateway(self.ledger, self.upstream, identities=self.identities,
                       lease_seconds=5, owner_termination=termination)

    @staticmethod
    def terminated(call, generation=1):
        return OwnerTermination(call.tenant, call.task, call.intent,
                                call.fingerprint, generation, 72)

    def test_matching_generation_allows_idempotent_recovery(self):
        raw = request('orders.create')
        call, old = self.claimed(raw)
        self.assertEqual(old.generation, 1)
        self.assertEqual(self.gateway(self.terminated(call)).call(raw, identity=trusted(raw))[0], 200)
        self.assertEqual(self.upstream.executions, 1)
        self.assertEqual(self.ledger.record(call).generation, 2)
        self.assertFalse(self.ledger.complete(call, 1, 'unknown', None, 'stale'))

    def test_wrong_generation_cannot_bypass_lease(self):
        raw = request('orders.create')
        call, _ = self.claimed(raw)
        self.assertEqual(self.gateway(self.terminated(call, 99)).call(raw, identity=trusted(raw))[0], 202)
        self.assertEqual(self.upstream.executions, 0)

    def test_old_certificate_does_not_take_over_new_owner(self):
        raw = request('orders.create')
        call, _ = self.claimed(raw)
        self.assertEqual(self.ledger.retry_claim(call, 1, 0), 2)
        self.assertEqual(self.gateway(self.terminated(call, 1)).call(raw, identity=trusted(raw))[0], 202)
        self.assertEqual(self.upstream.executions, 0)

    def test_mismatched_identity_never_takes_over(self):
        raw = request('orders.create')
        call, _ = self.claimed(raw)
        for t in (replace(self.terminated(call), fingerprint='wrong'),
                  replace(self.terminated(call), tenant='other'),
                  replace(self.terminated(call), intent='other')):
            with self.subTest(termination=t):
                self.assertEqual(self.gateway(t).call(raw, identity=trusted(raw))[0], 202)
        self.assertEqual(self.upstream.executions, 0)

    def test_non_idempotent_action_is_not_reexecuted(self):
        raw = request('notifications.send')
        call, _ = self.claimed(raw)
        self.assertIn(self.gateway(self.terminated(call)).call(raw, identity=trusted(raw))[0], (202, 409))
        self.assertEqual(self.upstream.executions, 0)

    def test_invalid_termination_contract_rejected(self):
        raw = request('orders.create')
        call, _ = self.claimed(raw)
        with self.assertRaises(ValueError):
            OwnerTermination(call.tenant, call.task, call.intent, call.fingerprint, 1, 0)
        with self.assertRaises(ValueError):
            self.gateway('untrusted-string')


if __name__ == '__main__':
    unittest.main()
