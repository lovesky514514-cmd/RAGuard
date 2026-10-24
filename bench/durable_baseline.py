"""独立耐久强基线：可信业务键、写前日志和正向证据，不借用 Gateway。"""

import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time

from raguard.model import Call, canonical
from raguard.policy import Effect, TOOLS
from raguard.transport import SandboxClient, UncertainResult


class DurableBaseline:
    """B5：拥有与 RAGuard 同级的业务身份与持久账本；并非外部框架官方复现。"""

    def __init__(self, database: str | Path, client: SandboxClient, *, receipt_ttl: float = 300):
        if receipt_ttl < 0:
            raise ValueError('回执有效期不能为负')
        self.database = str(database)
        self.client = client
        self.receipt_ttl = receipt_ttl
        with self._db() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS durable_operations (
                tenant TEXT NOT NULL, tool TEXT NOT NULL, business_ref TEXT NOT NULL,
                semantic TEXT NOT NULL, original TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, confirmed_at REAL,
                PRIMARY KEY (tenant, tool, business_ref))''')

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.database, timeout=10)
        try:
            conn.execute('PRAGMA busy_timeout=10000')
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _semantic(call: Call) -> str:
        # Agent 可以重新规划 task/step/intent，但不能变更业务主体、参数或依赖。
        return canonical([call.principal, call.args, list(call.dependencies)])

    @staticmethod
    def _original(call: Call) -> str:
        return canonical(dict(tenant=call.tenant, principal=call.principal,
                              task=call.task, step=call.step, intent=call.intent,
                              tool=call.tool, args=call.args,
                              dependencies=list(call.dependencies)))

    def _claim(self, call: Call, business_ref: str):
        # 在执行任何外部写入前完成持久化记录。
        with self._db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            key = (call.tenant, call.tool, business_ref)
            row = conn.execute('''SELECT semantic, original, state, result, confirmed_at
                                  FROM durable_operations
                                  WHERE tenant=? AND tool=? AND business_ref=?''', key).fetchone()
            if row is None:
                conn.execute('''INSERT INTO durable_operations
                                (tenant, tool, business_ref, semantic, original, state)
                                VALUES (?, ?, ?, ?, ?, ?)''',
                             (*key, self._semantic(call), self._original(call), 'pending'))
                return call, 'pending', None, True
        semantic, original, state, result, confirmed_at = row
        if self._semantic(call) != semantic:
            raise ValueError('可信业务引用的参数、主体或依赖发生冲突')
        return Call.parse(json.loads(original)), state, confirmed_at, False

    def _confirmed(self, call: Call, business_ref: str, result: dict) -> tuple[int, str]:
        with self._db() as conn:
            conn.execute('''UPDATE durable_operations SET state='confirmed', result=?, confirmed_at=?
                            WHERE tenant=? AND tool=? AND business_ref=?''',
                         (canonical(result), time.time(), call.tenant, call.tool, business_ref))
        return 200, 'confirmed'

    def call(self, raw: dict, business_ref: str) -> tuple[int, str]:
        call = Call.parse(raw)
        if call.tool not in TOOLS or not isinstance(business_ref, str) or not business_ref:
            return 403, 'identity_required'
        policy = TOOLS[call.tool]
        try:
            original, state, confirmed_at, first = self._claim(call, business_ref)
        except ValueError:
            return 409, 'identity_conflict'
        if state == 'confirmed':
            if confirmed_at is not None and time.time() - confirmed_at <= self.receipt_ttl:
                return 200, 'replay'
            verified = self.client.readback(original, policy)
            if verified is not None:
                return self._confirmed(original, business_ref, verified)
            return 409, 'unverifiable'

        # 未确认状态只能由正向证据恢复，不能把回读失败当作未执行。
        if not first:
            verified = self.client.readback(original, policy)
            if verified is not None:
                return self._confirmed(original, business_ref, verified)
            if not policy.retryable:
                return 409, 'unknown'

        # 在明确具备服务端稳定幂等键的前提下，才可重复原始请求。
        for _ in range(2 if policy.retryable else 1):
            try:
                result = self.client.execute(original, policy)
                if policy.readback:
                    verified = self.client.readback(original, policy)
                    if verified is not None:
                        result = verified
                    elif not (policy.effect == Effect.IDEMPOTENT and policy.authoritative_receipt):
                        return 409, 'unknown'
                return self._confirmed(original, business_ref, result)
            except UncertainResult:
                verified = self.client.readback(original, policy)
                if verified is not None:
                    return self._confirmed(original, business_ref, verified)
        return 409, 'unknown'
