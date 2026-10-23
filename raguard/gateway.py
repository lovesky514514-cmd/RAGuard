"""工具调用入口：先确定业务身份，再执行副作用门禁和恢复。"""

import time

from .identity import BusinessIntentRegistry, IdentityConflict, VerifiedIdentity
from .ledger import Conflict, Ledger
from .model import BadCall, Call
from .policy import Effect, TOOLS, ToolPolicy
from .recovery import RetryBudget, cached_evidence
from .transport import SandboxClient, UncertainResult


def reply(action: str, state: str, *, result: dict | None = None,
          reason: str = '', evidence: str = '') -> dict:
    response = {'action': action, 'state': state, 'result': result, 'reason': reason}
    if evidence:
        response['evidence'] = evidence
    return response


class Gateway:
    """在可验证的前提下重试；未知提交的危险写操作一律停止。"""

    def __init__(self, ledger: Ledger, upstream: SandboxClient,
                 lease_seconds: float = 5.0,
                 *, identities: BusinessIntentRegistry | None = None,
                 receipt_ttl_seconds: float = 300.0,
                 retry_budget: RetryBudget | None = None):
        if lease_seconds < 0 or receipt_ttl_seconds < 0:
            raise ValueError('租约与回执有效期不能小于零')
        self.ledger = ledger
        self.upstream = upstream
        self.lease_seconds = lease_seconds
        self.identities = identities
        self.receipt_ttl_seconds = receipt_ttl_seconds
        self.retry_budget = retry_budget or RetryBudget()

    def _identified(self, call: Call, identity: VerifiedIdentity | None,
                    *, allow_new: bool = True) -> Call:
        if TOOLS[call.tool].effect == Effect.READ:
            return call
        if self.identities is None or identity is None:
            raise IdentityConflict('网关缺少可信业务引用配置或已验证业务引用')
        return self.identities.resolve(identity, call, allow_new=allow_new)

    def call(self, raw: object, *,
             identity: VerifiedIdentity | None = None) -> tuple[int, dict]:
        try:
            call = Call.parse(raw)
        except BadCall as exc:
            return 400, reply('reject', 'invalid', reason=str(exc))
        policy = TOOLS.get(call.tool)
        if policy is None:
            return 403, reply('reject', 'forbidden', reason='工具不在可信注册表')
        try:
            call = self._identified(call, identity)
        except IdentityConflict as exc:
            status = 403 if identity is None or self.identities is None else 409
            state = 'identity_required' if status == 403 else 'identity_conflict'
            return status, reply('reject', state, reason=str(exc))
        try:
            record, is_new = self.ledger.claim(call)
            if is_new:
                return self._execute(call, policy, record.generation)
            if record.state == 'confirmed':
                return self._replay(call, policy)
            if record.state in {'compensated', 'compensating', 'compensation_unknown'}:
                return 409, reply('stop', record.state, reason='补偿已发生或状态不明')

            # 正向回读可以确认成功；没有结果则不代表外部操作未执行。
            verified = self.upstream.readback(call, policy)
            if verified is not None and self.ledger.reconcile(call, verified):
                return 200, reply('replay', 'confirmed', result=verified,
                                  evidence='positive_readback')
            if record.state == 'in_flight' and time.time() - record.updated < self.lease_seconds:
                return 202, reply('wait', 'in_flight', reason='原调用尚未确认')
            if not policy.retryable:
                self.ledger.mark_stale(call, record.generation)
                return 409, reply('stop', 'unknown', reason='可能已写入，禁止重复调用')
            generation = self.ledger.retry_claim(call, record.generation,
                                                 self.lease_seconds)
            if generation is None:
                return 202, reply('wait', 'in_flight', reason='另一请求已取得执行权')
            return self._execute(call, policy, generation)
        except Conflict as exc:
            return 409, reply('stop', 'conflict', reason=str(exc))

    def _replay(self, call: Call, policy: ToolPolicy) -> tuple[int, dict]:
        """复用已确认结果不是重新执行；回执过期则要求新的正向证据。"""
        if policy.readback:
            verified = self.upstream.readback(call, policy)
            if verified is not None:
                if self.ledger.reconcile(call, verified):
                    return 200, reply('replay', 'confirmed', result=verified,
                                      evidence='positive_readback')
                return self._current(call)

        # 检查最新账本状态，避免回读期间的补偿抢先完成。
        record = self.ledger.record(call)
        if record is None or record.state != 'confirmed':
            return self._current(call)
        evidence = cached_evidence(policy, record, time.time(), self.receipt_ttl_seconds)
        if evidence is None:
            return 409, reply('stop', 'unverifiable', reason='确认回执已过期或证据不足')
        return 200, reply('replay', 'confirmed', result=record.result, evidence=evidence)

    def _execute(self, call: Call, policy: ToolPolicy,
                 generation: int) -> tuple[int, dict]:
        attempts = self.retry_budget.attempts if policy.retryable else 1
        for index in range(attempts):
            try:
                result = self.upstream.execute(call, policy)
                if policy.readback:
                    confirmed = self.upstream.readback(call, policy)
                    if confirmed is not None:
                        result = confirmed
                        evidence = 'positive_readback'
                    elif policy.authoritative_receipt and policy.effect == Effect.IDEMPOTENT:
                        # 已核实的稳定幂等键服务，可以用同键成功回执确认证据。
                        evidence = 'idempotent_ack'
                    else:
                        raise UncertainResult('正向回读不可用')
                else:
                    evidence = 'http_receipt' 
                if self.ledger.complete(call, generation, 'confirmed', result, evidence):
                    return 200, reply('execute' if index == 0 else 'retry',
                                      'confirmed', result=result, evidence=evidence)
                return self._current(call)
            except UncertainResult:
                verified = self.upstream.readback(call, policy)
                if verified is not None and self.ledger.reconcile(call, verified):
                    return 200, reply('replay', 'confirmed', result=verified,
                                      evidence='positive_readback')
                if index + 1 < attempts:
                    time.sleep(self.retry_budget.delay(index))
        self.ledger.complete(call, generation, 'unknown', None, 'uncertain_commit')
        return self._current(call)

    def _current(self, call: Call) -> tuple[int, dict]:
        record = self.ledger.record(call)
        if record and record.state == 'confirmed':
            policy = TOOLS[call.tool]
            evidence = cached_evidence(policy, record, time.time(), self.receipt_ttl_seconds)
            if evidence is None:
                return 409, reply('stop', 'unverifiable', reason='现有正向回执过期或证据不足')
            return 200, reply('replay', 'confirmed', result=record.result, evidence=evidence)
        return 409, reply('stop', record.state if record else 'unknown',
                          reason='无足够的副作用证据，需要人工检查')

    def compensate(self, raw: object, *, approved: bool = False,
                   identity: VerifiedIdentity | None = None) -> tuple[int, dict]:
        """仅允许人工批准的已登记动作补偿，禁止新身份借补偿入口写入。"""
        if not approved:
            return 403, reply('reject', 'approval_required', reason='需要人工批准')
        try:
            call = Call.parse(raw)
        except BadCall as exc:
            return 400, reply('reject', 'invalid', reason=str(exc))
        policy = TOOLS.get(call.tool)
        if policy is None or policy.effect != Effect.REVERSIBLE:
            return 403, reply('stop', 'forbidden', reason='没有已登记的补偿动作')
        try:
            call = self._identified(call, identity, allow_new=False)
            claimed = self.ledger.claim_compensation(call)
        except IdentityConflict as exc:
            status = 403 if identity is None or self.identities is None else 409
            state = 'identity_required' if status == 403 else 'identity_conflict'
            return status, reply('reject', state, reason=str(exc))
        except Conflict as exc:
            return 409, reply('stop', 'conflict', reason=str(exc))
        if claimed is None:
            return 409, reply('stop', 'conflict', reason='无可补偿的已确认效果')
        generation, original = claimed
        try:
            confirmed = self.upstream.compensate(call, original)
        except UncertainResult:
            confirmed = False
        self.ledger.finish_compensation(call, generation, confirmed)
        if confirmed:
            return 200, reply('compensate', 'compensated', result=original)
        return 409, reply('stop', 'compensation_unknown', reason='补偿结果不明，不可盲目重试')
