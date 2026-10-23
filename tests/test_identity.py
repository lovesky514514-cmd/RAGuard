"""在登记接口处测试业务身份：不依赖网关内部 SQL 的具体写法。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from raguard.identity import BusinessIntentRegistry, IdentityConflict
from raguard.model import Call


def order(step='step-1', intent='intent-1', quantity=1, principal='alice'):
    return Call.parse({
        'tenant': 'tenant-1', 'principal': principal, 'task': 'task-1',
        'step': step, 'intent': intent, 'tool': 'orders.create',
        'args': {'sku': 'sku-1', 'quantity': quantity}, 'dependencies': [],
    })


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.work = TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.path = str(Path(self.work.name) / 'identities.db')
        self.registry = BusinessIntentRegistry(self.path)

    def test_same_business_reference_uses_first_intent(self):
        first = self.registry.bind('order-external-001', order())
        again = self.registry.bind('order-external-001', order('step-2', 'intent-2'))
        self.assertEqual(first, again)
        self.assertEqual(again.intent, 'intent-1')

    def test_same_reference_with_changed_parameters_is_rejected(self):
        self.registry.bind('order-external-001', order())
        with self.assertRaises(IdentityConflict):
            self.registry.bind('order-external-001', order(quantity=2))

    def test_same_reference_with_different_principal_is_rejected(self):
        self.registry.bind('order-external-001', order())
        with self.assertRaises(IdentityConflict):
            self.registry.bind('order-external-001', order(principal='mallory'))

    def test_same_parameters_can_represent_two_legitimate_orders(self):
        a = self.registry.bind('order-external-001', order())
        b = self.registry.bind('order-external-002', order('step-2', 'intent-2'))
        self.assertNotEqual(a.business_ref, b.business_ref)
        self.assertNotEqual(a.intent, b.intent)

    def test_binding_survives_restart(self):
        original = self.registry.bind('order-external-001', order())
        reopened = BusinessIntentRegistry(self.path)
        self.assertEqual(reopened.bind('order-external-001', order()), original)

    def test_concurrent_claim_returns_single_binding(self):
        calls = [order(f'step-{n}', f'intent-{n}') for n in range(1, 13)]
        with ThreadPoolExecutor(max_workers=12) as executor:
            bindings = list(executor.map(
                lambda item: self.registry.bind('order-external-001', item), calls))
        self.assertEqual(len({b.intent for b in bindings}), 1)

    def test_invalid_business_reference_rejected(self):
        with self.assertRaises(ValueError):
            self.registry.bind('中文业务键 with spaces', order())


if __name__ == '__main__':
    unittest.main()
