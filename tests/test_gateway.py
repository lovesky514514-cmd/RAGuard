"""网关公共接口：可信身份、风险门禁和持久化副作用状态。"""

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from raguard.gateway import Gateway
from raguard.identity import BusinessIntentRegistry
from raguard.ledger import Ledger
from raguard.model import Call
from raguard.policy import Effect
from tests.identity_helpers import trusted


def request(tool="orders.create", step="order", intent="order", deps=(), args=None):
    if args is None:
        args = ({"sku": "s", "quantity": 1} if tool == "orders.create"
                else {"sku": "s"} if tool == "inventory.read" else {"message": "test"})
    return {"tenant": "t", "principal": "p", "task": "task", "step": step,
            "intent": intent, "tool": tool, "args": args, "dependencies": list(deps)}


class FakeUpstream:
    """接口测试替身：维护独立可变化的外部业务状态。"""
    def __init__(self, fail_after=False, cannot_read=False, delay=0):
        self.effects = {}
        self.executions = 0
        self.compensations = 0
        self.readbacks = 0
        self.fail_after = fail_after
        self.cannot_read = cannot_read
        self.delay = delay
        self.lock = threading.Lock()

    def execute(self, call, policy):
        from raguard.transport import UncertainResult
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            if policy.effect == Effect.IDEMPOTENT and call.stable_key in self.effects:
                return self.effects[call.stable_key]
            self.executions += 1
            result = {"effect_id": str(self.executions)}
            self.effects[call.stable_key] = result
            if self.fail_after:
                raise UncertainResult("response lost after commit")
            return result

    def readback(self, call, policy):
        self.readbacks += 1
        if self.cannot_read or not policy.readback:
            return None
        return self.effects.get(call.stable_key)

    def compensate(self, call, result):
        self.compensations += 1
        return True


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(str(Path(self.tmp.name) / "ledger.db"))
        self.identities = BusinessIntentRegistry(str(Path(self.tmp.name) / 'identities.db'))

    def gateway(self, **kwargs):
        upstream = FakeUpstream(**kwargs)
        return Gateway(self.ledger, upstream, identities=self.identities), upstream

    @staticmethod
    def invoke(gateway, raw):
        return gateway.call(raw, identity=trusted(raw))

    def test_write_after_lost_response_uses_positive_evidence(self):
        gateway, upstream = self.gateway(fail_after=True)
        code, result = self.invoke(gateway, request())
        self.assertEqual((code, result["action"]), (200, "replay"))
        self.assertEqual(self.invoke(gateway, request())[0], 200)
        self.assertEqual(upstream.executions, 1)

    def test_verified_idempotent_success_receipt_skips_extra_readback(self):
        gateway, upstream = self.gateway()
        status, body = self.invoke(gateway, request())
        self.assertEqual((status, body['evidence']), (200, 'idempotent_ack'))
        self.assertEqual(upstream.readbacks, 0)
        self.assertEqual(upstream.executions, 1)

    def test_non_idempotent_ticket_still_requires_positive_readback(self):
        gateway, upstream = self.gateway()
        ticket = request('tickets.create')
        status, body = self.invoke(gateway, ticket)
        self.assertEqual((status, body['evidence']), (200, 'positive_readback'))
        self.assertEqual(upstream.readbacks, 1)

    def test_opaque_write_after_lost_response_never_retries(self):
        gateway, upstream = self.gateway(fail_after=True)
        notification = request("notifications.send")
        self.assertEqual(gateway.call(notification, identity=trusted(notification))[0], 409)
        self.assertEqual(gateway.call(notification, identity=trusted(notification))[0], 409)
        self.assertEqual(upstream.executions, 1)

    def test_idempotent_contract_recovers_when_readback_unavailable(self):
        gateway, upstream = self.gateway(fail_after=True, cannot_read=True)
        status, result = self.invoke(gateway, request())
        self.assertEqual((status, result['evidence']), (200, 'idempotent_ack'))
        self.assertEqual(upstream.executions, 1)
        # 正向回读缺失时，已核实的稳定键服务允许同键重试获取回执。
        self.assertEqual(self.invoke(gateway, request())[0], 200)
        self.assertEqual(upstream.executions, 1)

    def test_intent_changes_are_not_merged(self):
        gateway, upstream = self.gateway()
        self.assertEqual(self.invoke(gateway, request())[0], 200)
        changed = request(args={"sku": "s", "quantity": 2})
        code, _ = gateway.call(changed, identity=trusted(changed))
        self.assertEqual(code, 409)
        self.assertEqual(upstream.executions, 1)

    def test_dependency_must_be_confirmed(self):
        gateway, _ = self.gateway()
        dependent = request(deps=["inventory"])
        self.assertEqual(gateway.call(dependent, identity=trusted(dependent))[0], 409)
        inventory = request('inventory.read', 'inventory', 'inventory')
        self.assertEqual(self.invoke(gateway, inventory)[0], 200)
        self.assertEqual(gateway.call(dependent, identity=trusted(dependent))[0], 200)

    def test_stale_owner_is_fenced_after_idempotent_takeover(self):
        gateway, upstream = self.gateway()
        call = request()
        self.identities.bind(trusted(call).business_ref, Call.parse(call))
        old, fresh = self.ledger.claim(Call.parse(call))
        self.assertTrue(fresh)
        self.assertEqual(old.generation, 1)
        takeover = Gateway(self.ledger, upstream, lease_seconds=0,
                           identities=self.identities)
        self.assertEqual(takeover.call(call, identity=trusted(call))[0], 200)
        # 旧代次工作者不能覆盖新代次已确认的结果。
        self.assertFalse(self.ledger.complete(Call.parse(call), 1, "unknown", None, "old_worker"))
        self.assertEqual(self.ledger.record(Call.parse(call)).state, "confirmed")

    def test_stale_opaque_owner_does_not_get_dispatched(self):
        gateway, upstream = self.gateway()
        call = request("notifications.send")
        self.identities.bind(trusted(call).business_ref, Call.parse(call))
        self.ledger.claim(Call.parse(call))
        takeover = Gateway(self.ledger, upstream, lease_seconds=0,
                           identities=self.identities)
        self.assertEqual(takeover.call(call, identity=trusted(call))[0], 409)
        self.assertEqual(upstream.executions, 0)

    def test_parallel_callers_dispatch_one_effect(self):
        gateway, upstream = self.gateway(delay=0.1)
        with ThreadPoolExecutor(max_workers=12) as pool:
            statuses = list(pool.map(
                lambda _: self.invoke(gateway, request())[0], range(24),
            ))
        self.assertTrue(all(status in (200, 202) for status in statuses))
        self.assertEqual(upstream.executions, 1)

    def test_compensation_needs_approval_and_single_claim(self):
        gateway, upstream = self.gateway()
        ticket = request("tickets.create")
        self.assertEqual(gateway.call(ticket, identity=trusted(ticket))[0], 200)
        self.assertEqual(gateway.compensate(ticket, identity=trusted(ticket))[0], 403)
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(lambda _: gateway.compensate(ticket, approved=True, identity=trusted(ticket))[0], range(8)))
        self.assertEqual(statuses.count(200), 1)
        self.assertEqual(upstream.compensations, 1)
        self.assertEqual(gateway.call(ticket, identity=trusted(ticket))[0], 409)

    def test_compensation_blocks_confirmed_descendants(self):
        gateway, upstream = self.gateway()
        ticket = request("tickets.create", "ticket", "ticket")
        self.assertEqual(gateway.call(ticket, identity=trusted(ticket))[0], 200)
        downstream = request("inventory.read", "child", "child", ["ticket"])
        self.assertEqual(gateway.call(downstream, identity=trusted(downstream))[0], 200)
        self.assertEqual(gateway.compensate(ticket, approved=True, identity=trusted(ticket))[0], 409)
        self.assertEqual(upstream.compensations, 0)

    def test_replay_cannot_resurrect_compensated_effect(self):
        ticket = request("tickets.create")
        upstream = FakeUpstream()
        gateway = Gateway(self.ledger, upstream, identities=self.identities)
        self.assertEqual(gateway.call(ticket, identity=trusted(ticket))[0], 200)
        original_readback = upstream.readback
        triggered = False

        def racing_readback(call, policy):
            nonlocal triggered
            if not triggered:
                triggered = True
                # 回读过程中另一个执行者先完成补偿，网关必须拒绝回放。
                status, _ = gateway.compensate(ticket, approved=True, identity=trusted(ticket))
                self.assertEqual(status, 200)
            return original_readback(call, policy)

        upstream.readback = racing_readback
        self.assertEqual(gateway.call(ticket, identity=trusted(ticket))[0], 409)
        self.assertEqual(self.ledger.record(Call.parse(ticket)).state, "compensated")

    def test_malformed_and_unknown_tool_rejected(self):
        gateway, upstream = self.gateway()
        self.assertEqual(gateway.call({"foo": "bar"})[0], 400)
        self.assertEqual(self.invoke(gateway, request(tool="arbitrary.shell"))[0], 403)
        self.assertEqual(upstream.executions, 0)


if __name__ == "__main__":
    unittest.main()