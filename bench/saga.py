"""可复现实验用 Saga 基线：持久记录步骤，按反向顺序执行安全补偿。"""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3

from raguard.model import Call, canonical
from raguard.policy import Effect, TOOLS
from raguard.transport import UncertainResult


@dataclass(frozen=True)
class SagaStatus:
    state: str
    completed: int
    compensated: int
    failed_step: str = ''


class SagaJournal:
    """写前记录执行意图；重启后不能把未知提交误判为未执行。"""

    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS steps (
                task TEXT NOT NULL, step TEXT NOT NULL, fingerprint TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, PRIMARY KEY (task,step))''')

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute('PRAGMA busy_timeout=10000')
            yield db

    def state(self, call: Call) -> tuple[str, dict | None] | None:
        with self._db() as db:
            row = db.execute('SELECT fingerprint,state,result FROM steps WHERE task=? AND step=?',
                             (call.task, call.step)).fetchone()
        if row is None:
            return None
        if row[0] != call.fingerprint:
            raise ValueError('Saga 步骤语义变更，拒绝跨请求复用')
        return row[1], json.loads(row[2]) if row[2] else None

    def begin(self, call: Call) -> bool:
        # 提交写前记录，外部调用与数据库不能假设处于同一事务。
        with self._db() as db:
            inserted = db.execute('INSERT OR IGNORE INTO steps(task,step,fingerprint,state) VALUES(?,?,?,?)',
                                  (call.task, call.step, call.fingerprint, 'in_flight')).rowcount
            db.commit()
        self.state(call)
        return bool(inserted)

    def transition(self, call: Call, from_state: str, to_state: str, result: dict | None) -> bool:
        with self._db() as db:
            count = db.execute('''UPDATE steps SET state=?,result=?
                WHERE task=? AND step=? AND fingerprint=? AND state=?''',
                (to_state, canonical(result) if result is not None else None,
                 call.task, call.step, call.fingerprint, from_state)).rowcount
            db.commit()
        return bool(count)


class SagaExecutor:
    """固定依赖链的 Saga 基线，不声称是 SagaLLM 官方代码复现。"""

    def __init__(self, journal: SagaJournal, client):
        self.journal = journal
        self.client = client

    def _confirm(self, call: Call) -> dict | None:
        policy = TOOLS[call.tool]
        if not policy.readback:
            return None
        return self.client.readback(call, policy)

    def _execute(self, call: Call) -> bool:
        policy = TOOLS[call.tool]
        prior = self.journal.state(call)
        if prior and prior[0] == 'confirmed':
            return True
        if prior and prior[0] not in {'unknown', 'in_flight'}:
            return False
        if prior:
            # 进程重启后对不确定提交只取正向证据，不盲目重做。
            evidence = self._confirm(call)
            if evidence is not None:
                return self.journal.transition(call, prior[0], 'confirmed', evidence)
            if not policy.retryable:
                return False
            # 可安全同键重试的操作仍沿用原 logical intent。
            self.journal.transition(call, prior[0], 'in_flight', None)
        else:
            if not self.journal.begin(call):
                # 并发执行者必须观察已有状态，不得同时派发非幂等写。
                prior = self.journal.state(call)
                if prior and prior[0] == 'confirmed':
                    return True
                evidence = self._confirm(call)
                if evidence is not None and prior is not None:
                    return self.journal.transition(call, prior[0], 'confirmed', evidence)
                return False
        for _ in range(2 if policy.retryable else 1):
            try:
                result = self.client.execute(call, policy)
                if policy.readback:
                    result = self._confirm(call) or (result if policy.authoritative_receipt else None)
                if result is None:
                    raise UncertainResult('缺少可采信的状态证据')
                return self.journal.transition(call, 'in_flight', 'confirmed', result)
            except UncertainResult:
                evidence = self._confirm(call)
                if evidence is not None:
                    return self.journal.transition(call, 'in_flight', 'confirmed', evidence)
        self.journal.transition(call, 'in_flight', 'unknown', None)
        return False

    def run(self, calls: list[Call]) -> SagaStatus:
        """执行序列时保留原始调用顺序；未知提交必须停止工作流。"""
        completed = 0
        seen = set()
        for call in calls:
            if call.step in seen or not set(call.dependencies) <= seen:
                raise ValueError('Saga 步骤必须唯一，且依赖须按顺序先确认')
            seen.add(call.step)
        for call in calls:
            if not self._execute(call):
                return SagaStatus('stopped', completed, 0, call.step)
            completed += 1
        return SagaStatus('confirmed', completed, 0)

    def abort(self, calls: list[Call]) -> SagaStatus:
        """只在可信编排器明确终止、且没有未知下游提交时执行补偿。"""
        compensated = 0
        for call in reversed(calls):
            prior = self.journal.state(call)
            if prior is None or prior[0] == 'compensated':
                continue
            if prior[0] != 'confirmed':
                return SagaStatus('manual_review', 0, compensated, call.step)
            policy = TOOLS[call.tool]
            if policy.effect == Effect.READ:
                continue
            if policy.effect != Effect.REVERSIBLE:
                return SagaStatus('manual_review', 0, compensated, call.step)
            if not self.journal.transition(call, 'confirmed', 'compensating', prior[1]):
                return SagaStatus('manual_review', 0, compensated, call.step)
            try:
                success = self.client.compensate(call, prior[1])
            except UncertainResult:
                success = False
            final_state = 'compensated' if success else 'compensation_unknown'
            self.journal.transition(call, 'compensating', final_state, prior[1])
            if not success:
                return SagaStatus('manual_review', 0, compensated, call.step)
            compensated += 1
        return SagaStatus('compensated', 0, compensated)
