"""第一轮安全契约：业务引用、回执回放、补偿与并发。"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from raguard.gateway import Gateway
from raguard.http import create_server
from raguard.identity import BusinessIntentRegistry, IdentitySigner, VerifiedIdentity
from raguard.ledger import Ledger
from raguard.model import Call
from tests.test_gateway import FakeUpstream, request


class RecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.ledger_path = str(Path(self.work.name) / 'ledger.db')
        self.identity_path = str(Path(self.work.name) / 'identities.db')
        self.upstream = FakeUpstream()
        self.gateway = self.make_gateway()

    def make_gateway(self, **kwargs):
        return Gateway(
            Ledger(self.ledger_path), self.upstream,
            identities=BusinessIntentRegistry(self.identity_path), **kwargs,
        )

    @staticmethod
    def business(reference):
        return VerifiedIdentity(reference)

    def test_untrusted_write_cannot_enter_ledger(self):
        status, result = self.gateway.call(request())
        self.assertEqual((status, result['state']), (403, 'identity_required'))
        self.assertEqual(self.upstream.executions, 0)
        self.assertEqual(self.gateway.ledger.events(), [])

    def test_renamed_intent_does_not_duplicate_business_effect(self):
        first = request(step='initial', intent='original')
        renamed = request(step='new-step', intent='new-intent')
        identity = self.business('external-order-100')
        self.assertEqual(self.gateway.call(first, identity=identity)[0], 200)
        status, response = self.gateway.call(renamed, identity=identity)
        self.assertEqual((status, response['action']), (200, 'replay'))
        self.assertEqual(self.upstream.executions, 1)

    def test_two_real_orders_with_identical_arguments_are_not_merged(self):
        a = request(step='first', intent='first')
        b = request(step='second', intent='second')
        self.assertEqual(self.gateway.call(a, identity=self.business('order-001'))[0], 200)
        self.assertEqual(self.gateway.call(b, identity=self.business('order-002'))[0], 200)
        self.assertEqual(self.upstream.executions, 2)

    def test_same_reference_with_modified_amount_is_rejected_before_dispatch(self):
        original = request()
        changed = request(step='changed', intent='changed',
                          args={'sku': 's', 'quantity': 999})
        identity = self.business('external-order-100')
        self.assertEqual(self.gateway.call(original, identity=identity)[0], 200)
        status, response = self.gateway.call(changed, identity=identity)
        self.assertEqual((status, response['state']), (409, 'identity_conflict'))
        self.assertEqual(self.upstream.executions, 1)

    def test_restart_preserves_identity_and_no_duplicate_effect(self):
        first = request(step='original', intent='original')
        second = request(step='renamed', intent='renamed')
        ref = self.business('stable-order-7')
        self.assertEqual(self.gateway.call(first, identity=ref)[0], 200)
        reopened = self.make_gateway()
        self.assertEqual(reopened.call(second, identity=ref)[0], 200)
        self.assertEqual(self.upstream.executions, 1)

    def test_concurrent_renames_have_one_external_effect(self):
        def invoke(i):
            data = request(step=f'step-{i}', intent=f'intent-{i}')
            return self.gateway.call(data, identity=self.business('shared-order'))[0]

        with ThreadPoolExecutor(max_workers=12) as pool:
            statuses = list(pool.map(invoke, range(24)))
        self.assertTrue(all(status in (200, 202) for status in statuses))
        self.assertEqual(self.upstream.executions, 1)

    def test_positive_receipt_can_replay_during_readback_outage(self):
        data = request()
        ref = self.business('business-verified')
        self.assertEqual(self.gateway.call(data, identity=ref)[0], 200)
        self.upstream.cannot_read = True
        status, response = self.gateway.call(data, identity=ref)
        self.assertEqual((status, response['action']), (200, 'replay'))
        self.assertEqual(response['evidence'], 'receipt_only')
        self.assertEqual(self.upstream.executions, 1)

    def test_expired_receipt_does_not_claim_current_state(self):
        data = request()
        ref = self.business('business-expired')
        self.assertEqual(self.gateway.call(data, identity=ref)[0], 200)
        self.upstream.cannot_read = True
        strict = self.make_gateway(receipt_ttl_seconds=0)
        status, response = strict.call(data, identity=ref)
        self.assertEqual((status, response['state']), (409, 'unverifiable'))
        self.assertEqual(self.upstream.executions, 1)

    def test_compensation_must_use_existing_signed_business_reference(self):
        data = request('tickets.create')
        a = self.business('ticket-business-a')
        b = self.business('ticket-business-b')
        self.assertEqual(self.gateway.call(data, identity=a)[0], 200)
        self.assertEqual(self.gateway.compensate(data, approved=True, identity=b)[0], 409)
        self.assertEqual(self.gateway.compensate(data, approved=True, identity=a)[0], 200)
        self.assertEqual(self.gateway.call(data, identity=a)[0], 409)
        self.assertEqual(self.upstream.compensations, 1)


class HttpIdentityBoundaryTests(unittest.TestCase):
    def test_http_rejects_forged_signature_and_allows_valid_signed_call(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeUpstream()
            gateway = Gateway(
                Ledger(str(Path(folder) / 'ledger.db')), fake,
                identities=BusinessIntentRegistry(str(Path(folder) / 'identity.db')),
            )
            secret = 'test-signing-secret-at-least-32-characters'
            server = create_server(gateway, port=0, identity_secret=secret)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                payload = request()
                url = f'http://127.0.0.1:{server.server_port}/v1/calls'

                def post(headers, body):
                    req = urllib.request.Request(
                        url, data=json.dumps(body).encode('utf-8'), headers=headers,
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=3) as response:
                            return response.status
                    except urllib.error.HTTPError as exc:
                        return exc.code

                ref = 'trusted-order-21'
                sig = IdentitySigner(secret).sign(ref, Call.parse(payload))
                headers = {'X-RAGuard-Business-Ref': ref,
                           'X-RAGuard-Business-Signature': sig}
                self.assertEqual(post({}, payload), 403)
                forged = {**headers, 'X-RAGuard-Business-Signature': '0' * 64}
                self.assertEqual(post(forged, payload), 403)
                self.assertEqual(post(headers, request(args={'quantity': 2})), 403)
                self.assertEqual(post(headers, payload), 200)
                renamed = request(step='renamed', intent='renamed')
                self.assertEqual(post(headers, renamed), 200)
                self.assertEqual(fake.executions, 1)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
