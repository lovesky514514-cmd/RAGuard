"""观察实际本地子进程状态并对执行代次做原子交接的回归测试。"""

import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from raguard.ledger import Ledger
from raguard.model import Call
from raguard.policy import TOOLS
from raguard.supervisor import ObservedOwner


def make_call(tool='orders.create', sku='S-1'):
    return Call.parse({
        'tenant': 'lab', 'principal': 'tester', 'task': 'episode',
        'step': 'task', 'intent': 'task', 'tool': tool,
        'args': {'sku': sku, 'quantity': 1} if tool == 'orders.create'
                else {'message': 'test'}, 'dependencies': [],
    })


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(str(Path(self.tmp.name) / 'effects.sqlite3'))

    def exited(self):
        p = subprocess.Popen([sys.executable, '-c', 'import sys;sys.exit(72)'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p.wait(timeout=5)
        return p

    def test_live_worker_cannot_be_released(self):
        p = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(8)'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            call = make_call()
            self.ledger.claim(call)
            self.assertFalse(ObservedOwner(p, call, 1).release(self.ledger, TOOLS[call.tool]))
            self.assertEqual(self.ledger.record(call).state, 'in_flight')
        finally:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=5)

    def test_generation_and_request_binding(self):
        call = make_call()
        self.ledger.claim(call)
        p = self.exited()
        self.assertFalse(ObservedOwner(p, call, 3).release(self.ledger, TOOLS[call.tool]))
        self.assertFalse(ObservedOwner(p, make_call(sku='changed'), 1).release(
            self.ledger, TOOLS[call.tool]))
        self.assertEqual(self.ledger.record(call).state, 'in_flight')

    def test_concurrent_release_only_once_and_no_old_generation_reuse(self):
        call = make_call()
        self.ledger.claim(call)
        observed = ObservedOwner(self.exited(), call, 1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(
                lambda _: observed.release(self.ledger, TOOLS[call.tool]), range(8)))
        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(self.ledger.record(call).state, 'unknown')
        self.assertEqual(self.ledger.retry_claim(call, 1, 0), 2)
        self.assertFalse(observed.release(self.ledger, TOOLS[call.tool]))
        self.assertEqual(self.ledger.record(call).generation, 2)

    def test_no_release_for_opaque_write_or_confirmed_state(self):
        call = make_call('notifications.send')
        self.ledger.claim(call)
        p = self.exited()
        self.assertFalse(ObservedOwner(p, call, 1).release(self.ledger, TOOLS[call.tool]))
        self.assertEqual(self.ledger.record(call).state, 'in_flight')
        order = make_call('orders.create')
        other = Ledger(str(Path(self.tmp.name) / 'confirmed.sqlite3'))
        other.claim(order)
        self.assertTrue(other.complete(order, 1, 'confirmed', {'order_id': 'o1'}, 'idempotent_ack'))
        self.assertFalse(ObservedOwner(p, order, 1).release(other, TOOLS[order.tool]))
        self.assertEqual(other.record(order).state, 'confirmed')

    def test_invalid_process_or_generation(self):
        call = make_call()
        with self.assertRaises(TypeError):
            ObservedOwner('faked-pid', call, 1)
        with self.assertRaises(ValueError):
            ObservedOwner(self.exited(), call, False)
        with self.assertRaises(ValueError):
            self.ledger.release_terminated(call, False)


if __name__ == '__main__':
    unittest.main()
