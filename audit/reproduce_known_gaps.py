"""第一轮问题回归：业务意图改名不可重复写入，可信回执允许有限回放。"""

from pathlib import Path
import tempfile

from raguard.gateway import Gateway
from raguard.identity import BusinessIntentRegistry, VerifiedIdentity
from raguard.ledger import Ledger


class MemoryUpstream:
    def __init__(self):
        self.effects = {}
        self.commits = 0
        self.readback_available = True

    def execute(self, call, policy):
        self.commits += 1
        result = {'physical_effect_id': f'write-{self.commits}'}
        self.effects[call.stable_key] = result
        return result

    def readback(self, call, policy):
        if self.readback_available and policy.readback:
            return self.effects.get(call.stable_key)
        return None

    def compensate(self, call, result):
        return True


def order(step, intent):
    return {'tenant': 't', 'principal': 'p', 'task': 'T1',
            'step': step, 'intent': intent, 'tool': 'orders.create',
            'args': {'sku': 'sku-1', 'quantity': 1}, 'dependencies': []}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        upstream = MemoryUpstream()
        gateway = Gateway(
            Ledger(str(Path(tmp) / 'ledger.sqlite')),
            upstream,
            identities=BusinessIntentRegistry(str(Path(tmp) / 'identity.sqlite')),
        )
        ref = VerifiedIdentity('business-order-1')
        first = gateway.call(order('s1', 'agent_id_1'), identity=ref)
        renamed = gateway.call(order('s2', 'agent_id_2'), identity=ref)
        no_duplicate = first[0] == 200 and renamed[0] == 200 and upstream.commits == 1
        print(f'P0 改名后同业务写入一次：{ "PASS" if no_duplicate else "FAIL" }')
        upstream.readback_available = False
        replay = gateway.call(order('s2', 'agent_id_2'), identity=ref)
        receipt = replay[0] == 200 and replay[1].get('evidence') == 'receipt_only'
        print(f'P1 回读中断后有限回放：{ "PASS" if receipt else "FAIL" }')
        if not (no_duplicate and receipt):
            raise SystemExit(1)


if __name__ == '__main__':
    main()
